"""Request logging utility for tracking API calls."""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from requests import PreparedRequest, Response

logger = logging.getLogger(__name__)

# Log file location
REQUESTS_LOG_FILE = ".better-confluence-mcp/requests.log"


def is_request_logging_enabled() -> bool:
    """Check if request logging is enabled via env var.

    Enable with: CONFLUENCE_LOG_REQUESTS=true
    """
    return os.environ.get("CONFLUENCE_LOG_REQUESTS", "").lower() in ("true", "1", "yes")


def _ensure_log_dir() -> Path:
    """Ensure the log directory exists and return the log file path."""
    log_path = Path(REQUESTS_LOG_FILE)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return log_path


def log_request(response: Response, *args, **kwargs) -> None:
    """Log a request to the requests log file.

    This is a requests response hook that logs the URL and timestamp
    of each API call.

    Args:
        response: The response object from requests
        *args: Additional positional arguments (ignored)
        **kwargs: Additional keyword arguments (ignored)
    """
    try:
        request: PreparedRequest = response.request
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        method = request.method or "GET"
        url = request.url or ""
        status = response.status_code
        elapsed_ms = int(response.elapsed.total_seconds() * 1000)

        log_line = f"{timestamp} | {method:6} | {status} | {elapsed_ms:5}ms | {url}\n"

        log_path = _ensure_log_dir()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_line)

    except Exception as e:
        # Don't let logging errors break the application
        logger.debug(f"Failed to log request: {e}")


def install_request_logging(session) -> None:
    """Install request logging hook on a requests session.

    Only installs the hook if CONFLUENCE_LOG_REQUESTS=true.

    Args:
        session: A requests.Session object to add logging to
    """
    if not is_request_logging_enabled():
        return

    if "response" not in session.hooks:
        session.hooks["response"] = []

    # Avoid duplicate hooks
    if log_request not in session.hooks["response"]:
        session.hooks["response"].append(log_request)
        logger.debug("Request logging installed on session")
