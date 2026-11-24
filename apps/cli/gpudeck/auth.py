"""Session credential storage for the gpudeck CLI.

The login session token (the value of the ``gpudeck_session`` cookie, also accepted as
``Authorization: Bearer``) is stored per profile in the OS keychain through ``keyring``. When no
usable keychain backend exists (headless Linux, CI) or the profile sets ``use_keyring = false`` the
credentials go to ``<config dir>/credentials.json`` with mode 0600. ``GPUDECK_TOKEN`` in the
environment always wins and is never written anywhere.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import config_dir

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "gpudeck-cli"


@dataclass
class Credentials:
    token: str
    cookie_name: str = "gpudeck_session"
    csrf_token: str | None = None
    csrf_cookie_name: str = "gpudeck_csrf"
    csrf_header_name: str = "X-CSRF-Token"
    username: str | None = None
    expires_at: str | None = None
    session_id: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "Credentials":
        data = json.loads(raw)
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


def _file_path() -> Path:
    return config_dir() / "credentials.json"


def _read_file() -> dict[str, Any]:
    path = _file_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text() or "{}")
    except json.JSONDecodeError:
        logger.warning("credentials file %s is corrupt; ignoring", path)
        return {}


def _write_file(data: dict[str, Any]) -> None:
    path = _file_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    os.chmod(path, 0o600)


def _keyring():  # pragma: no cover - thin import shim
    try:
        import keyring
        from keyring.errors import NoKeyringError

        backend = keyring.get_keyring()
        # keyring's "fail" backend raises on every call; detect it up front
        if backend.__class__.__module__.startswith("keyring.backends.fail"):
            return None
        return keyring, NoKeyringError
    except Exception:  # noqa: BLE001
        return None


class CredentialStore:
    """Load/save/delete credentials for a profile; keyring first, 0600 file second."""

    def __init__(self, use_keyring: bool = True) -> None:
        self.use_keyring = use_keyring

    def load(self, profile: str) -> Credentials | None:
        env_token = os.environ.get("GPUDECK_TOKEN")
        if env_token:
            return Credentials(token=env_token)
        if self.use_keyring:
            kr = _keyring()
            if kr is not None:
                keyring, no_keyring = kr
                try:
                    raw = keyring.get_password(KEYRING_SERVICE, profile)
                    if raw:
                        return Credentials.from_json(raw)
                except no_keyring:
                    pass
                except Exception as exc:  # noqa: BLE001 - locked keychain etc.
                    logger.debug("keyring read failed: %s", exc)
        raw = _read_file().get(profile)
        return Credentials.from_json(json.dumps(raw)) if raw else None

    def save(self, profile: str, creds: Credentials) -> str:
        """Persist and return where it went (``keyring`` or ``file``)."""
        if self.use_keyring:
            kr = _keyring()
            if kr is not None:
                keyring, no_keyring = kr
                try:
                    keyring.set_password(KEYRING_SERVICE, profile, creds.to_json())
                    return "keyring"
                except no_keyring:
                    pass
                except Exception as exc:  # noqa: BLE001
                    logger.debug("keyring write failed, using file: %s", exc)
        data = _read_file()
        data[profile] = asdict(creds)
        _write_file(data)
        return "file"

    def delete(self, profile: str) -> None:
        if self.use_keyring:
            kr = _keyring()
            if kr is not None:
                keyring, _ = kr
                try:
                    keyring.delete_password(KEYRING_SERVICE, profile)
                except Exception:  # noqa: BLE001 - not stored there
                    pass
        data = _read_file()
        if profile in data:
            del data[profile]
            _write_file(data)
