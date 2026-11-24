"""
OpenSSH public key parsing for SSH GPU instances: validates ``authorized_keys``
lines and computes the ``SHA256:<base64>`` fingerprints shown in the UI.
Options prefixes (``from=...``, ``command=...``) are rejected — the control
plane hands keys to the agent verbatim and must not let a user smuggle in
forced commands for other users.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import struct
from typing import Any, Dict, List

from app.core.exceptions import ValidationError
from app.core.logger import get_logger

logger = get_logger(__name__)

KEY_TYPES = {
    "ssh-ed25519",
    "ssh-rsa",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
}
MAX_KEYS = 64


def fingerprint(blob: bytes) -> str:
    """OpenSSH ``SHA256:`` fingerprint (unpadded base64) of a key blob."""
    digest = hashlib.sha256(blob).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def parse_key(line: str) -> Dict[str, Any]:
    """One ``<type> <base64> [comment]`` line -> {key, type, fingerprint, comment}."""
    text = (line or "").strip()
    if not text or "\n" in text or "\r" in text:
        raise ValidationError("each authorized key must be a single non-empty line")
    parts = text.split(None, 2)
    if len(parts) < 2 or parts[0] not in KEY_TYPES:
        raise ValidationError(
            "authorized key must start with a supported key type "
            f"({', '.join(sorted(KEY_TYPES))}); options are not allowed"
        )
    key_type, b64 = parts[0], parts[1]
    try:
        blob = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError(f"authorized key is not valid base64: {exc}") from exc
    if len(blob) < 4:
        raise ValidationError("authorized key blob is too short")
    (name_len,) = struct.unpack(">I", blob[:4])
    if blob[4 : 4 + name_len].decode(errors="replace") != key_type:
        raise ValidationError("authorized key type does not match its blob")
    comment = parts[2].strip() if len(parts) > 2 else None
    return {
        "key": f"{key_type} {b64}" + (f" {comment}" if comment else ""),
        "type": key_type,
        "fingerprint": fingerprint(blob),
        "comment": comment,
    }


def parse_keys(lines: List[str]) -> List[Dict[str, Any]]:
    """Validate, de-duplicate by fingerprint, keep order."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for raw in lines or []:
        for line in str(raw).splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key = parse_key(line)
            if key["fingerprint"] in seen:
                continue
            seen.add(key["fingerprint"])
            out.append(key)
    if not out:
        raise ValidationError("at least one authorized SSH public key is required")
    if len(out) > MAX_KEYS:
        raise ValidationError(f"at most {MAX_KEYS} authorized keys")
    return out
