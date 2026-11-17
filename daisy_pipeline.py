# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import io
import logging
import os
import random
import shutil
import tempfile
import time
import traceback
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any, Optional

import requests
from lxml import etree as ElementTree
from requests_toolbelt.multipart.encoder import MultipartEncoder


class InMemoryLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.log_stream = io.StringIO()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.log_stream.write(self.format(record) + "\n")
        except Exception:
            self.handleError(record)

    def get_logs(self) -> str:
        return self.log_stream.getvalue()


class DaisyPipelineJob:
    """Kjør en DAISY Pipeline 2-jobb via web service API-et."""

    dp2_ws_namespace = {
        "d": "http://www.daisy.org/ns/pipeline/data"
    }

    engine_jobs: Optional[dict[str, dict[str, float]]] = None

    def __init__(
        self,
        script: str,
        arguments: dict[str, Any],
        context: Optional[dict[str, str | Path]] = None,
        priority: str = "medium",
        pipeline_and_script_version: Optional[
            list[tuple[str, str]] | tuple[str, str]
        ] = None,
    ) -> None:
        if isinstance(pipeline_and_script_version, tuple):
            pipeline_and_script_version = [
                pipeline_and_script_version
            ]

        self.script = script
        self.arguments = arguments
        self.context = context or {}
        self.priority = priority
        self.pipeline_and_script_version = (
            pipeline_and_script_version or [(None, None)]
        )

        self.logger = logging.getLogger(__name__)
        self.local_log_handler = InMemoryLogHandler()
        self.local_log_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(levelname)s - %(message)s"
            )
        )
        self.logger.addHandler(self.local_log_handler)

        self._dir_output_obj: Optional[
            tempfile.TemporaryDirectory
        ] = None
        self.dir_output: Optional[str] = None
        self.job_log: Optional[str] = None
        self.job_id: Optional[str] = None
        self.pipeline: Any = None
        self.status: Optional[str] = None
        self.engine: Optional[dict[str, Any]] = None
        self.found_pipeline_version: Optional[str] = None
        self.found_script_version: Optional[str] = None

        self.engines = self._build_engines()

    # ------------------------------------------------------------------
    # Miljø og timeout
    # ------------------------------------------------------------------

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default

    @classmethod
    def _discovery_timeout(cls) -> tuple[float, float]:
        return (
            cls._env_float(
                "DAISY_PIPELINE_CONNECT_TIMEOUT_SECONDS",
                2.0,
            ),
            cls._env_float(
                "DAISY_PIPELINE_READ_TIMEOUT_SECONDS",
                5.0,
            ),
        )

    @classmethod
    def _job_request_timeout(cls) -> tuple[float, float]:
        return (
            cls._env_float(
                "DAISY_PIPELINE_CONNECT_TIMEOUT_SECONDS",
                2.0,
            ),
            cls._env_float(
                "DAISY_PIPELINE_JOB_REQUEST_TIMEOUT_SECONDS",
                60.0,
            ),
        )

    @classmethod
    def _status_timeout(cls) -> tuple[float, float]:
        return (
            cls._env_float(
                "DAISY_PIPELINE_CONNECT_TIMEOUT_SECONDS",
                2.0,
            ),
            cls._env_float(
                "DAISY_PIPELINE_STATUS_TIMEOUT_SECONDS",
                10.0,
            ),
        )

    @classmethod
    def _result_timeout(cls) -> tuple[float, float]:
        return (
            cls._env_float(
                "DAISY_PIPELINE_CONNECT_TIMEOUT_SECONDS",
                2.0,
            ),
            cls._env_float(
                "DAISY_PIPELINE_RESULT_TIMEOUT_SECONDS",
                300.0,
            ),
        )

    @classmethod
    def _log_timeout(cls) -> tuple[float, float]:
        return (
            cls._env_float(
                "DAISY_PIPELINE_CONNECT_TIMEOUT_SECONDS",
                2.0,
            ),
            cls._env_float(
                "DAISY_PIPELINE_LOG_TIMEOUT_SECONDS",
                30.0,
            ),
        )

    # ------------------------------------------------------------------
    # Endpoint-konfigurasjon
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_endpoint(value: str) -> str:
        endpoint = value.strip().rstrip("/")

        if not endpoint:
            raise ValueError("Tomt DAISY Pipeline-endepunkt.")

        if not endpoint.endswith("/ws"):
            endpoint += "/ws"

        return endpoint

    @classmethod
    def _build_engines(cls) -> list[dict[str, Any]]:
        """
        Bruk eksplisitt DAISY_PIPELINE_URL hvis den finnes.

        Eldre REMOTE_PIPELINE2_WS_* støttes som fallback, men gamle
        localhost-/host.docker.internal-lister genereres ikke automatisk.
        """

        configured = os.getenv(
            "DAISY_PIPELINE_URL",
            "",
        ).strip()

        if configured:
            return [
                {
                    "endpoint": cls._normalize_endpoint(
                        configured
                    ),
                    "authentication": os.getenv(
                        "DAISY_PIPELINE_AUTHENTICATION",
                        "false",
                    ).strip().lower(),
                    "key": os.getenv(
                        "DAISY_PIPELINE_AUTHENTICATION_KEY"
                    ),
                    "secret": os.getenv(
                        "DAISY_PIPELINE_AUTHENTICATION_SECRET"
                    ),
                    "local": False,
                }
            ]

        remote_endpoints = os.getenv(
            "REMOTE_PIPELINE2_WS_ENDPOINTS",
            "",
        ).split()

        if remote_endpoints:
            authentications = os.getenv(
                "REMOTE_PIPELINE2_WS_AUTHENTICATION",
                "",
            ).split()
            keys = os.getenv(
                "REMOTE_PIPELINE2_WS_AUTHENTICATION_KEYS",
                "",
            ).split()
            secrets = os.getenv(
                "REMOTE_PIPELINE2_WS_AUTHENTICATION_SECRETS",
                "",
            ).split()

            engines: list[dict[str, Any]] = []

            for index, endpoint in enumerate(
                remote_endpoints
            ):
                engines.append(
                    {
                        "endpoint": cls._normalize_endpoint(
                            endpoint
                        ),
                        "authentication": (
                            authentications[index]
                            if index < len(authentications)
                            else "false"
                        ),
                        "key": (
                            keys[index]
                            if index < len(keys)
                            else None
                        ),
                        "secret": (
                            secrets[index]
                            if index < len(secrets)
                            else None
                        ),
                        "local": False,
                    }
                )

            return engines

        port = os.getenv(
            "DAISY_PIPELINE_PORT",
            "8181",
        ).strip() or "8181"

        fallback = (
            f"http://daisypipeline2:{port}/ws"
        )

        return [
            {
                "endpoint": fallback,
                "authentication": "false",
                "key": None,
                "secret": None,
                "local": False,
            }
        ]

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "DaisyPipelineJob":
        self._dir_output_obj = tempfile.TemporaryDirectory(
            prefix="produksjonssystem-",
            suffix="-daisy-pipeline-output",
        )
        self.dir_output = self._dir_output_obj.name

        self.logger.info(
            "Tilgjengelige Pipeline-endepunkter: %s",
            [
                engine["endpoint"]
                for engine in self.engines
            ],
        )

        if not self.choose_engine():
            self.logger.error(
                "Pipeline 2 er ikke tilgjengelig."
            )
            self.status = None
            return self

        try:
            self.post_job()
            self._wait_for_completion()

            if self.status not in {
                "SUCCESS",
                "DONE",
            }:
                return self

            self.job_log = self.get_log()
            self.get_results()

        except Exception as exc:
            self.logger.error(
                "En feil oppstod ved kjøring av Pipeline 2-jobben "
                "(%s): %s",
                self.job_id,
                exc,
            )
            self.logger.debug(
                traceback.format_exc()
            )
            self.status = None

        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        trace,
    ) -> None:
        try:
            if self.job_id and self.engine:
                self.delete_job(
                    self.engine,
                    self.job_id,
                )
        finally:
            self.logger.removeHandler(
                self.local_log_handler
            )
            self.local_log_handler.close()

    # ------------------------------------------------------------------
    # Engine discovery
    # ------------------------------------------------------------------

    def choose_engine(
        self,
        use_local: bool = False,
    ) -> bool:
        del use_local

        self.engine = None
        self.found_pipeline_version = None
        self.found_script_version = None

        for index, (
            pipeline_version,
            script_version,
        ) in enumerate(
            self.pipeline_and_script_version
        ):
            self.logger.info(
                "Desired pipeline version %s and script %s",
                pipeline_version,
                script_version,
            )

            if index > 0:
                self.logger.warning(
                    "Ønsket kombinasjon ble ikke funnet. "
                    "Prøver Pipeline %s og script %s.",
                    pipeline_version,
                    script_version,
                )

            for engine in self.engines:
                self.logger.info(
                    "Checking engine: %s",
                    engine["endpoint"],
                )

                if not self.script_available(
                    engine,
                    pipeline_version=pipeline_version,
                    script_version=script_version,
                ):
                    continue

                self.engine = engine
                self.found_pipeline_version = (
                    pipeline_version
                )
                self.found_script_version = (
                    script_version
                )

                self.logger.info(
                    "Appropriate engine found"
                )
                self.logger.info(
                    "Desired pipeline version found %s "
                    "and script %s",
                    pipeline_version,
                    script_version,
                )
                break

            if self.engine:
                break

        if self.engine:
            self.logger.info(
                "Bruker Pipeline 2-instans på: %s",
                self.engine["endpoint"],
            )
            self.logger.info(
                "Pipeline 2-versjon: %s",
                self.found_pipeline_version,
            )
            self.logger.info(
                "Versjon av %s: %s",
                self.script,
                self.found_script_version,
            )
            return True

        self.logger.warning(
            "Fant ingen brukbar Pipeline 2-instans."
        )
        return False

    def script_available(
        self,
        engine: dict[str, Any],
        pipeline_version: Optional[str],
        script_version: Optional[str],
    ) -> bool:
        timeout = self._discovery_timeout()

        alive_url = self.encode_url(
            engine,
            "/alive",
            {},
        )

        try:
            alive_response = requests.get(
                alive_url,
                timeout=timeout,
            )
            alive_response.raise_for_status()
            alive_xml = ElementTree.fromstring(
                alive_response.content
            )
        except Exception as exc:
            self.logger.warning(
                "Pipeline 2 kjører ikke på %s: %s",
                engine["endpoint"],
                exc,
            )
            return False

        engine_pipeline_version = (
            alive_xml.attrib.get("version")
        )

        if (
            pipeline_version is not None
            and pipeline_version
            != engine_pipeline_version
        ):
            self.logger.info(
                "Feil Pipeline-versjon på %s. "
                "Ønsket %s, fant %s.",
                engine["endpoint"],
                pipeline_version,
                engine_pipeline_version,
            )
            return False

        scripts_url = self.encode_url(
            engine,
            "/scripts",
            {},
        )

        try:
            scripts_response = requests.get(
                scripts_url,
                timeout=timeout,
            )
            scripts_response.raise_for_status()
            scripts_xml = ElementTree.fromstring(
                scripts_response.content
            )
        except Exception as exc:
            self.logger.warning(
                "Klarte ikke å hente scriptliste fra %s: %s",
                engine["endpoint"],
                exc,
            )
            return False

        engine_script = scripts_xml.xpath(
            "/d:scripts/d:script[@id=$script_id]",
            namespaces=self.dp2_ws_namespace,
            script_id=self.script,
        )

        if not engine_script:
            self.logger.info(
                "Script %s finnes ikke på %s.",
                self.script,
                engine["endpoint"],
            )
            return False

        version_nodes = engine_script[0].xpath(
            "d:version",
            namespaces=self.dp2_ws_namespace,
        )
        engine_script_version = (
            version_nodes[0].text
            if version_nodes
            else None
        )

        if (
            script_version is not None
            and script_version
            != engine_script_version
        ):
            self.logger.info(
                "Feil scriptversjon. Ønsket %s, fant %s.",
                script_version,
                engine_script_version,
            )
            return False

        return True

    # ------------------------------------------------------------------
    # Job submission
    # ------------------------------------------------------------------

    def post_job(self) -> str:
        if not self.engine:
            raise RuntimeError(
                "Ingen Pipeline-engine er valgt."
            )

        self.logger.info("Posting job")

        script_href = self.encode_url(
            self.engine,
            f"/scripts/{self.script}",
            {},
        )

        script_response = requests.get(
            script_href,
            timeout=self._job_request_timeout(),
        )
        script_response.raise_for_status()

        script_xml = ElementTree.fromstring(
            script_response.content
        )

        ns = (
            "http://www.daisy.org/ns/pipeline/data"
        )

        job_request = ElementTree.Element(
            f"{{{ns}}}jobRequest"
        )

        priority = ElementTree.SubElement(
            job_request,
            f"{{{ns}}}priority",
        )
        priority.text = self.priority

        ElementTree.SubElement(
            job_request,
            f"{{{ns}}}script",
            href=script_href,
        )

        for input_element in script_xml.xpath(
            "/d:script/d:input",
            namespaces=self.dp2_ws_namespace,
        ):
            name = input_element.attrib["name"]

            if name not in self.arguments:
                continue

            values = self.arguments[name]

            if not isinstance(values, list):
                values = [values]

            input_node = ElementTree.SubElement(
                job_request,
                f"{{{ns}}}input",
                name=name,
            )

            for value in values:
                normalized = self._normalize_argument_value(
                    value,
                    local=self.engine["local"],
                )

                ElementTree.SubElement(
                    input_node,
                    f"{{{ns}}}item",
                    value=str(normalized),
                )

        for option_element in script_xml.xpath(
            "/d:script/d:option",
            namespaces=self.dp2_ws_namespace,
        ):
            name = option_element.attrib["name"]

            if name not in self.arguments:
                continue

            values = self.arguments[name]

            if not isinstance(values, list):
                values = [values]

            option_node = ElementTree.SubElement(
                job_request,
                f"{{{ns}}}option",
                name=name,
            )

            normalized_values = [
                self._normalize_argument_value(
                    value,
                    local=(
                        self.engine["local"]
                        and option_element.attrib.get(
                            "type"
                        )
                        in {
                            "anyFileURI",
                            "anyDirURI",
                        }
                    ),
                )
                for value in values
            ]

            if len(normalized_values) == 1:
                option_node.text = str(
                    normalized_values[0]
                )
            else:
                for value in normalized_values:
                    ElementTree.SubElement(
                        option_node,
                        f"{{{ns}}}item",
                        value=str(value),
                    )

        job_request_bytes = ElementTree.tostring(
            job_request,
            xml_declaration=True,
            encoding="UTF-8",
            pretty_print=True,
        )

        self.logger.info(
            "Job request: %s",
            job_request_bytes.decode(
                "utf-8",
                errors="replace",
            ),
        )

        jobs_url = self.encode_url(
            self.engine,
            "/jobs",
            {},
        )

        if self.context and not self.engine["local"]:
            response = self._post_multipart_job(
                jobs_url,
                job_request_bytes,
            )
        else:
            response = requests.post(
                jobs_url,
                data=job_request_bytes,
                headers={
                    "Content-Type": "application/xml"
                },
                timeout=self._job_request_timeout(),
            )

        response.raise_for_status()

        self.logger.info(
            response.text
        )

        job_xml = ElementTree.fromstring(
            response.content
        )

        self.job_id = job_xml.attrib["id"]

        self.logger.info(
            "returning job id"
        )

        return self.job_id

    def _post_multipart_job(
        self,
        jobs_url: str,
        job_request_bytes: bytes,
    ) -> requests.Response:
        context_temp = tempfile.NamedTemporaryFile(
            suffix=".zip",
            delete=False,
        )
        context_path = context_temp.name
        context_temp.close()

        try:
            with zipfile.ZipFile(
                context_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                for href, file_path in self.context.items():
                    self.logger.debug(
                        "zipping context: %s from %s",
                        href,
                        file_path,
                    )
                    archive.write(
                        str(file_path),
                        arcname=href,
                    )

            with open(
                context_path,
                "rb",
            ) as context_file:
                multipart = MultipartEncoder(
                    fields={
                        "job-request": (
                            "jobRequest.xml",
                            job_request_bytes,
                            "application/xml",
                        ),
                        "job-data": (
                            "context.zip",
                            context_file,
                            "application/zip",
                        ),
                    }
                )

                return requests.post(
                    jobs_url,
                    data=multipart,
                    headers={
                        "Content-Type": (
                            multipart.content_type
                        )
                    },
                    timeout=self._job_request_timeout(),
                )

        finally:
            try:
                os.remove(context_path)
            except OSError:
                pass

    def _normalize_argument_value(
        self,
        value: Any,
        *,
        local: bool,
    ) -> Any:
        if not local or not self.context:
            return value

        if value not in self.context:
            return value

        path = Path(
            self.context[value]
        ).resolve()

        return path.as_uri()

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _wait_for_completion(self) -> None:
        poll_interval = self._env_float(
            "DAISY_PIPELINE_POLL_INTERVAL_SECONDS",
            5.0,
        )
        idle_timeout = self._env_float(
            "DAISY_PIPELINE_IDLE_TIMEOUT_SECONDS",
            9000.0,
        )
        running_timeout = self._env_float(
            "DAISY_PIPELINE_RUNNING_TIMEOUT_SECONDS",
            7200.0,
        )
        max_health_failures = self._env_int(
            "DAISY_PIPELINE_MAX_HEALTH_FAILURES",
            3,
        )

        self.status = "IDLE"
        idle_started = time.monotonic()
        running_started = time.monotonic()
        health_failures = 0

        while self.status in {
            "IDLE",
            "RUNNING",
        }:
            if (
                self.status == "IDLE"
                and time.monotonic() - idle_started
                > idle_timeout
            ):
                self.logger.error(
                    "Pipeline-jobben ventet for lenge i IDLE."
                )
                self.status = None
                return

            if (
                self.status == "RUNNING"
                and time.monotonic() - running_started
                > running_timeout
            ):
                self.logger.error(
                    "Pipeline-jobben brukte for lang tid."
                )
                self.status = None
                return

            time.sleep(poll_interval)

            if not self.engine:
                self.status = None
                return

            if not self.is_alive(self.engine):
                health_failures += 1
                self.logger.warning(
                    "Pipeline-healthcheck feilet (%s/%s).",
                    health_failures,
                    max_health_failures,
                )

                if health_failures >= max_health_failures:
                    self.logger.error(
                        "Pipeline 2 kjører ikke lenger. "
                        "Avbryter."
                    )
                    self.status = None
                    return
            else:
                health_failures = 0

            previous_status = self.status

            self.logger.info(
                "Getting job status"
            )
            self.get_status()
            self.logger.info(
                "Pipeline 2 status: %s",
                self.status,
            )

            if (
                previous_status == "IDLE"
                and self.status == "RUNNING"
            ):
                running_started = time.monotonic()

    def get_status(self) -> Optional[str]:
        if not self.engine or not self.job_id:
            return self.status

        url = self.encode_url(
            self.engine,
            f"/jobs/{self.job_id}",
            {},
        )

        try:
            response = requests.get(
                url,
                timeout=self._status_timeout(),
            )

            if not response.ok:
                self.logger.warning(
                    "Statuskall svarte HTTP %s.",
                    response.status_code,
                )
                return self.status

            xml = ElementTree.fromstring(
                response.content
            )
            self.status = xml.attrib.get(
                "status",
                self.status,
            )

            if self.status == "DONE":
                self.status = "SUCCESS"

        except requests.RequestException as exc:
            self.logger.warning(
                "Statuskall feilet: %s",
                exc,
            )

        except ElementTree.XMLSyntaxError as exc:
            self.logger.warning(
                "Ugyldig XML i statusrespons: %s",
                exc,
            )

        return self.status

    # ------------------------------------------------------------------
    # Resultat og logg
    # ------------------------------------------------------------------

    def get_log(self) -> str:
        if not self.engine or not self.job_id:
            return ""

        url = self.encode_url(
            self.engine,
            f"/jobs/{self.job_id}/log",
            {},
        )

        response = requests.get(
            url,
            timeout=self._log_timeout(),
        )
        response.raise_for_status()

        return response.text

    def get_results(self) -> None:
        if (
            not self.engine
            or not self.job_id
            or not self.dir_output
        ):
            raise RuntimeError(
                "Jobb, engine eller outputmappe mangler."
            )

        result_file = tempfile.NamedTemporaryFile(
            prefix="daisy-pipeline-results-",
            suffix=".zip",
            delete=False,
        )
        result_path = result_file.name
        result_file.close()

        url = self.encode_url(
            self.engine,
            f"/jobs/{self.job_id}/result",
            {},
        )

        try:
            with requests.get(
                url,
                stream=True,
                timeout=self._result_timeout(),
            ) as response:
                response.raise_for_status()

                with open(
                    result_path,
                    "wb",
                ) as output:
                    for chunk in response.iter_content(
                        chunk_size=1024 * 1024
                    ):
                        if chunk:
                            output.write(chunk)

            if (
                not os.path.isfile(result_path)
                or os.path.getsize(result_path) == 0
            ):
                raise RuntimeError(
                    f"Ingen gyldig resultatfil fra {url}."
                )

            os.makedirs(
                self.dir_output,
                exist_ok=True,
            )

            if not zipfile.is_zipfile(
                result_path
            ):
                raise RuntimeError(
                    "Pipeline-resultatet er ikke en gyldig ZIP-fil."
                )

            self.logger.info(
                "Result is a ZIP file, attempting to extract."
            )

            with zipfile.ZipFile(
                result_path,
                "r",
            ) as archive:
                self._safe_extract_zip(
                    archive,
                    Path(self.dir_output),
                )

            self.logger.info(
                "Extracted '%s' to '%s'",
                result_path,
                self.dir_output,
            )

        finally:
            try:
                os.remove(result_path)
            except OSError:
                pass

    @staticmethod
    def _safe_extract_zip(
        archive: zipfile.ZipFile,
        destination: Path,
    ) -> None:
        destination = destination.resolve()

        for member in archive.infolist():
            target = (
                destination
                / member.filename
            ).resolve()

            try:
                target.relative_to(destination)
            except ValueError as exc:
                raise RuntimeError(
                    "Utrygg sti i Pipeline-resultat: "
                    f"{member.filename}"
                ) from exc

        archive.extractall(destination)

    # ------------------------------------------------------------------
    # Job cleanup
    # ------------------------------------------------------------------

    def delete_job(
        self,
        engine: dict[str, Any],
        job_id: str,
    ) -> bool:
        self.logger.info(
            "Deleting: %s @ %s",
            job_id,
            engine["endpoint"],
        )

        url = self.encode_url(
            engine,
            f"/jobs/{job_id}",
            {},
        )

        for attempt in range(3):
            try:
                response = requests.delete(
                    url,
                    timeout=self._status_timeout(),
                )

                if response.status_code in {
                    200,
                    204,
                    404,
                }:
                    self.logger.info(
                        "Deleted (or already gone): %s @ %s",
                        job_id,
                        engine["endpoint"],
                    )
                    return True

                self.logger.warning(
                    "Delete failed (%s): %s @ %s",
                    response.status_code,
                    job_id,
                    engine["endpoint"],
                )

            except requests.RequestException as exc:
                self.logger.warning(
                    "Delete attempt %s failed: %s",
                    attempt + 1,
                    exc,
                )

            time.sleep(
                0.5 * (2 ** attempt)
            )

        return False

    @classmethod
    def is_alive(
        cls,
        engine: dict[str, Any],
    ) -> bool:
        url = cls.encode_url(
            engine,
            "/alive",
            {},
        )

        try:
            response = requests.get(
                url,
                timeout=cls._discovery_timeout(),
            )
            return response.ok
        except requests.RequestException:
            return False

    def get_queue_size(
        self,
        engine: dict[str, Any],
    ) -> int:
        url = self.encode_url(
            engine,
            "/jobs",
            {},
        )

        try:
            response = requests.get(
                url,
                timeout=self._status_timeout(),
            )

            if not response.ok:
                return 10

            xml = ElementTree.fromstring(
                response.content
            )

        except Exception:
            return 10

        queue_size = 0
        job_ids: list[str] = []

        for job in xml.xpath(
            "/d:jobs/d:job",
            namespaces=self.dp2_ws_namespace,
        ):
            job_id = job.attrib.get("id")

            if job_id:
                job_ids.append(job_id)

            if job.attrib.get("status") in {
                "IDLE",
                "RUNNING",
            }:
                queue_size += 1

        self.delete_old_jobs(
            engine,
            job_ids,
        )

        return queue_size

    def delete_old_jobs(
        self,
        engine: dict[str, Any],
        job_ids: list[str],
    ) -> None:
        if DaisyPipelineJob.engine_jobs is None:
            DaisyPipelineJob.engine_jobs = {}

        endpoint = engine["endpoint"]

        DaisyPipelineJob.engine_jobs.setdefault(
            endpoint,
            {},
        )

        known = DaisyPipelineJob.engine_jobs[
            endpoint
        ]

        now = time.time()

        for job_id in job_ids:
            known.setdefault(
                job_id,
                now,
            )

        for job_id in list(
            known.keys()
        ):
            if job_id not in job_ids:
                del known[job_id]

        max_age = self._env_float(
            "DAISY_PIPELINE_JOB_CLEANUP_AGE_SECONDS",
            10800.0,
        )

        for job_id, created in list(
            known.items()
        ):
            if now - created > max_age:
                self.delete_job(
                    engine,
                    job_id,
                )

    # ------------------------------------------------------------------
    # URL-signering
    # ------------------------------------------------------------------

    @staticmethod
    def encode_url(
        engine: dict[str, Any],
        endpoint: str,
        parameters: dict[str, Any],
    ) -> str:
        parameters = dict(parameters)

        if engine.get("authentication") == "true":
            iso8601 = datetime.datetime.utcnow().strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            nonce = str(
                random.randint(
                    10 ** 29,
                    10 ** 30 - 1,
                )
            )

            parameters["authid"] = engine.get(
                "key"
            )
            parameters["time"] = iso8601
            parameters["nonce"] = nonce

        url = (
            engine["endpoint"].rstrip("/")
            + endpoint
        )

        if parameters:
            url += "?" + urllib.parse.urlencode(
                parameters
            )

        if engine.get("authentication") == "true":
            secret = engine.get("secret")

            if not secret:
                raise RuntimeError(
                    "Pipeline-autentisering er aktiv, "
                    "men secret mangler."
                )

            digest = hmac.new(
                secret.encode("utf-8"),
                url.encode("utf-8"),
                digestmod=hashlib.sha1,
            )

            signature = base64.b64encode(
                digest.digest()
            ).decode("utf-8")

            signature = signature.replace(
                "+",
                "%2B",
            )

            separator = (
                "&"
                if "?" in url
                else "?"
            )
            url += (
                f"{separator}sign={signature}"
            )

        return url
