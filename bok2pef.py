import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import zipfile
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from lxml import etree

from config import Config
from daisy_pipeline import DaisyPipelineJob


PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_ROOT / "static"
JAR_DIR = PROJECT_ROOT / "jar"
UID = "bok_to_pef"

XSLT_STEPS = (
    (
        "prepare-for-braille.xsl",
        "Tilpasser innhold for punktskrift…",
    ),
    (
        "pre-processing.xsl",
        "Bedre hefteinndeling, fjern tittelside og innholdsfortegnelse, "
        "flytte kolofon og opphavsrettside til slutten av boka…",
    ),
    (
        "add-table-classes.xsl",
        "Bedre håndtering av tabeller…",
    ),
    (
        "insert-boilerplate.xsl",
        "Lag ny tittelside og bokinformasjon…",
    ),
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


class InMemoryLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.log_stream = io.StringIO()

    def emit(self, record):
        self.log_stream.write(self.format(record) + "\n")

    def get_logs(self):
        return self.log_stream.getvalue()


def _env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _pipeline_url():
    value = os.getenv("DAISY_PIPELINE_URL", "").strip()

    if not value:
        port = os.getenv("DAISY_PIPELINE_PORT", "8181").strip() or "8181"
        value = f"http://daisypipeline2:{port}/ws"

    value = value.rstrip("/")
    if not value.endswith("/ws"):
        value += "/ws"

    return value


def _check_pipeline(base_url):
    """Rask tilgjengelighetstest. /ws alene gir normalt 404."""
    scripts_url = f"{base_url.rstrip('/')}/scripts"
    timeout = (
        _env_float("DAISY_PIPELINE_CONNECT_TIMEOUT_SECONDS", 2),
        _env_float("DAISY_PIPELINE_READ_TIMEOUT_SECONDS", 5),
    )

    try:
        response = requests.get(
            scripts_url,
            timeout=timeout,
            headers={"Accept": "application/xml"},
        )
    except requests.RequestException as exc:
        return False, f"Pipeline svarte ikke på {scripts_url}: {exc}"

    if response.status_code != 200:
        return False, f"Pipeline svarte HTTP {response.status_code} på {scripts_url}"

    if "html-to-pef" not in response.text:
        return False, f"Pipeline svarer, men html-to-pef finnes ikke på {scripts_url}"

    return True, scripts_url


def _pipeline_versions():
    preferred = (
        os.getenv("DAISY_PIPELINE_VERSION", "1.15.2"),
        os.getenv("DAISY_HTML_TO_PEF_VERSION", "8.2.1"),
    )

    result = [preferred]

    for item in (
        *getattr(Config, "PIP_PEF_TEST", ()),
        *getattr(Config, "PIP_PEF_STABLE", ()),
    ):
        if isinstance(item, (tuple, list)) and len(item) == 2:
            pair = (str(item[0]), str(item[1]))
            if pair not in result:
                result.append(pair)

    return result


def _resolve_saxon_jar():
    configured = os.getenv("SAXON_JAR", "").strip()
    candidates = []

    if configured:
        candidates.append(Path(configured))

    candidates.extend(
        [
            JAR_DIR / "saxon-he-9.5.1.5-1.jar",
            Path("/app/jar/saxon-he-9.5.1.5-1.jar"),
        ]
    )

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    raise FileNotFoundError(
        "Fant ikke Saxon JAR. Sjekket: "
        + ", ".join(str(path) for path in candidates)
    )


def _resolve_static_file(filename):
    path = STATIC_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"Mangler static-fil: {path}")
    return path


def _resolve_braille_stylesheet():
    """
    Finn samme braille.scss som det gamle produksjonssystemet brukte.

    Den verifiserte gamle filen lå i:
      /usr/src/app/xslt/prepare-for-braille/braille.scss

    I den nye modulen skal den ligge i:
      /app/static/braille.scss
    """
    candidates = [
        STATIC_DIR / "braille.scss",
        PROJECT_ROOT / "xslt" / "prepare-for-braille" / "braille.scss",
    ]

    for candidate in candidates:
        if candidate.is_file():
            logger.info("Bruker punktskrift-stylesheet: %s", candidate)
            return candidate

    raise FileNotFoundError(
        "Fant ikke braille.scss. Sjekket: "
        + ", ".join(str(path) for path in candidates)
    )


def _filter_pipeline_arguments(base_url, script_id, arguments):
    """
    Filtrer argumentsettet mot det html-to-pef faktisk eksponerer.

    Nyere html-to-pef-versjoner eksponerer ikke alle legacy-parametrene som
    fantes i eldre PEF-produksjoner. Disse må derfor ikke sendes som vanlige
    Pipeline-options, fordi DaisyPipelineJob ellers bare vil ignorere dem.

    Returnerer:
      - filtered_arguments: argumentene som faktisk kan serialiseres til jobRequest
      - unsupported_arguments: legacy/ukjente argumenter som ikke sendes direkte
    """
    script_url = f"{base_url.rstrip('/')}/scripts/{script_id}"
    timeout = (
        _env_float("DAISY_PIPELINE_CONNECT_TIMEOUT_SECONDS", 5),
        _env_float("DAISY_PIPELINE_JOB_REQUEST_TIMEOUT_SECONDS", 120),
    )

    try:
        response = requests.get(
            script_url,
            timeout=timeout,
            headers={"Accept": "application/xml"},
        )
        response.raise_for_status()
        root = etree.fromstring(response.content)
    except Exception as exc:
        raise RuntimeError(
            f"Klarte ikke å lese scriptbeskrivelsen fra {script_url}: {exc}"
        ) from exc

    supported = set()

    for element in root.xpath(
        "//*[local-name()='input' or local-name()='option']"
    ):
        name = element.get("name")
        if name:
            supported.add(name)

    filtered = {
        name: value
        for name, value in arguments.items()
        if name in supported
    }

    unsupported = {
        name: value
        for name, value in arguments.items()
        if name not in supported
    }

    logger.info(
        "html-to-pef eksponerer %d input/options: %s",
        len(supported),
        sorted(supported),
    )

    logger.info(
        "DAISY Pipeline-parametre som faktisk sendes: %s",
        filtered,
    )

    if unsupported:
        logger.warning(
            "Legacy-parametre som ikke sendes direkte til html-to-pef: %s",
            unsupported,
        )

    return filtered, unsupported


