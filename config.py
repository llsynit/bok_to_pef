"""
Container configuration.

Sets up logging and provides the ListHandler for capturing log records
to return to the controller, and a fake_report for testing report handling.
"""

# Built-in
import logging
import sys
import os
import ast

def setup_logger():
    """
    Configures and returns the module-level logger.

    Outputs INFO-level logs to stdout in timestamped format.
    """
    log_level_env_key = os.getenv("MODULE_NAME", "unknown").upper() + "_LOG_LEVEL" # Pulls log level from .env
    level = int(os.getenv(log_level_env_key, logging.INFO))
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    logging.getLogger("python_multipart.multipart").setLevel(logging.WARNING) # Supresses DEBUG prints from python-multipart
    return logging.getLogger(__name__)

logger = setup_logger()

class LogCollector(logging.Handler):
    """Captures log records into a list for returning to controller."""
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append({
            "level": record.levelname,
            "message": record.getMessage(),
            "timestamp": record.created,
        })

class Config():
    PIP_PEF_STABLE = ast.literal_eval(os.getenv("PIP_PEF_STABLE", "(('1.14.17-p1', '6.2.0'),)"))
    PIP_PEF_TEST = ast.literal_eval(os.getenv("PIP_PEF_TEST", "(('1.15.4-SNAPSHOT', '9.0.0'), ('1.15.2', '8.2.1'))"))





















# ARCHIVE

# # config.py
# import os
# import socket
# from pathlib import Path
# import logging
# import sys
# import ast
# from dotenv import load_dotenv, find_dotenv
# # loads .env if present; safe in Docker too
# load_dotenv(find_dotenv(), override=False)

# # -----------------------------------------------------------------------------
# # Logger
# # -----------------------------------------------------------------------------

# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s - %(levelname)s - %(message)s",
#     handlers=[logging.StreamHandler(sys.stdout)]
# )
# logger = logging.getLogger(__name__)

# # =============================================================================
# # Config 
# # =============================================================================
# class Config:
#     logger.info("Initializing configuration from environment variables")
#     MODULE_NAME = os.getenv("MODULE_NAME_BOK_TO_PEF", "bok_to_pef")
#     PORT = int(os.getenv("PORT_BOK_TO_PEF", "39013"))

#     print(f"Starting {MODULE_NAME} on port {PORT}.....")

#     # RabbitMQ
#     RABBITMQ_URL = None
#     RABBITMQ_URL_DOCKER = os.getenv("RABBITMQ_URL_DOCKER")
#     RABBITMQ_URL_LOCAL = os.getenv("RABBITMQ_URL_LOCAL")

#     if RABBITMQ_URL_DOCKER:
#         try:
#             # check if Docker hostname is resolvable
#             socket.gethostbyname("rabbitmq")
#             RABBITMQ_URL = RABBITMQ_URL_DOCKER
#             print("Using RABBITMQ_URL_DOCKER")
#         except socket.gaierror:
#             if RABBITMQ_URL_LOCAL:
#                 RABBITMQ_URL = RABBITMQ_URL_LOCAL
#                 print("Docker hostname not found, falling back to RABBITMQ_URL_LOCAL")
#             else:
#                 raise RuntimeError(
#                     "RabbitMQ hostname not resolvable and no local URL set")
#     elif RABBITMQ_URL_LOCAL:
#         RABBITMQ_URL = RABBITMQ_URL_LOCAL
#         print("Using RABBITMQ_URL_LOCAL")
#     else:
#         raise RuntimeError(
#             "Either RABBITMQ_URL_DOCKER or RABBITMQ_URL_LOCAL must be set")

#     print(f"Connecting to RabbitMQ: {RABBITMQ_URL}")

#     WORK_EXCHANGE = os.getenv("WORK_EXCHANGE", "work.ex")            # direct
#     RESULTS_EXCHANGE = os.getenv("RESULTS_EXCHANGE", "results.ex")   # topic
#     WORK_ROUTING_KEY = os.getenv(
#         "WORK_ROUTING_KEY_BOK_TO_PEF", "nordic_to_bok")     # stage name
#     WORK_QUEUE_NAME = os.getenv(
#         "WORK_QUEUE_NAME_BOK_TO_PEF", "nordic_to_bok.q")     # durable queue

#     # Artifacts are EPHEMERAL here — the controller should fetch and persist them.
#     WORKER_BASE_URL = os.getenv(
#         "WORKER_BASE_URL_BOK_TO_PEF", f"http://{MODULE_NAME}:{PORT}")


#     BASE_DIR = Path(__file__).parent
#     ARTIFACTS_ROOT = (BASE_DIR / "artifacts").resolve()
#     ARTIFACTS_ROOT.mkdir(parents=True, exist_ok=True)

#     ARTIFACTS_RETENTION_HOURS = int(
#         os.getenv("ARTIFACTS_RETENTION_HOURS", "24"))  # default 24h
#     ARTIFACTS_CLEAN_INTERVAL_SEC = int(
#         os.getenv("ARTIFACTS_CLEAN_INTERVAL_SEC", "900"))  # default 15 min




#     PIP_PEF_STABLE = ast.literal_eval(os.getenv("PIP_PEF_STABLE", "(('1.14.17-p1', '6.2.0'),)"))
#     PIP_PEF_TEST = ast.literal_eval(os.getenv("PIP_PEF_TEST", "(('1.15.4-SNAPSHOT', '9.0.0'), ('1.15.2', '8.2.1'))"))



#     logger.info (".env Daisy pipeline html_to_pef versions:")
#     logger.info (f"PIP_PEF_STABLE: {PIP_PEF_STABLE}")
#     logger.info (f"PIP_PEF_TEST: {PIP_PEF_TEST}")
#     logger.info("Configuration initialized.")