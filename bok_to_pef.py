import os
import sys
import io
import traceback
import shutil
import tempfile
import zipfile
from pathlib import Path
import logging
import time
from datetime import datetime
import json
from daisy_pipeline_light import RemoteDaisyPipelineJob
from prepare_for_pef import prepare_for_pef

PROJECT_ROOT = Path(__file__).resolve().parent
XSLT_DIR = PROJECT_ROOT / "xslt" / "prepare-for-braille"
uid = "bok_to_pef"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


class InMemoryLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.log_stream = io.StringIO()

    def emit(self, record):
        msg = self.format(record)
        self.log_stream.write(msg + "\n")

    def get_logs(self):
        return self.log_stream.getvalue()


def save_artifact(pip_output, job_status, message, production_number, uid, job_id, handler, pip_log=None,  artifacts_folder="artifacts"):
    """
    Save artifacts to the artifacts folder
    """

    print("Saving artifacts ****** ---> " + job_status)
    os.makedirs(artifacts_folder, exist_ok=True)
    job_folder = os.path.join(artifacts_folder, job_id)
    os.makedirs(job_folder, exist_ok=True)
    # 1) Write logs and message
    combined_log = pip_log or "No pipeline log available."
    logs_txt_path = os.path.join(job_folder, "logs.txt")
    dagsrapport = f"{datetime.now().strftime('%Y-%m-%d')}-{uid}.txt"
    # check if logs_txt_path and dagsrapport already exist, and if so, rename them with a timestamp
    if os.path.exists(logs_txt_path):
        print("logs exist ******")
    if os.path.exists(dagsrapport):
        print("dagsrapport exist ******")

    # save epub_as_folder
    # if epub_as_folder and os.path.exists(epub_as_folder):
    #    shutil.copytree(epub_as_folder, os.path.join(
    #        job_folder, f"{production_number}_folder"))

    if job_status in ("DONE", "SUCCESS") and pip_output is not None and os.path.exists(pip_output):
        print("Saving pip_output ******")
        logger.info(f"Saving artifacts to {job_folder}")
        # check if pip_output is a zip file and extract it else it is just a folder copy thte content
        if zipfile.is_zipfile(pip_output):
            print("pip_output is a zip file ******")
            logger.info("pip_output is a zip file")
            with zipfile.ZipFile(pip_output, 'r') as zip_ref:
                zip_ref.extractall(job_folder)
        else:
            logger.info("pip_output is a folder")
            print("pip_output is a folder ******")
            # copy the content of the folder to job_folder
            for item in os.listdir(pip_output):
                s = os.path.join(pip_output, item)
                d = os.path.join(job_folder, item)
                if os.path.isdir(s):
                    shutil.copytree(s, d, dirs_exist_ok=True)
                else:
                    shutil.copy2(s, d)

        # clean up pip_output folder
        shutil.rmtree(pip_output, ignore_errors=True)
    print("Finished saving artifacts ******")
    status = ""
    if job_status == "DONE" or job_status == "SUCCESS":
        status = "success"
        logger.info(f"Job succeeded: {message}")
    else:
        status = "fail"
        logger.error(f"Job failed: {message}")

    with open(logs_txt_path, "w", encoding="utf-8") as f:
        # 1) write handler logs first if available
        if handler:
            handler_logs = handler.get_logs()
            if handler_logs:
                f.write("===== bok to pef log =====\n")
                f.write(handler_logs)
                f.write("\n\n")
        # 2) then append the combined pipeline log
        if combined_log:
            f.write(combined_log)
    with open(os.path.join(job_folder, dagsrapport), "w", encoding="utf-8") as f:
        f.write(message or "")

    return status


