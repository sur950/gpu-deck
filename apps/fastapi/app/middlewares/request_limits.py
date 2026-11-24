"""
Request body size cap (the API layer caps request size and time).

Rejects early on Content-Length; for chunked bodies the cap is enforced while
reading. Time caps are applied per call site in the serving layer (httpx
timeouts), not here, so streaming responses are never cut by a blanket timeout.
"""

from app.core.exceptions import serving_error_body, serving_format_for_scope
from app.core.settings import settings
from fastapi import FastAPI
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

ORDER = 20
PROCESSES = ("admin", "serving")


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, process: str):
        self.app = app
        self.process = process

    def _limit_for(self, path: str, content_type: str) -> int:
        if self.process == "serving":
            if content_type.startswith("multipart/"):
                return settings.MAX_UPLOAD_BODY_BYTES
            return settings.MAX_REQUEST_BODY_BYTES
        return settings.ADMIN_MAX_REQUEST_BODY_BYTES  # admin API and the hub's join POST

    def _too_large(self, scope: Scope) -> JSONResponse:
        if self.process == "serving":
            fmt = serving_format_for_scope(scope)
            return JSONResponse(
                status_code=413,
                content=serving_error_body(fmt, 413, "request body too large"),
            )
        return JSONResponse(
            status_code=413,
            content={
                "success": False,
                "data": None,
                "error": {
                    "code": "REQUEST_TOO_LARGE",
                    "message": "Request body too large",
                    "details": None,
                    "field": None,
                },
                "meta": None,
            },
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_type = ""
        content_length = None
        for key, value in scope.get("headers") or ():
            if key == b"content-type":
                content_type = value.decode("latin-1")
            elif key == b"content-length":
                content_length = value.decode("latin-1")
        path = scope.get("path", "")
        limit = self._limit_for(path, content_type)

        if content_length and content_length.isdigit() and int(content_length) > limit:
            await self._too_large(scope)(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # Too late to send a response from here cleanly; raise so the
                    # server closes the connection instead of buffering more.
                    raise RuntimeError("request body exceeds configured limit")
            return message

        await self.app(scope, limited_receive, send)


def install(app: FastAPI, process: str) -> None:
    app.add_middleware(RequestBodyLimitMiddleware, process=process)
