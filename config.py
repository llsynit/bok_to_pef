"""
Felles konfigurasjon for bok_to_pef.

Miljøvariabler kan overstyre Pipeline- og scriptversjonene:

    PIP_PEF_STABLE_PIPELINE_VERSION
    PIP_PEF_STABLE_SCRIPT_VERSION
    PIP_PEF_TEST_PIPELINE_VERSION
    PIP_PEF_TEST_SCRIPT_VERSION
    PIP_PEF_TEST2_PIPELINE_VERSION
    PIP_PEF_TEST2_SCRIPT_VERSION

Standardverdiene samsvarer med versjonene som bok2pef.py forventer.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Final


def _get_log_level() -> int:
    """
    Finn loggnivå fra miljøvariabler.

    Først forsøkes:

        <MODULE_NAME>_LOG_LEVEL

    Deretter:

        LOG_LEVEL

    Verdien kan være eksempelvis INFO, DEBUG eller et numerisk nivå.
    """

    module_name = os.getenv("MODULE_NAME", "bok_to_pef").upper()
    value = os.getenv(
        f"{module_name}_LOG_LEVEL",
        os.getenv("LOG_LEVEL", "INFO"),
    )

    if isinstance(value, str):
        stripped = value.strip()

        if stripped.isdigit():
            return int(stripped)

        resolved = getattr(logging, stripped.upper(), None)

        if isinstance(resolved, int):
            return resolved

    return logging.INFO


def setup_logger() -> logging.Logger:
    """
    Konfigurer og returner modulens logger.
    """

    logging.basicConfig(
        level=_get_log_level(),
        format=(
            "%(asctime)s - %(levelname)s - "
            "%(name)s - %(message)s"
        ),
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    logging.getLogger(
        "python_multipart.multipart"
    ).setLevel(logging.WARNING)

    return logging.getLogger(__name__)


logger = setup_logger()


class ListHandler(logging.Handler):
    """
    Samle loggposter i en liste.

    Brukes når loggposter skal returneres til controlleren.
    """

    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, object]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(
            {
                "level": record.levelname,
                "message": record.getMessage(),
                "timestamp": record.created,
            }
        )


class Config:
    """
    Konfigurasjon brukt av bok2pef og DaisyPipelineJob.

    Hver PIP_PEF-verdi er en tuple av kombinasjoner:

        (
            ("pipeline-versjon", "script-versjon"),
            ...
        )

    Dette er viktig fordi bok2pef.py gjør:

        [
            *Config.PIP_PEF_STABLE,
            *Config.PIP_PEF_TEST,
        ]
    """

    MODULE_NAME: Final[str] = os.getenv(
        "MODULE_NAME",
        "bok_to_pef",
    )

    MODULE_VERSION: Final[str] = os.getenv(
        "BOK_TO_PEF_VERSION",
        os.getenv(
            f"{MODULE_NAME.upper()}_VERSION",
            "unknown",
        ),
    )

    PIP_PEF_STABLE: Final[tuple[tuple[str, str], ...]] = (
        (
            os.getenv(
                "PIP_PEF_STABLE_PIPELINE_VERSION",
                "1.14.17-p1",
            ),
            os.getenv(
                "PIP_PEF_STABLE_SCRIPT_VERSION",
                "6.2.0",
            ),
        ),
    )

    PIP_PEF_TEST: Final[tuple[tuple[str, str], ...]] = (
        (
            os.getenv(
                "PIP_PEF_TEST_PIPELINE_VERSION",
                "1.15.4-SNAPSHOT",
            ),
            os.getenv(
                "PIP_PEF_TEST_SCRIPT_VERSION",
                "9.0.0",
            ),
        ),
        (
            os.getenv(
                "PIP_PEF_TEST2_PIPELINE_VERSION",
                "1.15.2",
            ),
            os.getenv(
                "PIP_PEF_TEST2_SCRIPT_VERSION",
                "8.2.1",
            ),
        ),
    )

    ARTIFACTS_DIR: Final[str] = os.getenv(
        "ARTIFACTS_DIR",
        "artifacts",
    )

    ARTIFACTS_RETENTION_HOURS: Final[int] = int(
        os.getenv(
            "ARTIFACTS_RETENTION_HOURS",
            "24",
        )
    )
