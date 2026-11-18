"""Access control: the service is reachable only at URLs that contain its secret token.

    https://<host>/<token>/api/...     the HTTP API (the web app's VITE_API_URL is
                                       https://<host>/<token>)
    https://<host>/<token>/mcp         MCP
    https://<host>/mcp/<token>         MCP again, in the form most MCP servers use

Every other path is an empty ``404``. Never ``401`` and never ``WWW-Authenticate``: Claude
and ChatGPT start an OAuth flow when they see one, and there is no OAuth here. Anything that
holds one of these URLs holds the token, so treat them as secrets.
"""

from __future__ import annotations

import hmac
import logging
import re

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{32,}")


def check_token(token: str) -> str:
    if not TOKEN_PATTERN.fullmatch(token or ""):
        raise ValueError("API_TOKEN must have at least 32 URL-safe characters (A-Z a-z 0-9 _ -)")
    return token


class PathToken:
    """ASGI middleware: strip the token from the path, or answer 404."""

    def __init__(self, app, token: str):
        self.app = app
        self.expected = check_token(token).encode()

    def _matches(self, candidate: str) -> bool:
        return hmac.compare_digest(candidate.encode(), self.expected)

    def _inner_path(self, path: str) -> str | None:
        """The path the app should see, or None when the request has no valid token."""
        if path.startswith("/mcp/") and self._matches(path[len("/mcp/") :].rstrip("/")):
            return "/mcp"
        first, slash, rest = path[1:].partition("/")
        if path.startswith("/") and self._matches(first):
            return slash + rest or "/"
        return None

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http":
            inner = self._inner_path(scope.get("path", ""))
            if inner is not None:
                await self.app({**scope, "path": inner, "raw_path": inner.encode()}, receive, send)
                return
            await send(
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [(b"content-length", b"0")],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})


class HideToken(logging.Filter):
    """Mask the token in log lines: the server's access log prints each request's path."""

    def __init__(self, token: str):
        super().__init__()
        self.token = token

    def _mask(self, value):
        return value.replace(self.token, "***") if isinstance(value, str) else value

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._mask(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self._mask(a) for a in record.args)
        return True
