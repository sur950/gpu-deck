"""
Selective GZip: compress responses except on streaming (SSE) paths, where
buffering would break token-by-token delivery.
"""

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from starlette.types import Receive, Scope, Send

ORDER = 10
PROCESSES = ("admin", "serving")

# Path suffixes that stream (any format prefix). Extend for new streaming formats.
STREAMING_SUFFIXES = (
    "/chat/completions",
    "/completions",
    "/responses",
    "/messages",
    "/api/chat",
    "/api/generate",
    "/agent-hub",
)


class SelectiveGZipMiddleware(GZipMiddleware):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope.get("type") == "websocket" or path.endswith(STREAMING_SUFFIXES):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


def install(app: FastAPI, process: str) -> None:
    app.add_middleware(SelectiveGZipMiddleware, minimum_size=1000)
