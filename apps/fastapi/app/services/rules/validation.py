"""
Rule parameter validation against ``libs/contracts/rules/*.schema.json``.

The schemas are the single source of truth (the UI renders its forms from
them and ``GET /rules/schemas`` serves them verbatim). This module loads them
once, resolves the cross-file ``$ref``s, validates ``params`` / ``target`` /
layout bodies with JSON Schema draft 2020-12 and fills in schema defaults so
stored rules are always complete.
"""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.exceptions import ValidationError
from app.core.logger import get_logger
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

logger = get_logger(__name__)

# apps/fastapi/app/services/rules/validation.py -> repo root is parents[5]
_PARENTS = Path(__file__).resolve().parents
_REPO_ROOT = _PARENTS[5] if len(_PARENTS) > 5 else _PARENTS[-1]
_ENV = __import__("os").environ
CONTRACTS_RULES_DIR = Path(
    _ENV.get("GPU_DECK_RULES_SCHEMA_DIR")
    or (
        Path(_ENV["GPU_DECK_CONTRACTS_DIR"]) / "rules"
        if _ENV.get("GPU_DECK_CONTRACTS_DIR")
        else _REPO_ROOT / "libs/contracts/rules"
    )
)

RULE_TYPES = (
    "idle_unload",
    "time_schedule",
    "low_traffic_shrink",
    "high_load_grow",
    "make_room_by_priority",
    "auto_heal",
)

_TIME_WINDOW_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")


@lru_cache(maxsize=1)
def load_schemas() -> Dict[str, Dict[str, Any]]:
    """Every ``*.schema.json`` in the contracts folder keyed by file stem (``idle_unload``...)."""
    schemas: Dict[str, Dict[str, Any]] = {}
    for path in sorted(CONTRACTS_RULES_DIR.glob("*.schema.json")):
        with open(path, "r", encoding="utf-8") as fh:
            schemas[path.name.replace(".schema.json", "").replace("-", "_")] = json.load(fh)
    if not schemas:
        raise RuntimeError(f"No rule schemas found in {CONTRACTS_RULES_DIR}")
    return schemas


@lru_cache(maxsize=1)
def _registry() -> Registry:
    resources = []
    for schema in load_schemas().values():
        resource = Resource(contents=schema, specification=DRAFT202012)
        resources.append((schema["$id"], resource))
    return Registry().with_resources(resources)


@lru_cache(maxsize=16)
def _validator(schema_key: str) -> Draft202012Validator:
    schema = load_schemas()[schema_key]
    return Draft202012Validator(schema, registry=_registry())


def schemas_for_api() -> Dict[str, Any]:
    """Payload of ``GET /rules/schemas`` — the contract files verbatim."""
    schemas = load_schemas()
    return {
        "rule": schemas["rule"],
        "target_selector": schemas["target_selector"],
        "layout": schemas["layout"],
        "params": {rule_type: schemas[rule_type] for rule_type in RULE_TYPES},
    }


def _format_errors(errors: List[Any], prefix: str) -> str:
    parts = []
    for err in sorted(errors, key=lambda e: list(e.absolute_path)):
        path = ".".join(str(p) for p in err.absolute_path) or "(root)"
        parts.append(f"{prefix}{path}: {err.message}")
    return "; ".join(parts)


def apply_defaults(schema: Dict[str, Any], value: Dict[str, Any]) -> Dict[str, Any]:
    """Fill missing top-level properties (and nested objects) from ``default``."""
    out = copy.deepcopy(value)
    for key, prop in (schema.get("properties") or {}).items():
        if key not in out and "default" in prop:
            out[key] = copy.deepcopy(prop["default"])
        elif key in out and isinstance(out[key], dict) and isinstance(prop.get("properties"), dict):
            out[key] = apply_defaults(prop, out[key])
    return out


def validate_params(rule_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Validate ``params`` for ``rule_type``; returns params with defaults applied."""
    if rule_type not in RULE_TYPES:
        raise ValidationError(f"Unknown rule type {rule_type!r}")
    if not isinstance(params, dict):
        raise ValidationError("params must be an object")
    schema = load_schemas()[rule_type]
    filled = apply_defaults(schema, params)
    errors = list(_validator(rule_type).iter_errors(filled))
    if errors:
        raise ValidationError(_format_errors(errors, "params."))
    _semantic_checks(rule_type, filled)
    return filled


def validate_target(target: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    target = target or {}
    if not isinstance(target, dict):
        raise ValidationError("target must be an object")
    errors = list(_validator("target_selector").iter_errors(target))
    if errors:
        raise ValidationError(_format_errors(errors, "target."))
    return target


def validate_layout_document(document: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a layout body against layout.schema.json (defaults applied to items)."""
    schema = load_schemas()["layout"]
    filled = apply_defaults(schema, document)
    items = []
    for item in filled.get("items") or []:
        items.append(apply_defaults(schema["properties"]["items"]["items"], item))
    filled["items"] = items
    errors = list(_validator("layout").iter_errors(filled))
    if errors:
        raise ValidationError(_format_errors(errors, "layout."))
    return filled


def validate_timezone(name: str, field: str = "timezone") -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValidationError(f"params.{field}: unknown IANA timezone {name!r}")


def validate_cron(expr: str) -> None:
    from apscheduler.triggers.cron import CronTrigger

    try:
        CronTrigger.from_crontab(expr)
    except ValueError as exc:
        raise ValidationError(f"params.cron: invalid cron expression ({exc})")


def _semantic_checks(rule_type: str, params: Dict[str, Any]) -> None:
    """Checks JSON Schema cannot express (timezone names, cron syntax, window order)."""
    if rule_type == "time_schedule":
        validate_timezone(params.get("timezone") or "UTC")
        if params.get("cron"):
            validate_cron(params["cron"])
    if rule_type == "idle_unload":
        validate_timezone(params.get("timezone") or "UTC")
        for window in params.get("respect_time_windows") or []:
            if not _TIME_WINDOW_RE.match(window):
                raise ValidationError(f"params.respect_time_windows: bad window {window!r}")
    if rule_type == "low_traffic_shrink" and params.get("action") == "shrink_slice":
        if not params.get("shrink_slice"):
            raise ValidationError("params.shrink_slice is required when action=shrink_slice")
    if rule_type == "make_room_by_priority":
        if "always_on" in (params.get("evict_priorities") or []):
            raise ValidationError("params.evict_priorities may not include always_on")
    if rule_type == "high_load_grow":
        if (
            params.get("waiting_requests_above") is None
            and params.get("kv_cache_pct_above") is None
        ):
            raise ValidationError("params: one of waiting_requests_above / kv_cache_pct_above")
    if rule_type == "auto_heal":
        if params.get("health_failures") is None and params.get("error_rate_pct_above") is None:
            raise ValidationError("params: one of health_failures / error_rate_pct_above")
