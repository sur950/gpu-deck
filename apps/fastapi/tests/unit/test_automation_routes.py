"""
Unit tests: every admin.yaml operation of the Rules / Layouts / Swaps /
Alerts tags is mounted, and a read-only route works end to end.
"""

import uuid
from pathlib import Path
from types import SimpleNamespace

import yaml
from app.main import app as admin_app
from app.middlewares.session_verification import get_current_admin

ADMIN_YAML = Path(__file__).resolve().parents[4] / "libs/contracts/openapi/admin.yaml"
AUTOMATION_PREFIXES = (
    "/rules",
    "/rule-runs",
    "/layouts",
    "/swaps",
    "/alert-channels",
    "/alert-events",
)


def test_every_automation_contract_operation_is_mounted():
    spec = yaml.safe_load(ADMIN_YAML.read_text())
    wanted = {
        (method.upper(), path)
        for path, item in spec["paths"].items()
        if path.startswith(AUTOMATION_PREFIXES)
        for method in item
        if method in ("get", "post", "patch", "put", "delete")
    }
    mounted = {
        (method.upper(), path.removeprefix("/api/v1"))
        for path, item in admin_app.openapi()["paths"].items()
        for method in item
    }
    assert wanted, "no automation operations found in admin.yaml"
    assert wanted - mounted == set()


def test_rule_schemas_route(test_client):
    admin_app.dependency_overrides[get_current_admin] = lambda: SimpleNamespace(id=uuid.uuid4())
    resp = test_client.get("/api/v1/rules/schemas")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert set(body["data"]["params"]) >= {"idle_unload", "auto_heal"}
    assert body["data"]["layout"]["title"] == "GPU layout"


def test_automation_routes_require_auth(test_client):
    resp = test_client.get("/api/v1/rules")
    assert resp.status_code in (401, 503)