def _transfer_metadata_from_html_to_pef(
    html_file,
    pef_file,
    pipeline_version,
    script_id,
    script_version,
    provenance_arguments,
    log,
):
    """
    Kopier metadata fra HTML til PEF og legg inn DAISY Pipeline-provenance.

    Basert på transfer_metadata_from_html_to_pef() i det gamle
    produksjonssystemet. Hele argumentsettet beholdes som provenance,
    også argumenter som ikke eksponeres direkte av dagens html-to-pef.
    """
    html_file = Path(html_file)
    pef_file = Path(pef_file)

    html_xml = etree.parse(str(html_file)).getroot()
    pef_xml_document = etree.parse(str(pef_file))
    pef_xml = pef_xml_document.getroot()

    html_meta_elements = html_xml.xpath(
        "/*/*[local-name()='head']/*"
    )
    pef_meta_matches = pef_xml.xpath(
        "/*/*[local-name()='head']/*[local-name()='meta']"
    )

    if not pef_meta_matches:
        raise RuntimeError(f"Fant ikke <meta> i PEF-filen {pef_file}")

    pef_meta = pef_meta_matches[0]
    pef_meta_elements = pef_xml.xpath(
        "/*/*[local-name()='head']/*[local-name()='meta']/*"
    )

    dc_namespace = "http://purl.org/dc/elements/1.1/"
    dc = "{" + dc_namespace + "}"

    tail = None
    lasttail = None

    for meta in pef_meta_elements:
        if tail is None:
            tail = meta.tail
        lasttail = meta.tail

        if (
            meta.tag in [f"{dc}format", f"{dc}date"]
            or str(meta.tag).endswith("sheet-count")
        ):
            meta.tail = tail

    if tail is None:
        tail = "\\n         "
    if lasttail is None:
        lasttail = "\\n      "

    for meta in html_meta_elements:
        tag = None
        value = None
        prefix = None
        local_name = str(meta.tag).split("}")[-1]

        if local_name == "title":
            namespace = dc_namespace
            tag = "{" + namespace + "}title"
            value = meta.text or ""

        elif (
            local_name != "meta"
            or "name" not in meta.attrib
            or "content" not in meta.attrib
        ):
            continue

        elif meta.attrib["name"] in [
            "dc:format",
            "dcterms:modified",
            "viewport",
        ]:
            continue

        elif meta.attrib["name"] == "description":
            namespace = dc_namespace
            tag = "{" + namespace + "}description.abstract"
            value = meta.attrib["content"]

        else:
            meta_name = meta.attrib["name"]
            prefix = meta_name.split(":")[0] if ":" in meta_name else None
            if prefix is None:
                continue

            namespace = meta.nsmap.get(prefix)
            if namespace is None:
                continue

            tag = "{" + namespace + "}" + meta_name.split(":", 1)[1]
            value = meta.attrib["content"]

        nsmap = {}
        if prefix and namespace:
            nsmap[prefix] = namespace
        elif namespace == dc_namespace:
            nsmap["dc"] = dc_namespace

        element = etree.Element(tag, nsmap=nsmap or None)
        element.text = value

        if namespace == dc_namespace:
            element = etree.Comment(
                " " + etree.tounicode(element) + " "
            )

        element.tail = tail
        pef_meta.append(element)

    additional_metadata = [
        (
            "daisy-pipeline-engine-version",
            "nlbprod",
            "http://www.nlb.no/production",
            None,
            pipeline_version,
        ),
        (
            "daisy-pipeline-script-id",
            "nlbprod",
            "http://www.nlb.no/production",
            None,
            script_id,
        ),
        (
            "daisy-pipeline-script-version",
            "nlbprod",
            "http://www.nlb.no/production",
            None,
            script_version,
        ),
    ]

    for argument, argument_value in provenance_arguments.items():
        if argument in ("source", "html"):
            continue

        values = (
            argument_value
            if isinstance(argument_value, list)
            else [argument_value]
        )

        for value in values:
            additional_metadata.append(
                (
                    "daisy-pipeline-argument",
                    "nlbprod",
                    "http://www.nlb.no/production",
                    argument,
                    str(value),
                )
            )

    for tagname, prefix, namespace, attribname, value in additional_metadata:
        if value is None:
            continue

        element = etree.Element(
            "{" + namespace + "}" + tagname,
            nsmap={prefix: namespace},
        )

        if attribname is not None:
            element.attrib["name"] = attribname

        element.text = str(value)
        element.tail = tail
        pef_meta.append(element)

    children = pef_meta.xpath("*")
    if children:
        children[-1].tail = lasttail

    pef_xml_document.write(
        str(pef_file),
        method="XML",
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=False,
    )

    log.info(
        "Kopierte HTML-metadata og DAISY Pipeline-provenance til %s",
        pef_file,
    )


def _add_metadata_to_pef_output(
    pef_output_dir,
    html_file,
    pipeline_version,
    script_id,
    script_version,
    provenance_arguments,
    log,
):
    """Legg metadata i alle PEF-filer i resultatmappen."""
    pef_files = list(Path(pef_output_dir).rglob("*.pef"))

    if not pef_files:
        raise RuntimeError(
            f"Fant ingen .pef-filer i resultatmappen {pef_output_dir}"
        )

    for pef_file in pef_files:
        _transfer_metadata_from_html_to_pef(
            html_file=html_file,
            pef_file=pef_file,
            pipeline_version=pipeline_version,
            script_id=script_id,
            script_version=script_version,
            provenance_arguments=provenance_arguments,
            log=log,
        )


def _local_name(tag):
    """Returner lokalt XML-navn uten namespace."""
    if not isinstance(tag, str):
        return ""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _normalize_language(value):
    """
    Normaliser språk til en verdi som html-to-pef/Liblouis kan bruke.

    Vi beholder eksplisitte språkverdier, men normaliserer vanlige norske
    varianter. Hvis språk mangler helt brukes BOK_TO_PEF_DEFAULT_LANGUAGE,
    standard "nb".
    """
    value = (value or "").strip().replace("_", "-")

    if not value:
        value = os.getenv("BOK_TO_PEF_DEFAULT_LANGUAGE", "nb").strip() or "nb"

    lower = value.lower()

    aliases = {
        "no-no": "no",
        "nb-no": "nb",
        "nn-no": "nn",
        "nob": "nb",
        "nno": "nn",
        "nor": "no",
    }

    return aliases.get(lower, lower)


