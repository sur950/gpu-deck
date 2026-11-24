"""
Unit tests: in-memory routing table (strategies, ejection, aliases, type
checks, waiting for a loading instance, failover before first byte), format
resolution / mount prefixes, and in-flight counters.
"""

from __future__ import annotations

import asyncio
from collections import Counter

import httpx
import pytest
from app.services.serving import formats
from app.services.serving.errors import ServingError
from app.services.serving.inflight import InflightTracker
from app.services.serving.routing import RoutingTable, dev_engine_table

pytestmark = pytest.mark.unit


def _inst(iid, weight=100, active=0, state="ready", health="healthy", accepting=True, url=None):
    return {
        "instance_id": iid,
        "node_id": f"node-{iid}",
        "proxy_url": url or f"https://{iid}:9443",
        "served_model_name": "served",
        "state": state,
        "health": health,
        "accepting": accepting,
        "weight": weight,
        "active_sessions": active,
    }


def _table(instances, strategy="least_sessions", mtype="chat", **extra):
    t = RoutingTable()
    t.load_from_dict(
        {
            "version": 3,
            "models": {
                "Qwen3-Test": {
                    "type": mtype,
                    "aliases": ["qwen3"],
                    "deployment_id": "d1",
                    "traffic": {
                        "strategy": strategy,
                        "failover": True,
                        "max_retries": 2,
                    },
                    "instances": instances,
                    **extra,
                }
            },
            "nodes": {"n1": {"status": "online"}},
        }
    )
    return t


def test_resolve_by_name_alias_case_insensitive():
    t = _table([_inst("a")])
    assert t.resolve("qwen3-test", "chat/completions", {}).instance.instance_id == "a"
    assert t.resolve("QWEN3", "messages", {}).model.name == "Qwen3-Test"
    with pytest.raises(ServingError) as exc:
        t.resolve("missing", "chat/completions", {})
    assert exc.value.code == "model_not_found" and exc.value.status_code == 404


def test_type_mismatch():
    t = _table([_inst("a")], mtype="embedding")
    with pytest.raises(ServingError) as exc:
        t.resolve("qwen3", "chat/completions", {})
    assert exc.value.code == "model_type_mismatch"
    assert t.resolve("qwen3", "embeddings", {}).instance.instance_id == "a"


def test_skips_unselectable_and_reports_not_ready():
    t = _table(
        [
            _inst("drain", accepting=False),
            _inst("zero", weight=0),
            _inst("sick", health="unhealthy"),
            _inst("load", state="loading"),
        ]
    )
    with pytest.raises(ServingError) as exc:
        t.resolve("qwen3", "chat/completions", {})
    assert exc.value.code == "no_instance_available"  # ready ones exist but none selectable
    t2 = _table([_inst("load", state="loading")])
    with pytest.raises(ServingError) as exc:
        t2.resolve("qwen3", "chat/completions", {})
    assert exc.value.code == "model_not_ready" and exc.value.headers["Retry-After"]


def test_least_sessions_uses_live_and_inflight_counts():
    t = _table([_inst("a", active=3), _inst("b", active=1)])
    assert t.pick(t.get("qwen3"), {}).instance_id == "b"
    assert t.pick(t.get("qwen3"), {"b": 5}).instance_id == "a"


def test_round_robin_and_weighted():
    t = _table([_inst("a"), _inst("b")], strategy="round_robin")
    picks = [t.pick(t.get("qwen3"), {}).instance_id for _ in range(4)]
    assert picks == ["a", "b", "a", "b"]
    t = _table([_inst("a", weight=900), _inst("b", weight=100)], strategy="weighted")
    counts = Counter(t.pick(t.get("qwen3"), {}).instance_id for _ in range(2000))
    assert counts["a"] > counts["b"] * 4


def test_ejection_expires():
    t = _table([_inst("a"), _inst("b")])
    t.eject("a", seconds=60)
    assert {t.pick(t.get("qwen3"), {}).instance_id for _ in range(5)} == {"b"}
    t.eject("b", seconds=0)
    assert not t.is_ejected("b")


def test_dev_engine_wildcard(monkeypatch):
    from app.core.settings import settings

    monkeypatch.setattr(settings, "SERVING_DEV_ENGINE_URL", "http://127.0.0.1:11434")
    monkeypatch.setattr(settings, "SERVING_DEV_ENGINE_MODELS", "*")
    t = RoutingTable()
    t.load_from_dict(dev_engine_table())
    target = t.resolve("qwen3:0.6b", "chat/completions", {})
    assert target.instance.direct and target.instance.served_model_name == "qwen3:0.6b"
    assert target.instance.engine_url("v1/chat/completions") == (
        "http://127.0.0.1:11434/v1/chat/completions"
    )
    assert t.list_models() == []


def test_proxy_engine_url():
    t = _table([_inst("abc")])
    inst = t.get("qwen3").instances[0]
    assert inst.engine_url("v1/chat/completions") == (
        "https://abc:9443/instances/abc/v1/chat/completions"
    )


@pytest.mark.parametrize(
    "path,headers,expected",
    [
        ("/openai/v1/chat/completions", {}, "openai"),
        ("/anthropic/v1/messages", {}, "anthropic"),
        ("/ollama/api/chat", {}, "ollama"),
        ("/v1/messages", {}, "anthropic"),
        ("/v1/messages/batches/x", {}, "anthropic"),
        ("/v1/chat/completions", {}, "openai"),
        ("/v1/models", {}, "openai"),
        ("/v1/models", {"anthropic-version": "2023-06-01"}, "anthropic"),
        ("/v1/models", {"x-api-key": "k"}, "anthropic"),
        ("/v1/models/x", {"x-api-key": "k", "authorization": "Bearer k"}, "openai"),
        ("/api/tags", {}, "ollama"),
        ("/health", {}, None),
    ],
)
def test_format_for_request(path, headers, expected):
    assert formats.format_for_request(path, headers) == expected


