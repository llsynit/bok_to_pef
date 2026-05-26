# app.py
import os
import shutil
import time
import uuid
import tempfile
import io
import logging
from contextlib import suppress, asynccontextmanager
import asyncio
import sys
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse
from datetime import datetime
import json
import aio_pika
import httpx
from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from aiormq.exceptions import ChannelInvalidStateError, AMQPConnectionError
from bok_to_pef import bok_to_pef
from utils import summarize_artifacts, cleanup_artifacts_once
from config import Config, logger

logging.getLogger("aio_pika").setLevel(logging.WARNING)
logging.getLogger("aiormq").setLevel(logging.WARNING)

uid = "bok_to_pef"
DOWNLOADS: Dict[str, bytes] = {}

logger.info(f"Starting {Config.MODULE_NAME} on port {Config.PORT}.....")

# AMQP CALLBACK FUNCTIONS (used by _setup_amqp)

def _on_reconnect(connection):
    """Called when connection is re-established"""
    logger.info(f"[{Config.MODULE_NAME}] AMQP connection restored!")
    app.state.amqp_enabled = True


def _on_connection_lost(connection, exc):
    """Called when connection is lost"""
    logger.warning(f"[{Config.MODULE_NAME}] AMQP connection lost: {exc}")
    app.state.amqp_enabled = False


# setup AMQP connection

async def _setup_amqp():  # Removed "_once" - this is the only version needed
    """
    Setup AMQP connection. connect_robust handles reconnection automatically.
    """
    try:
        # connect_robust will keep retrying internally
        app.state.amqp_conn = await aio_pika.connect_robust(
            Config.RABBITMQ_URL,
            reconnect_interval=5,      # Retry every 5 seconds if it drops
            fail_fast=False,           # Don't give up, keep retrying
        )
        
        # Add a callback to know when connection is lost/restored
        app.state.amqp_conn.reconnect_callbacks.add(_on_reconnect)
        app.state.amqp_conn.close_callbacks.add(_on_connection_lost)
        
        ch = await app.state.amqp_conn.channel()
        await ch.set_qos(prefetch_count=1)
        app.state.amqp_ch = ch

        # Declare exchanges
        app.state.work_ex = await ch.declare_exchange(
            Config.WORK_EXCHANGE, aio_pika.ExchangeType.DIRECT, durable=True
        )
        app.state.results_ex = await ch.declare_exchange(
            Config.RESULTS_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
        )

        # Declare and bind queue
        q = await ch.declare_queue(Config.WORK_QUEUE_NAME, durable=True)
        await q.bind(app.state.work_ex, routing_key=Config.WORK_ROUTING_KEY)

        # Start consuming
        await q.consume(_handle_work_message)
        
        logger.info(
            f"[{Config.MODULE_NAME}] AMQP connected and consuming from '{Config.WORK_QUEUE_NAME}'"
        )
        app.state.amqp_enabled = True
        
    except asyncio.CancelledError:
        logger.info(f"[{Config.MODULE_NAME}] AMQP setup task was cancelled")
        raise
    except (AMQPConnectionError, OSError, ConnectionRefusedError) as e:
        # Initial connection failed - but connect_robust will keep trying in background
        logger.warning(
            f"[{Config.MODULE_NAME}] Initial AMQP connection failed: {e}. "
            "Will retry automatically in background."
        )
        app.state.amqp_enabled = False
    except Exception as e:
        logger.error(
            f"[{Config.MODULE_NAME}] Unexpected error during AMQP connection setup: {e}",
            exc_info=True
        )
        app.state.amqp_enabled = False


# =============================================================================
#  Utility functions
# =============================================================================

