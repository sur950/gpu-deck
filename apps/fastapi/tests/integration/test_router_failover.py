"""
llama.cpp router child crash -> failover through the whole API layer (FULL-TEST #2).

A fake node proxy + llama.cpp router runs under uvicorn on a real port and serves two
replicas of one deployment as two router children (``qwen-a``, ``qwen-b``). After
``kill -9`` of one child, the real router answers ``400 {"error": {"code": 400,
"message": "model is not loaded"}}`` for that model until the agent's health check
marks the instance unhealthy. On the H200, 30 of 200 client requests failed in that
window; the API layer must now eject the instance and fail over, so every client
request (plain and streaming, concurrency 4) succeeds on the surviving child, while an
ordinary engine 400 is still passed through.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, StreamingResponse
from tests.integration.test_proxy_overhead import _free_port, _Server

pytestmark = [pytest.mark.integration]

CHILDREN = {"qwen-a": True, "qwen-b": True}
SERVED: list = []


def _fake_router() -> FastAPI:
    app = FastAPI()

    @app.post("/instances/{instance_id}/v1/chat/completions")
    async def chat(instance_id: str, request: Request):
        body = await request.json()
        model = body.get("model")
        hdr = {"x-gpudeck-instance": instance_id}
        if body.get("temperature") == 9:
            err = {"error": {"code": 400, "message": "temperature out of range"}}
            return JSONResponse(err, status_code=400, headers=hdr)
        if not CHILDREN.get(model):
            err = {"error": {"code": 400, "message": "model is not loaded", "type": "invalid"}}
            return JSONResponse(err, status_code=400, headers=hdr)
        SERVED.append(model)
        if body.get("stream"):

            async def frames():
                chunk = {
                    "id": "c",
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": "hi"}}],
                }
                yield f"data: {json.dumps(chunk)}\n\n".encode()
                yield b"data: [DONE]\n\n"

            return StreamingResponse(frames(), media_type="text/event-stream", headers=hdr)
        message = {"role": "assistant", "content": "hi"}
        payload = {
            "id": "c",
            "object": "chat.completion",
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        return JSONResponse(payload, headers=hdr)

    return app


def _table(base: str) -> dict:
    def inst(iid: str, served: str) -> dict:
        return {
            "instance_id": iid,
            "node_id": "node-1",
            "proxy_url": base,
            "served_model_name": served,
            "engine": "llama_cpp",
        }

    return {
        "version": 1,
        "models": {
            "qwen3-0.6b": {
                "type": "chat",
                "deployment_id": None,
                "traffic": {"strategy": "round_robin", "failover": True, "max_retries": 1},
                "instances": [inst("inst-a", "qwen-a"), inst("inst-b", "qwen-b")],
            }
        },
        "nodes": {},
        "ca_bundle_pem": None,
    }


async def test_router_child_gone_fails_over_through_api_layer(monkeypatch):
    from app.api_layer_main import app as serving_app
    from app.core.settings import settings
    from app.services.serving import auth, upstream
    from app.services.serving.auth import ApiKeyContext
    from app.services.serving.routing import get_routing_table

    port = _free_port()
    router = _Server(_fake_router(), port)
    router.start_and_wait()
    monkeypatch.setattr(settings, "SERVING_UPSTREAM_VERIFY_TLS", False)
    upstream.set_transport_factory(None)
    table = get_routing_table()
    saved = table.to_dict()
    table.load_from_dict(_table(f"http://127.0.0.1:{port}"))
    auth.set_verifiers_for_tests(lambda key: ApiKeyContext(id="k", name="t"), None)

    class Allowed:
        allowed = True
        reason = "test"

    async def allow(ip, scope):
        return Allowed()

    monkeypatch.setattr("app.middlewares.ip_rules.check_ip", allow)
    monkeypatch.setattr("app.workers.tasks.metering_tasks.enqueue_usage_event", lambda e: None)
    CHILDREN.update({"qwen-a": True, "qwen-b": True})
    SERVED.clear()
    transport = httpx.ASGITransport(app=serving_app)
    headers = {"Authorization": "Bearer gd-test"}
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://api", headers=headers, timeout=20
        ) as client:

            async def one(i: int) -> int:
                body = {
                    "model": "qwen3-0.6b",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": i % 2 == 1,
                }
                r = await client.post("/v1/chat/completions", json=body)
                if body["stream"]:
                    assert b"[DONE]" in r.content
                return r.status_code

            assert [await one(i) for i in range(4)] == [200] * 4
            assert set(SERVED) == {"qwen-a", "qwen-b"}  # both children took traffic

            CHILDREN["qwen-a"] = False  # kill -9 of one child; the router stays up
            SERVED.clear()
            sem = asyncio.Semaphore(4)

            async def limited(i: int) -> int:
                async with sem:
                    return await one(i)

            statuses = await asyncio.gather(*(limited(i) for i in range(40)))
            assert statuses == [200] * 40, statuses
            assert SERVED == ["qwen-b"] * 40
            assert table.is_ejected("inst-a") and not table.is_ejected("inst-b")

            # An ordinary engine 400 is still the client's error, not a failover.
            msgs = [{"role": "user", "content": "hi"}]
            bad = {"model": "qwen3-0.6b", "messages": msgs, "temperature": 9}
            r = await client.post("/v1/chat/completions", json=bad)
            assert r.status_code == 400
            assert "temperature out of range" in r.text
            assert not table.is_ejected("inst-b")

            # Both children gone: a clean 503 model_not_ready, not the router's 400.
            CHILDREN["qwen-b"] = False
            table._ejected.clear()
            r = await client.post(
                "/v1/chat/completions", json={"model": "qwen3-0.6b", "messages": msgs}
            )
            assert r.status_code == 503, r.text
    finally:
        table._ejected.clear()
        table.load_from_dict(saved)
        auth.set_verifiers_for_tests(None, None)
        auth._resolved = False
        await upstream.close_upstream_client()
        upstream.set_transport_factory(None)
        router.stop()
