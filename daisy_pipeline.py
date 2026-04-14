"""
Fil laget av TIBI for å connecte opp mot Daisy Pipeline 2. Ikke lokalt laget.
"""


# -*- coding: utf-8 -*-
import io
import pathlib
import logging
import base64
import datetime
import hashlib
import hmac
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
import traceback
import urllib
import zipfile

import requests
from lxml import etree as ElementTree
from requests_toolbelt.multipart.encoder import MultipartEncoder


class InMemoryLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.log_stream = io.StringIO()

    def emit(self, record):
        msg = self.format(record)
        self.log_stream.write(msg + "\n")

    def get_logs(self):
        return self.log_stream.getvalue()


class DaisyPipelineJob():
    """Class used to run DAISY Pipeline 2 jobs"""

    # treat these as instance variables
    _dir_output_obj = None  # store TemporaryDirectory object in instance so that it's not cleaned up
    dir_output = None
    job_log = None
    job_id = None
    pipeline = None
    status = None
    engine = None
    script = None
    arguments = None
    context = None
    priority = None
    found_pipeline_version = None
    found_script_version = None

    # treat these as class variables, specific for local jobs
    pid = None

    dp2_home = None
    dp2_cli = None

    # treat these as class variables
    engines = None
    
    engine_jobs = None  # for cleaning up old jobs

    dp2_ws_namespace = {"d": 'http://www.daisy.org/ns/pipeline/data'}

    @staticmethod
    def init_environment():

        DaisyPipelineJob.engines = []

        if "REMOTE_PIPELINE2_WS_ENDPOINTS" in os.environ:
            endpoints = re.sub(r"\s+", " ", os.getenv("REMOTE_PIPELINE2_WS_ENDPOINTS", "")).strip().split(" ")
            authentication = re.sub(r"\s+", " ", os.getenv("REMOTE_PIPELINE2_WS_AUTHENTICATION", "")).strip().split(" ")
            keys = re.sub(r"\s+", " ", os.getenv("REMOTE_PIPELINE2_WS_AUTHENTICATION_KEYS", "")).strip().split(" ")
            secrets = re.sub(r"\s+", " ", os.getenv("REMOTE_PIPELINE2_WS_AUTHENTICATION_SECRETS", "")).strip().split(" ")
            for e in range(0, len(endpoints)):
                print("endpoints ---")
                print(endpoints[e])
                DaisyPipelineJob.engines.append({
                    "endpoint": endpoints[e],
                    "authentication": authentication[e] if e < len(authentication) else "false",
                    "key": keys[e] if e < len(keys) else None,
                    "secret": secrets[e] if e < len(secrets) else None,
                    "local": False,
                })


    def __init__(self, script, arguments, context={}, priority="medium", pipeline_and_script_version=None):
        if isinstance(pipeline_and_script_version, tuple):
            pipeline_and_script_version = [pipeline_and_script_version]
        self.script = script
        self.arguments = arguments
        self.context = context
        self.priority = priority
        self.pipeline_and_script_version = pipeline_and_script_version

        self.logger = logging.getLogger(__name__)
        self.local_log_handler = InMemoryLogHandler()
        self.local_log_handler.setFormatter(logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s"))
        self.logger.addHandler(self.local_log_handler)


    def __enter__(self):
        DaisyPipelineJob.init_environment()

        self._dir_output_obj = tempfile.TemporaryDirectory(prefix="produksjonssystem-", suffix="-daisy-pipeline-output")
        self.dir_output = self._dir_output_obj.name

        if self.choose_engine():
            self.logger.info("trying to post......")
            try:
                self.post_job()

                self.status = "IDLE"
                idle_start = time.time()
                running_start = time.time()
                idle_timeout = 3600 * 2.5
                running_timeout = 3600 * 2
                timed_out = False
                engine_died = False
                while not timed_out and self.status in ["IDLE", "RUNNING"]:
                    timed_out = self.status == "IDLE" and time.time() - idle_start > idle_timeout or time.time() - running_start > running_timeout
                    time.sleep(5)

                    if self.status == "IDLE":
                        running_start = time.time()

                    is_alive = False
                    for retry in range(10):
                        is_alive = DaisyPipelineJob.is_alive(self.engine)
                        if is_alive:
                            break
                        time.sleep(5)
                    if not is_alive:
                        engine_died = True
                        self.logger.error("Pipeline 2 kjører ikke lenger. Avbryter…")
                        break

                    # get job status
                    self.logger.info("Getting job status")
                    self.get_status()

                    self.logger.info("Pipeline 2 status: " + self.status)

                if timed_out:
                    self.logger.error("Pipeline 2 brukte for lang tid")
                    self.status = None

                elif engine_died:
                    pass  # Nothing we can do

                else:
                    # get job log (the run method will log stdout/stderr as debug output)
                    self.logger.debug("Getting job log")
                    #self.logger.debug(self.get_log())
                    self.job_log = self.get_log()

                    # get job results
                    self.logger.debug("Getting job results")
                    self.get_results()

            except subprocess.TimeoutExpired:
                self.logger.error("Pipeline 2-jobben {} tok for lang tid og ble derfor stoppet".format(self.job_id))
                self.status = None

            except Exception:
                self.logger.debug(traceback.format_exc(), preformatted=True)
                self.logger.error("En feil oppstod ved kjøring av Pipeline 2-jobben (" + str(self.job_id) + ")")
                self.status = None

        else:
            self.logger.error("Pipeline 2 er ikke tilgjengelig")

        return self

    def choose_engine(self, use_local=False):
    
        self.engine = None
        (self.found_pipeline_version, self.found_script_version) = (None, None)

        for (pipeline_version, script_version) in self.pipeline_and_script_version:
            self.logger.info(f"Desired pipeline version {pipeline_version} and script {script_version}")
            if (pipeline_version, script_version) != self.pipeline_and_script_version[0]:
                self.logger.warning("Desired version of Pipeline 2 engine and version of script not found.")
                self.logger.warning(
                    "Trying Pipeline 2 engine version '{}' and script version '{}' instead…".format(pipeline_version, script_version)
                )

            for engine in DaisyPipelineJob.engines:
                self.logger.info(f"Checking engine: {engine['endpoint']}")
                if not self.script_available(engine, pipeline_version=pipeline_version, script_version=script_version):
                    # desired script is not available or engine is not available: don't use this engine
                    continue

                """queue_size = self.get_queue_size(engine)
                if queue_size < min_queue_size:
                    # smaller queue than any previously found: use this engine
                    self.engine = engine
                    (self.found_pipeline_version, self.found_script_version) = (pipeline_version, script_version)
                if queue_size == 0:
                    # empty queue: no point checking other engines
                    break"""
                self.logger.info("Appropriate engine found")
                self.logger.info(f"Desired pipeline version found {pipeline_version} and script {script_version}")
                self.engine = engine
                self.found_pipeline_version = pipeline_version
                self.found_script_version = script_version
                

            if self.engine:
                break  # if we've found an appropriate engine, don't try alternative versions


        if self.engine:
            self.logger.info("Bruker Pipeline 2-instans på: {}".format(self.engine["endpoint"]))
            self.logger.info("Pipeline 2-versjon: {}".format(self.found_pipeline_version))
            self.logger.info("Versjon av {}: {}".format(self.script, self.found_script_version))
        else:
            self.logger.warning("Fant ingen brukbar Pipeline 2-instans")

    
        return self.engine is not None


    def script_available(self, engine, pipeline_version, script_version):
        alive = None
        scripts = None

        try:
            self.logger.debug(DaisyPipelineJob.encode_url(engine, "/alive", {}))
            alive = requests.get(DaisyPipelineJob.encode_url(engine, "/alive", {}))
            if alive.ok:
                alive = str(alive.content, 'utf-8')
                alive = ElementTree.XML(alive.split("?>")[-1])
            else:
                alive = None
        except Exception:
            alive = None

        if alive is None:
            self.logger.warning("Pipeline 2 kjører ikke på: {}".format(engine["endpoint"]))
            return False

        # find engine version
        engine_pipeline_version = alive.attrib.get("version")

        # test for correct engine version
        if pipeline_version is not None and pipeline_version != engine_pipeline_version:
            self.logger.debug("Incorrect version of Pipeline 2. Looking for {} but found {}.".format(pipeline_version,
                                                                                                                    engine_pipeline_version))
            return False

        try:
            scripts = requests.get(DaisyPipelineJob.encode_url(engine, "/scripts", {}))
            if scripts.ok:
                scripts = str(scripts.content, 'utf-8')
                scripts = ElementTree.XML(scripts.split("?>")[-1])
            else:
                scripts = None
        except Exception:
            scripts = None

        if scripts is None:
            self.logger.warning("Klarte ikke å hente liste over skript fra Pipeline 2 på: {}".format(engine["endpoint"]))
            return False

        self.logger.debug(f"script ********* id repr: {self.script!r}")
        assert self.script is not None
        # find script
        engine_script = scripts.xpath("/d:scripts/d:script[@id='{}']".format(self.script), namespaces=DaisyPipelineJob.dp2_ws_namespace)
        engine_script = engine_script[0] if len(engine_script) else None

        # test if script was found
        if engine_script is None:
            self.logger.debug("Script not found: {}".format(self.script))
            return False

        # find script version
        engine_script_version = engine_script.xpath("d:version", namespaces=DaisyPipelineJob.dp2_ws_namespace) if len(engine_script) else None
        engine_script_version = engine_script_version[0].text if len(engine_script_version) else None

        # test if script version is correct
        if script_version is not None and script_version != engine_script_version:
            self.logger.info("Incorrect version of Pipeline 2. Looking for {} but found {}.".format(script_version,
                                                                                                                    engine_script_version))
            return False

        return True

    def __exit__(self, exc_type, exc_value, trace):
        if self.job_id:
            self.delete_job(self.engine, self.job_id)

    def post_job(self):
        self.logger.info("Posting job")

        script_href = DaisyPipelineJob.encode_url(self.engine, "/scripts/{}".format(self.script), {})
        response = requests.get(script_href)
        response = str(response.content, 'utf-8')
        script = ElementTree.XML(response.split("?>")[-1])

        jobRequest = ElementTree.XML("<jobRequest xmlns=\"http://www.daisy.org/ns/pipeline/data\"/>")
        jobRequest.append(ElementTree.XML("<priority xmlns=\"http://www.daisy.org/ns/pipeline/data\">{}</priority>".format(self.priority)))
        jobRequest.append(ElementTree.XML("<script href=\"{}\" xmlns=\"http://www.daisy.org/ns/pipeline/data\"/>".format(script_href)))

        for input in script.xpath("/d:script/d:input", namespaces=DaisyPipelineJob.dp2_ws_namespace):
            if input.attrib["name"] in self.arguments:
                values = []
                argument = self.arguments[input.attrib["name"]]
                if not isinstance(argument, list):
                    argument = [argument]
                for argument_value in argument:
                    value = argument_value

                    if self.engine["local"] and self.context:
                        # we're dealing with a local Pipeline 2 instance, which we assume are running with localfs=true,
                        # so that we need to use file: URIs
                        for href in self.context:
                            if value == href:
                                value = self.context[href]
                                value = pathlib.PurePath(value) if value[0] == "/" else pathlib.PureWindowsPath(value)
                                value = value.as_uri()

                    values.append(value)

                input_xml = "<input name=\"{}\" xmlns=\"http://www.daisy.org/ns/pipeline/data\">".format(input.attrib["name"])
                for value in values:
                    input_xml += "<item value=\"{}\"/>".format(value)
                input_xml += "</input>"
                jobRequest.append(ElementTree.XML(input_xml))

        for option in script.xpath("/d:script/d:option", namespaces=DaisyPipelineJob.dp2_ws_namespace):
            if option.attrib["name"] in self.arguments:
                values = []
                argument = self.arguments[option.attrib["name"]]
                if not isinstance(argument, list):
                    argument = [argument]
                for argument_value in argument:
                    value = argument_value

                    if self.engine["local"] and self.context and option.attrib.get("type") in ["anyFileURI", "anyDirURI"]:
                        # we're dealing with a local Pipeline 2 instance, which we assume are running with localfs=true,
                        # so that we need to use file: URIs
                        for href in self.context:
                            if value == href:
                                value = self.context[href]
                                value = pathlib.PurePath(value) if value[0] == "/" else pathlib.PureWindowsPath(value)
                                value = value.as_uri()

                    values.append(value)

                option_xml = "<option name=\"{}\" xmlns=\"http://www.daisy.org/ns/pipeline/data\">".format(option.attrib["name"])
                if len(values) == 1:
                    option_xml += values[0]
                else:
                    for value in values:
                        option_xml += "<item value=\"{}\"/>".format(value)
                option_xml += "</option>"
                jobRequest.append(ElementTree.XML(option_xml))
        # Temporary files
        jobRequest_file_obj = tempfile.NamedTemporaryFile(suffix=".xml")
        context_file_obj = tempfile.NamedTemporaryFile(suffix=".zip")

        multipart_fields = {}

        response = None
        jobRequest_file = None
        context_file = None

        try:  # use `try`/`except` instead of `with` for `open`ing the files

            # Save jobRequest as a file
            jobRequest_path = jobRequest_file_obj.name
            jobRequest_document = ElementTree.ElementTree(jobRequest)
            jobRequest_document.write(jobRequest_path, xml_declaration=True, encoding='UTF-8', pretty_print=True)
            jobRequest_file = open(jobRequest_path, 'rb')
            multipart_fields["job-request"] = ('jobRequest.xml', jobRequest_file, 'application/xml')
            with open(jobRequest_path) as f:
                self.logger.info("Job request: " + "".join(f.readlines()))

            # URL to POST to
            url = DaisyPipelineJob.encode_url(self.engine, "/jobs", {})

            # If there's a context, zip it and POST the request as a multipart request
            if self.context and not self.engine["local"]:
                context_path = context_file_obj.name
                """Zip the contents of `dir`"""
                with zipfile.ZipFile(context_path, 'w') as archive:
                    for href in self.context:
                        file = self.context[href]
                        self.logger.debug("zipping context: " + href + " from " + str(file))
                        archive.write(str(file), href, compress_type=zipfile.ZIP_DEFLATED)

                context_file = open(context_path, 'rb')
                multipart_fields["job-data"] = ('context.zip', context_file, 'application/zip')

                multipart = MultipartEncoder(fields=multipart_fields)

                response = requests.post(url, data=multipart, headers={"Content-Type": multipart.content_type})

            else:  # there's no context documents; do a normal POST
                response = requests.post(url, data=jobRequest_file, headers={"Content-Type": "application/xml"})

            response = str(response.content, 'utf-8')

            self.logger.info(response)

        finally:
            try:
                if jobRequest_file is not None:
                    jobRequest_file.close()
            finally:
                if context_file is not None:
                    context_file.close()

        try:
            job = ElementTree.XML(response.split("?>")[-1])
            self.job_id = job.attrib["id"]
        except Exception as e:
            logging.debug(response)
            raise e
        self.logger.info("returning job id")
        return self.job_id

    def get_status(self):
        url = DaisyPipelineJob.encode_url(self.engine, "/jobs/{}".format(self.job_id), {})
        try:
            response = requests.get(url)
            if not response.ok:
                return self.status  # avoid failing if there's a single failed status request (return previous response instead)
        except Exception:
            return self.status  # avoid failing if there's a single failed status request (return previous response instead)

        response = str(response.content, 'utf-8')
        xml = ElementTree.XML(response.split("?>")[-1])
        self.status = xml.attrib["status"]
        if self.status == "DONE":
            self.status = "SUCCESS"

        return self.status

    def delete_job(self, engine, job_id):
        self.logger.info("Deleting: %s @ %s", job_id, engine["endpoint"])
        url = DaisyPipelineJob.encode_url(engine, f"/jobs/{job_id}", {})
        for attempt in range(3):
            try:
                resp = requests.delete(url, timeout=5)
                if resp.status_code in (200, 204, 404):  # idempotent success
                    self.logger.info("Deleted (or already gone): %s @ %s", job_id, engine["endpoint"])
                    return True
                self.logger.warning("Delete failed (%s): %s @ %s", resp.status_code, job_id, engine["endpoint"])
            except requests.RequestException as e:
                self.logger.warning("Delete attempt %s failed: %s", attempt+1, e)
            time.sleep(0.5 * (2**attempt))  # backoff
        return False

    def get_log(self):
        url = DaisyPipelineJob.encode_url(self.engine, "/jobs/{}/log".format(self.job_id), {})
        response = requests.get(url)
        return str(response.content, 'utf-8')

    """def get_results(self):
        result_obj = tempfile.NamedTemporaryFile(prefix="daisy-pipeline-results-", suffix=".zip")
        result = result_obj.name

        url = DaisyPipelineJob.encode_url(self.engine, "/jobs/{}/result".format(self.job_id), {})

        with requests.get(url, stream=True) as r:
            with open(result, 'wb') as f:
                shutil.copyfileobj(r.raw, f)

        if os.path.isfile(result) and os.path.getsize(result) > 0:
            #Filesystem.unzip(self.pipeline.utils.report, result, self.dir_output)
            if os.path.isdir(result):
                self.logger.info("Results if is a folder")
                #.copy(result, self.dir_output)
            self.logger.info("Results if is a zip file")
            try:
                with zipfile.ZipFile(result, 'r') as zip_ref:
                    zip_ref.extractall(self.dir_output)
                self.logger.error(f"Extracted '{input}' to '{self.dir_output}'")
            except zipfile.BadZipFile:
                self.logger.error(f" Invalid or corrupted ZIP file: {input}")
            except Exception as e:
              self.logger.error(f"Error extracting ZIP: {e}") """
    
    def get_results(self):
        # Temporary file for ZIP results
        result_obj = tempfile.NamedTemporaryFile(prefix="daisy-pipeline-results-", suffix=".zip", delete=False)
        result = result_obj.name
        result_obj.close()

        url = DaisyPipelineJob.encode_url(self.engine, f"/jobs/{self.job_id}/result", {})

        with requests.get(url, stream=True) as r:
            r.raise_for_status()  # ensure HTTP 200
            with open(result, "wb") as f:
                shutil.copyfileobj(r.raw, f)

        if not os.path.exists(result) or os.path.getsize(result) == 0:
            self.logger.error(f"No valid result file downloaded from {url}")
            return

        # Ensure output directory exists
        os.makedirs(self.dir_output, exist_ok=True)

        # --- CASE 1: result is a folder (some pipelines return a directory instead of zip)
        if os.path.isdir(result):
            self.logger.info(f"Result is a directory: copying from '{result}' to '{self.dir_output}'")
            try:
                # remove existing content first
                if os.path.exists(self.dir_output) and os.listdir(self.dir_output):
                    shutil.rmtree(self.dir_output, ignore_errors=True)
                shutil.copytree(result, self.dir_output, dirs_exist_ok=True)
                self.logger.info(f"Copied result directory to '{self.dir_output}'")
            except Exception as e:
                self.logger.error(f"Error copying result folder '{result}': {e}")
            return

        # --- CASE 2: result is a zip file
        self.logger.info("Result is a ZIP file, attempting to extract.")
        try:
            if not zipfile.is_zipfile(result):
                self.logger.error(f"Downloaded file is not a valid ZIP: {result}")
                return
            with zipfile.ZipFile(result, "r") as zip_ref:
                zip_ref.extractall(self.dir_output)
            self.logger.info(f"Extracted '{result}' to '{self.dir_output}'")
        except zipfile.BadZipFile:
            self.logger.error(f"Invalid or corrupted ZIP file: {result}")
        except Exception as e:
            self.logger.error(f"Error extracting ZIP '{result}': {e}")
        finally:
            # clean up temp file
            try:
                os.remove(result)
            except Exception:
                self.logger.warning(f"Could not remove temporary file: {result}")
    

    @staticmethod
    def is_alive(engine):
        url = DaisyPipelineJob.encode_url(engine, "/alive", {})
        try:
            response = requests.get(url)
            return response.ok
        except Exception:
            return False

    def get_queue_size(self, engine):
        url = DaisyPipelineJob.encode_url(engine, "/jobs", {})
        try:
            response = requests.get(url)
            if not response.ok:
                return 10  # assume many jobs instead of failing
        except Exception:
            return 10  # assume many jobs instead of failing

        response = str(response.content, 'utf-8')
        xml = ElementTree.XML(response.split("?>")[-1])

        queue_size = 0
        jobs = xml.xpath("/d:jobs/d:job", namespaces=DaisyPipelineJob.dp2_ws_namespace)
        job_ids = []
        for job in jobs:
            job_ids.append(job.attrib.get("id"))

            # possible Pipeline 2 job statuses: IDLE, RUNNING, SUCCESS, ERROR, FAIL
            if job.attrib.get("status") in ["IDLE", "RUNNING"]:
                queue_size += 1

        self.delete_old_jobs(engine, job_ids)

        return queue_size

    def delete_old_jobs(self, engine, job_ids):
        # initialize engine_jobs if necessary
        if DaisyPipelineJob.engine_jobs is None:
            DaisyPipelineJob.engine_jobs = {}
        if engine["endpoint"] not in DaisyPipelineJob.engine_jobs:
            DaisyPipelineJob.engine_jobs[engine["endpoint"]] = {}

        # add newly found jobs
        for job_id in job_ids:
            if job_id not in DaisyPipelineJob.engine_jobs[engine["endpoint"]]:
                DaisyPipelineJob.engine_jobs[engine["endpoint"]][job_id] = time.time()

        # remove jobs that are no longer present in the engine
        for job_id in list(DaisyPipelineJob.engine_jobs[engine["endpoint"]].keys()):
            if job_id not in job_ids:
                del DaisyPipelineJob.engine_jobs[engine["endpoint"]][job_id]

        # delete old jobs
        for job_id in DaisyPipelineJob.engine_jobs[engine["endpoint"]]:
            age = time.time() - DaisyPipelineJob.engine_jobs[engine["endpoint"]][job_id]
            if age > 3600*3:
                self.delete_job(engine, job_id)

    @staticmethod
    def encode_url(engine, endpoint, parameters):
        if engine["authentication"] == "true":
            iso8601 = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            nonce = str(random.randint(10**29, 10**30-1))  # 30 digits

            parameters["authid"] = engine["key"]
            parameters["time"] = iso8601
            parameters["nonce"] = nonce

        url = engine["endpoint"] + endpoint
        if parameters:
            url += "?" + urllib.parse.urlencode(parameters)

        if engine["authentication"] == "true":
            # Use RFC 2104 HMAC for keyed hashing of the URL
            hash = hmac.new(engine["secret"].encode('utf-8'),
                            url.encode('utf-8'),
                            digestmod=hashlib.sha1)

            # Use base 64 encoding
            hash = base64.b64encode(hash.digest()).decode('utf-8')

            # Base64 encoding uses + which we have to encode in URL parameters.
            hash = hash.replace("+", "%2B")

            # Append hash as parameter to the end of the URL
            url += "&sign=" + hash

        return url