def _ensure_xhtml_namespace(root, log):
    """
    Sørg for at HTML-elementer ligger i XHTML-namespace.

    XSLT-ene bruker namespace-aware XPath. En rå, men ellers XML-gyldig fil
    med <html><head>... uten XHTML-namespace ville derfor passert parsing,
    men nesten ingen XSLT-regler ville treffe.
    """
    xhtml_ns = "http://www.w3.org/1999/xhtml"
    changed = 0

    for element in root.iter():
        if not isinstance(element.tag, str):
            continue

        if element.tag.startswith("{"):
            continue

        element.tag = f"{{{xhtml_ns}}}{element.tag}"
        changed += 1

    if changed:
        log.warning(
            "Input manglet XHTML-namespace på %d elementer; "
            "normaliserte dem til %s.",
            changed,
            xhtml_ns,
        )

    return xhtml_ns


def _first_element_by_local_name(root, name):
    for element in root.iter():
        if _local_name(element.tag).lower() == name.lower():
            return element
    return None


def _find_meta(head, name):
    wanted = name.lower()

    for element in head:
        if _local_name(element.tag).lower() != "meta":
            continue

        meta_name = (element.get("name") or "").strip().lower()
        if meta_name == wanted:
            return element

    return None


def _text_content(element):
    if element is None:
        return ""

    return " ".join(
        part.strip()
        for part in element.itertext()
        if part and part.strip()
    ).strip()


def _derive_document_title(root, head, source_path):
    """
    Finn en trygg tittel uten å være avhengig av NLB/Statped-metadata.
    Prioritet:
      1. <head><title>
      2. dc:title
      3. første element med epub:type som inneholder fulltitle/title
      4. første h1
      5. filnavnet
    """
    for child in head:
        if _local_name(child.tag).lower() == "title":
            value = _text_content(child)
            if value:
                return value

    meta = _find_meta(head, "dc:title")
    if meta is not None:
        value = (meta.get("content") or "").strip()
        if value:
            return value

    for element in root.iter():
        epub_type = ""
        for attr_name, attr_value in element.attrib.items():
            if _local_name(attr_name) == "type":
                # Bare bruk namespaced epub:type eller eksplisitt "epub:type".
                if (
                    attr_name == "epub:type"
                    or "idpf.org/2007/ops" in attr_name
                ):
                    epub_type = attr_value or ""
                    break

        tokens = set(epub_type.split())
        if {"fulltitle", "title"} & tokens:
            value = _text_content(element)
            if value:
                return value

    for element in root.iter():
        if _local_name(element.tag).lower() == "h1":
            value = _text_content(element)
            if value:
                return value

    return source_path.stem or "Untitled"


def _derive_document_language(root, head):
    """
    Finn språk fra dc:language, html/@xml:lang, html/@lang eller body.
    """
    meta = _find_meta(head, "dc:language")
    if meta is not None:
        value = (meta.get("content") or "").strip()
        if value:
            return _normalize_language(value)

    xml_lang = "{http://www.w3.org/XML/1998/namespace}lang"

    for element in [root, _first_element_by_local_name(root, "body")]:
        if element is None:
            continue

        value = (
            element.get(xml_lang)
            or element.get("lang")
            or ""
        ).strip()

        if value:
            return _normalize_language(value)

    return _normalize_language(None)


def _ensure_heading_ids(root, log):
    """
    Sørg for stabile, unike ID-er på h1-h6.

    html-to-pef sin TOC-generator lager lenker til overskrifter. Rå XHTML
    mangler ofte ID-er, mens produksjonsklare NLBPUB-filer normalt har dem.
    """
    used_ids = set()

    for element in root.iter():
        element_id = (element.get("id") or "").strip()
        if element_id:
            used_ids.add(element_id)

    added = 0
    counter = 1

    for element in root.iter():
        local = _local_name(element.tag).lower()
        if local not in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            continue

        if (element.get("id") or "").strip():
            continue

        while True:
            candidate = f"bok-to-pef-heading-{counter}"
            counter += 1
            if candidate not in used_ids:
                break

        element.set("id", candidate)
        used_ids.add(candidate)
        added += 1

    if added:
        log.info(
            "La til stabile ID-er på %d overskrifter for TOC-generering.",
            added,
        )


