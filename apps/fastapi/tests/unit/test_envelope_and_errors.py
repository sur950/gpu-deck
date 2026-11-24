"""
Response envelope, admin error handlers and serving-format error shapes.
"""

import pytest
from app.core.exceptions import (
    ResourceNotFoundError,
    serving_error_body,
    serving_format_for_path,
)
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


@pytest.mark.unit
def test_success_response_envelope():
    body = success_response(data={"a": 1}, meta=paginated_meta(total=45, page=2, page_size=20))
    assert body == {
        "success": True,
        "data": {"a": 1},
        "error": None,
        "meta": {"total": 45, "page": 2, "page_size": 20, "has_more": True},
    }


@pytest.mark.unit
def test_handle_exceptions_wraps_and_maps_errors():
    app = FastAPI()
    router = APIRouter()

    @router.get("/plain")
    @handle_exceptions
    async def plain():
        return {"x": 1}

    @router.get("/missing")
    @handle_exceptions
    async def missing():
        raise ResourceNotFoundError(resource_type="Node")

    @router.get("/boom")
    @handle_exceptions
    async def boom():
        raise RuntimeError("kaboom")

    app.include_router(router)
    client = TestClient(app)

    assert client.get("/plain").json() == {
        "success": True,
        "data": {"x": 1},
        "error": None,
        "meta": None,
    }

    r = client.get("/missing")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert r.json()["error"]["message"] == "Node not found"

    r = client.get("/boom")
    assert r.status_code == 500
    assert r.json()["success"] is False
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"


@pytest.mark.unit
def test_serving_format_detection():
    assert serving_format_for_path("/v1/chat/completions") == "openai"
    assert serving_format_for_path("/anthropic/v1/messages") == "anthropic"
    assert serving_format_for_path("/api/chat") == "ollama"
    assert serving_format_for_path("/health") is None
    # Explicit prefixes (serving.md §1) and bare-path disambiguation (§2).
    assert serving_format_for_path("/openai/v1/chat/completions") == "openai"
    assert serving_format_for_path("/ollama/api/chat") == "ollama"
    assert serving_format_for_path("/v1/messages") == "anthropic"
    assert serving_format_for_path("/v1/messages/count_tokens") == "anthropic"
    assert serving_format_for_path("/v1/models") == "openai"
    assert serving_format_for_path("/v1/models", {"anthropic-version": "2023-06-01"}) == "anthropic"
    assert serving_format_for_path("/v1/models", {"x-api-key": "gd-x"}) == "anthropic"
    assert (
        serving_format_for_path("/v1/models", {"x-api-key": "gd-x", "authorization": "Bearer y"})
        == "openai"
    )


@pytest.mark.unit
def test_serving_error_bodies_match_wire_formats():
    assert serving_error_body("openai", 401, "bad key") == {
        "error": {
            "message": "bad key",
            "type": "api_error",
            "code": "authentication_error",
        }
    }
    assert serving_error_body("anthropic", 429, "slow down") == {
        "type": "error",
        "error": {"type": "rate_limit_error", "message": "slow down"},
    }
    assert serving_error_body("ollama", 404, "model not found") == {"error": "model not found"}


@pytest.mark.unit
def test_serving_app_returns_format_specific_errors(serving_client):
    r = serving_client.get("/v1/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["type"] == "api_error"

    r = serving_client.get("/anthropic/v1/does-not-exist")
    assert r.status_code == 404
    assert r.json() == {
        "type": "error",
        "error": {"type": "not_found_error", "message": "Not Found"},
    }

    r = serving_client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert r.json() == {"error": "Not Found"}


@pytest.mark.unit
def test_admin_app_returns_envelope_errors(test_client):
    r = test_client.get("/api/v1/does-not-exist")
    assert r.status_code == 404
    assert r.json()["success"] is False
    assert r.json()["error"]["code"] == "HTTP_ERROR"
    assert "X-Request-ID" in r.headers
    assert r.headers["X-Content-Type-Options"] == "nosniff"
