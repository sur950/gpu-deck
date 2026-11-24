"""
Base class and shared helpers for per-domain settings classes.

Do not put fields here. Add a ``<domain>.py`` next to this file instead (see
``app/core/settings/__init__.py``).
"""

from __future__ import annotations

import os
import types
import typing
from pathlib import Path
from typing import Any, Dict

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/fastapi/.env — resolved from this file so tests and scripts run from any cwd.
APP_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = Path(os.environ.get("GPU_DECK_ENV_FILE", APP_ROOT / ".env"))


def _is_nullable(annotation: Any) -> bool:
    """True for Optional[X] / X | None annotations."""
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        return type(None) in typing.get_args(annotation)
    return annotation is type(None)


class DomainSettings(BaseSettings):
    """Marker base for one domain's settings. Subclasses are merged into Settings."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="before")
    @classmethod
    def _empty_string_is_none(cls, data: Any) -> Any:
        """
        ``.env.example`` leaves optional values blank (``TLS_CERT_FILE=``).
        An empty string is "unset" for every nullable field, so a blank
        ``SESSION_COOKIE_SECURE=`` does not fail bool parsing and a blank
        ``SESSION_COOKIE_DOMAIN=`` does not become a cookie domain of "".
        """
        if not isinstance(data, dict):
            return data
        cleaned: Dict[str, Any] = dict(data)
        for key, value in data.items():
            if value != "":
                continue
            field = cls.model_fields.get(key) or cls.model_fields.get(str(key).upper())
            if field is not None and _is_nullable(field.annotation):
                cleaned[key] = None
        return cleaned


def strip_quotes(value: str) -> str:
    """Strip surrounding single or double quotes from an env value."""
    if value and isinstance(value, str):
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            return value[1:-1]
    return value
