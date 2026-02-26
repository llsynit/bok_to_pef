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
#from daisy_pipeline_light import RemoteDaisyPipelineJob
from daisy_pipeline import DaisyPipelineJob
from prepare_for_pef import prepare_for_pef
from config import Config

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


def save_artifact(pip_output, job_status, message, production_number, uid, job_id, handler, pip_log=None,  artifacts_folder="artifacts", prepared_xhtml_file: str | Path | None = None):
    """
    Save artifacts to the artifacts folder
    """

    os.makedirs(artifacts_folder, exist_ok=True)
    job_folder = os.path.join(artifacts_folder, job_id)
    os.makedirs(job_folder, exist_ok=True)
    # 1) Write logs and message
    combined_log = pip_log or "No pipeline log available."
    logs_txt_path = os.path.join(job_folder, "logs.txt")
    dagsrapport = f"{datetime.now().strftime('%Y-%m-%d')}-{uid}.txt"

    # save epub_as_folder
    # if epub_as_folder and os.path.exists(epub_as_folder):
    #    shutil.copytree(epub_as_folder, os.path.join(
    #        job_folder, f"{production_number}_folder"))
    if prepared_xhtml_file and Path(prepared_xhtml_file).exists():
        dest_prepared = os.path.join(job_folder, f"{production_number}_prepared.html")
        shutil.copy2(prepared_xhtml_file, dest_prepared)
        logger.info(f"[save_artifact] Saved prepared file: {dest_prepared}")
    
    logger.info(f"[save_artifact] prepared_file not found: {prepared_xhtml_file}")


    if job_status in ("DONE", "SUCCESS"):
        logger.info(f"Saving artifacts to {job_folder}")
        # check if pip_output is a zip file and extract it else it is just a folder copy thte content
        if zipfile.is_zipfile(pip_output):
            logger.info("pip_output is a zip file")
            with zipfile.ZipFile(pip_output, 'r') as zip_ref:
                zip_ref.extractall(job_folder)
        else:
            logger.info("pip_output is a folder")
            # copy the content of the folder to job_folder
            for item in os.listdir(pip_output):
                print("items in output")
                print(item)
                s = os.path.join(pip_output, item)
                d = os.path.join(job_folder, item)
                if os.path.isdir(s):
                    shutil.copytree(s, d, dirs_exist_ok=True)
                else:
                    shutil.copy2(s, d)

        # clean up pip_output folder
        shutil.rmtree(pip_output, ignore_errors=True)
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
               log_handler=None, **kwargs,):
    save_prepared_xhtml = kwargs.pop("save_prepared_xhtml", True)
    pip_output = None
    pip_log = None
    prepared_xhtml_file = None
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
            message=message or "",
            prepared_xhtml_file = prepared_xhtml_file,
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
        name = getattr(html, "filename", "input.html")
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

    prepared_html = Path(status["html_path"])
    if save_prepared_xhtml:
        prepared_xhtml_file = prepared_html
    logger.info(f"Prepared HTML located at::::: {prepared_html}")

    # Stage a working copy (optional, but matches your original intent)
    temp_dir = Path(tempfile.mkdtemp())
    target_name = prepared_html.name
    html_path_context = temp_dir / target_name
    shutil.copyfile(prepared_html, html_path_context)
    file_name = f"{production_number}.html"
    logger.info(f"Prepared HTML copied to working dir::::: {prepared_html.name}")
    try:
        args_from_queue = json.loads(braille_arguments_from_queue)
    except json.JSONDecodeError:
        args_from_queue = {}

    common_args = {
        "page-width": '38',
        "page-height": '29',
        "toc-depth": '2',
        #"maximum-number-of-sheets": '50',
        "include-production-notes": 'true',
        "hyphenation": 'none',
        "include-preview": 'true',
        #"include-pdf": 'true',
        "hyphenation-at-page-breaks": 'except-at-volume-breaks',
        "allow-volume-break-inside-leaf-section-factor": '10',
        "prefer-volume-break-before-higher-level-factor": '1',
        #"stylesheet-parameters": "(skip-margin-top-of-page:true)",
        "stylesheet-parameters": "(skip-margin-top-of-page:true)(maximum-number-of-sheets:50)"
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

    """pipeline_and_script_version = [
         ("1.15.4-SNAPSHOT", "9.0.0"),
        # ("1.15.2", "8.2.1"),
        ("1.14.17-p1", "6.2.0"),
        #("1.14.17-p2-SNAPSHOT", "6.2.0"),
        #("1.14.14", "6.1.0"),
        #("1.14.14", "6.1.0"),
    ]"""

    #pipeline_and_script_version = [Config.PIP_PEF_STABLE, Config.PIP_PEF_TEST, Config.PIP_PEF_TEST2,]
    pipeline_and_script_version = [*Config.PIP_PEF_STABLE, *Config.PIP_PEF_TEST]

    script_id = "html-to-pef"
    braille_arguments = arguments
    with DaisyPipelineJob(script_id,
                              braille_arguments,
                              pipeline_and_script_version=pipeline_and_script_version,
                              context=context
                              ) as dp2_job:
            found_pipeline_version = dp2_job.found_pipeline_version
            found_script_version = dp2_job.found_script_version


           
            if dp2_job.status != "SUCCESS":
                logger.info("Klarte ikke å konvertere boken")
                message=  production_number + " feilet 😭👎" 
                pip_log = dp2_job.job_log
                return _finish(False, "FAIL", message)
            
            dp2_pef_dir = os.path.join(dp2_job.dir_output, "pef-output-dir")
            dp2_new_pef_dir = os.path.join(dp2_job.dir_output, "output-dir")
            #for pip version 1.14.15 and newer
            dp2_result_dir = os.path.join(dp2_job.dir_output, "result")
            if not os.path.exists(dp2_pef_dir) and os.path.exists(dp2_new_pef_dir):
                dp2_pef_dir = dp2_new_pef_dir

            if not os.path.exists(dp2_pef_dir) and not os.path.exists(dp2_new_pef_dir):
                dp2_pef_dir = dp2_result_dir

            if not os.path.isdir(dp2_pef_dir):
                logger.info("Finner ikke den konverterte boken.")
                message=  production_number + " feilet 😭👎" 
                return False
            if os.path.isdir(os.path.join(dp2_job.dir_output, "preview-output-dir")):
                logger.info("Preview files exist - copy to output")

            # get pef-preview files and copy to output
            if os.path.isdir(os.path.join(dp2_job.dir_output, "preview-output-dir")):
                logger.info("Copying preview files to output")
                preview_output_dir = os.path.join(dp2_job.dir_output, "preview-output-dir")
                for item in os.listdir(preview_output_dir):
                    s = os.path.join(preview_output_dir, item)
                    d = os.path.join(dp2_pef_dir, item)
                    if os.path.isdir(s):
                        shutil.copytree(s, d, dirs_exist_ok=True)
                    else:
                        shutil.copy2(s, d)
            else:
                logger.info("No preview files to copy")

            pip_output = dp2_pef_dir
            
            message = production_number + " ble konvertert 👍😄"
            pip_log = dp2_job.job_log
            return _finish(True, "DONE", message)


    

    
