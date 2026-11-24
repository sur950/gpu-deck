"""
Security headers and small security helpers.

Password hashing lives in app/utils/password.py, sessions and CSRF in
app/services/auth and app/middlewares. This module only holds what
every process needs: the response headers.
"""

import ipaddress
from functools import lru_cache
from typing import Dict, Iterable, Optional, Tuple

from app.core.logger import get_logger

logger = get_logger(__name__)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    ),
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": (
        "geolocation=(), microphone=(), camera=(), payment=(), usb=(), magnetometer=()"
    ),
}


def security_headers_kind(path: str) -> str:
    """Which header set a path gets: ``branding`` | ``docs`` | ``default``."""
    if path.endswith(("/branding/logo", "/branding/favicon", "/branding/logo-dark")):
        return "branding"
    if path in ("/docs", "/redoc") or path.endswith("/openapi.json"):
        return "docs"
    return "default"


@lru_cache(maxsize=None)
def encoded_security_headers(kind: str) -> Tuple[Tuple[bytes, bytes], ...]:
    """ASGI-ready (lower-cased name, value) pairs for a header kind (cached)."""
    sample = {"branding": "/branding/logo", "docs": "/docs"}.get(kind, "/")
    return tuple(
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in get_security_headers(sample).items()
    )


def get_security_headers(path: str) -> Dict[str, str]:
    """
    Security headers for a response. Documentation endpoints get a relaxed CSP
    so Swagger/ReDoc assets load from their CDNs; branding assets are sandboxed
    because an SVG opened directly would otherwise run under the API origin.
    """
    headers = SECURITY_HEADERS.copy()

    if path.endswith(("/branding/logo", "/branding/favicon", "/branding/logo-dark")):
        headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; sandbox"
        )
        return headers

    if path in ["/docs", "/redoc"] or path.endswith("/openapi.json"):
        headers["X-Frame-Options"] = "SAMEORIGIN"
        headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
            "https://cdn.jsdelivr.net https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com "
            "https://fonts.googleapis.com; "
            "img-src 'self' data: https: blob:; "
            "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net; "
            "connect-src 'self' https:; "
            "frame-ancestors 'self';"
        )

    return headers


def ip_matches(client_ip: Optional[str], entries: Iterable[str]) -> bool:
    """True if client_ip equals an entry or falls inside a CIDR entry."""
    if not client_ip:
        return False
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for entry in entries:
        try:
            if "/" in entry:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            elif addr == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False


__all__ = ["SECURITY_HEADERS", "get_security_headers", "ip_matches"]
