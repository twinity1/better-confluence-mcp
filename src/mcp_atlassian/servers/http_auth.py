"""HTTP authentication middleware for streamable-http transport.

Extracts Confluence credentials from HTTP request headers and makes them
available to tool functions via contextvars. Also sets a per-user storage
namespace so each user's synced data is isolated.
"""

import base64
import hashlib
import logging
from contextvars import ContextVar
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger("mcp-atlassian.http_auth")


@dataclass
class RequestAuth:
    """Per-request authentication credentials extracted from HTTP headers."""

    username: str
    api_token: str


# Context variable holding per-request auth (set by middleware, read by tools)
_request_auth: ContextVar[RequestAuth | None] = ContextVar("request_auth", default=None)

# Context variable holding the per-user storage namespace (set by middleware)
_user_storage_namespace: ContextVar[str | None] = ContextVar("user_storage_namespace", default=None)


def get_request_auth() -> RequestAuth | None:
    """Get the authentication credentials for the current request."""
    return _request_auth.get()


def get_user_storage_namespace() -> str | None:
    """Get the per-user storage namespace for the current request.

    Returns None in stdio mode (no namespace = shared root).
    In HTTP mode returns a stable hash-based directory name derived from the username.
    """
    return _user_storage_namespace.get()


def _username_to_namespace(username: str) -> str:
    """Convert a username to a stable, filesystem-safe namespace.

    Uses a short hash prefix + sanitized username for readability.
    Example: 'ales.kutek@designeo.cz' -> 'a1b2c3_ales.kutek'
    """
    digest = hashlib.sha256(username.lower().encode()).hexdigest()[:8]
    # Keep the local part of the email for readability
    safe_name = username.split("@")[0].replace(" ", "_")
    # Remove any filesystem-unsafe chars
    safe_name = "".join(c for c in safe_name if c.isalnum() or c in "._-")
    return f"{digest}_{safe_name}"


class ConfluenceAuthMiddleware(BaseHTTPMiddleware):
    """Middleware that extracts Confluence auth from HTTP headers.

    Supports two header formats:
    1. Authorization: Basic base64(username:token)
    2. X-Confluence-Username + X-Confluence-Token headers
    """

    async def dispatch(self, request: Request, call_next: ...) -> Response:  # type: ignore[override]
        auth = None

        # Try Authorization: Basic header first
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                username, _, token = decoded.partition(":")
                if username and token:
                    auth = RequestAuth(username=username, api_token=token)
            except Exception:
                logger.warning("Failed to decode Basic auth header")

        # Fall back to custom headers
        if auth is None:
            username = request.headers.get("x-confluence-username", "")
            token = request.headers.get("x-confluence-token", "")
            if username and token:
                auth = RequestAuth(username=username, api_token=token)

        if auth is None:
            # Allow health check / discovery endpoints without auth
            if request.url.path in ("/health", "/"):
                return await call_next(request)

            return JSONResponse(
                status_code=401,
                content={
                    "error": "Authentication required. Provide either "
                    "'Authorization: Basic base64(username:token)' header or "
                    "'X-Confluence-Username' + 'X-Confluence-Token' headers."
                },
            )

        auth_token = _request_auth.set(auth)
        ns_token = _user_storage_namespace.set(_username_to_namespace(auth.username))
        try:
            return await call_next(request)
        finally:
            _user_storage_namespace.reset(ns_token)
            _request_auth.reset(auth_token)
