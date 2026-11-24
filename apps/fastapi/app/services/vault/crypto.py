"""
Vault encryption helpers (MultiFernet).

VAULT_KEYS is a comma-separated list of Fernet keys, newest first: the first
key encrypts, every key decrypts. Rotation = prepend a new key, restart, run
``VaultService.rotate_keys()`` (re-encrypts rows still on an old key), then
drop the old key from .env.

Plaintext never leaves the process: callers get ciphertext for storage and
decrypt only inside authorised code paths. ``mask_value`` is the only form of
a secret that may appear in responses, logs or audit entries.
"""

from typing import List, Optional, Tuple

from app.core.logger import get_logger
from app.core.settings import settings
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

logger = get_logger(__name__)

_cached_keys: Optional[Tuple[str, ...]] = None
_cached_fernets: List[Fernet] = []
_cached_multi: Optional[MultiFernet] = None


def _fernets() -> List[Fernet]:
    global _cached_keys, _cached_fernets, _cached_multi
    keys = tuple(settings.get_vault_keys())
    if keys != _cached_keys:
        if not keys:
            raise RuntimeError("VAULT_KEYS is empty")
        _cached_fernets = [Fernet(k.encode()) for k in keys]
        _cached_multi = MultiFernet(_cached_fernets)
        _cached_keys = keys
    return _cached_fernets


def get_multi_fernet() -> MultiFernet:
    _fernets()
    assert _cached_multi is not None
    return _cached_multi


def encrypt_value(plaintext: str) -> str:
    """Ciphertext (token string) under the primary key (key_version 1)."""
    return get_multi_fernet().encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    """Plaintext for ciphertext made with any configured key; raises InvalidToken otherwise."""
    return get_multi_fernet().decrypt(ciphertext.encode()).decode()


def key_version_of(ciphertext: str) -> int:
    """1-based index of the VAULT_KEYS entry that can decrypt ``ciphertext`` (0 = none)."""
    for index, fernet in enumerate(_fernets(), start=1):
        try:
            fernet.decrypt(ciphertext.encode())
            return index
        except InvalidToken:
            continue
    return 0


def needs_rotation(ciphertext: str) -> bool:
    """True when the ciphertext was not produced by the current primary key."""
    return key_version_of(ciphertext) != 1


def rotate_ciphertext(ciphertext: str) -> str:
    """Re-encrypt under the primary key (MultiFernet.rotate)."""
    return get_multi_fernet().rotate(ciphertext.encode()).decode()


def mask_value(plaintext: str) -> str:
    """ "sk-abc123def456" -> "sk-abc...f456"; short values -> "****"."""
    if plaintext is None:
        return "****"
    if len(plaintext) <= 4:
        return "****"
    prefix = plaintext[:6] if len(plaintext) > 10 else plaintext[:3]
    return f"{prefix}...{plaintext[-4:]}"


__all__ = [
    "encrypt_value",
    "decrypt_value",
    "mask_value",
    "needs_rotation",
    "rotate_ciphertext",
    "key_version_of",
    "get_multi_fernet",
    "InvalidToken",
]