@pytest.mark.parametrize(
    "configured,expected",
    [
        ("/v1", "/openai/v1"),
        ("/openai", "/openai/v1"),
        ("/openai/v1/", "/openai/v1"),
        ("", "/openai/v1"),
    ],
)
def test_explicit_prefix_normalisation(monkeypatch, configured, expected):
    from app.core.settings import settings

    monkeypatch.setattr(settings, "SERVING_OPENAI_PREFIX", configured)
    assert formats.explicit_prefix("openai") == expected


def test_prefix_defaults_for_all_formats():
    assert formats.explicit_prefix("anthropic") == "/anthropic/v1"
    assert formats.explicit_prefix("ollama") == "/ollama/api"


def test_inflight_tracker_counts():
    tracker = InflightTracker()
    assert tracker.acquire("i1") == 0
    assert tracker.acquire("i1") == 1
    tracker.release("i1")
    tracker.release("i1")
    tracker.release("i1")  # extra release never goes negative
    assert tracker.snapshot() == {} and tracker.total == 0


async def test_failover_before_first_byte_ejects_dead_instance(monkeypatch):
    """Connection refused on one instance -> retried on another, dead one ejected."""
    from app.services.serving import upstream
    from app.services.serving.auth import ApiKeyContext
    from app.services.serving.context import ServingContext
    from app.services.serving.pipeline import ProxyEngine
    from starlette.requests import Request

    def handler(request: httpx.Request):
        if request.url.host == "dead":
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={"ok": True, "model": "served"})

    upstream.set_transport_factory(lambda: httpx.MockTransport(handler))
    try:
        engine = ProxyEngine()
        engine.table = _table(
            [
                _inst("dead", url="https://dead:9443", active=0),
                _inst("live", url="https://live:9443", active=5),
            ]
        )
        req = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/v1/chat/completions",
                "headers": [],
            }
        )
        ctx = ServingContext(req, "openai", "chat/completions", ApiKeyContext(id="k", name="k"))
        result = await engine.open(ctx, "v1/chat/completions", {"model": "qwen3", "messages": []})
        data = await result.read_json()
        assert data["ok"] and ctx.target.instance.instance_id == "live"
        assert engine.table.is_ejected("dead")
        assert engine.inflight.total == 0
    finally:
        upstream.set_transport_factory(None)


async def test_llama_router_child_gone_fails_over(monkeypatch):
    """llama.cpp router '400 model is not loaded' (child exited) -> eject + retry elsewhere;
    any other 400 from the engine is passed through untouched."""
    from app.services.serving import upstream
    from app.services.serving.auth import ApiKeyContext
    from app.services.serving.context import ServingContext
    from app.services.serving.pipeline import ProxyEngine
    from starlette.requests import Request

    def handler(request: httpx.Request):
        hdr = {"x-gpudeck-instance": request.url.host}
        if request.url.host == "gone":
            body = {"error": {"code": 400, "message": "model is not loaded"}}
            return httpx.Response(400, json=body, headers=hdr)
        if request.url.host == "bad":
            return httpx.Response(400, json={"error": {"message": "bad request"}}, headers=hdr)
        return httpx.Response(200, json={"ok": True, "model": "served"}, headers=hdr)

    def ctx():
        req = Request(
            {"type": "http", "method": "POST", "path": "/v1/chat/completions", "headers": []}
        )
        return ServingContext(req, "openai", "chat/completions", ApiKeyContext(id="k", name="k"))

    upstream.set_transport_factory(lambda: httpx.MockTransport(handler))
    try:
        engine = ProxyEngine()
        engine.table = _table(
            [
                _inst("gone", url="https://gone:9443", active=0),
                _inst("live", url="https://live:9443", active=5),
            ]
        )
        for inst in engine.table.get("qwen3").instances:
            inst.engine = "llama_cpp"
        c = ctx()
        result = await engine.open(c, "v1/chat/completions", {"model": "qwen3", "messages": []})
        assert (await result.read_json())["ok"] and c.target.instance.instance_id == "live"
        assert engine.table.is_ejected("gone")

        engine.table = _table([_inst("bad", url="https://bad:9443")])
        engine.table.get("qwen3").instances[0].engine = "llama_cpp"
        result = await engine.open(ctx(), "v1/chat/completions", {"model": "qwen3", "messages": []})
        assert result.status_code == 400
        assert (await result.read_json())["error"]["message"] == "bad request"
        assert not engine.table.is_ejected("bad")
        assert engine.inflight.total == 0
    finally:
        upstream.set_transport_factory(None)


async def test_waits_for_loading_instance(monkeypatch):
    from app.core.settings import settings
    from app.services.serving.auth import ApiKeyContext
    from app.services.serving.context import ServingContext
    from app.services.serving.pipeline import ProxyEngine
    from starlette.requests import Request

    monkeypatch.setattr(settings, "SERVING_NO_INSTANCE_WAIT_SECONDS", 3)
    engine = ProxyEngine()
    engine.table = _table([_inst("a", state="loading")])
    req = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [],
        }
    )
    ctx = ServingContext(req, "openai", "chat/completions", ApiKeyContext(id="k", name="k"))

    async def become_ready():
        await asyncio.sleep(0.6)
        engine.table.get("qwen3").instances[0].state = "ready"

    task = asyncio.create_task(become_ready())
    target = await engine.resolve(ctx, "qwen3")
    await task
    assert target.instance.instance_id == "a"
