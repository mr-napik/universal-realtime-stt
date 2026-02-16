"""
Shared utilities — logging setup.

Configures dual-output logging: console (all levels) and a timestamped file
in log/. Project modules (lib.*) log at DEBUG; third-party libraries are
filtered to INFO+ in the file handler to keep logs readable.
"""
from datetime import datetime
from logging import getLogger, basicConfig, DEBUG, INFO, FileHandler, Formatter, Filter
from pathlib import Path

from config import LOG_PATH

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


# Prefixes for project modules (DEBUG level in file)
PROJECT_PREFIXES = ("lib.", "__main__")
LOG_FORMAT = "%(asctime)s %(levelname)s:%(name)s:%(funcName)s(): %(message)s"


class _ThirdPartyLogFilter(Filter):
    """Filter that only passes records from 3rd party modules at INFO+."""

    def filter(self, record):
        is_project = record.name.startswith(PROJECT_PREFIXES)
        if is_project:
            return True  # project code: pass all levels
        return record.levelno >= INFO  # 3rd party: INFO and above only


def setup_logging(level: int = DEBUG) -> Path:
    """
    Configure logging for the application.

    Returns the path to the log file.
    """
    # Development: verbose logging for this app, except 3rd party libs
    basicConfig(level=level, format=LOG_FORMAT)
    getLogger("websockets.client").setLevel(INFO)
    getLogger("httpcore").setLevel(INFO)
    getLogger("urllib3").setLevel(INFO)
    getLogger("google").setLevel(INFO)

    # File handler
    log_filename = LOG_PATH / f"app_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    file_handler = FileHandler(log_filename, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(Formatter(LOG_FORMAT))
    file_handler.addFilter(_ThirdPartyLogFilter())
    getLogger().addHandler(file_handler)

    getLogger(__name__).info("Logging to file: %s", log_filename)
    return log_filename
