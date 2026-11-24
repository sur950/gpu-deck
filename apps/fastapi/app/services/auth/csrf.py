"""
CSRF tokens (double submit, HMAC-bound to the session).

token = HMAC-SHA256(SECRET_KEY, "csrf:" + session token_hash). Nothing is
stored: the middleware recomputes it from the session cookie and compares it
with both the readable CSRF cookie and the X-CSRF-Token header.
"""

import hashlib
import hmac

from app.core.settings import settings


def generate_csrf_token(token_hash: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(), f"csrf:{token_hash}".encode(), hashlib.sha256
    ).hexdigest()


def verify_csrf_token(token_hash: str, presented: str) -> bool:
    if not token_hash or not presented:
        return False
    return hmac.compare_digest(generate_csrf_token(token_hash), presented.strip())
