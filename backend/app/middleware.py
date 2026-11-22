"""Pure-ASGI middleware for the backend (Phase 8.1).

Both run *inside* ``CORSMiddleware`` (``main.create_app`` adds them first), so their
responses carry CORS headers and the browser can read the error instead of reporting a
network failure.
"""

from __future__ import annotations

import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings
from app.services.document_service import too_large_message

logger = logging.getLogger(__name__)

UPLOAD_PATH = "/api/admin/documents"
# A multipart body is the file plus boundaries and part headers: a few hundred bytes, so
# 1 MB of slack never rejects a file that is within the limit itself.
MULTIPART_SLACK_BYTES = 1024 * 1024
SERVER_ERROR = "Something went wrong on the server. Please try again."


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


class UploadSizeLimit:
    """Reject an oversized upload from its ``Content-Length`` before the body is read.

    Without this, Starlette spools the whole multipart body to a temporary file before the
    route's own streaming check runs. That check stays the authority (a chunked upload has
    no ``Content-Length``); this only saves receiving a file that is plainly too big.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and scope["path"].rstrip("/") == UPLOAD_PATH
        ):
            length = _content_length(scope)
            max_bytes = get_settings().max_upload_bytes
            if length is not None and length > max_bytes + MULTIPART_SLACK_BYTES:
                response = JSONResponse({"detail": too_large_message(max_bytes)}, status_code=413)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class CatchAllErrors:
    """Turn an unhandled exception into a JSON 500 that CORS can still wrap.

    Starlette's own ``ServerErrorMiddleware`` sits outside ``CORSMiddleware``, so its plain
    text 500 reaches the browser without CORS headers and looks like "could not reach the
    server". The traceback is logged here; the client gets a plain sentence.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def send_tracking(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_tracking)
        except Exception:
            logger.exception("unhandled error on %s %s", scope["method"], scope["path"])
            if started:  # too late for a clean response; let the server close it
                raise
            response = JSONResponse({"detail": SERVER_ERROR}, status_code=500)
            await response(scope, receive, send)