def _normalize_raw_xhtml(source_path, log):
    """
    Gjør vilkårlig XML-gyldig XHTML robust nok for prepare-for-braille/html-to-pef.

    Dette er med hensikt konservativt: vi oppretter bare struktur/metadata som
    kan utledes sikkert eller har en dokumentert fallback. Vi forsøker ikke å
    gjette forfatter, forlag, produksjonsnummer eller annen bibliografisk
    informasjon.
    """
    source_path = Path(source_path)

    parser = etree.XMLParser(
        remove_blank_text=False,
        resolve_entities=False,
        no_network=True,
        recover=False,
        huge_tree=True,
    )

    try:
        document = etree.parse(str(source_path), parser)
    except etree.XMLSyntaxError as exc:
        raise RuntimeError(
            f"Input er ikke XML-gyldig XHTML: {exc}"
        ) from exc

    root = document.getroot()

    if _local_name(root.tag).lower() != "html":
        raise RuntimeError(
            f"Forventet <html> som rotelement, fant <{_local_name(root.tag)}>."
        )

    xhtml_ns = _ensure_xhtml_namespace(root, log)

    head = _first_element_by_local_name(root, "head")
    body = _first_element_by_local_name(root, "body")

    if body is None:
        raise RuntimeError(
            "XHTML-filen mangler <body>; kan ikke konvertere til PEF."
        )

    if head is None:
        head = etree.Element(f"{{{xhtml_ns}}}head")
        root.insert(0, head)
        log.warning("Input manglet <head>; opprettet et minimalt <head>.")

    title = _derive_document_title(root, head, source_path)
    language = _derive_document_language(root, head)
    identifier = source_path.stem or "input"

    # Sørg for <title>.
    title_element = None
    for child in head:
        if _local_name(child.tag).lower() == "title":
            title_element = child
            break

    if title_element is None:
        title_element = etree.Element(f"{{{xhtml_ns}}}title")
        head.insert(0, title_element)

    if not _text_content(title_element):
        title_element.text = title
        log.info("Opprettet <title> fra tilgjengelig dokumentinformasjon: %s", title)

    # dc:title
    dc_title = _find_meta(head, "dc:title")
    if dc_title is None:
        dc_title = etree.Element(f"{{{xhtml_ns}}}meta")
        dc_title.set("name", "dc:title")
        dc_title.set("content", title)
        head.append(dc_title)
        log.info("La til manglende dc:title.")

    # dc:language
    dc_language = _find_meta(head, "dc:language")
    if dc_language is None:
        dc_language = etree.Element(f"{{{xhtml_ns}}}meta")
        dc_language.set("name", "dc:language")
        dc_language.set("content", language)
        head.append(dc_language)
        log.warning(
            "Input manglet dc:language; bruker språkfallback %r.",
            language,
        )
    elif not (dc_language.get("content") or "").strip():
        dc_language.set("content", language)

    # Gjør språk også tilgjengelig på <html>.
    xml_lang = "{http://www.w3.org/XML/1998/namespace}lang"
    if not (root.get(xml_lang) or "").strip():
        root.set(xml_lang, language)
    if not (root.get("lang") or "").strip():
        root.set("lang", language)

    # dc:identifier er ikke et krav om Statped-produksjonsnummer. For rå XHTML
    # bruker vi filnavnets stem som stabil lokal dokument-ID.
    dc_identifier = _find_meta(head, "dc:identifier")
    if dc_identifier is None:
        dc_identifier = etree.Element(f"{{{xhtml_ns}}}meta")
        dc_identifier.set("name", "dc:identifier")
        dc_identifier.set("content", identifier)
        head.append(dc_identifier)
        log.info(
            "Input manglet dc:identifier; bruker filnavn som lokal ID: %s",
            identifier,
        )
    elif not (dc_identifier.get("content") or "").strip():
        dc_identifier.set("content", identifier)

    _ensure_heading_ids(root, log)

    # En tom body kan ikke gi meningsfull PEF.
    body_text = _text_content(body)
    has_content_elements = any(
        isinstance(child.tag, str)
        for child in body.iterdescendants()
    )

    if not body_text and not has_content_elements:
        raise RuntimeError("XHTML-filen har tom <body>.")

    document.write(
        str(source_path),
        method="XML",
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=False,
    )

    log.info(
        "Normaliserte rå XHTML: title=%r, language=%r, identifier=%r",
        title,
        language,
        identifier,
    )


def _table_rows(table):
    """Returner alle tr-elementer under en tabell."""
    return [
        element
        for element in table.iter()
        if _local_name(element.tag).lower() == "tr"
    ]


def _row_cells(row):
    """Returner direkte td/th-barn i en rad."""
    return [
        child
        for child in row
        if _local_name(child.tag).lower() in {"td", "th"}
    ]


def _cell_block_children(cell):
    """Tell blokk-elementer i en tabellcelle."""
    block_names = {
        "p", "div", "section", "aside", "blockquote",
        "ul", "ol", "dl", "pre", "figure", "table",
        "h1", "h2", "h3", "h4", "h5", "h6",
    }
    return [
        child
        for child in cell
        if _local_name(child.tag).lower() in block_names
    ]


def _is_empty_element(element):
    if element is None:
        return True
    if _text_content(element):
        return False
    return not any(
        isinstance(child.tag, str)
        for child in element.iterdescendants()
    )


def _looks_like_layout_table(table):
    """
    Konservativ deteksjon av typiske Pandoc/DOCX-layout-tabeller.

    Vi flater bare ut tabeller når de åpenbart brukes som tekstbeholder,
    ikke når de ser ut som ekte tabelldata.

    Kriterier:
      - nøyaktig én ikke-tom celle i hele tabellen, og
      - enten cellen inneholder flere blokk-elementer,
        eller tabellen har tom tbody,
        eller tabellen er eksplisitt énkolonne med 100% bredde.
    """
    rows = _table_rows(table)

    nonempty_cells = []

    for row in rows:
        cells = _row_cells(row)
        nonempty_cells.extend(
            cell for cell in cells if not _is_empty_element(cell)
        )

    if len(nonempty_cells) != 1:
        return False

    cell = nonempty_cells[0]
    blocks = _cell_block_children(cell)

    has_multiple_blocks = len(blocks) > 1

    empty_tbody = False
    for child in table:
        if _local_name(child.tag).lower() == "tbody":
            tbody_rows = [
                row
                for row in child
                if _local_name(row.tag).lower() == "tr"
            ]
            if not tbody_rows or all(
                all(_is_empty_element(c) for c in _row_cells(row))
                for row in tbody_rows
            ):
                empty_tbody = True

    one_column_100_percent = False
    col_elements = [
        element
        for element in table.iter()
        if _local_name(element.tag).lower() == "col"
    ]
    if len(col_elements) == 1:
        style = (col_elements[0].get("style") or "").replace(" ", "").lower()
        width = (col_elements[0].get("width") or "").replace(" ", "").lower()
        if "width:100%" in style or width == "100%":
            one_column_100_percent = True

    return (
        has_multiple_blocks
        or empty_tbody
        or one_column_100_percent
    )


def _flatten_layout_table(table, log, index):
    """
    Erstatt en layout-tabell med vanlig blokkstruktur.

    Innholdet i den ene meningsfulle cellen beholdes i samme rekkefølge.
    """
    parent = table.getparent()
    if parent is None:
        return False

    rows = _table_rows(table)
    nonempty_cells = []

    for row in rows:
        for cell in _row_cells(row):
            if not _is_empty_element(cell):
                nonempty_cells.append(cell)

    if len(nonempty_cells) != 1:
        return False

    cell = nonempty_cells[0]
    ns = table.tag.split("}", 1)[0] + "}" if "}" in table.tag else ""

    wrapper = etree.Element(f"{ns}div")
    wrapper.set("class", "pandoc-layout-table")

    if cell.text and cell.text.strip():
        p = etree.Element(f"{ns}p")
        p.text = cell.text
        wrapper.append(p)

    for child in list(cell):
        cell.remove(child)
        wrapper.append(child)

    if len(wrapper) == 0 and _text_content(cell):
        p = etree.Element(f"{ns}p")
        p.text = _text_content(cell)
        wrapper.append(p)

    wrapper.tail = table.tail

    position = parent.index(table)
    parent.remove(table)
    parent.insert(position, wrapper)

    log.warning(
        "Flatet ut Pandoc-layout-tabell #%d til vanlig blokkstruktur.",
        index,
    )

    return True