def bok_to_pef(html, braille_arguments_from_queue, job_id, production_number,
               log_handler=None):
    pip_output = None
    pip_log = None
    handler = InMemoryLogHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    def _finish(ok: bool, dp_status: str, message: str) -> dict:
        """
        Create a single final zip via prepare_final_output and return a consistent dict.
        """
        status = save_artifact(
            # epub_as_folder=epub.asDir(),          # put logs next to the working epub dir
            pip_output=pip_output,            # may be None or missing on failures
            pip_log=pip_log or "",                # ensure we always write logs.txt
            job_status=dp_status,
            job_id=job_id,                          # "DONE" for success; anything else for failure
            uid=uid,
            production_number=production_number,
            handler=handler,
            message=message or ""
        )
        return {"status": status, "message": message}

    def _pre_fail(msg):
        logger.error(msg)
        return {
            "status": "failed",
            "message": msg,
        }

   # Ensure we have a filesystem path for prepare_for_pef
    if hasattr(html, "file"):  # looks like UploadFile / file-like
        # If you still want to support UploadFile here:
        tmp_dir = Path(tempfile.mkdtemp())
        name = getattr(html, "filename", "input.xhtml")
        html_path = tmp_dir / name
        with open(html_path, "wb") as f:
            f.write(html.file.read())
    else:
        # Assume it's a str/Path
        html_path = Path(html)
        if not html_path.exists():
            return _pre_fail(f"Input XHTML not found: {html_path}")

    # Run your pre-processing; it returns a path string in xhtml_path
    status = prepare_for_pef(str(html_path), logger)
    if not status.get("success"):
        return _pre_fail(f"prepare_for_pef failed: {status.get('errors')}")

    prepared_html = Path(status["xhtml_path"])

    print(f"Prepared HTML located at::::: {prepared_html}")

    # Stage a working copy (optional, but matches your original intent)
    temp_dir = Path(tempfile.mkdtemp())
    target_name = prepared_html.name
    html_path_context = temp_dir / target_name
    shutil.copyfile(prepared_html, html_path_context)
    file_name = f"{production_number}.xhtml"
    print(f"Prepared HTML copied to working dir::::: {prepared_html.name}")
    try:
        args_from_queue = json.loads(braille_arguments_from_queue)
    except json.JSONDecodeError:
        args_from_queue = {}

    common_args = {
        "page-width": '38',
        "page-height": '29',
        "toc-depth": '2',
        "maximum-number-of-sheets": '50',
        "include-production-notes": 'true',
        "hyphenation": 'none',
        "include-preview": 'true',
        "hyphenation-at-page-breaks": 'except-at-volume-breaks',
        "allow-volume-break-inside-leaf-section-factor": '10',
        "prefer-volume-break-before-higher-level-factor": '1',
        "stylesheet-parameters": "(skip-margin-top-of-page:true)",
    }
    if args_from_queue:
        logger.info("Using arguments from queue")
        arguments = {
            "source": file_name,
            "transform": "(formatter:dotify)(translator:liblouis)(dots:6)(grade:0)",
            "stylesheet": "braille.scss",
            **args_from_queue
        }
    else:
        logger.info("Using default arguments")
        arguments = {
            "source": file_name,
            "transform": "(formatter:dotify)(translator:liblouis)(dots:6)(grade:0)",
            "stylesheet": "braille.scss",
            **common_args
        }

    context = {
        file_name: html_path_context,
        "braille.scss": os.path.join(XSLT_DIR, "braille.scss")
    }

    versions = [
        ("1.14.17-p1", "6.2.0"),
        ("1.14.17-p2-SNAPSHOT", "6.2.0"),
    ]

    init_args = {
        "script_id": "html-to-pef",
        "arguments": arguments,
        "context": context,
        "versions": versions,
        "log_handler": log_handler,
    }

    dp2_job = RemoteDaisyPipelineJob(**init_args)
    result = dp2_job.run()
    pip_job_id = result.get("job_id")
    status = "RUNNING"
    timeout = time.time() + 600  # 10 min

    while status in ("RUNNING", "IDLE") and time.time() < timeout:
        status = dp2_job.get_status(pip_job_id)

        logger.info(
            f"Job {job_id} with pip job_id: {pip_job_id} status: {status}")
        if status == "DONE" or status == "SUCCESS":
            logger.info(
                f"Job {job_id} pip job_id: {job_id} completed successfully.")

            # job.download_all(job_id)
            pip_output = dp2_job.download_all(pip_job_id)
            if not pip_output:
                logger.error(
                    f"Job {job_id} pip job_id: {pip_job_id} failed to download report.")
                return False
            pip_log = dp2_job.get_log()
            if not pip_log:
                logger.error(
                    f"Job {job_id} pip job_id: {pip_job_id} failed to get log.")
                # return False
            break

        if status == "ERROR":
            logger.error("Klarte ikke å validere boken")
            logger.error(f"{production_number} feilet 😭👎")
            logger.error(
                f"Job with pip job_id: {pip_job_id} failed with error.")

            pip_log = dp2_job.get_log() or ""
            return _finish(False, status, f"{production_number} html to pef failed.")
        if status not in ("IDLE", "RUNNING"):
            pip_log = dp2_job.get_log() or ""
            return _finish(False, status, "PIP: unexpected status.")

        time.sleep(5)
    message = production_number + " ble konvertert 👍😄"

    return _finish(True, "DONE", message)
