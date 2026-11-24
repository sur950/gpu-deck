"""
CSRF middleware — double-submit cookie + header, HMAC-bound to the session.

Applies to state-changing methods (POST/PUT/PATCH/DELETE) on the admin API
whose credentials come from the session cookie (no explicit ``Authorization``
header). The request must carry ``X-CSRF-Token`` equal to the readable CSRF
cookie, and that value must equal HMAC(SECRET_KEY, sha256(session token)) —
so a token only works together with the session that minted it.

Exempt: login (no session yet), health/metrics/branding (public, read-only
anyway), the agent hub (WebSocket; separate mTLS listener) and the whole
serving API (API keys, no cookies). Installed outside cookie_auth (higher
ORDER) so it sees the raw headers before the cookie becomes a Bearer header.
"""

from http.cookies import SimpleCookie
from typing import Optional

from app.core.logger import get_logger
from app.core.settings import settings
from app.middlewares.cookie_auth import (
    has_bearer_authorization,
    session_cookie_from_scope,
)
from app.services.auth.csrf import verify_csrf_token
from app.services.auth.token_crypto import hash_token
from fastapi import FastAPI
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = get_logger(__name__)

ORDER = 45
PROCESSES = ("admin",)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def exempt_paths() -> tuple:
    prefix = settings.API_V1_PREFIX
    return (
        f"{prefix}/auth/login",
        f"{prefix}/health",
        f"{prefix}/branding",
        f"{prefix}/agent-hub",
        f"{prefix}/pki/ca",
        f"{prefix}/pki/crl",
        "/metrics",
        "/health",
        "/docs",
        "/redoc",
    )


def _csrf_cookie_from_scope(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name == b"cookie":
            try:
                jar: SimpleCookie = SimpleCookie()
                jar.load(value.decode("latin-1"))
            except Exception:  # noqa: BLE001
                return ""
            morsel = jar.get(settings.CSRF_COOKIE_NAME)
            return morsel.value if morsel else ""
    return ""


def _header(scope: Scope, name: str) -> Optional[str]:
    wanted = name.lower().encode("latin-1")
    for key, value in scope.get("headers", []):
        if key == wanted:
            return value.decode("latin-1")
    return None


def _reject(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={
            "success": False,
            "data": None,
            "error": {
                "code": "CSRF_ERROR",
                "message": message,
                "details": None,
                "field": None,
            },
            "meta": None,
        },
    )


class CSRFMiddleware:
    """Pure ASGI double-submit check."""

    def __init__(self, app: ASGIApp):
        self.app = app

    def _is_exempt(self, path: str) -> bool:
        for exempt in exempt_paths():
            if path == exempt or path.startswith(exempt + "/"):
                return True
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not settings.CSRF_ENABLED:
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET").upper()
        if method in SAFE_METHODS or self._is_exempt(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        if has_bearer_authorization(scope):
            # Bearer clients (CLI, Terraform) cannot be CSRF'd: a browser never attaches a
            # Bearer header cross-site. Other schemes (Basic from an auth proxy) are sent
            # automatically by browsers and must not skip the check while a session cookie
            # authenticates the request.
            await self.app(scope, receive, send)
            return
        session_token = session_cookie_from_scope(scope)
        if not session_token:
            await self.app(scope, receive, send)  # no session -> auth will 401
            return

        header_value = _header(scope, settings.CSRF_HEADER_NAME) or ""
        cookie_value = _csrf_cookie_from_scope(scope)
        if not header_value or not cookie_value:
            await _reject("CSRF token missing")(scope, receive, send)
            return
        if header_value.strip() != cookie_value.strip():
            await _reject("CSRF token mismatch")(scope, receive, send)
            return
        if not verify_csrf_token(hash_token(session_token), header_value):
            await _reject("CSRF token invalid for this session")(scope, receive, send)
            return
        await self.app(scope, receive, send)


def install(app: FastAPI, process: str) -> None:
    app.add_middleware(CSRFMiddleware)
