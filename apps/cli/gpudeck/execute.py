"""Run one spec operation: build the request from the parsed options, send it, render the result.

Body assembly, lowest to highest precedence: ``--data`` / ``--file``, then the per-field options,
then ``--set``. The finished body is checked for required fields and validated against the
operation's JSON schema (``jsonschema``; skip with ``--no-validate``) before anything is sent.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import click

from . import output
from .errors import ConfigError
from .state import get_state

if TYPE_CHECKING:  # pragma: no cover
    from .openapi import Operation, Spec
    from .registry import Binding

logger = logging.getLogger(__name__)

PAGE_SIZE_ALL = 200


def to_wire(value: Any) -> Any:
    """CLI values (UUID, datetime, date, tuples) -> JSON / query-string values."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [to_wire(v) for v in value]
    return value


def parse_document(text: str, origin: str) -> Any:
    try:
        if text.lstrip().startswith(("{", "[")):
            return json.loads(text)
        import yaml

        return yaml.safe_load(text)
    except ValueError as exc:
        raise ConfigError(f"cannot parse {origin}: {exc}") from exc
    except Exception as exc:  # yaml.YAMLError
        raise ConfigError(f"cannot parse {origin}: {exc}") from exc


def load_body_file(path: str) -> Any:
    if path == "-":
        return parse_document(sys.stdin.read(), "stdin")
    try:
        text = Path(path).expanduser().read_text()
    except OSError as exc:
        raise ConfigError(f"cannot read body file {path}: {exc}") from exc
    return parse_document(text, f"body file {path}")


def load_data_option(value: str) -> Any:
    """``--data``: ``@path`` (JSON/YAML file), ``-`` (stdin) or inline JSON."""
    if value == "-" or value.startswith("@"):
        return load_body_file(value[1:] if value.startswith("@") else "-")
    try:
        return json.loads(value)
    except ValueError as exc:
        raise ConfigError(f"--data is not valid JSON ({exc}); use @file for YAML") from exc


def apply_sets(data: dict[str, Any], sets: list[str] | tuple[str, ...]) -> dict[str, Any]:
    for item in sets:
        key, sep, raw = item.partition("=")
        if not sep:
            raise ConfigError(f"--set expects key=value, got {item!r}")
        try:
            value: Any = json.loads(raw)
        except ValueError:
            value = raw
        cur = data
        parts = key.split(".")
        for part in parts[:-1]:
            nxt = cur.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[part] = nxt
            cur = nxt
        cur[parts[-1]] = value
    return data


def _merge(base: Any, extra: Any, origin: str) -> Any:
    if base is None:
        return extra
    if isinstance(base, dict) and isinstance(extra, dict):
        return {**base, **extra}
    raise ConfigError(f"{origin}: cannot combine a non-object body with other body input")


def validate_body(op: "Operation", spec: "Spec", body: Any) -> None:
    """Required fields first (clear message), then the full JSON schema."""
    assert op.body is not None
    if isinstance(body, dict):
        missing = [f for f in op.body.required_fields if f not in body]
        if missing:
            raise ConfigError(
                f"missing required field {missing[0]!r}"
                + (f" (also: {', '.join(missing[1:])})" if missing[1:] else "")
                + "; pass it as an option, --set, --data or --file"
            )
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import best_match
    except ImportError:  # pragma: no cover - jsonschema is a dependency
        return
    schema = {"components": spec.doc.get("components") or {}, "allOf": [op.body.schema]}
    try:
        error = best_match(Draft202012Validator(schema).iter_errors(body))
    except Exception as exc:  # noqa: BLE001 - a schema the validator cannot handle
        logger.debug("skipping body validation: %s", exc)
        return
    if error is not None:
        where = ".".join(str(p) for p in error.absolute_path) or "body"
        raise ConfigError(f"invalid request body at {where}: {error.message} (--no-validate skips)")


def build_body(op: "Operation", bindings: dict[str, "Binding"], values: dict[str, Any]) -> Any:
    body: Any = None
    if values.get("body_data"):
        body = _merge(body, load_data_option(values["body_data"]), "--data")
    if values.get("body_file"):
        body = _merge(body, load_body_file(values["body_file"]), "--file")
    fields = {
        b.name: to_wire(values[py])
        for py, b in bindings.items()
        if b.kind == "body" and values.get(py) is not None and values.get(py) != ()
    }
    if fields:
        body = _merge(body, fields, "body options")
    sets = values.get("body_set") or ()
    if sets:
        if body is None:
            body = {}
        if not isinstance(body, dict):
            raise ConfigError("--set needs an object body")
        body = apply_sets(body, sets)
    return body


def run_operation(
    ctx: click.Context, op: "Operation", bindings: dict[str, "Binding"], values: dict[str, Any]
) -> None:
    from .http import NO_BODY

    state = get_state(ctx)
    loaded = state.spec()
    path = op.path
    params: dict[str, Any] = {}
    headers: dict[str, str] = {}
    for py, b in bindings.items():
        value = values.get(py)
        if b.kind == "path":
            path = path.replace("{" + b.name + "}", quote(str(to_wire(value)), safe=""))
        elif value is None or value == ():
            continue
        elif b.kind == "query":
            params[b.name] = to_wire(list(value) if isinstance(value, tuple) else value)
        elif b.kind == "header":
            headers[b.name] = str(to_wire(value))

    body: Any = NO_BODY
    if op.body is not None:
        built = build_body(op, bindings, values)
        if built is not None or op.body.required:
            built = {} if built is None else built
            if state.validate:
                validate_body(op, loaded.spec, built)
            body = built

    session = state.session()
    if op.paginated and values.get("fetch_all"):
        source = ctx.get_parameter_source(_py_for(bindings, "query", "page_size") or "")
        if source in (None, click.core.ParameterSource.DEFAULT):
            limit = next(
                (p.schema.get("maximum") for p in op.params if p.name == "page_size"), None
            )
            params["page_size"] = min(PAGE_SIZE_ALL, int(limit or PAGE_SIZE_ALL))
        rows: list[Any] = []
        page = int(params.get("page") or 1)
        meta: Any = None
        while True:
            params["page"] = page
            data_page, meta = session.request(
                op.method, path, params=params, body=body, headers=headers, auth=op.auth
            )
            rows.extend(output.to_plain(data_page) or [])
            if not (isinstance(meta, dict) and meta.get("has_more")):
                break
            page += 1
        if isinstance(meta, dict):
            meta = {**meta, "page": 1, "page_size": len(rows)}
        output.render(rows, meta, fmt=state.fmt, fields=state.fields)
        return
    data_out, meta = session.request(
        op.method, path, params=params, body=body, headers=headers, auth=op.auth
    )
    output.render(data_out, meta, fmt=state.fmt, fields=state.fields, title=None)


def _py_for(bindings: dict[str, "Binding"], kind: str, name: str) -> str | None:
    return next((py for py, b in bindings.items() if b.kind == kind and b.name == name), None)


__all__ = ["apply_sets", "build_body", "load_body_file", "run_operation", "validate_body"]
