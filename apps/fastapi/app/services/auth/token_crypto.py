"""
Opaque session tokens.

The client receives a Fernet-encrypted token carrying ``{"u": admin_id,
"s": session_id, "v": 1}``; the database stores only its SHA-256. The Fernet
key is derived from SECRET_KEY (first 32 bytes,
right-padded with "0", base64url). Changing SECRET_KEY invalidates every
session, which is the intended way to force a global logout.
"""

import base64
import hashlib
import json
from typing import Dict, Optional, Tuple

from app.core.logger import get_logger
from app.core.settings import settings
from cryptography.fernet import Fernet, InvalidToken

logger = get_logger(__name__)

TOKEN_VERSION = 1
_fernet: Optional[Fernet] = None
_fernet_secret: Optional[str] = None


def _get_session_fernet() -> Fernet:
    """Fernet keyed from SECRET_KEY (first 32 bytes, right-padded, base64url)."""
    global _fernet, _fernet_secret
    if _fernet is None or _fernet_secret != settings.SECRET_KEY:
        raw = settings.SECRET_KEY.encode()[:32].ljust(32, b"0")
        _fernet = Fernet(base64.urlsafe_b64encode(raw))
        _fernet_secret = settings.SECRET_KEY
    return _fernet


def hash_token(raw_token: str) -> str:
    """SHA-256 hex of the raw cookie token (sessions.token_hash)."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


def generate_session_token(admin_id: str, session_id: str) -> Tuple[str, str]:
    """Returns (raw_token for the client, token_hash for the database)."""
    payload = json.dumps(
        {"u": str(admin_id), "s": str(session_id), "v": TOKEN_VERSION},
        separators=(",", ":"),
    )
    raw_token = _get_session_fernet().encrypt(payload.encode()).decode()
    return raw_token, hash_token(raw_token)


def decrypt_session_token(raw_token: str) -> Optional[Dict[str, str]]:
    """Payload dict for a genuine token, None when tampered/foreign/wrong version."""
    if not raw_token:
        return None
    try:
        payload = json.loads(_get_session_fernet().decrypt(raw_token.encode()).decode())
    except (InvalidToken, ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("v") != TOKEN_VERSION:
        return None
    if not payload.get("u") or not payload.get("s"):
        return None
    return payload