def _normalize_pandoc_tables(html_path, log):
    """
    Normaliser tabeller som typisk oppstår ved DOCX -> XHTML via Pandoc.

    Ekte datatabeller beholdes. Bare åpenbare én-celle-layout-tabeller flates ut.
    """
    html_path = Path(html_path)

    parser = etree.XMLParser(
        remove_blank_text=False,
        resolve_entities=False,
        no_network=True,
        recover=False,
        huge_tree=True,
    )
    document = etree.parse(str(html_path), parser)
    root = document.getroot()

    tables = [
        element
        for element in root.iter()
        if _local_name(element.tag).lower() == "table"
    ]

    flattened = 0

    for index, table in enumerate(list(tables), start=1):
        if _looks_like_layout_table(table):
            if _flatten_layout_table(table, log, index):
                flattened += 1

    if flattened:
        document.write(
            str(html_path),
            method="XML",
            xml_declaration=True,
            encoding="UTF-8",
            pretty_print=False,
        )

    log.info(
        "Pandoc-tabellnormalisering: %d tabeller kontrollert, %d layout-tabeller flatet ut.",
        len(tables),
        flattened,
    )


def _remove_school_grade(html_path, log):
    """Samme forhåndsrydding som i gamle prepare_for_braille.py."""
    parser = etree.XMLParser(encoding="utf-8")
    tree = etree.parse(str(html_path), parser)
    elements = tree.getroot().xpath(
        "//*[local-name()='p' and @class='school-grade']"
    )

    if not elements:
        log.info("Fant ingen p med class='school-grade'.")
        return

    for element in elements:
        log.info(
            "Fjerner p med class='school-grade': %s",
            element.text or "",
        )
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)

    tree.write(
        str(html_path),
        method="XML",
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=False,
    )


def _run_xslt(source, stylesheet, target, log):
    saxon_jar = _resolve_saxon_jar()

    command = [
        "java",
        "-jar",
        str(saxon_jar),
        f"-s:{source}",
        f"-xsl:{stylesheet}",
        f"-o:{target}",
    ]

    log.info("Running XSLT")
    log.info("Processing %s", command)

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )

    if result.stdout.strip():
        log.info("Saxon stdout:\n%s", result.stdout.strip())

    if result.stderr.strip():
        log.info("Saxon stderr:\n%s", result.stderr.strip())

    if result.returncode != 0:
        raise RuntimeError(
            f"XSLT {Path(stylesheet).name} feilet med exit code "
            f"{result.returncode}: {result.stderr.strip() or result.stdout.strip()}"
        )

    if not Path(target).is_file():
        raise RuntimeError(
            f"XSLT {Path(stylesheet).name} fullførte uten å lage {target}"
        )

    log.info("XSLT processing complete")


def _filename_from_identifier(identifier):
    """Gjør dc:identifier trygt som filnavn uten å kreve numerisk boknummer."""
    identifier = (identifier or "").strip()
    if not identifier:
        return None

    safe = re.sub(r"[^0-9A-Za-z._-]+", "_", identifier).strip("._")
    return safe or None


def _identifier_from_html(html_path):
    parser = etree.XMLParser(encoding="utf-8")
    root = etree.parse(str(html_path), parser).getroot()
    matches = root.xpath(
        "/*/*[local-name()='head']/*[@name='dc:identifier']/@content"
    )
    return matches[0].strip() if matches and matches[0].strip() else None


def _postprocess_braille_sections(html_path, log):
    """
    Flytt braille-specific-info/pef-about og rydd aside-whitespace.

    Dette tilsvarer Python-etterbehandlingen i gamle prepare_for_braille.py.
    """
    with open(html_path, "r", encoding="utf-8") as file_handle:
        soup = BeautifulSoup(file_handle, "html.parser")

    pef_title = soup.find("section", class_="pef-titlepage")
    frontmatter = soup.find("section", attrs={"epub:type": "frontmatter"})
    pef_about = soup.find("section", class_="pef-about")
    braille_section = soup.find("section", class_="braille-specific-info")

    # insert-boilerplate kan legge produksjonsnummer/boknummer i denne linjen.
    # Som i det gamle systemet erstattes den med et tomt avsnitt.
    if pef_title:
        p_right = pef_title.find("p", class_="Høyre-justert")
        if p_right:
            new_p = soup.new_tag("p")
            new_p.string = "\u00A0"
            p_right.replace_with(new_p)
            log.info("Erstattet p produksjonsnummer med tom <p> </p>.")
        else:
            log.info("Fant ikke p produksjonsnummer.")

    if braille_section:
        braille_section.extract()
    else:
        braille_section = soup.new_tag(
            "section",
            **{"class": "braille-specific-info"},
        )

    if pef_title:
        log.info(
            "Plasserer braille-specific-info rett under "
            "<section class='pef-titlepage'>"
        )
        pef_title.insert_after(braille_section)
    elif frontmatter:
        log.info(
            "Ingen titlepage – plasserer rett over første "
            "<section epub:type='frontmatter'>"
        )
        frontmatter.insert_before(braille_section)
    else:
        bodymatter = soup.find("section", attrs={"epub:type": "bodymatter"})
        if bodymatter:
            log.info(
                "Ingen titlepage/frontmatter – plasserer rett over "
                "<section epub:type='bodymatter'>"
            )
            bodymatter.insert_before(braille_section)
        else:
            log.info(
                "Ingen titlepage, frontmatter eller bodymatter – "
                "plasserer øverst i <body>"
            )
            if soup.body:
                soup.body.insert(0, braille_section)
            else:
                soup.insert(0, braille_section)

    if pef_about:
        log.info(
            "Flytter innhold fra pef-about inn i braille-specific-info "
            "og fjerner pef-about."
        )
        # Reversed + insert(0) bevarer samme rekkefølge som i gammel kode.
        for child in reversed(list(pef_about.contents)):
            braille_section.insert(0, child.extract())
        pef_about.decompose()

    # Samme whitespace-rydding i <aside> som i det gamle systemet.
    for aside in soup.find_all("aside"):
        aside_id = aside.get("id")

        while True:
            text_nodes = list(aside.find_all(string=True))
            if not text_nodes:
                break

            first_node = text_nodes[0]
            if not first_node.strip():
                log.info(
                    "Fjernet tom ledende tekstnode i aside id=%s: %r",
                    aside_id,
                    first_node,
                )
                first_node.extract()
                continue

            lstripped = first_node.lstrip()
            if first_node != lstripped:
                log.info(
                    "Strippet ledende whitespace fra aside id=%s: %r -> %r",
                    aside_id,
                    first_node,
                    lstripped,
                )
                first_node.replace_with(lstripped)
            break

        while True:
            text_nodes = list(aside.find_all(string=True))
            if not text_nodes:
                break

            last_node = text_nodes[-1]
            if not last_node.strip():
                log.info(
                    "Fjernet tom etterfølgende tekstnode i aside id=%s: %r",
                    aside_id,
                    last_node,
                )
                last_node.extract()
                continue

            rstripped = last_node.rstrip()
            if last_node != rstripped:
                log.info(
                    "Strippet etterfølgende whitespace fra aside id=%s: %r -> %r",
                    aside_id,
                    last_node,
                    rstripped,
                )
                last_node.replace_with(rstripped)
            break

    with open(html_path, "w", encoding="utf-8") as file_handle:
        file_handle.write(str(soup))

    log.info("Ferdig med flytting av punktskrift merknader.")