async def _http_download_to(dst: Path, url: str):
    """Download http(s) or copy file:// to dst"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    u = urlparse(url)
    #logger.info(f"downloading from {u}")
    if u.scheme in ("http", "https"):
        async with httpx.AsyncClient(timeout=120) as http:
            r = await http.get(url)
            r.raise_for_status()
            dst.write_bytes(r.content)
    elif u.scheme == "file":
        src = Path(u.path)
        if not src.exists():
            raise FileNotFoundError(f"file:// source not found: {src}")
        dst.write_bytes(src.read_bytes())
    else:
        raise HTTPException(400, f"Unsupported URI scheme: {u.scheme}")


def _art_uri(job_id: str,  name: str) -> str:
    return f"{Config.WORKER_BASE_URL}/artifacts/{job_id}/{name}"

# Cleanup loop
async def _cleanup_loop():
    """
    Periodically clean up old artifacts. Never raises.
    """
    while True:
        try:
            stats = cleanup_artifacts_once(
                Config.ARTIFACTS_ROOT, Config.ARTIFACTS_RETENTION_HOURS, logger)
            logger.debug("Artifacts cleanup stats: %s", stats)
        except Exception as e:
            logger.warning("Artifacts cleanup loop error: %r", e)
        await asyncio.sleep(Config.ARTIFACTS_CLEAN_INTERVAL_SEC)


# Result publishing
async def _publish_result(stage: str, job_id: str, status: str, artifacts: Dict, correlation_id: Optional[str]):
    rk = f"job.{job_id}.stage.{stage}.status.{status}"
    payload = {
        "job_id": job_id,
        "stage": stage,
        "status": status,          # "ok" | "fail"
        "artifacts": artifacts,    # URIs (ephemeral here)
        "finished_at": time.time()
    }
    body = __import__("json").dumps(
        payload, ensure_ascii=False).encode("utf-8")
    msg = aio_pika.Message(
        body=body,
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        correlation_id=correlation_id,
        message_id=str(uuid.uuid4()),
    )
    await app.state.results_ex.publish(msg, routing_key=rk)


async def _save_and_publish_result(
    stage: str,
    job_id: str,
    status: str,
    artifacts: Dict,
    correlation_id: Optional[str]
):
    """
    Save result to disk first, then try to publish.
    This ensures we never lose results even if publishing fails.
    """
    # 1. Save result to disk (ALWAYS succeeds)
    result_data = {
        "job_id": job_id,
        "stage": stage,
        "status": status,
        "artifacts": artifacts,
        "correlation_id": correlation_id,
        "finished_at": time.time(),
        "published": False
    }
    
    result_file = Config.ARTIFACTS_ROOT / job_id / "result.json"
    try:
        result_file.write_text(json.dumps(result_data, indent=2, ensure_ascii=False))
        logger.info(f"[{job_id}] Result saved to disk")
    except Exception as e:
        logger.error(f"[{job_id}] CRITICAL: Failed to save result: {e}")
        # Continue anyway and try to publish
    
    # 2. Try to publish to RabbitMQ (might fail)
    try:
        if not app.state.amqp_enabled:
            logger.warning(f"[{job_id}] AMQP not connected, result saved locally only")
            return
            
        await _publish_result(stage, job_id, status, artifacts, correlation_id)
        
        # Mark as published
        result_data["published"] = True
        result_file.write_text(json.dumps(result_data, indent=2, ensure_ascii=False))
        logger.info(f"[{job_id}] Result published to RabbitMQ")
        
    except (ChannelInvalidStateError, AMQPConnectionError) as e:
        logger.warning(
            f"[{job_id}] Failed to publish result: {e}. "
            "Saved locally - will republish on message redelivery."
        )
    except Exception as e:
        logger.error(f"[{job_id}] Unexpected error publishing: {e}")

async def _republish_pending_results():
    """
    Background task to republish results that were saved but not published.
    """
    while True:
        try:
            await asyncio.sleep(60)  # Check every minute
            
            if not app.state.amqp_enabled:
                continue

            # Find unpublished results older than 5 seconds
            # This prevents race condition with fresh results
            cutoff_time = time.time() - 5
            
            # Find unpublished results
            for result_file in Config.ARTIFACTS_ROOT.rglob("result.json"):
                try:
                    # Check file modification time first
                    if result_file.stat().st_mtime > cutoff_time:
                        continue  # Too recent, skip to avoid race condition

                    result_data = json.loads(result_file.read_text())
                    
                    if result_data.get("published", False):
                        continue  # Already published
                    
                    job_id = result_data["job_id"]
                    logger.info(f"[{job_id}] Republishing pending result")
                    
                    await _publish_result(
                        result_data["stage"],
                        job_id,
                        result_data["status"],
                        result_data["artifacts"],
                        result_data.get("correlation_id")
                    )
                    
                    # Mark as published
                    result_data["published"] = True
                    result_file.write_text(json.dumps(result_data, indent=2))
                    logger.info(f"[{job_id}] Pending result republished")
                    
                except Exception as e:
                    logger.warning(f"Failed to republish result from {result_file}: {e}")
                    
        except Exception as e:
            logger.error(f"Error in republish loop: {e}")



# =============================================================================
# RabbitMQ consumer with idempotency
# =============================================================================

async def _handle_work_message(m: aio_pika.IncomingMessage):
    """
    Handle work messages with idempotency protection.
    If connection drops during processing, message will be redelivered.
    """
    job_id = None
    
    try:
        async with m.process():
            # Parse message
            data = json.loads(m.body.decode("utf-8"))
            job_id = data.get("job_id")
            stage = data.get("stage", "bok_to_pef")
            inputs = data.get("inputs", {})
            corr_id = data.get("correlation_id") or m.correlation_id
            production_number = str(data.get("production_number", ""))
            save_prepared_xhtml = bool(data.get("save_prepared_xhtml", False))
            braille_arguments_from_queue = str(data.get("braille_arguments_from_queue", "{}"))
            xhtml_uri = inputs.get("xhtml_uri")

            if not (job_id and xhtml_uri and production_number):
                logger.error(f"[{job_id or '?'}] Missing required fields")
                await _publish_result(
                    stage, job_id or "?", "fail",
                    {"error": "missing job_id/xhtml_uri/production_number"}, 
                    corr_id
                )
                return

            # Setup workspace
            job_dir = Config.ARTIFACTS_ROOT / job_id
            job_dir.mkdir(parents=True, exist_ok=True)
            
            # =================================================================
            # IDEMPOTENCY CHECK
            # =================================================================
            result_file = job_dir / "result.json"
            
            if result_file.exists():
                # Already processed - just republish the result
                logger.info(f"[{job_id}] Job already processed, republishing result")
                result_data = json.loads(result_file.read_text())
                
                if not result_data.get("published", False):
                    await _publish_result(
                        result_data["stage"],
                        job_id,
                        result_data["status"],
                        result_data["artifacts"],
                        result_data.get("correlation_id")
                    )
                    # Mark as published
                    result_data["published"] = True
                    result_file.write_text(json.dumps(result_data, indent=2))
                    logger.info(f"[{job_id}] Result republished successfully")
                else:
                    logger.info(f"[{job_id}] Result already published, acknowledging message")
                
                return
            # =================================================================

            # Download input
            tmp_xhtml = job_dir / "input.xhtml"
            await _http_download_to(tmp_xhtml, xhtml_uri)

            # Process the job
            logger.info(f"[{job_id}] Starting processing")
            try:
                status = bok_to_pef(
                    tmp_xhtml, 
                    braille_arguments_from_queue, 
                    job_id, 
                    production_number,
                    save_prepared_xhtml=save_prepared_xhtml
                )
            except Exception as e:
                logger.error(f"[{job_id}] Processing failed: {e}", exc_info=True)
                await _save_and_publish_result(
                    stage, job_id, "fail", 
                    {"error": f"bok_to_pef crashed: {e}"}, 
                    corr_id
                )
                return

            # Check output
            if not job_dir.exists():
                logger.error(f"[{job_id}] Output directory missing")
                await _save_and_publish_result(
                    stage, job_id, "fail",
                    {"error": f"Could not find artifact folder: {job_dir}"},
                    corr_id
                )
                return

            # Collect artifacts
            artifacts = {}

            excluded_files = {"result.json", "input.xhtml", "input.html",
                    f"{production_number}_prepared.html"}  # Always exclude
            for path in job_dir.rglob("*"):
                if not path.is_file():
                    continue
                    
                # Skip images folder
                if "images" in path.parts:
                    continue
                
                # Skip excluded files
                if path.name in excluded_files:
                    logger.debug(f"[{job_id}] Excluding: {path.name}")
                    continue
                
                # Add to artifacts
                #rel_path = str(path.relative_to(job_dir))
                #artifacts[rel_path] = _art_uri(job_id, rel_path)
               
                artifact_name = str(path.relative_to(job_dir))
                artifact_url = _art_uri(job_id, str(path.relative_to(job_dir)))
                #artifacts[str(path.relative_to(job_dir))] = _art_uri(job_id, str(path.relative_to(job_dir)))
                logger.info(f"[{job_id}] Adding artifact: {artifact_name} -> {artifact_url}")
                artifacts[artifact_name] = artifact_url

            # Determine status
            if isinstance(status, dict):
                status_value = status.get("status", "ok")
            else:
                status_value = "ok" if status else "fail"

            logger.info(f"[{job_id}] Processing completed with status: {status_value}")
            
            # Save result and publish
            await _save_and_publish_result(
                stage, job_id, status_value, artifacts, corr_id
            )
            
    except ChannelInvalidStateError:
        # Connection dropped during processing
        # Message will be redelivered and idempotency will handle it
        logger.warning(
            f"[{job_id or '?'}] Channel closed during processing. "
            "Message will be redelivered automatically."
        )
        
    except Exception as e:
        # Unexpected error - log it
        logger.error(
            f"[{job_id or '?'}] Unexpected error: {e}",
            exc_info=True
        )


# =============================================================================
# Lifespan Context Manager
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup and shutdown events
    """
    # Startup
    logger.info(f"[{Config.MODULE_NAME}] Starting up...")
    
    # Start AMQP connection in background to avoid blocking HTTP routes
    logger.info(f"[{Config.MODULE_NAME}] Starting AMQP connection task in background...")
    app.state._amqp_task = asyncio.create_task(_setup_amqp())
    
    # Start cleanup loop
    logger.info("Starting artifacts cleanup loop...")
    app.state._cleanup_task = asyncio.create_task(_cleanup_loop())

    # Start republisher
    logger.info("Starting result republisher...")
    app.state._republish_task = asyncio.create_task(_republish_pending_results())
    
    yield
    
    # Shutdown
    logger.info(f"[{Config.MODULE_NAME}] Shutting down...")
    
    # Stop AMQP setup task if still running
    amqp_task = getattr(app.state, "_amqp_task", None)
    if amqp_task:
        amqp_task.cancel()
        with suppress(asyncio.CancelledError):
            await amqp_task

    # Stop cleanup loop
    task = getattr(app.state, "_cleanup_task", None)
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    
    # Stop republisher
    task = getattr(app.state, "_republish_task", None)
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    # Close AMQP
    conn = getattr(app.state, "amqp_conn", None)
    if conn:
        with suppress(Exception):
            await conn.close()
    
    logger.info(f"[{Config.MODULE_NAME}] Shutdown complete")



