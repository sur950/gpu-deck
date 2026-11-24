"""
The served admin OpenAPI spec matches the contract (CLI-SPEC finding, 2026-11-23).

``/api/v1/openapi.json`` must list the same operations (method + path) as
``libs/contracts/openapi/admin.yaml`` with the same operationIds, and document the
join-token parameters of the agent download routes. ``/live/ws`` is a WebSocket (not an
HTTP operation in FastAPI's spec); it is checked as a route instead.
"""

from __future__ import annotations

from collections import Counter

import yaml
from app.api.operation_ids import _TABLE, CONTRACT, OPERATION_IDS, contract_table
from app.core.settings import settings

METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
WEBSOCKETS = {("GET", "/live/ws")}


def _ops(spec, strip: str = ""):
    out = {}
    for path, item in (spec.get("paths") or {}).items():
        for method, op in (item or {}).items():
            if method in METHODS:
                rel = path[len(strip) :] if strip and path.startswith(strip) else path
                out[(method.upper(), rel)] = op
    return out


def _served():
    from app.main import app

    app.openapi_schema = None
    return app, app.openapi()


def test_operation_id_table_matches_the_contract():
    assert _TABLE == contract_table(), "run: venv/bin/python -m app.api.operation_ids --write"
    assert len(OPERATION_IDS) == len(set(OPERATION_IDS.values()))


def test_served_spec_matches_contract_paths_methods_and_operation_ids():
    contract = _ops(yaml.safe_load(CONTRACT.read_text()))
    _, served_spec = _served()
    served = _ops(served_spec, settings.API_V1_PREFIX)
    missing = sorted(set(contract) - set(served) - WEBSOCKETS)
    extra = sorted(set(served) - set(contract))
    assert not missing, f"in admin.yaml, not served: {missing}"
    assert not extra, f"served, not in admin.yaml: {extra}"
    wrong = {
        k: (served[k].get("operationId"), contract[k].get("operationId"))
        for k in served
        if served[k].get("operationId") != contract[k].get("operationId")
    }
    assert not wrong, f"operationId (served, contract): {wrong}"
    dupes = [i for i, n in Counter(o.get("operationId") for o in served.values()).items() if n > 1]
    assert not dupes
    from app.api.v1.routes.metrics import router as metrics_router
    from fastapi.routing import APIWebSocketRoute

    ws_paths = {r.path for r in metrics_router.routes if isinstance(r, APIWebSocketRoute)}
    assert {path for _, path in WEBSOCKETS} <= ws_paths


def test_agent_download_routes_document_the_join_token():
    _, served_spec = _served()
    served = _ops(served_spec, settings.API_V1_PREFIX)
    for key in (
        ("GET", "/agent/downloads/{version}/{target}"),
        ("GET", "/agent/downloads/{version}/{target}/sha256"),
        ("GET", "/agent/install.sh"),
    ):
        params = {(p["name"], p["in"]) for p in served[key].get("parameters") or []}
        assert ("X-GPUDeck-Join-Token", "header") in params, key
        assert ("token", "query") in params, key
    install = {p["name"] for p in served[("GET", "/agent/install.sh")]["parameters"]}
    assert "version" in install
    assert served[("GET", "/health/live")]["operationId"] == "getLiveness"
