"""
Core HTTP middlewares every process gets: request id, request context,
security headers, access logging, CORS, TrustedHost (production).

They are ONE pure-ASGI middleware rather than inline
``@app.middleware("http")`` functions (no ``BaseHTTPMiddleware``:
no extra task + memory stream per request, which mattered on the serving hot
path — the serving API process in particular).

Access log policy:

- admin / hub: one INFO line per request (``METHOD path -> status (ms)``).
- serving: per-request lines at DEBUG; an INFO access line is kept for a
  sample of requests (``SERVING_ACCESS_LOG_SAMPLE_RATE``, default 1 %) and
  for every 5xx (WARNING). Usage rows and traces carry the full per-request
  record, so the log does not need to.
"""

from __future__ import annotations

import random
import time
import uuid
from typing import Optional

from app.core.logger import get_logger
from app.core.request_context import set_request_context
from app.core.security import encoded_security_headers, security_headers_kind
from app.core.settings import settings
from app.services.security.client_ip import resolve_client_ip
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = get_logger(__name__)

ORDER = 0
PROCESSES = ("admin", "serving")

_X_REQUEST_ID = b"x-request-id"
_QUIET_PATHS = frozenset({"/metrics", "/health", "/health/live"})


def _header(scope: Scope, name: bytes) -> Optional[str]:
    for key, value in scope.get("headers") or ():
        if key == name:
            return value.decode("latin-1")
    return None


def _inbound_request_id(value: Optional[str]) -> str:
    """Echo an inbound X-Request-ID only when it is a UUID (serving.md §8; the
    ``request_id`` columns are uuid), otherwise mint a new one."""
    if value and len(value) <= 64:
        try:
            return str(uuid.UUID(value.strip()))
        except ValueError:
            pass
    return str(uuid.uuid4())


class CoreHTTPMiddleware:
    """Request id + context + security headers + access log, in one ASGI layer."""

    def __init__(self, app: ASGIApp, process: str = "admin"):
        self.app = app
        self.process = process
        self.serving = process == "serving"
        self.quiet = _QUIET_PATHS | {
            f"{settings.API_V1_PREFIX}/health",
            f"{settings.API_V1_PREFIX}/health/live",
        }

    def _sampled(self) -> bool:
        rate = float(getattr(settings, "SERVING_ACCESS_LOG_SAMPLE_RATE", 0.0) or 0.0)
        return rate >= 1.0 or (rate > 0.0 and random.random() < rate)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        request_id = _inbound_request_id(_header(scope, _X_REQUEST_ID))
        ip = resolve_client_ip(scope) or None
        ua = _header(scope, b"user-agent") or None
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        state["client_ip"] = ip
        state["user_agent"] = ua
        set_request_context(ip, ua, request_id)

        path = scope.get("path", "")
        extra_headers = encoded_security_headers(security_headers_kind(path))
        request_id_header = (b"x-request-id", request_id.encode("latin-1"))
        status_holder = [500]

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder[0] = message["status"]
                headers = list(message.get("headers") or ())
                present = {name.lower() for name, _ in headers}
                # X-Request-ID always reflects this request (overrides any app value).
                headers = [(n, v) for n, v in headers if n.lower() != _X_REQUEST_ID]
                headers.append(request_id_header)
                headers.extend(h for h in extra_headers if h[0] not in present)
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if path not in self.quiet:
                self._log(scope.get("method", ""), path, status_holder[0], start)

    def _log(self, method: str, path: str, status: int, start: float) -> None:
        elapsed_ms = (time.perf_counter() - start) * 1000
        line = f"{method} {path} -> {status} ({elapsed_ms:.1f} ms)"
        if not self.serving:
            logger.info(line)
        elif status >= 500:
            logger.warning(line)
        elif self._sampled():
            logger.info(f"[sampled] {line}")
        else:
            logger.debug(line)


def install(app: FastAPI, process: str) -> None:
    app.add_middleware(CoreHTTPMiddleware, process=process)

    if process == "admin":
        # Serving traffic comes from an AI gateway or SDK (server to server); only the
        # admin API serves browsers and needs CORS.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.get_cors_origins(),
            allow_credentials=settings.CORS_CREDENTIALS,
            allow_methods=settings.cors_methods_list(),
            allow_headers=settings.cors_headers_list(),
            expose_headers=["X-Request-ID"],
        )

    if settings.APP_ENV == "production" and process in ("admin", "serving"):
        # The hub is reached by agents under AGENT_HUB_PUBLIC_URL and authenticates with mTLS.
        app.add_middleware(HostCheckMiddleware, allowed_hosts=settings.get_trusted_hosts())


# Probes and scrapes arrive with whatever Host the platform uses (pod IP,
# service name, 127.0.0.1): never host-checked.
HOST_CHECK_EXEMPT_PREFIXES = (
    "/health",
    "/api/v1/health",
    "/metrics",
    "/api/v1/metrics/prometheus",
)


class HostCheckMiddleware:
    """TrustedHostMiddleware for everything except health checks and Prometheus scrapes."""

    def __init__(self, app, allowed_hosts):
        self.app = app
        self.checked = TrustedHostMiddleware(app, allowed_hosts=allowed_hosts)

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and scope.get("path", "").startswith(
            HOST_CHECK_EXEMPT_PREFIXES
        ):
            await self.app(scope, receive, send)
            return
        await self.checked(scope, receive, send)
