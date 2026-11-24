"""
Wire formats and mount prefixes of the serving API (serving.md §1-§2).

Explicit prefixes always work (``/openai/v1``, ``/anthropic/v1``,
``/ollama/api``); bare compat paths (``/v1/...`` for OpenAI and Anthropic,
``/api/...`` for Ollama) are mounted too when ``SERVING_ENABLE_BARE_PATHS``.

The explicit roots come from ``SERVING_{OPENAI,ANTHROPIC,OLLAMA}_PREFIX``.
env.md defines them as ``/openai`` / ``/anthropic`` / ``/ollama``; the original
skeleton defaulted them to ``/v1`` / ``/anthropic/v1`` / ``/api``. Both forms
are accepted: a trailing ``/v1`` or ``/api`` is stripped and an empty root
falls back to the env.md default, so the mounted paths are the same either way.

``format_for_request`` resolves the format of any serving path (used by the
serving exception handlers and the error renderer), including the bare
``/v1/models`` disambiguation by headers.
"""

from __future__ import annotations

from typing import Mapping, Optional

from app.core.settings import settings

FORMATS = ("openai", "anthropic", "ollama")

_DEFAULT_ROOTS = {"openai": "/openai", "anthropic": "/anthropic", "ollama": "/ollama"}
_VERSION_SEGMENT = {"openai": "/v1", "anthropic": "/v1", "ollama": "/api"}
BARE_PREFIX = {"openai": "/v1", "anthropic": "/v1", "ollama": "/api"}

# Bare /v1 paths owned by Anthropic (everything else under bare /v1 is OpenAI).
_ANTHROPIC_BARE = ("/v1/messages",)
_SHARED_BARE = ("/v1/models",)


def _setting_for(fmt: str) -> str:
    return str(getattr(settings, f"SERVING_{fmt.upper()}_PREFIX", "") or "")


def explicit_root(fmt: str) -> str:
    """``/openai`` | ``/anthropic`` | ``/ollama`` (or the configured equivalent)."""
    raw = _setting_for(fmt).strip().rstrip("/")
    seg = _VERSION_SEGMENT[fmt]
    if raw.endswith(seg):
        raw = raw[: -len(seg)]
    raw = raw.rstrip("/")
    if not raw:
        raw = _DEFAULT_ROOTS[fmt]
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw


def explicit_prefix(fmt: str) -> str:
    """Mount prefix of a format package: ``/openai/v1``, ``/anthropic/v1``, ``/ollama/api``."""
    return explicit_root(fmt) + _VERSION_SEGMENT[fmt]


def bare_prefix(fmt: str) -> str:
    return BARE_PREFIX[fmt]


def _anthropic_by_headers(headers: Optional[Mapping[str, str]]) -> bool:
    if not headers:
        return False
    if headers.get("anthropic-version"):
        return True
    return bool(headers.get("x-api-key")) and not headers.get("authorization")


def format_for_request(path: str, headers: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The serving format of a request path, or None for non-serving paths."""
    for fmt in FORMATS:
        prefix = explicit_prefix(fmt)
        if path == prefix or path.startswith(prefix + "/"):
            return fmt
    if path.startswith(_ANTHROPIC_BARE):
        return "anthropic"
    if path.startswith(_SHARED_BARE):
        return "anthropic" if _anthropic_by_headers(headers) else "openai"
    if path == "/v1" or path.startswith("/v1/"):
        return "openai"
    if path == "/api" or path.startswith("/api/"):
        return "ollama"
    return None


def wants_anthropic_models(headers: Optional[Mapping[str, str]]) -> bool:
    """Bare ``/v1/models*`` disambiguation (serving.md §2 rule 2)."""
    return _anthropic_by_headers(headers)