# =============================================================================
# FastAPI
# =============================================================================

# app = FastAPI(title=Config.MODULE_NAME, version="2.0.0", debug=True)
app = FastAPI(title=Config.MODULE_NAME, version="2.0.0", debug=True, lifespan=lifespan)
app.state.amqp_enabled = False
app.state.amqp_conn = None
app.state.amqp_ch = None
app.state._amqp_reconnector_task = None  # background task handle
RECONNECT_DELAY_SECONDS = 30

# Track current job to avoid deleting it while processing (optional but recommended)
app.state.current_job = getattr(app.state, "current_job", {
                                "running": False, "production_number": None, "status": None, "job_id": None})


# Serve ephemeral artifacts (no persistent volume!)
app.mount("/artifacts", StaticFiles(directory=str(Config.ARTIFACTS_ROOT)),
          name="artifacts")

# =============================================================================
# HTTP API (kept for manual testing)
# =============================================================================

@app.post("/run")
async def run(
    request: Request,
    xhtml: UploadFile = File(...),
    braille_arguments_from_queue: Optional[str] = Form(default="{}"),
    save_prepared_xhtml: Optional[bool] = Form(default=True),
):
    """
    Input: XHTML file (UploadFile)
    Returns a pef file and logs.
    Manual test endpoint — not used by RabbitMQ flow.
    """
    t0 = time.time()
    # Get original filename
    xhtml_name = xhtml.filename or ""
    suffix = Path(xhtml.filename or "").suffix or ".xhtml"

    # Extract production_number from filename (basename without extension)
    production_number = Path(xhtml_name).stem
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await xhtml.read())
        xhtml_path = tmp.name

    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S%f")
    job_id = f"{production_number}-{timestamp}"

    try:
        status = bok_to_pef(
            xhtml_path, braille_arguments_from_queue, job_id, production_number,save_prepared_xhtml=save_prepared_xhtml,

        )
    finally:
        try:
            os.unlink(xhtml_path)
        except Exception:
            pass
    logger.info("Validation completed, preparing artifacts...")
    job_dir = Config.ARTIFACTS_ROOT / job_id
    logger.info(f"Job dir: {job_dir}")

    if not os.path.isdir(job_dir):
        logger.warning(
            f"Could not find artifact folder for this job: {job_dir}")
        raise HTTPException(
            500, f"Could not find artifact folder for this job: {job_dir}")

    # Zip the folder to a temp file for download
    zip_base = os.path.join(tempfile.gettempdir(), f"{job_id}")
    # returns path/to/<base>.zip
    zip_path = shutil.make_archive(zip_base, "zip", job_dir)

    headers = {
        "X-Validation-Status": status.get("status"),
        "X-Processing-Time-ms": str(int((time.time() - t0) * 1000)),
    }
    download_name = f"{production_number}-artifacts.zip"
    return FileResponse(zip_path, media_type="application/zip", filename=download_name, headers=headers)


@app.get("/download/{token}")
async def download(token: str):
    data = DOWNLOADS.pop(token, None)
    if data is None:
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename=\"result.zip\"'},
    )


@app.post("/admin/cleanup-artifacts")
async def admin_cleanup_artifacts():
    stats = cleanup_artifacts_once(
        Config.ARTIFACTS_ROOT, Config.ARTIFACTS_RETENTION_HOURS, logger)
    return {"ok": True, "retention_hours": Config.ARTIFACTS_RETENTION_HOURS, "stats": stats}


@app.get("/health")
def health():
    artifacts = summarize_artifacts(Config.ARTIFACTS_ROOT)
    print(artifacts)
    return {"status": True, "module": Config.MODULE_NAME}

