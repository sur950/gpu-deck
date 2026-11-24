"""
IP allow/block middleware (scope ``admin`` on the admin API, ``api_layer`` on
the serving API). Runs before authentication and CSRF (highest ORDER of the
auth-ish middlewares) and answers 403 IP_BLOCKED in the envelope or the
serving format's error shape.

Liveness and Prometheus scrapes are exempt so probes keep working whatever
the rules say. The client address comes from ``resolve_client_ip``: forwarded
headers count only when the peer is in TRUSTED_PROXIES.
"""

from app.core.exceptions import serving_error_body, serving_format_for_scope
from app.core.logger import get_logger
from app.services.security.client_ip import resolve_client_ip
from app.services.security.ip_rules import check_ip
from fastapi import FastAPI
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = get_logger(__name__)

ORDER = 60
PROCESSES = ("admin", "serving")

EXEMPT_SUFFIXES = ("/health/live", "/metrics")


class IpRulesMiddleware:
    def __init__(self, app: ASGIApp, process: str):
        self.app = app
        self.process = process
        self.scope_name = "api_layer" if process == "serving" else "admin"

    def _blocked(self, scope: Scope, reason: str) -> JSONResponse:
        if self.process == "serving":
            fmt = serving_format_for_scope(scope)
            code = None if fmt == "anthropic" else "ip_blocked"
            return JSONResponse(
                status_code=403,
                content=serving_error_body(fmt, 403, "source IP is not allowed", code),
            )
        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "data": None,
                "error": {
                    "code": "IP_BLOCKED",
                    "message": "Source IP is not allowed",
                    "details": {"reason": reason},
                    "field": None,
                },
                "meta": None,
            },
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        # Exact segments only: "/metrics" and ".../health/live", not any path ending in them.
        if scope["type"] == "http" and (path == "/metrics" or path.endswith(EXEMPT_SUFFIXES[:1])):
            await self.app(scope, receive, send)
            return
        ip = resolve_client_ip(scope)
        try:
            decision = await check_ip(ip, self.scope_name)
        except Exception as exc:  # noqa: BLE001 - never take the API down for a cache error
            logger.error(f"ip_rules check failed (fail open): {exc}")
            await self.app(scope, receive, send)
            return
        if not decision.allowed:
            logger.warning(f"IP blocked: ip={ip} scope={self.scope_name} reason={decision.reason}")
            if scope["type"] == "websocket":
                # Close before accept: the server answers the handshake with 403.
                await send({"type": "websocket.close", "code": 1008, "reason": "ip_blocked"})
                return
            await self._blocked(scope, decision.reason)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def install(app: FastAPI, process: str) -> None:
    app.add_middleware(IpRulesMiddleware, process=process)
