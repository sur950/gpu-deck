"""
Middleware registry and the request body limit.
"""

import pytest
from app.middlewares import discover_middleware_modules


@pytest.mark.unit
def test_core_middlewares_discovered_in_order():
    admin = [name for _, name, _ in discover_middleware_modules("admin")]
    assert admin[:3] == ["core_middlewares", "gzip_middleware", "request_limits"]
    serving = [name for _, name, _ in discover_middleware_modules("serving")]
    assert "core_middlewares" in serving


@pytest.mark.unit
def test_request_body_limit_on_serving_api(serving_client, app_settings):
    too_big = app_settings.MAX_REQUEST_BODY_BYTES + 1
    r = serving_client.post(
        "/v1/chat/completions",
        headers={"content-length": str(too_big), "content-type": "application/json"},
    )
    assert r.status_code == 413
    assert r.json()["error"]["type"] == "api_error"


@pytest.mark.unit
def test_request_body_limit_on_admin_api(test_client, app_settings):
    too_big = app_settings.ADMIN_MAX_REQUEST_BODY_BYTES + 1
    r = test_client.post(
        "/api/v1/anything",
        headers={"content-length": str(too_big), "content-type": "application/json"},
    )
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "REQUEST_TOO_LARGE"


@pytest.mark.unit
def test_root_and_metrics(test_client, serving_client):
    assert test_client.get("/").json()["process"] == "admin"
    assert serving_client.get("/").json()["process"] == "serving"
    assert "http_requests_total" in test_client.get("/metrics").text
    assert test_client.get("/api/v1/health/live").json() == {"status": "ok"}
    assert serving_client.get("/health/live").json() == {"status": "ok"}