def prepare_html_for_pef(source_path, log):
    """
    Klargjør én XHTML/HTML-fil for html-to-pef.

    Flyten er hentet fra gamle prepare_for_braille.py, men uten EPUB-/arkivkrav:
      1. arbeid på en kopi av input
      2. normaliser rå/Pandoc-XHTML (namespace, head, språk, tittel, lokal ID, heading-ID-er)
      3. flat ut åpenbare Pandoc-layout-tabeller
      4. håndter manglende Pandoc-bilder via alt-tekst
      5. fjern p.school-grade
      6. kjør fire XSLT-er SEKVENSiELT
      7. bruk dc:identifier som filnavn dersom det finnes
      8. flytt braille-specific-info/pef-about
      9. trim ytterste whitespace i aside-elementer

    Ingen dc:identifier eller Statped-produksjonsnummer er påkrevd.
    """
    source_path = Path(source_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"Input XHTML finnes ikke: {source_path}")

    work_dir = Path(tempfile.mkdtemp(prefix="bok-to-pef-prepare-"))
    working = work_dir / source_path.name
    shutil.copy2(source_path, working)

    # Gjør også ikke-produksjonsforberedt/Pandoc-generert XHTML trygg nok
    # for XSLT og html-to-pef før den gamle prepare-for-braille-flyten kjøres.
    _normalize_raw_xhtml(working, log)

    # Pandoc/Word bruker ofte tabeller som ren layout. Dotify kan feile på
    # slike strukturer. Flat bare ut åpenbare layout-tabeller.
    _normalize_pandoc_tables(working, log)

    # Behold <img>-elementene også når den refererte bildefilen ikke
    # finnes lokalt. prepare-for-braille/braille.scss bruker img[alt]
    # som semantisk markør for blant annet "Bilde: <alt-tekst>".

    _remove_school_grade(working, log)

    for stylesheet_name, description in XSLT_STEPS:
        stylesheet = _resolve_static_file(stylesheet_name)
        output = work_dir / f"{working.stem}.{stylesheet_name}.tmp.html"

        log.info("Processing stylesheet: %s", stylesheet)
        log.info("Using XSLT ----------- : %s", stylesheet)
        log.info("XSLT: %s - %s", stylesheet_name, description)

        _run_xslt(working, stylesheet, output, log)

        # Kritisk: neste XSLT skal bruke resultatet fra forrige XSLT.
        shutil.move(str(output), str(working))

    identifier = _identifier_from_html(working)
    if identifier:
        safe_identifier = _filename_from_identifier(identifier)
        log.info("Fant dc:identifier i klargjort HTML: %s", identifier)
    else:
        safe_identifier = None
        log.info(
            "Fant ingen dc:identifier; beholder filnavn basert på input."
        )

    final_stem = safe_identifier or source_path.stem or "input"
    final_path = work_dir / f"{final_stem}.html"

    if working != final_path:
        if final_path.exists():
            final_path.unlink()
        working.rename(final_path)
    else:
        final_path = working

    log.info("Plasserer braille-specific-info riktig og flytter innhold fra pef-about")
    _postprocess_braille_sections(final_path, log)

    return final_path


