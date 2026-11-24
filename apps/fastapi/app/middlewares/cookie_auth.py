"""
Cookie -> Bearer middleware.

When a request carries the session cookie and no ``Authorization`` header,
the cookie value is appended as ``Authorization: Bearer <token>`` so a single
HTTPBearer dependency serves browsers and API clients alike. The ASGI scope is
also marked ``state.auth_via_cookie = True`` so the CSRF middleware (installed
outside this one) and routes know the credentials came from a cookie.

Admin process only: the serving API authenticates with API keys.
"""

from http.cookies import SimpleCookie

from app.core.logger import get_logger
from app.core.settings import settings
from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

logger = get_logger(__name__)

ORDER = 40
PROCESSES = ("admin",)


def session_cookie_from_scope(scope: Scope) -> str:
    """Value of the session cookie in a raw ASGI scope ('' when absent)."""
    for name, value in scope.get("headers", []):
        if name == b"cookie":
            try:
                jar: SimpleCookie = SimpleCookie()
                jar.load(value.decode("latin-1"))
            except Exception:  # noqa: BLE001 - malformed cookie header
                return ""
            morsel = jar.get(settings.SESSION_COOKIE_NAME)
            return morsel.value if morsel else ""
    return ""


def has_authorization_header(scope: Scope) -> bool:
    return any(name == b"authorization" for name, _ in scope.get("headers", []))


def has_bearer_authorization(scope: Scope) -> bool:
    """True for ``Authorization: Bearer <token>`` (CLI / Terraform), not Basic or other schemes.

    Browsers attach ``Authorization: Basic`` on their own (cached credentials of an auth proxy in
    front of the admin UI), so only an explicit bearer token proves a non-browser client.
    """
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            return scheme.lower() == "bearer" and bool(token.strip())
    return False


class CookieAuthMiddleware:
    """Pure ASGI: no response buffering, streaming responses untouched."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not has_authorization_header(scope):
            token = session_cookie_from_scope(scope)
            if token:
                headers = list(scope.get("headers", []))
                headers.append((b"authorization", f"Bearer {token}".encode("latin-1")))
                scope = dict(scope)
                scope["headers"] = headers
                scope.setdefault("state", {})["auth_via_cookie"] = True
        await self.app(scope, receive, send)


def install(app: FastAPI, process: str) -> None:
    app.add_middleware(CookieAuthMiddleware)
