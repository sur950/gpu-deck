"""Spec loading: live fetch, per-server cache and TTL, refresh, bundled fallback, version check."""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from httpx import Response

from gpudeck import spec as spec_mod
from gpudeck.openapi import Spec, snake

from .conftest import BASE, envelope, run, run_json

CONTRACT = Path(__file__).resolve().parents[3] / "libs" / "contracts" / "openapi" / "admin.yaml"


@lru_cache(maxsize=1)
def _contract() -> dict[str, Any]:
    return yaml.load(CONTRACT.read_text(), Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))


def fastapi_spec(version: str = "1.0.0", extra_paths: dict[str, Any] | None = None) -> dict:
    """The contract reshaped like FastAPI serves it: /api/v1 paths, <func>_<path>_<method> ids."""
    doc = copy.deepcopy(_contract())
    doc["info"]["version"] = version
    doc.pop("servers", None)
    renamed = {"checkIp": "check_ip_rule", "getHealth": "comprehensive_health_check"}
    paths = {}
    for path, item in doc["paths"].items():
        full = "/api/v1" + path
        for method, op in item.items():
            if isinstance(op, dict) and "operationId" in op:
                func = renamed.get(op["operationId"]) or snake(op["operationId"])
                op["operationId"] = f"{func}{re.sub(r'[^0-9A-Za-z_]', '_', full)}_{method}"
        paths[full] = item
    paths.update(extra_paths or {})
    doc["paths"] = paths
    return doc


WIDGETS = {
    "/api/v1/widgets": {
        "get": {
            "tags": ["Widgets"],
            "summary": "List widgets",
            "operationId": "list_widgets_api_v1_widgets_get",
            "security": [{"HTTPBearer": []}],
            "parameters": [
                {
                    "name": "color",
                    "in": "query",
                    "required": False,
                    "schema": {"anyOf": [{"enum": ["red", "blue"], "type": "string"}]},
                }
            ],
            "responses": {"200": {"description": "ok"}},
        }
    }
}


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GPUDECK_SPEC", "auto")


def _spec_info(capsys) -> dict[str, Any]:
    return run_json(["config", "spec"], capsys)


def test_live_spec_is_fetched_cached_and_used(api, logged_in, live, capsys, isolated_config):
    route = api.get("/openapi.json").mock(
        return_value=Response(200, json=fastapi_spec(extra_paths=WIDGETS))
    )
    info = _spec_info(capsys)
    assert info["source"] == "live" and info["server"] == BASE and route.call_count == 1
    cached = Path(info["location"])
    assert cached.is_file() and cached.name.endswith("-1.0.0.json")
    assert json.loads(cached.read_text())["op_keys"]["POST /ip-rules/check"] == "check_ip"

    # second run: served from the cache, no request
    assert _spec_info(capsys)["source"] == "cache" and route.call_count == 1

    # a server-only operation shows up under its tag; contract operations keep their names
    widgets = api.get("/widgets").mock(return_value=envelope([{"id": "w1"}]))
    data = run_json(["widgets", "list-widgets", "--color", "red"], capsys)
    assert data == [{"id": "w1"}] and widgets.calls[0].request.url.params["color"] == "red"
    check = api.post("/ip-rules/check").mock(return_value=envelope({"allowed": True}))
    assert (
        run_json(["ip-rules", "check", "--ip", "10.0.0.1", "--scope", "admin"], capsys)["allowed"]
        is True
    )
    assert json.loads(check.calls[0].request.content)["ip"] == "10.0.0.1"


def test_ttl_expiry_and_refresh_refetch(api, logged_in, live, capsys, monkeypatch):
    route = api.get("/openapi.json").mock(return_value=Response(200, json=fastapi_spec()))
    _spec_info(capsys)
    assert run_json(["--refresh-spec", "config", "spec"], capsys)["source"] == "live"
    assert route.call_count == 2
    monkeypatch.setenv("GPUDECK_SPEC_TTL", "0")
    assert _spec_info(capsys)["source"] == "live" and route.call_count == 3


def test_unreachable_server_falls_back_to_bundled_and_backs_off(api, logged_in, live, capsys):
    route = api.get("/openapi.json").mock(side_effect=httpx.ConnectError("refused"))
    info = _spec_info(capsys)
    assert info["source"] == "bundled" and info["location"].endswith("admin.yaml")
    assert route.call_count == 1
    # the failure is remembered: the next command does not try again
    assert _spec_info(capsys)["source"] == "bundled" and route.call_count == 1
    # commands still work from the bundled contract
    api.get("/clusters").mock(return_value=envelope([], {"total": 0, "page": 1}))
    assert run_json(["clusters", "list"], capsys)["data"] == []


def test_stale_cache_beats_bundled_when_the_server_is_down(
    api, logged_in, live, capsys, monkeypatch
):
    route = api.get("/openapi.json").mock(
        return_value=Response(200, json=fastapi_spec(extra_paths=WIDGETS))
    )
    _spec_info(capsys)
    route.mock(side_effect=httpx.ConnectError("refused"))
    monkeypatch.setenv("GPUDECK_SPEC_TTL", "0")
    code, out, err = run(["--refresh-spec", "config", "spec"], capsys)
    assert code == 0 and json.loads(out)["source"] == "stale-cache"
    assert "could not fetch the API spec" in err
    assert "widgets" in run(["widgets", "--help"], capsys)[1]


