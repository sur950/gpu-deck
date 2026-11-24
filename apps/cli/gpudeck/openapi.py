"""Read an OpenAPI 3.1 document into the operations the CLI turns into commands.

Works on both shapes the CLI sees: the contract (``libs/contracts/openapi/admin.yaml``: paths
relative to ``/api/v1``, camelCase operationIds, shared ``components/parameters``) and the live
document FastAPI serves at ``/api/v1/openapi.json`` (paths carry ``/api/v1``, operationIds are
``<function>_<path>_<method>``). Every operation gets a stable ``key``: the contract operationId
in snake_case, which is what ``registry.COMMAND_MAP`` is keyed by.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
API_PREFIX = "/api/v1"
# Headers the session sets itself (auth, CSRF) or that only streaming endpoints use.
MANAGED_HEADERS = {"x-csrf-token", "authorization", "cookie", "last-event-id"}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def snake(name: str) -> str:
    """``createApiKey`` / ``X-GPUDeck-Join-Token`` / ``downloadAgentSha256`` -> snake_case.

    Same word splitting as the previous generated client (``Sha256`` -> ``sha_256``) so command
    keys and option names did not change.
    """
    s = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    s = re.sub(r"([A-Za-z])(\d)", r"\1_\2", s)
    s = re.sub(r"[^0-9A-Za-z]+", "_", s)
    return s.strip("_").lower()


def kebab(name: str) -> str:
    return snake(name).replace("_", "-")


def norm_path(path: str) -> str:
    """``/clusters/{cluster_id}`` -> ``/clusters/{}`` (parameter names may differ per spec)."""
    return re.sub(r"\{[^}]+\}", "{}", path)


def route_key(method: str, path: str) -> str:
    return f"{method.upper()} {norm_path(path)}"


@dataclass
class Param:
    name: str
    location: str  # path | query | header
    required: bool
    schema: dict[str, Any]
    nullable: bool
    description: str
    default: Any = None


@dataclass
class BodyField:
    name: str
    schema: dict[str, Any]
    required: bool
    description: str


@dataclass
class Body:
    schema: dict[str, Any]  # unresolved schema as written (may be a $ref), for validation
    resolved: dict[str, Any]
    required: bool
    fields: list[BodyField] = field(default_factory=list)
    required_fields: list[str] = field(default_factory=list)


@dataclass
class Operation:
    key: str
    method: str
    path: str  # relative to /api/v1
    operation_id: str
    summary: str
    description: str
    tags: list[str]
    params: list[Param]
    body: Body | None
    auth: str  # required | public | optional (spec does not say)
    response_types: list[str]

    @property
    def path_params(self) -> list[Param]:
        return [p for p in self.params if p.location == "path"]

    @property
    def paginated(self) -> bool:
        names = {p.name for p in self.params if p.location == "query"}
        return "page" in names and "page_size" in names

    @property
    def streaming(self) -> bool:
        return "text/event-stream" in self.response_types


class Spec:
    """A parsed OpenAPI document plus the operations in it."""

    def __init__(self, doc: dict[str, Any], op_keys: dict[str, str] | None = None) -> None:
        self.doc = doc
        self.op_keys = op_keys or {}
        info = doc.get("info") or {}
        self.version = str(info.get("version") or "")
        self.title = str(info.get("title") or "")
        paths = doc.get("paths") or {}
        self.base_path = (
            API_PREFIX if paths and all(p.startswith(API_PREFIX + "/") for p in paths) else ""
        )
        self._operations: list[Operation] | None = None

    # ----- $ref ----------------------------------------------------------------------------
    def resolve(self, obj: Any, depth: int = 0) -> Any:
        """Follow local ``$ref`` chains (``#/components/...``)."""
        while isinstance(obj, dict) and "$ref" in obj and depth < 32:
            ref = str(obj["$ref"])
            if not ref.startswith("#/"):
                logger.debug("external $ref %s not supported", ref)
                return {}
            target: Any = self.doc
            for part in ref[2:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                target = target.get(part) if isinstance(target, dict) else None
            if target is None:
                return {}
            # sibling keywords next to $ref (3.1) override the target's
            extra = {k: v for k, v in obj.items() if k != "$ref"}
            obj = {**target, **extra} if extra and isinstance(target, dict) else target
            depth += 1
        return obj

    def simplify(self, schema: Any) -> tuple[dict[str, Any], bool]:
        """Resolve refs and strip ``null`` alternatives; returns (schema, nullable)."""
        schema = self.resolve(schema) if schema else {}
        if not isinstance(schema, dict):
            return {}, False
        nullable = False
        for key in ("anyOf", "oneOf"):
            alts = schema.get(key)
            if isinstance(alts, list):
                rest = [a for a in alts if self.resolve(a).get("type") != "null"]
                if len(rest) < len(alts):
                    nullable = True
                if len(rest) == 1:
                    inner, inner_null = self.simplify(rest[0])
                    merged = {k: v for k, v in schema.items() if k != key}
                    return {**inner, **merged}, nullable or inner_null
        all_of = schema.get("allOf")
        if isinstance(all_of, list) and len(all_of) == 1:
            inner, inner_null = self.simplify(all_of[0])
            merged = {k: v for k, v in schema.items() if k != "allOf"}
            return {**inner, **merged}, inner_null
        typ = schema.get("type")
        if isinstance(typ, list):
            types = [t for t in typ if t != "null"]
            nullable = nullable or len(types) < len(typ)
            if len(types) == 1:
                schema = {**schema, "type": types[0]}
        return schema, nullable

    def object_properties(self, schema: Any) -> tuple[dict[str, Any], list[str]]:
        """Properties and required names of an object schema (``allOf`` parts merged)."""
        schema, _ = self.simplify(schema)
        props: dict[str, Any] = dict(schema.get("properties") or {})
        required: list[str] = list(schema.get("required") or [])
        for part in schema.get("allOf") or []:
            p, r = self.object_properties(part)
            props.update(p)
            required.extend(r)
        return props, required

    # ----- operations ----------------------------------------------------------------------
    def operations(self) -> list[Operation]:
        if self._operations is None:
            self._operations = self._parse_operations()
        return self._operations

    def _parse_operations(self) -> list[Operation]:
        ops: list[Operation] = []
        root_security = self.doc.get("security")
        for raw_path, item in (self.doc.get("paths") or {}).items():
            if not isinstance(item, dict):
                continue
            path = raw_path[len(self.base_path) :] if self.base_path else raw_path
            shared = item.get("parameters") or []
            for method in HTTP_METHODS:
                op = item.get(method)
                if not isinstance(op, dict):
                    continue
                ops.append(self._operation(method, path, raw_path, op, shared, root_security))
        return ops

    def operation_key(self, method: str, path: str, raw_path: str, op: dict[str, Any]) -> str:
        known = self.op_keys.get(route_key(method, path))
        if known:
            return known
        op_id = str(op.get("operationId") or "")
        # FastAPI default ids: <function name><path with non-word chars as _>_<method>
        suffix = re.sub(r"\W", "_", raw_path) + "_" + method
        if op_id.endswith(suffix) and len(op_id) > len(suffix):
            return snake(op_id[: -len(suffix)])
        if op_id:
            return snake(op_id)
        return snake(f"{method}_{path}")

    def _operation(
        self,
        method: str,
        path: str,
        raw_path: str,
        op: dict[str, Any],
        shared: list[Any],
        root_security: Any,
    ) -> Operation:
        params: dict[tuple[str, str], Param] = {}
        for raw in list(shared) + list(op.get("parameters") or []):
            p = self.resolve(raw)
            if not isinstance(p, dict) or p.get("in") not in ("path", "query", "header"):
                continue
            if p["in"] == "header" and str(p.get("name", "")).lower() in MANAGED_HEADERS:
                continue
            schema, nullable = self.simplify(p.get("schema") or {})
            params[(p["in"], p["name"])] = Param(
                name=str(p["name"]),
                location=str(p["in"]),
                required=bool(p.get("required")) or p["in"] == "path",
                schema=schema,
                nullable=nullable,
                description=str(p.get("description") or schema.get("description") or ""),
                default=schema.get("default"),
            )
        # path parameters in template order
        order = re.findall(r"\{([^}]+)\}", path)
        path_params = [params[("path", n)] for n in order if ("path", n) in params]
        for n in order:
            if ("path", n) not in params:
                path_params.append(Param(n, "path", True, {"type": "string"}, False, ""))
        others = [p for p in params.values() if p.location != "path"]

        security = op.get("security", root_security)
        auth = "optional" if security is None else ("public" if security == [] else "required")
        responses: list[str] = []
        for resp in (op.get("responses") or {}).values():
            for ctype in (self.resolve(resp) or {}).get("content") or {}:
                if ctype not in responses:
                    responses.append(ctype)
        return Operation(
            key=self.operation_key(method, path, raw_path, op),
            method=method.upper(),
            path=path,
            operation_id=str(op.get("operationId") or ""),
            summary=str(op.get("summary") or "").strip(),
            description=str(op.get("description") or "").strip(),
            tags=[str(t) for t in op.get("tags") or []],
            params=path_params + others,
            body=self._body(op.get("requestBody")),
            auth=auth,
            response_types=responses,
        )

    def _body(self, raw: Any) -> Body | None:
        rb = self.resolve(raw) if raw else None
        if not isinstance(rb, dict):
            return None
        content = rb.get("content") or {}
        media = content.get("application/json") or next(
            (v for k, v in content.items() if "json" in k), None
        )
        if not isinstance(media, dict):
            return None
        schema = media.get("schema") or {}
        resolved, _ = self.simplify(schema)
        props, required = self.object_properties(schema)
        fields = []
        for name, prop in props.items():
            ps, _ = self.simplify(prop)
            fields.append(
                BodyField(
                    name=name,
                    schema=ps,
                    required=name in required,
                    description=str(ps.get("description") or ""),
                )
            )
        return Body(
            schema=schema,
            resolved=resolved,
            required=bool(rb.get("required")),
            fields=fields,
            required_fields=required,
        )

    # ----- scalar classification -------------------------------------------------------------
    def scalar(self, schema: dict[str, Any]) -> tuple[str, Any] | None:
        """Classify a (simplified) schema for a CLI option.

        Returns ``(kind, extra)`` with kind one of ``string``, ``uuid``, ``datetime``, ``date``,
        ``integer``, ``number``, ``boolean``, ``enum`` (extra = values) or ``array`` (extra = the
        item's ``(kind, extra)``); ``None`` for objects, unions and anything else not expressible
        as a flag value.
        """
        schema, _ = self.simplify(schema)
        if "enum" in schema and isinstance(schema["enum"], list):
            values = [v for v in schema["enum"] if v is not None]
            if values and all(isinstance(v, str) for v in values):
                return "enum", values
            if values and all(isinstance(v, int) for v in values):
                return "integer", None
            return None
        if "const" in schema and isinstance(schema["const"], str):
            return "enum", [schema["const"]]
        typ = schema.get("type")
        if typ == "string":
            fmt = schema.get("format")
            if fmt == "uuid":
                return "uuid", None
            if fmt == "date-time":
                return "datetime", None
            if fmt == "date":
                return "date", None
            if fmt == "binary":
                return None
            return "string", None
        if typ in ("integer", "number", "boolean"):
            return str(typ), None
        if typ == "array":
            item = self.scalar(schema.get("items") or {})
            if item is None or item[0] == "array":
                return None
            return "array", item
        return None


__all__ = [
    "API_PREFIX",
    "Body",
    "BodyField",
    "Operation",
    "Param",
    "Spec",
    "UNSAFE_METHODS",
    "kebab",
    "norm_path",
    "route_key",
    "snake",
]
