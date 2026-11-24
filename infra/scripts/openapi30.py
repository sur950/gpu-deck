#!/usr/bin/env python3
"""Downgrade libs/contracts/openapi/admin.yaml (OpenAPI 3.1) to 3.0.3 for code generators.

oapi-codegen (Terraform provider) only understands 3.0.x. This
script rewrites the 3.1-only constructs the contract uses and writes a 3.0.3 copy; it never edits
the contract itself. Run:

    python3 infra/scripts/openapi30.py libs/contracts/openapi/admin.yaml /tmp/admin-3.0.yaml

Rewrites applied (lossless for codegen purposes):
  * ``type: [X, 'null']``            -> ``type: X`` + ``nullable: true``
  * ``type: 'null'``                 -> ``nullable: true`` (free-form object)
  * multi-type arrays                -> free-form schema + ``nullable`` when 'null' present
  * ``const: v``                     -> ``enum: [v]``
  * ``oneOf: [$ref, {type: null}]``  -> ``allOf: [$ref]`` + ``nullable: true``
  * ``examples: [..]`` on schemas    -> ``example: first``
  * numeric ``exclusiveMinimum/Maximum`` -> boolean form + ``minimum/maximum``
  * ``patternProperties``            -> ``additionalProperties``
  * ``info.summary``, ``$schema``, ``$id``, ``$comment``, ``contentMediaType``,
    ``contentEncoding``, ``webhooks``, ``unevaluatedProperties`` -> dropped
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

DROP_KEYS = {
    "$schema",
    "$id",
    "$comment",
    "contentMediaType",
    "contentEncoding",
    "unevaluatedProperties",
}


def _is_null_schema(node: Any) -> bool:
    return isinstance(node, dict) and node.get("type") == "null" and len(node) <= 2


def convert_schema(node: Any) -> Any:
    """Recursively rewrite one schema object (or any nested container)."""
    if isinstance(node, list):
        return [convert_schema(n) for n in node]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in DROP_KEYS:
            continue
        out[key] = value

    # type arrays
    typ = out.get("type")
    if isinstance(typ, list):
        types = [t for t in typ if t != "null"]
        if len(typ) != len(types):
            out["nullable"] = True
        if len(types) == 1:
            out["type"] = types[0]
        else:
            # free-form: drop type (3.0 has no union types)
            out.pop("type", None)
            for k in ("properties", "items", "additionalProperties", "required"):
                # keep structural keys only if a single structural type remains
                pass
    elif typ == "null":
        out.pop("type", None)
        out["nullable"] = True

    # const -> enum
    if "const" in out:
        out["enum"] = [out.pop("const")]

    # examples -> example
    if "examples" in out and isinstance(out["examples"], list):
        ex = out.pop("examples")
        if ex:
            out.setdefault("example", ex[0])

    # exclusiveMinimum / exclusiveMaximum numeric form
    for bound, excl in (("minimum", "exclusiveMinimum"), ("maximum", "exclusiveMaximum")):
        if isinstance(out.get(excl), (int, float)) and not isinstance(out.get(excl), bool):
            out[bound] = out.pop(excl)
            out[excl] = True

    # patternProperties -> additionalProperties
    if "patternProperties" in out:
        pp = out.pop("patternProperties")
        if isinstance(pp, dict) and pp and "additionalProperties" not in out:
            out["additionalProperties"] = next(iter(pp.values()))

    # oneOf / anyOf with a null branch
    for comb in ("oneOf", "anyOf"):
        branches = out.get(comb)
        if isinstance(branches, list):
            non_null = [b for b in branches if not _is_null_schema(b)]
            if len(non_null) != len(branches):
                out["nullable"] = True
                if len(non_null) == 1:
                    out.pop(comb)
                    only = non_null[0]
                    if "$ref" in only and len(only) == 1:
                        out["allOf"] = [only]
                    else:
                        merged = convert_schema(only)
                        for k, v in merged.items():
                            out.setdefault(k, v)
                else:
                    out[comb] = non_null

    # recurse
    for key in list(out.keys()):
        if key in ("properties", "patternProperties") and isinstance(out[key], dict):
            out[key] = {k: convert_schema(v) for k, v in out[key].items()}
        elif key in ("items", "additionalProperties", "not") and isinstance(out[key], dict):
            out[key] = convert_schema(out[key])
        elif key in ("allOf", "oneOf", "anyOf") and isinstance(out[key], list):
            out[key] = [convert_schema(b) for b in out[key]]
        elif key in (
            "schema",
            "content",
            "responses",
            "requestBody",
            "parameters",
            "paths",
            "components",
            "schemas",
            "headers",
            "callbacks",
            "encoding",
        ):
            out[key] = convert_schema(out[key])
        elif isinstance(out[key], (dict, list)) and key not in (
            "enum",
            "example",
            "default",
            "required",
            "x-codegen",
        ):
            out[key] = convert_schema(out[key])
    return out


def downgrade(spec: dict[str, Any]) -> dict[str, Any]:
    spec = dict(spec)
    spec["openapi"] = "3.0.3"
    info = dict(spec.get("info", {}))
    info.pop("summary", None)
    spec["info"] = info
    spec.pop("webhooks", None)
    spec.pop("jsonSchemaDialect", None)
    out = convert_schema(spec)
    # The contract marks X-CSRF-Token as required because the browser always sends it; API
    # clients that authenticate with ``Authorization: Bearer`` must not (admin.yaml, CsrfHeader
    # description). Make it optional so generated clients only send it in cookie mode.
    csrf = out.get("components", {}).get("parameters", {}).get("CsrfHeader")
    if isinstance(csrf, dict):
        csrf["required"] = False
    _dedupe_class_names(out.get("components", {}).get("schemas", {}))
    return out


def _pascal(name: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in name.replace("-", "_").split("_") if p)


def _dedupe_class_names(schemas: dict[str, Any]) -> None:
    """Give generators distinct class names where the contract's names collide.

    openapi-python-client names classes in PascalCase, so the enum ``cloud_provider`` and the
    object ``CloudProvider`` (or an inline ``GpuInstance.limits`` object and the component
    ``GpuInstanceLimits``) would map to the same class and one of them gets dropped. A ``title``
    renames only the generated class; the wire format is unchanged.
    """
    if not isinstance(schemas, dict):
        return
    taken = {_pascal(n): n for n in schemas if n == _pascal(n)}
    for name, schema in schemas.items():
        if not isinstance(schema, dict):
            continue
        if name != _pascal(name) and _pascal(name) in taken and "title" not in schema:
            renamed = _pascal(name) + ("Enum" if "enum" in schema else "Type")
            schema["title"] = renamed  # openapi-python-client
            schema["x-go-name"] = renamed  # oapi-codegen
        for prop, sub in (schema.get("properties") or {}).items():
            inline = _pascal(name) + _pascal(prop)
            if (
                isinstance(sub, dict)
                and "$ref" not in sub
                and sub.get("type") == "object"
                and inline in taken
                and "title" not in sub
            ):
                sub["title"] = inline + "Object"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    src, dst = Path(argv[1]), Path(argv[2])
    spec = yaml.safe_load(src.read_text())
    out = downgrade(spec)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=120))
    print(f"wrote {dst} ({dst.stat().st_size} bytes, openapi {out['openapi']})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