def save_artifact(
    pip_output,
    job_status,
    message,
    production_number,
    uid,
    job_id,
    handler,
    pip_log=None,
    artifacts_folder="artifacts",
    prepared_xhtml_file: str | Path | None = None,
):
    """Save worker diagnostics and Pipeline results."""
    os.makedirs(artifacts_folder, exist_ok=True)
    job_folder = os.path.join(artifacts_folder, job_id)
    os.makedirs(job_folder, exist_ok=True)

    combined_log = pip_log or "No pipeline log available."
    logs_txt_path = os.path.join(job_folder, "logs.txt")
    dagsrapport = f"{datetime.now().strftime('%Y-%m-%d')}-{uid}.txt"

    if prepared_xhtml_file and Path(prepared_xhtml_file).exists():
        dest_prepared = os.path.join(
            job_folder,
            f"{production_number}_prepared.html",
        )
        shutil.copy2(prepared_xhtml_file, dest_prepared)
        logger.info("[save_artifact] Saved prepared file: %s", dest_prepared)
    elif prepared_xhtml_file:
        logger.info(
            "[save_artifact] prepared_file not found: %s",
            prepared_xhtml_file,
        )

    if job_status in ("DONE", "SUCCESS") and pip_output:
        logger.info("Saving artifacts to %s", job_folder)

        if os.path.isfile(pip_output) and zipfile.is_zipfile(pip_output):
            logger.info("pip_output is a zip file")
            with zipfile.ZipFile(pip_output, "r") as zip_ref:
                zip_ref.extractall(job_folder)
        elif os.path.isdir(pip_output):
            logger.info("pip_output is a folder")
            for item in os.listdir(pip_output):
                source = os.path.join(pip_output, item)
                destination = os.path.join(job_folder, item)
                if os.path.isdir(source):
                    shutil.copytree(
                        source,
                        destination,
                        dirs_exist_ok=True,
                    )
                else:
                    shutil.copy2(source, destination)

            shutil.rmtree(pip_output, ignore_errors=True)

    if job_status in ("DONE", "SUCCESS"):
        status = "success"
        logger.info("Job succeeded: %s", message)
    else:
        status = "fail"
        logger.error("Job failed: %s", message)

    with open(logs_txt_path, "w", encoding="utf-8") as file_handle:
        if handler:
            handler_logs = handler.get_logs()
            if handler_logs:
                file_handle.write("===== bok to pef log =====\n")
                file_handle.write(handler_logs)
                file_handle.write("\n\n")

        if combined_log:
            file_handle.write(combined_log)

    with open(
        os.path.join(job_folder, dagsrapport),
        "w",
        encoding="utf-8",
    ) as file_handle:
        file_handle.write(message or "")

    return status


