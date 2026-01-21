from datetime import datetime
from logging import getLogger, basicConfig, DEBUG, INFO, WARNING, FileHandler, Formatter, Filter
from pathlib import Path

from config import LOG_LEVEL, LOG_PATH


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

# Prefixes for project modules (DEBUG level in file)
PROJECT_PREFIXES = ("lib.", "__main__", "app")


class _ThirdPartyLogFilter(Filter):
    """Filter that only passes records from 3rd party modules at INFO+."""
    def filter(self, record):
        is_project = record.name.startswith(PROJECT_PREFIXES)
        if is_project:
            return True  # project code: pass all levels
        return record.levelno >= INFO  # 3rd party: INFO and above only


_LOG_FORMAT = "%(asctime)s %(levelname)s:%(name)s:%(funcName)s(): %(message)s"


def setup_logging() -> Path:
    """
    Configure logging for the application.

    Console: DEV mode = DEBUG (INFO for 3rd party), PROD mode = WARNING (INFO for app)
    File: Always DEBUG for project code, INFO for 3rd party.

    Returns the path to the log file.
    """
    # Development: verbose logging for this app, except 3rd party libs
    basicConfig(level=DEBUG, format=_LOG_FORMAT)
    getLogger("websockets.client").setLevel(INFO)
    getLogger("httpcore").setLevel(INFO)
    getLogger("urllib3").setLevel(INFO)
    getLogger("google").setLevel(INFO)

    # File handler: DEBUG for project code, INFO for 3rd party
    log_filename = LOG_PATH / f"app_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    file_handler = FileHandler(log_filename, encoding="utf-8")
    file_handler.setLevel(DEBUG)
    file_handler.setFormatter(Formatter(_LOG_FORMAT))
    file_handler.addFilter(_ThirdPartyLogFilter())
    getLogger().addHandler(file_handler)

    getLogger(__name__).info("Logging to file: %s", log_filename)
    return log_filename
