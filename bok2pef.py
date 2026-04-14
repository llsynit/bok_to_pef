# Local
from config import logger
from daisy_pipeline import DaisyPipelineJob
from prepare_for_pef import prepare_for_pef

# Built-in
import os
import shutil
import logging
from config import Config
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
XSLT_DIR = PROJECT_ROOT / "xslt" / "prepare-for-braille"


def bok_to_pef(file_path, config_data, current_job, tmp_dir):

    # Run your pre-processing; it returns a path string in xhtml_path
    current_job["status"] = "processing"
    current_job["step"] = "1/3 - preparing file for Daisy"
    status = prepare_for_pef(str(file_path), tmp_dir)
    if not status.get("success"):
        raise RuntimeError(f"prepare_for_pef failed: {status.get('errors')}")
    prepared_html = Path(status["html_path"])
    logger.info(f"Prepared HTML located at: {prepared_html}")

    # Stage a working copy (optional, but matches your original intent)
    file_name = prepared_html.name
    html_path_context = tmp_dir / file_name

    arguments = {
        "source": file_name,
        "transform": "(formatter:dotify)(translator:liblouis)(dots:6)(grade:0)",
        "stylesheet": "braille.scss",
        **config_data
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
    current_job["step"] = "2/3 - file in Daisy pipeline"
    with DaisyPipelineJob(script_id,
                              arguments,
                              pipeline_and_script_version=pipeline_and_script_version,
                              context=context
                              ) as dp2_job:


           
            if dp2_job.status != "SUCCESS":
                logger.info("Klarte ikke å konvertere boken")
                raise RuntimeError(f"{file_name} failed in DAISY pipeline")
            current_job["step"] = "3/3 - retriving files from Daisy pipeline"

            # Helper - If return paths have changed
            if logger.isEnabledFor(logging.DEBUG):
                for p in Path(dp2_job.dir_output).rglob("*"):
                    logger.debug(p)

            # Retrieval 1 - retrieving main pef file
            dp2_pef_dir = os.path.join(dp2_job.dir_output, "result")
            pef_files = list(Path(dp2_pef_dir).glob("*.pef"))
            if not pef_files:
                raise RuntimeError(f"No .pef file found in {dp2_pef_dir}")
            pef_path = pef_files[0]
            return_path = tmp_dir / pef_path.name
            shutil.copy2(pef_path, return_path)

            # Retrieval 2 - retrieving pef preview files
            if config_data.get("include-preview") == "true":
                gathering_folder = tmp_dir / "gather"
                gathering_folder.mkdir()
                preview_output_dir = os.path.join(dp2_job.dir_output, "preview")
                for item in os.listdir(preview_output_dir):
                        from_path = os.path.join(preview_output_dir, item)
                        to_path = os.path.join(gathering_folder, item)
                        if os.path.isdir(from_path):
                            shutil.copytree(from_path, to_path, dirs_exist_ok=True)
                        else:
                            shutil.copy2(from_path, to_path)
                return_path2 = tmp_dir / "preview.zip"
                shutil.make_archive(str(tmp_dir / "preview"), "zip", gathering_folder)
            else:
                return_path2 = None
            
            # Retrieval 3 - retrieving internal logs from Daisy pipeline
            report = dp2_job.job_log.encode("utf-8")
            report_extension = ".txt"
            
            # Return
            logger.debug("bok2pef is returning")
            return return_path, return_path2, report, report_extension


    

    
# Args previoulsy used
    # common_args = {
    #     "page-width": '38',
    #     "page-height": '29',
    #     "toc-depth": '2',
    #     #"maximum-number-of-sheets": '50',
    #     "include-production-notes": 'true',
    #     "hyphenation": 'none',
    #     "include-preview": 'true',
    #     #"include-pdf": 'true',
    #     "hyphenation-at-page-breaks": 'except-at-volume-breaks',
    #     "allow-volume-break-inside-leaf-section-factor": '10',
    #     "prefer-volume-break-before-higher-level-factor": '1',
    #     #"stylesheet-parameters": "(skip-margin-top-of-page:true)",
    #     "stylesheet-parameters": "(skip-margin-top-of-page:true)(maximum-number-of-sheets:50)"
    # }