def bok_to_pef(
    html,
    braille_arguments_from_queue,
    job_id,
    production_number=None,
    log_handler=None,
    **kwargs,
):
    """
    Convert an arbitrary XHTML/HTML file to PEF with DAISY Pipeline.

    production_number is optional metadata/identification only. It is not
    required by the preparation stage or used as the Pipeline source name.
    """
    del log_handler  # Beholdes i signaturen for bakoverkompatibilitet.

    save_prepared_xhtml = kwargs.pop("save_prepared_xhtml", True)
    pip_output = None
    pip_log = None
    prepared_xhtml_file = None

    handler = InMemoryLogHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    if production_number is None or not str(production_number).strip():
        if hasattr(html, "filename"):
            identifier_source = getattr(html, "filename", "input")
        else:
            identifier_source = str(html)
        production_number = Path(identifier_source).stem or "input"
    else:
        production_number = str(production_number)

    def _finish(dp_status: str, message: str) -> dict:
        try:
            status = save_artifact(
                pip_output=pip_output,
                pip_log=pip_log or "",
                job_status=dp_status,
                job_id=job_id,
                uid=UID,
                production_number=production_number,
                handler=handler,
                message=message or "",
                prepared_xhtml_file=prepared_xhtml_file,
            )
            return {
                "status": status,
                "message": message,
            }
        finally:
            logger.removeHandler(handler)
            handler.close()

    def _fail(message: str) -> dict:
        logger.error(message)
        return _finish("FAIL", message)

    pipeline_url = _pipeline_url()
    os.environ["DAISY_PIPELINE_URL"] = pipeline_url
    logger.info("Konfigurert DAISY Pipeline: %s", pipeline_url)

    pipeline_ok, pipeline_message = _check_pipeline(pipeline_url)
    if not pipeline_ok:
        return _fail(pipeline_message)

    logger.info("Pipeline tilgjengelig: %s", pipeline_message)

    # Sørg for en vanlig filsti også dersom funksjonen kalles med UploadFile.
    if hasattr(html, "file"):
        input_dir = Path(tempfile.mkdtemp(prefix="bok-to-pef-input-"))
        name = getattr(html, "filename", "input.html") or "input.html"
        html_path = input_dir / Path(name).name
        with html_path.open("wb") as file_handle:
            file_handle.write(html.file.read())
    else:
        html_path = Path(html)
        if not html_path.exists():
            return _fail(f"Input XHTML not found: {html_path}")

    try:
        prepared_html = prepare_html_for_pef(html_path, logger)
    except Exception as exc:
        logger.exception("Klargjøring for punktskrift feilet")
        pip_log = traceback.format_exc()
        return _fail(f"Klargjøring for punktskrift feilet: {exc}")

    if not prepared_html.is_file():
        return _fail(
            f"Klargjøringen oppga en fil som ikke finnes: {prepared_html}"
        )

    if save_prepared_xhtml:
        prepared_xhtml_file = prepared_html

    logger.info("Prepared HTML located at: %s", prepared_html)

    # Pipeline-konteksten får navnet fra faktisk preparert HTML, ikke fra
    # production_number.
    context_dir = Path(tempfile.mkdtemp(prefix="bok-to-pef-context-"))
    file_name = prepared_html.name or "input.html"
    html_path_context = context_dir / file_name
    shutil.copyfile(prepared_html, html_path_context)

    logger.info("Prepared HTML copied to working dir as: %s", file_name)

    if isinstance(braille_arguments_from_queue, dict):
        args_from_queue = braille_arguments_from_queue
    elif isinstance(braille_arguments_from_queue, str):
        try:
            args_from_queue = json.loads(braille_arguments_from_queue)
        except json.JSONDecodeError:
            logger.warning(
                "Kunne ikke tolke braille_arguments som JSON; "
                "bruker standardargumenter."
            )
            args_from_queue = {}
    else:
        args_from_queue = {}

    if not isinstance(args_from_queue, dict):
        logger.warning(
            "braille_arguments må være et JSON-objekt; "
            "bruker standardargumenter."
        )
        args_from_queue = {}

    # Eksakt samme standardargumenter som gammelsystemets Statped-gren.
    # Hele settet beholdes som provenance. Bare input/options som html-to-pef
    # faktisk eksponerer blir sendt i jobRequest.
    default_arguments = {
        "source": file_name,
        "transform": (
            "(formatter:dotify)"
            "(translator:liblouis)"
            "(dots:6)"
            "(grade:0)"
        ),
        "stylesheet": "braille.scss",
        "page-width": "38",
        "page-height": "29",
        "toc-depth": "2",
        "include-production-notes": "true",
        "hyphenation": "none",
        "include-preview": "true",
        "hyphenation-at-page-breaks": "except-at-volume-breaks",
        "allow-volume-break-inside-leaf-section-factor": "10",
        "prefer-volume-break-before-higher-level-factor": "1",
        "stylesheet-parameters": (
            "(skip-margin-top-of-page:true)"
            "(maximum-number-of-sheets:50)"
        ),
    }

    requested_arguments = {
        **default_arguments,
        **args_from_queue,
        "source": file_name,
    }

    if args_from_queue:
        logger.info(
            "Using default DAISY Pipeline arguments with overrides: %s",
            args_from_queue,
        )
    else:
        logger.info("Using default DAISY Pipeline arguments")

    logger.info(
        "Requested DAISY Pipeline arguments før filtrering: %s",
        requested_arguments,
    )

    try:
        braille_stylesheet = _resolve_braille_stylesheet()
    except Exception as exc:
        return _fail(str(exc))

    # Pipeline-parameteren er "stylesheet=braille.scss", akkurat som i
    # gammelsystemet. static/braille.scss er verifisert byte-for-byte mot
    # gammelcontainerens stylesheet.
    context = {
        file_name: html_path_context,
        "braille.scss": str(braille_stylesheet),
    }

    logger.info(
        "DAISY Pipeline context: source=%s, stylesheet braille.scss=%s",
        html_path_context,
        braille_stylesheet,
    )

    try:
        arguments, unsupported_arguments = _filter_pipeline_arguments(
            pipeline_url,
            "html-to-pef",
            requested_arguments,
        )
    except Exception as exc:
        logger.exception("Lesing av DAISY Pipeline-scriptbeskrivelse feilet")
        return _fail(str(exc))

    if "source" not in arguments:
        return _fail(
            "html-to-pef eksponerer ikke input-parameteren 'source'."
        )

    if unsupported_arguments:
        logger.info(
            "Legacy-verdier beholdt for sammenligning/provenance: %s",
            unsupported_arguments,
        )

    pipeline_and_script_version = _pipeline_versions()
    logger.info(
        "Pipeline-/scriptversjoner i prioritert rekkefølge: %s",
        pipeline_and_script_version,
    )

    try:
        with DaisyPipelineJob(
            "html-to-pef",
            arguments,
            pipeline_and_script_version=pipeline_and_script_version,
            context=context,
        ) as dp2_job:
            found_pipeline_version = dp2_job.found_pipeline_version
            found_script_version = dp2_job.found_script_version

            logger.info(
                "DAISY Pipeline-valg: pipeline=%s, script=%s, status=%s",
                found_pipeline_version,
                found_script_version,
                dp2_job.status,
            )

            if dp2_job.status != "SUCCESS":
                pip_log = dp2_job.job_log or ""
                logger.error(
                    "DAISY Pipeline-konvertering feilet. "
                    "status=%s, pipeline=%s, script=%s\n%s",
                    dp2_job.status,
                    found_pipeline_version,
                    found_script_version,
                    pip_log,
                )
                return _finish(
                    "FAIL",
                    "DAISY Pipeline-konvertering feilet "
                    f"(status={dp2_job.status}).",
                )

            candidate_output_dirs = [
                os.path.join(dp2_job.dir_output, "pef-output-dir"),
                os.path.join(dp2_job.dir_output, "output-dir"),
                os.path.join(dp2_job.dir_output, "result"),
            ]

            dp2_pef_dir = next(
                (
                    candidate
                    for candidate in candidate_output_dirs
                    if os.path.isdir(candidate)
                ),
                None,
            )

            if dp2_pef_dir is None:
                pip_log = dp2_job.job_log or ""
                logger.error(
                    "DAISY Pipeline rapporterte SUCCESS, men ingen "
                    "PEF-resultatmappe ble funnet. output=%s, "
                    "forventede mapper=%s\n%s",
                    dp2_job.dir_output,
                    candidate_output_dirs,
                    pip_log,
                )
                return _finish(
                    "FAIL",
                    "DAISY Pipeline fullførte, men ingen "
                    "PEF-resultatmappe ble funnet.",
                )

            # Pipeline <= 1.14.14 used "preview-output-dir", while newer
            # versions of html-to-pef store the generated HTML preview in
            # "preview". Support both layouts.
            preview_candidates = [
                os.path.join(dp2_job.dir_output, "preview-output-dir"),
                os.path.join(dp2_job.dir_output, "preview"),
            ]
            preview_output_dir = next(
                (
                    candidate
                    for candidate in preview_candidates
                    if os.path.isdir(candidate)
                ),
                None,
            )

            if preview_output_dir is not None:
                logger.info(
                    "Copying preview files from %s to output",
                    preview_output_dir,
                )
                for item in os.listdir(preview_output_dir):
                    source = os.path.join(preview_output_dir, item)
                    destination = os.path.join(dp2_pef_dir, item)
                    if os.path.isdir(source):
                        shutil.copytree(
                            source,
                            destination,
                            dirs_exist_ok=True,
                        )
                    else:
                        shutil.copy2(source, destination)
            else:
                logger.info(
                    "No preview files to copy; checked: %s",
                    preview_candidates,
                )

            # Gammelsystemet kopierte metadata fra HTML til PEF etter
            # konverteringen og la samtidig inn full Pipeline-provenance.
            try:
                _add_metadata_to_pef_output(
                    pef_output_dir=dp2_pef_dir,
                    html_file=prepared_html,
                    pipeline_version=found_pipeline_version,
                    script_id="html-to-pef",
                    script_version=found_script_version,
                    provenance_arguments=requested_arguments,
                    log=logger,
                )
            except Exception as exc:
                pip_log = dp2_job.job_log or ""
                logger.exception(
                    "Klarte ikke å legge metadata/provenance inn i PEF"
                )
                return _finish(
                    "FAIL",
                    f"PEF ble laget, men metadataoverføring feilet: {exc}",
                )

            pip_output = dp2_pef_dir
            pip_log = dp2_job.job_log or ""

            return _finish(
                "DONE",
                f"{production_number} ble konvertert til PEF.",
            )

    except Exception as exc:
        pip_log = pip_log or traceback.format_exc()
        logger.exception("Uventet feil under DAISY Pipeline-kjøring")
        return _finish(
            "FAIL",
            f"DAISY Pipeline-kjøringen feilet: {exc}",
        )
