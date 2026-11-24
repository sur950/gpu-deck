"""
Session and CSRF cookies.

Session cookie: HttpOnly, Secure (production or SESSION_COOKIE_SECURE),
SameSite=Strict in production / Lax otherwise, path=/.
CSRF cookie: same flags minus HttpOnly, so the SPA can read it and echo it in
the X-CSRF-Token header (double submit).
"""

from typing import Optional

from app.core.settings import settings
from fastapi import Request, Response


class CookieService:
    """Set / clear / read the auth cookies."""

    @staticmethod
    def _flags() -> dict:
        return {
            "secure": settings.session_cookie_secure,
            "samesite": settings.session_cookie_samesite,
            "path": "/",
            "domain": settings.SESSION_COOKIE_DOMAIN or None,
        }

    @classmethod
    def set_session_cookie(cls, response: Response, token: str, max_age_seconds: int) -> None:
        response.set_cookie(
            key=settings.SESSION_COOKIE_NAME,
            value=token,
            max_age=max_age_seconds,
            httponly=True,
            **cls._flags(),
        )

    @classmethod
    def set_csrf_cookie(cls, response: Response, token: str, max_age_seconds: int) -> None:
        response.set_cookie(
            key=settings.CSRF_COOKIE_NAME,
            value=token,
            max_age=max_age_seconds,
            httponly=False,
            **cls._flags(),
        )

    @classmethod
    def clear_auth_cookies(cls, response: Response) -> None:
        flags = cls._flags()
        for name in (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME):
            response.delete_cookie(
                key=name,
                path="/",
                domain=flags["domain"],
                secure=flags["secure"],
                samesite=flags["samesite"],
            )

    @staticmethod
    def get_session_token(request: Request) -> Optional[str]:
        return request.cookies.get(settings.SESSION_COOKIE_NAME)

    @staticmethod
    def get_token_from_cookie_or_header(request: Request) -> Optional[str]:
        """Bearer header first (API clients), then the cookie (browsers)."""
        auth = request.headers.get("authorization")
        if auth and auth.lower().startswith("bearer "):
            return auth[7:].strip() or None
        return request.cookies.get(settings.SESSION_COOKIE_NAME)
