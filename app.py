"""
FastAPI-wrapper for bok_to_pef.

Containerens standardkommando kan være:

    uvicorn app:app --host 0.0.0.0 --port 34510

API:

    GET  /health
    GET  /status
    POST /process

/process forventer multipart/form-data med:

    file:   XHTML, HTML eller EPUB
    config: JSON-fil med konfigurasjon

Eksempel på config:

{
  "job_id": "test-864110",
  "production_number": "864110",
  "braille_arguments": {
    "page-width": "38",
    "page-height": "29",
    "toc-depth": "2"
  }
}
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import shutil
import tempfile
import traceback
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

from bok2pef import bok_to_pef


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)

logger = logging.getLogger("bok_to_pef.api")


# -----------------------------------------------------------------------------
# Konfigurasjon
# -----------------------------------------------------------------------------

MODULE_NAME = os.getenv("MODULE_NAME", "bok_to_pef")
MODULE_VERSION = os.getenv(
    "BOK_TO_PEF_VERSION",
    os.getenv(f"{MODULE_NAME.upper()}_VERSION", "unknown"),
)

APP_ROOT = Path(__file__).resolve().parent
ARTIFACTS_ROOT = APP_ROOT / "artifacts"

SUPPORTED_XHTML_SUFFIXES = {
    ".html",
    ".htm",
    ".xhtml",
}

SUPPORTED_SUFFIXES = SUPPORTED_XHTML_SUFFIXES | {
    ".epub",
}


# -----------------------------------------------------------------------------
# FastAPI
# -----------------------------------------------------------------------------

app = FastAPI(
    title="Bok til PEF",
    description="Konverterer XHTML til PEF ved hjelp av DAISY Pipeline.",
    version=MODULE_VERSION,
)


# bok2pef og DAISY Pipeline-klienten bruker blant annet globale loggerressurser.
# Vi begrenser derfor denne containeren til én konvertering om gangen.
process_lock = asyncio.Lock()

current_job: dict[str, Any] = {
    "status": "Idle",
    "step": None,
    "job_id": None,
    "production_number": None,
    "filename": None,
    "message": None,
}


# -----------------------------------------------------------------------------
# Hjelpefunksjoner
# -----------------------------------------------------------------------------

def update_status(
    status: str,
    *,
    step: str | None = None,
    job_id: str | None = None,
    production_number: str | None = None,
    filename: str | None = None,
    message: str | None = None,
) -> None:
    """Oppdater global statusinformasjon."""

    current_job.update(
        {
            "status": status,
            "step": step,
            "job_id": job_id,
            "production_number": production_number,
            "filename": filename,
            "message": message,
        }
    )


def safe_filename(filename: str | None, fallback: str = "input.xhtml") -> str:
    """
    Fjern eventuelle katalogkomponenter fra et opplastet filnavn.
    """

    if not filename:
        return fallback

    name = Path(filename).name.strip()
    return name or fallback


def safe_identifier(value: Any, fallback: str) -> str:
    """
    Lag en identifikator som kan brukes som mappenavn.

    Vi tillater bokstaver, tall, bindestrek, understrek og punktum.
    Andre tegn erstattes med understrek.
    """

    text = str(value or "").strip()

    if not text:
        text = fallback

    cleaned = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in text
    )

    cleaned = cleaned.strip("._")

    return cleaned or fallback


async def read_json_upload(config: UploadFile) -> dict[str, Any]:
    """Les og valider JSON-konfigurasjonen."""

    raw = await config.read()

    if not raw:
        return {}

    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail="Config-filen må være UTF-8-kodet JSON.",
        ) from exc

    try:
        value = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                "Ugyldig JSON i config-filen: "
                f"linje {exc.lineno}, kolonne {exc.colno}: {exc.msg}"
            ),
        ) from exc

    if not isinstance(value, dict):
        raise HTTPException(
            status_code=400,
            detail="Config-filen må inneholde et JSON-objekt.",
        )

    return value


def get_braille_arguments(config_data: dict[str, Any]) -> str:
    """
    Hent Pipeline-argumenter fra config.

    Følgende feltnavn støttes:

        braille_arguments
        braille_arguments_from_queue
        arguments

    bok_to_pef forventer en JSON-streng.
    """

    value = config_data.get("braille_arguments")

    if value is None:
        value = config_data.get("braille_arguments_from_queue")

    if value is None:
        value = config_data.get("arguments")

    if value is None:
        # Controlleren sender module_parameters som et flatt config-objekt.
        # Godta derfor de kjente html-to-pef-argumentene direkte, samtidig
        # som tjenestefelter som production_number/job_id holdes utenfor.
        pipeline_argument_names = {
            "transform",
            "stylesheet",
            "page-width",
            "page-height",
            "toc-depth",
            "include-production-notes",
            "hyphenation",
            "include-preview",
            "hyphenation-at-page-breaks",
            "allow-volume-break-inside-leaf-section-factor",
            "prefer-volume-break-before-higher-level-factor",
            "stylesheet-parameters",
        }
        flat_arguments = {
            key: config_data[key]
            for key in pipeline_argument_names
            if key in config_data
        }
        return json.dumps(flat_arguments, ensure_ascii=False)

    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)

    if isinstance(value, str):
        stripped = value.strip()

        if not stripped:
            return "{}"

        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400,
                detail=(
                    "braille_arguments må være et JSON-objekt "
                    "eller en streng som inneholder gyldig JSON."
                ),
            ) from exc

        if not isinstance(decoded, dict):
            raise HTTPException(
                status_code=400,
                detail="braille_arguments må dekode til et JSON-objekt.",
            )

        return json.dumps(decoded, ensure_ascii=False)

    raise HTTPException(
        status_code=400,
        detail=(
            "braille_arguments må være et JSON-objekt "
            "eller en JSON-streng."
        ),
    )


def validate_uploaded_suffix(filename: str) -> str:
    """Kontroller filtypen og returner suffix i små bokstaver."""

    suffix = Path(filename).suffix.lower()

    if suffix not in SUPPORTED_SUFFIXES:
        allowed = ", ".join(sorted(SUPPORTED_SUFFIXES))

        raise HTTPException(
            status_code=415,
            detail=(
                f"Filtypen {suffix or '<ingen filtype>'} støttes ikke. "
                f"Tillatte filtyper: {allowed}."
            ),
        )

    return suffix


def safe_extract_epub(epub_path: Path, destination: Path) -> None:
    """
    Pakk ut EPUB uten path traversal.

    ZIP-medlemmer som forsøker å skrive utenfor destination blir avvist.
    """

    destination_resolved = destination.resolve()

    try:
        archive = zipfile.ZipFile(epub_path)
    except zipfile.BadZipFile as exc:
        raise HTTPException(
            status_code=400,
            detail="EPUB-filen er ikke et gyldig ZIP/EPUB-arkiv.",
        ) from exc

    with archive:
        for member in archive.infolist():
            member_path = PurePosixPath(member.filename)

            if member_path.is_absolute() or ".." in member_path.parts:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "EPUB-filen inneholder en ugyldig eller utrygg sti: "
                        f"{member.filename}"
                    ),
                )

            target = destination.joinpath(*member_path.parts).resolve()

            try:
                target.relative_to(destination_resolved)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "EPUB-filen inneholder en sti utenfor "
                        "utpakkingsområdet."
                    ),
                ) from exc

        archive.extractall(destination)


def find_epub_package_document(epub_root: Path) -> Path:
    """Finn OPF-filen gjennom META-INF/container.xml."""

    container_path = epub_root / "META-INF" / "container.xml"

    if not container_path.is_file():
        raise HTTPException(
            status_code=400,
            detail="EPUB mangler META-INF/container.xml.",
        )

    try:
        root = ET.parse(container_path).getroot()
    except ET.ParseError as exc:
        raise HTTPException(
            status_code=400,
            detail="EPUB har ugyldig META-INF/container.xml.",
        ) from exc

    rootfile = root.find(
        ".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
    )

    if rootfile is None:
        # Litt mer tolerant fallback dersom namespace mangler.
        rootfile = root.find(".//rootfile")

    if rootfile is None:
        raise HTTPException(
            status_code=400,
            detail="Fant ingen rootfile i EPUB-containeren.",
        )

    full_path = rootfile.attrib.get("full-path")

    if not full_path:
        raise HTTPException(
            status_code=400,
            detail="EPUB-rootfile mangler full-path.",
        )

    package_path = epub_root.joinpath(*PurePosixPath(full_path).parts).resolve()

    try:
        package_path.relative_to(epub_root.resolve())
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="EPUB-rootfile peker utenfor EPUB-mappen.",
        ) from exc

    if not package_path.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"Fant ikke package document: {full_path}",
        )

    return package_path


def get_epub_spine_documents(package_path: Path) -> list[Path]:
    """
    Finn XHTML-dokumentene i EPUB-ens spine.

    For øyeblikket kan bok2pef bare få én XHTML-fil. Derfor krever
    denne API-wrapperen at EPUB-en har nøyaktig ett spine-dokument.
    """

    try:
        root = ET.parse(package_path).getroot()
    except ET.ParseError as exc:
        raise HTTPException(
            status_code=400,
            detail="EPUB har en ugyldig OPF-fil.",
        ) from exc

    namespace = ""

    if root.tag.startswith("{"):
        namespace = root.tag.split("}", 1)[0] + "}"

    manifest_items: dict[str, tuple[str, str]] = {}

    for item in root.findall(f".//{namespace}manifest/{namespace}item"):
        item_id = item.attrib.get("id")
        href = item.attrib.get("href")
        media_type = item.attrib.get("media-type", "")

        if item_id and href:
            manifest_items[item_id] = (href, media_type)

    spine_paths: list[Path] = []
    package_dir = package_path.parent.resolve()

    for itemref in root.findall(f".//{namespace}spine/{namespace}itemref"):
        idref = itemref.attrib.get("idref")

        if not idref or idref not in manifest_items:
            continue

        href, media_type = manifest_items[idref]

        href_without_fragment = href.split("#", 1)[0]
        suffix = Path(href_without_fragment).suffix.lower()

        if (
            media_type not in {
                "application/xhtml+xml",
                "text/html",
            }
            and suffix not in SUPPORTED_XHTML_SUFFIXES
        ):
            continue

        document_path = package_dir.joinpath(
            *PurePosixPath(href_without_fragment).parts
        ).resolve()

        try:
            document_path.relative_to(package_dir)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Ugyldig href i EPUB-manifestet: {href}",
            ) from exc

        if not document_path.is_file():
            raise HTTPException(
                status_code=400,
                detail=f"Fant ikke spine-dokumentet {href}.",
            )

        spine_paths.append(document_path)

    return spine_paths


def prepare_input_file(input_path: Path, work_dir: Path) -> Path:
    """
    Klargjør input for bok2pef.

    XHTML og HTML returneres direkte.

    EPUB støttes bare når EPUB-en har nøyaktig ett XHTML-dokument i
    leserekkefølgen. En EPUB med mange dokumenter må først slås sammen
    av en egen EPUB-til-XHTML-komponent.
    """

    suffix = input_path.suffix.lower()

    if suffix in SUPPORTED_XHTML_SUFFIXES:
        return input_path

    if suffix != ".epub":
        raise HTTPException(
            status_code=415,
            detail=f"Kan ikke klargjøre filtypen {suffix}.",
        )

    epub_root = work_dir / "epub"
    epub_root.mkdir(parents=True, exist_ok=True)

    safe_extract_epub(input_path, epub_root)

    package_path = find_epub_package_document(epub_root)
    spine_documents = get_epub_spine_documents(package_path)

    if not spine_documents:
        raise HTTPException(
            status_code=400,
            detail="Fant ingen XHTML-dokumenter i EPUB-ens spine.",
        )

    if len(spine_documents) > 1:
        raise HTTPException(
            status_code=422,
            detail=(
                "EPUB-en inneholder "
                f"{len(spine_documents)} XHTML-dokumenter i leserekkefølgen. "
                "Den nåværende bok2pef-funksjonen tar én samlet XHTML-fil. "
                "Konverter EPUB-en til én samlet XHTML-fil før Bok til PEF."
            ),
        )

    return spine_documents[0]


def find_primary_artifact(job_folder: Path) -> Path | None:
    """Velg primærresultatet, helst en PEF-fil."""

    files = sorted(
        path
        for path in job_folder.rglob("*")
        if path.is_file()
    )

    pef_files = [
        path
        for path in files
        if path.suffix.lower() == ".pef"
    ]

    if pef_files:
        return pef_files[0]

    excluded_names = {
        "manifest.json",
        "log.json",
        "logs.txt",
    }

    for path in files:
        if path.name not in excluded_names:
            return path

    return None


def build_result_zip(
    job_folder: Path,
    *,
    result: dict[str, Any],
    original_filename: str,
    job_id: str,
    production_number: str,
) -> tuple[bytes, str]:
    """
    Lag controller-kompatibel ZIP med manifest og alle artefakter.
    """

    if not job_folder.is_dir():
        raise RuntimeError(
            f"Artefaktmappen ble ikke opprettet: {job_folder}"
        )

    primary_path = find_primary_artifact(job_folder)
    primary_name = None

    if primary_path is not None:
        delivered_path = primary_path.relative_to(job_folder)
        if (primary_path.suffix.lower() == ".pef"
                and production_number.isascii() and production_number.isdigit()
                and production_number.endswith("0")):
            delivered_path = delivered_path.with_name(production_number[:-1] + "3.pef")
            if (job_folder / delivered_path).exists() and job_folder / delivered_path != primary_path:
                raise ValueError(f"Duplicate PEF delivery name: {delivered_path}")
        primary_name = delivered_path.as_posix()

    manifest = {
        "primary": primary_name,
        "secondary": None,

        # Controlleren forventer at manifest["log"] peker på JSON.
        "log": "log.json",

        # Behold eventuell menneskelesbar logg som et separat artefakt,
        # men ikke oppgi den som den strukturerte loggen.
        "text_log": (
            "logs.txt"
            if (job_folder / "logs.txt").is_file()
            else None
        ),

        "report": None,
        "status": result.get("status"),
        "message": result.get("message"),
        "module": MODULE_NAME,
        "module_version": MODULE_VERSION,
        "job_id": job_id,
        "production_number": production_number,
        "source": original_filename,
    }

    log_records = [
        {
            "level": "INFO",
            "message": f"{MODULE_NAME} {MODULE_VERSION}",
            "timestamp": None,
        },
        {
            "level": (
                "INFO"
                if str(result.get("status", "")).lower()
                in {"success", "done"}
                else "ERROR"
            ),
            "message": str(
                result.get("message")
                or "Ingen melding fra bok_to_pef."
            ),
            "timestamp": None,
        },
    ]

    buffer = io.BytesIO()

    with zipfile.ZipFile(
        buffer,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                manifest,
                indent=2,
                ensure_ascii=False,
            ),
        )

        archive.writestr(
            "log.json",
            json.dumps(
                log_records,
                indent=2,
                ensure_ascii=False,
            ),
        )

        for artifact in sorted(job_folder.rglob("*")):
            if not artifact.is_file():
                continue

            relative_name = artifact.relative_to(job_folder).as_posix()

            # manifest.json og log.json lages av API-wrapperen.
            if relative_name in {
                "manifest.json",
                "log.json",
            }:
                continue

            archive.write(
                artifact,
                arcname=primary_name if artifact == primary_path else relative_name,
            )

    zip_name = f"{production_number}.zip"

    return buffer.getvalue(), zip_name


# -----------------------------------------------------------------------------
# Endepunkter
# -----------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict[str, Any]:
    """Returner containerens helsetilstand."""

    return {
        "status": "ok",
        "health": "ok",
        "module": MODULE_NAME,
        "version": MODULE_VERSION,
        "busy": process_lock.locked(),
    }


@app.get("/status")
async def status() -> dict[str, Any]:
    """Returner status for gjeldende eller siste jobb."""

    return dict(current_job)


@app.post("/process")
async def process(
    file: UploadFile = File(...),
    config: UploadFile = File(...),
    file2: UploadFile | None = File(default=None),
) -> Response:
    """
    Kjør Bok til PEF.

    file2 aksepteres av hensyn til controller-kontrakten, men brukes ikke.
    """

    del file2

    if process_lock.locked():
        raise HTTPException(
            status_code=409,
            detail="Bok til PEF behandler allerede en annen jobb.",
        )

    async with process_lock:
        temp_root: Path | None = None
        job_folder: Path | None = None

        original_filename = safe_filename(file.filename)
        validate_uploaded_suffix(original_filename)

        config_data = await read_json_upload(config)

        fallback_production_number = Path(original_filename).stem
        production_number = safe_identifier(
            config_data.get("production_number")
            or config_data.get("produksjonsnummer")
            or fallback_production_number,
            fallback=fallback_production_number or "unknown",
        )

        job_id = safe_identifier(
            config_data.get("job_id")
            or config_data.get("jobId")
            or uuid.uuid4().hex,
            fallback=uuid.uuid4().hex,
        )

        braille_arguments = get_braille_arguments(config_data)

        job_folder = ARTIFACTS_ROOT / job_id

        update_status(
            "Running",
            step="1/4 - mottar filer",
            job_id=job_id,
            production_number=production_number,
            filename=original_filename,
            message=None,
        )

        logger.info(
            "Starter /process: job_id=%s, production_number=%s, file=%s",
            job_id,
            production_number,
            original_filename,
        )

        try:
            temp_root = Path(
                tempfile.mkdtemp(prefix=f"bok-to-pef-{job_id}-")
            )

            upload_dir = temp_root / "input"
            upload_dir.mkdir(parents=True, exist_ok=True)

            input_path = upload_dir / original_filename

            with input_path.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    output.write(chunk)

            if input_path.stat().st_size == 0:
                raise HTTPException(
                    status_code=400,
                    detail="Den opplastede filen er tom.",
                )

            update_status(
                "Running",
                step="2/4 - klargjør input",
                job_id=job_id,
                production_number=production_number,
                filename=original_filename,
            )

            prepared_input = prepare_input_file(
                input_path,
                temp_root,
            )

            logger.info(
                "Input til bok2pef: %s",
                prepared_input,
            )

            # Fjern eventuelle artefakter fra en tidligere jobb med samme ID.
            if job_folder.exists():
                shutil.rmtree(job_folder)

            update_status(
                "Running",
                step="3/4 - kjører DAISY Pipeline",
                job_id=job_id,
                production_number=production_number,
                filename=original_filename,
            )

            result = await asyncio.to_thread(
                bok_to_pef,
                html=str(prepared_input),
                braille_arguments_from_queue=braille_arguments,
                job_id=job_id,
                production_number=production_number,
            )

            if not isinstance(result, dict):
                raise RuntimeError(
                    "bok_to_pef returnerte ikke et resultatobjekt: "
                    f"{result!r}"
                )

            result_status = str(result.get("status", "")).lower()

            update_status(
                "Running",
                step="4/4 - pakker resultat",
                job_id=job_id,
                production_number=production_number,
                filename=original_filename,
                message=str(result.get("message") or ""),
            )

            zip_bytes, zip_name = build_result_zip(
                job_folder,
                result=result,
                original_filename=original_filename,
                job_id=job_id,
                production_number=production_number,
            )

            if result_status not in {
                "success",
                "done",
            }:
                update_status(
                    "Failed",
                    step=None,
                    job_id=job_id,
                    production_number=production_number,
                    filename=original_filename,
                    message=str(
                        result.get("message")
                        or "Konverteringen feilet."
                    ),
                )

                # Vi returnerer ZIP-en også ved Pipeline-feil, fordi den
                # inneholder logs.txt og den forberedte XHTML-filen.
                return Response(
                    content=zip_bytes,
                    status_code=422,
                    media_type="application/zip",
                    headers={
                        "Content-Disposition": (
                            f'attachment; filename="{zip_name}"'
                        ),
                        "X-Job-Status": "failed",
                        "X-Job-Id": job_id,
                    },
                )

            update_status(
                "Completed",
                step=None,
                job_id=job_id,
                production_number=production_number,
                filename=original_filename,
                message=str(result.get("message") or ""),
            )

            logger.info(
                "Fullførte /process: job_id=%s, production_number=%s",
                job_id,
                production_number,
            )

            return Response(
                content=zip_bytes,
                media_type="application/zip",
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="{zip_name}"'
                    ),
                    "X-Job-Status": "success",
                    "X-Job-Id": job_id,
                },
            )

        except HTTPException as exc:
            update_status(
                "Failed",
                step=None,
                job_id=job_id,
                production_number=production_number,
                filename=original_filename,
                message=str(exc.detail),
            )

            raise

        except Exception as exc:
            logger.error(
                "Uventet feil i /process:\n%s",
                traceback.format_exc(),
            )

            update_status(
                "Failed",
                step=None,
                job_id=job_id,
                production_number=production_number,
                filename=original_filename,
                message=str(exc),
            )

            return JSONResponse(
                status_code=500,
                content={
                    "status": "failed",
                    "message": str(exc),
                    "job_id": job_id,
                    "production_number": production_number,
                },
            )

        finally:
            if temp_root is not None:
                shutil.rmtree(
                    temp_root,
                    ignore_errors=True,
                )

            # Artefaktene er nå pakket inn i responsen og kan ryddes.
            if job_folder is not None:
                shutil.rmtree(
                    job_folder,
                    ignore_errors=True,
                )
