"""
Route wiring: every fleet / metrics / logs / traces / PKI operation from
admin.yaml is mounted with the documented method, and all of them refuse
unauthenticated callers (no DB needed: the auth dependency fails first).
"""

import pytest
import yaml
from app.main import app

pytestmark = pytest.mark.unit

FLEET_TAGS = {
    "Clusters",
    "Nodes",
    "Devices",
    "Topology",
    "Metrics",
    "Logs",
    "Traces",
    "PKI",
    "Live",
}


def _contract_ops():
    from pathlib import Path

    spec_path = Path(__file__).resolve().parents[4] / "libs/contracts/openapi/admin.yaml"
    spec = yaml.safe_load(spec_path.read_text())
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if (
                method in ("get", "post", "put", "patch", "delete")
                and set(op.get("tags", [])) & FLEET_TAGS
            ):
                yield method.upper(), "/api/v1" + path


def _mounted():
    out = set()
    for path, item in app.openapi()["paths"].items():
        for method in item:
            out.add((method.upper(), path))
    return out


def test_every_fleet_operation_is_mounted():
    mounted = _mounted()
    missing = [
        (method, path)
        for method, path in _contract_ops()
        if not path.endswith("/live/ws") and (method, path) not in mounted
    ]
    assert not missing, missing


def test_live_ws_requires_a_session(test_client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc:
        with test_client.websocket_connect("/api/v1/live/ws") as ws:
            ws.receive_text()
    assert exc.value.code == 4401


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v1/clusters"),
        ("GET", "/api/v1/nodes"),
        ("POST", "/api/v1/nodes/join-tokens"),
        ("GET", "/api/v1/devices"),
        ("GET", "/api/v1/topology"),
        ("GET", "/api/v1/metrics/overview"),
        ("GET", "/api/v1/metrics/gpu"),
        ("GET", "/api/v1/logs"),
        ("GET", "/api/v1/traces"),
        ("GET", "/api/v1/pki/ca"),
    ],
)
def test_fleet_routes_require_admin(test_client, method, path):
    response = test_client.request(method, path, json={} if method == "POST" else None)
    assert response.status_code in (401, 403), response.text
