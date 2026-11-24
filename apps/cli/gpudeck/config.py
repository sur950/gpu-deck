"""Profiles and settings for the gpudeck CLI.

Config file: ``$GPUDECK_CONFIG_DIR/config.toml`` (default ``~/.config/gpudeck``)::

    current_profile = "default"

    [profiles.default]
    endpoint = "http://localhost:8180"
    auth_mode = "bearer"          # bearer | cookie
    verify_tls = true
    ca_bundle = ""                # optional PEM path
    use_keyring = true

Environment overrides (highest precedence): ``GPUDECK_ENDPOINT``, ``GPUDECK_TOKEN``,
``GPUDECK_PROFILE``, ``GPUDECK_INSECURE`` (``1`` disables TLS verification), ``GPUDECK_CA_BUNDLE``,
``GPUDECK_NO_KEYRING``.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import tomli_w

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "http://localhost:8180"
API_PREFIX = "/api/v1"


def config_dir() -> Path:
    override = os.environ.get("GPUDECK_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "gpudeck"


def config_path() -> Path:
    return config_dir() / "config.toml"


@dataclass
class Profile:
    name: str = "default"
    endpoint: str = DEFAULT_ENDPOINT
    auth_mode: str = "bearer"
    verify_tls: bool = True
    ca_bundle: str = ""
    use_keyring: bool = True
    timeout_seconds: float = 30.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        """Admin API base (endpoint + /api/v1), tolerant of a trailing slash or prefix."""
        ep = self.endpoint.rstrip("/")
        if ep.endswith(API_PREFIX):
            return ep
        return ep + API_PREFIX

    def to_toml(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("name")
        extra = d.pop("extra") or {}
        d.update(extra)
        return d


@dataclass
class Config:
    current_profile: str = "default"
    profiles: dict[str, Profile] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or config_path()
        cfg = cls()
        if path.exists():
            try:
                raw = tomllib.loads(path.read_text())
            except tomllib.TOMLDecodeError as exc:  # pragma: no cover - defensive
                logger.warning("ignoring unreadable config %s: %s", path, exc)
                raw = {}
            cfg.current_profile = str(raw.get("current_profile", "default"))
            for name, values in (raw.get("profiles") or {}).items():
                cfg.profiles[name] = _profile_from_dict(name, values)
        return cfg

    def save(self, path: Path | None = None) -> None:
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        doc = {
            "current_profile": self.current_profile,
            "profiles": {n: p.to_toml() for n, p in self.profiles.items()},
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(tomli_w.dumps(doc))
        os.chmod(tmp, 0o600)
        tmp.replace(path)

    def get(self, name: str | None = None) -> Profile:
        name = name or os.environ.get("GPUDECK_PROFILE") or self.current_profile
        stored = self.profiles.get(name)
        # copy so env/flag overrides never leak into config.toml on the next save()
        prof = replace(stored, extra=dict(stored.extra)) if stored else Profile(name=name)
        return apply_env(prof)

    def upsert(self, profile: Profile, make_current: bool = True) -> None:
        self.profiles[profile.name] = profile
        if make_current:
            self.current_profile = profile.name


def _profile_from_dict(name: str, values: dict[str, Any]) -> Profile:
    known = {f for f in Profile.__dataclass_fields__ if f not in ("name", "extra")}
    kwargs = {k: v for k, v in values.items() if k in known}
    extra = {k: v for k, v in values.items() if k not in known}
    return Profile(name=name, extra=extra, **kwargs)


def apply_env(profile: Profile) -> Profile:
    """Return a copy with environment overrides applied."""
    ep = os.environ.get("GPUDECK_ENDPOINT")
    if ep:
        profile.endpoint = ep
    if os.environ.get("GPUDECK_INSECURE", "").lower() in ("1", "true", "yes"):
        profile.verify_tls = False
    ca = os.environ.get("GPUDECK_CA_BUNDLE")
    if ca:
        profile.ca_bundle = ca
    if os.environ.get("GPUDECK_NO_KEYRING", "").lower() in ("1", "true", "yes"):
        profile.use_keyring = False
    return profile
