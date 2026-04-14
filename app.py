"""
Module container API — exposes endpoints for health checks, status polling,
and file processing. Receives files from the controller, runs module logic,
and returns results as a structured zip attachment.
"""

# -----------------------------------------------------------------------------
# Imports
# -----------------------------------------------------------------------------

# Local
from bok2pef import bok_to_pef
from config import logger, LogCollector

# In-built
import io
import zipfile
import json
import os
import asyncio
import tempfile
import shutil
from typing import Optional
from pathlib import Path

# Pip installed
from fastapi import FastAPI, UploadFile
from fastapi.responses import Response


# -----------------------------------------------------------------------------
# FastAPI app
# -----------------------------------------------------------------------------

module_name = os.getenv("MODULE_NAME", "unknown")
module_version = os.getenv(f"{module_name.upper()}_VERSION", "unknown")
app = FastAPI(title=module_name, version=module_version)

# -----------------------------------------------------------------------------
# API methods
# -----------------------------------------------------------------------------

current_job = {"status": "Idle", "step": None} # Suggested return: "x/y - step name" meaning currently on x out of y steps

@app.get("/health")
async def health():
    """Returns health state of container."""
    return {"health": "ok"}

@app.get("/status")
async def status():
    """Returns process status of container."""
    return current_job

@app.post("/process")
async def process(file: UploadFile, config: UploadFile, file2: Optional[UploadFile] = None):
    """
    Receives files and config from the controller, runs module processing,
    and returns results as a zip.

    The zip always contains a manifest.json describing its contents, used by
    the controller to unpack the response.

    Args:
        file: Primary file to process (multipart form upload).
        config: JSON config file with module parameters (multipart form upload).
        file2: Optional secondary file (e.g. PDF reference for validation).

    Returns:
        Response: application/zip containing:
            - manifest.json (describes zip contents and filenames),
            - processed primary file,
            - zipped preview file if requested,
            - log.json (list of log records, with module version as first entry),
            - report file form Daisy pipeline.
    """

    # Setup
    log_collector = LogCollector()
    logger.addHandler(log_collector)
    logger.info("/process inside %s started", module_name)
    input_file_bytes = await file.read()
    input_file_name = file.filename
    parameter_data = json.loads(await config.read())
    logger.debug(
        "Recieved data loaded in. File: %s is %s bytes long, Configuration keys: %s",
        input_file_name, len(input_file_bytes), parameter_data)

    # Setup: temporary directory
    try:
        tmp_dir = Path(tempfile.mkdtemp())
        input_file_path = tmp_dir / input_file_name
        input_file_path.write_bytes(input_file_bytes)
        logger.debug("File written to temporary directory %s as %s", tmp_dir, input_file_path)

        # Main: Calls container process
        logger.debug("Calling main processing function.")
        output_file_path, output_file2_path, report, report_extension = await asyncio.to_thread(
            bok_to_pef,
            input_file_path, parameter_data, current_job, tmp_dir
        )
        logger.debug("Main process returns.")

        # Packing: packs various content into a zip file
        log_collector.records.insert(0, {
            "level": "INFO",
            "message": module_name + " " + module_version,
            "timestamp": None
        })

        output_file_bytes = output_file_path.read_bytes()
        output_file2_bytes = output_file2_path.read_bytes() if output_file2_path else None
        output_file_name = output_file_path.name
        output_file2_name = output_file2_path.name if output_file2_path else None

        buf = io.BytesIO()
        manifest = {
            "primary": output_file_name,
            "secondary": output_file2_name, 
            "log": "log.json",
            "report": f"report{report_extension}"
        }
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("log.json", json.dumps(log_collector.records, indent=2))
            zf.writestr(output_file_name, output_file_bytes)
            if output_file2_path:
                zf.writestr(output_file2_name, output_file2_bytes)
            zf.writestr(f"report{report_extension or '.bin'}", report)
        buf.seek(0)
        logger.debug("Returning files zipped up as: %s", manifest)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.removeHandler(log_collector)

    # Return
    logger.debug("/process inside %s returns", module_name)
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{Path(output_file_name).stem}.zip"'}
    )