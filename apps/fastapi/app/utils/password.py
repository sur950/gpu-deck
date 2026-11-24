"""
Password hashing utilities using Argon2id.

A small ``PasswordHasher`` interface (hash / verify / is_usable /
needs_update). Parameters come from
ARGON2_TIME_COST / ARGON2_MEMORY_KIB / ARGON2_PARALLELISM (libs/contracts/env.md);
the OWASP defaults apply when settings are not importable (one-off scripts).
"""

from typing import Optional

from argon2 import PasswordHasher as _Argon2Hasher
from argon2 import Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_DEFAULT_TIME_COST = 3
_DEFAULT_MEMORY_KIB = 64 * 1024
_DEFAULT_PARALLELISM = 2

_hasher: Optional[_Argon2Hasher] = None

# A hash of a random password, verified against when the username is unknown
# so a wrong username costs the same time as a wrong password.
_DUMMY_HASH: Optional[str] = None


def _build_hasher() -> _Argon2Hasher:
    try:
        from app.core.settings import settings

        return _Argon2Hasher(
            time_cost=int(settings.ARGON2_TIME_COST),
            memory_cost=int(settings.ARGON2_MEMORY_KIB),
            parallelism=int(settings.ARGON2_PARALLELISM),
            type=Type.ID,
        )
    except Exception:  # noqa: BLE001 - settings unavailable (scripts) -> OWASP defaults
        return _Argon2Hasher(
            time_cost=_DEFAULT_TIME_COST,
            memory_cost=_DEFAULT_MEMORY_KIB,
            parallelism=_DEFAULT_PARALLELISM,
            type=Type.ID,
        )


def _get_hasher() -> _Argon2Hasher:
    global _hasher
    if _hasher is None:
        _hasher = _build_hasher()
    return _hasher


class PasswordHasher:
    """Password hashing and verification using Argon2id."""

    @staticmethod
    def hash(password: str) -> str:
        """Hash a password; returns the encoded $argon2id$ string."""
        return _get_hasher().hash(password)

    @staticmethod
    def verify(plain_password: str, hashed_password: Optional[str]) -> bool:
        """
        True if the password matches. Never raises: a missing, corrupt or
        foreign hash simply never matches (raising would turn a wrong login
        into a 500).
        """
        if not hashed_password:
            return False
        try:
            return _get_hasher().verify(hashed_password, plain_password)
        except (VerifyMismatchError, VerificationError, InvalidHashError, ValueError):
            return False

    @staticmethod
    def is_usable(hashed_password: Optional[str]) -> bool:
        """True if the stored value is an Argon2 hash verify() can match."""
        return bool(hashed_password) and hashed_password.startswith("$argon2")

    @staticmethod
    def needs_update(hashed_password: str) -> bool:
        """True when the hash was made with weaker parameters than current ones."""
        try:
            return _get_hasher().check_needs_rehash(hashed_password)
        except (InvalidHashError, ValueError):
            return True

    @staticmethod
    def dummy_verify(plain_password: str) -> None:
        """Burn one verification so unknown-username logins take as long as bad passwords."""
        global _DUMMY_HASH
        if _DUMMY_HASH is None:
            import secrets

            _DUMMY_HASH = _get_hasher().hash(secrets.token_urlsafe(16))
        PasswordHasher.verify(plain_password, _DUMMY_HASH)