def test_version_mismatch_warns(api, logged_in, live, capsys):
    api.get("/openapi.json").mock(return_value=Response(200, json=fastapi_spec(version="2.3.0")))
    code, out, err = run(["config", "spec"], capsys)
    assert code == 0 and json.loads(out)["version"] == "2.3.0"
    assert "server API 2.3.0" in err and "differs" in err
    # a cache per server and version
    api.get("/openapi.json").mock(return_value=Response(200, json=fastapi_spec(version="2.4.0")))
    info = run_json(["--refresh-spec", "config", "spec"], capsys)
    files = list(Path(info["location"]).parent.glob("spec-*.json"))
    assert [f.name.rsplit("-", 1)[1] for f in files] == ["2.4.0.json"]


def test_same_minor_version_does_not_warn(api, logged_in, live, capsys):
    bundled = spec_mod.load_bundled().version
    api.get("/openapi.json").mock(return_value=Response(200, json=fastapi_spec(version=bundled)))
    code, _, err = run(["config", "spec"], capsys)
    assert code == 0 and "differs" not in err


def test_non_spec_response_falls_back(api, logged_in, live, capsys):
    api.get("/openapi.json").mock(return_value=Response(404, json={"detail": "Not Found"}))
    assert _spec_info(capsys)["source"] == "bundled"


def test_root_help_never_fetches(api, logged_in, live, capsys):
    route = api.get("/openapi.json").mock(return_value=Response(200, json=fastapi_spec()))
    code, out, _ = run(["--help"], capsys)
    assert code == 0 and "clusters" in out and not route.called


def test_spec_file_override(tmp_path, logged_in, capsys, monkeypatch, api):
    doc = {
        "openapi": "3.1.0",
        "info": {"title": "t", "version": "9.9.9"},
        "paths": WIDGETS,
    }
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(doc))
    monkeypatch.setenv("GPUDECK_SPEC", str(path))
    info = _spec_info(capsys)
    assert info["source"] == "file" and info["version"] == "9.9.9"
    code, out, _ = run(["--help"], capsys)
    assert "widgets" in out and "clusters" not in out


def test_bundled_spec_comes_from_the_repository_in_a_checkout():
    assert spec_mod.bundled_spec_path() == CONTRACT
    loaded = spec_mod.load_bundled()
    assert loaded.source == "bundled" and len(loaded.spec.operations()) >= 200


def test_yaml_parse_is_cached_as_json(tmp_path, monkeypatch):
    monkeypatch.setenv("GPUDECK_CACHE_DIR", str(tmp_path / "c"))
    first = spec_mod.read_document(CONTRACT)
    cached = list((tmp_path / "c").glob("parsed-*.json"))
    assert len(cached) == 1
    assert spec_mod.read_document(CONTRACT) == first


def test_fastapi_operation_ids_map_to_contract_keys():
    live = Spec(fastapi_spec())
    keys = {op.key for op in live.operations()}
    contract = {op.key for op in Spec(_contract()).operations()}
    # without the contract index only the two renamed functions differ
    assert contract - keys == {"check_ip", "get_health"}
    mapped = Spec(fastapi_spec(), op_keys=spec_mod.op_keys_from_contract(live))
    assert {op.key for op in mapped.operations()} == contract
    assert all(
        op.path.startswith("/") and not op.path.startswith("/api/v1") for op in live.operations()
    )


def test_optional_auth_without_session_reports_not_logged_in(api, capsys, monkeypatch, live):
    """Live specs may not mark public operations; a 401 without a session means log in."""
    from .conftest import ENDPOINT, error

    api.get("/openapi.json").mock(
        return_value=Response(
            200,
            json={
                "openapi": "3.1.0",
                "info": {"version": "1"},
                "paths": {
                    "/api/v1/auth/me": {
                        "get": {
                            "operationId": "get_me_api_v1_auth_me_get",
                            "tags": ["Auth"],
                            "responses": {},
                        }
                    },
                },
            },
        )
    )
    api.get("/auth/me").mock(return_value=error(401, "AUTHENTICATION_ERROR", "no session"))
    code, _, err = run(["-e", ENDPOINT, "auth", "whoami"], capsys)
    assert code == 3 and "not logged in" in err


def test_broken_spec_file_keeps_hand_written_commands(tmp_path, capsys, monkeypatch):
    bad = tmp_path / "bad.yaml"
    bad.write_text("paths: [unclosed\n")
    monkeypatch.setenv("GPUDECK_SPEC", str(bad))
    code, out, _ = run(["--help"], capsys)
    assert code == 0 and "config" in out and "login" in out
    code, _, err = run(["config", "spec"], capsys)
    assert code == 2 and "cannot load the API spec" in err
    monkeypatch.setenv("GPUDECK_SPEC", str(tmp_path / "missing.yaml"))
    code, _, err = run(["clusters", "list"], capsys)
    assert code == 2
