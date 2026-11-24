"""
Performance check: API-layer proxy overhead on time-to-first-byte.

A fake streaming OpenAI engine, a bare pass-through relay and the serving app run
under uvicorn on random free localhost ports (real sockets, real HTTP). The same
streaming chat request is timed, interleaved, directly against the engine, through
the relay and through the API layer.

- API-layer overhead = p50(API layer) - p50(direct).
- Hop cost = p50(relay) - p50(direct): what one extra Python HTTP hop (uvicorn +
  httpx streaming, no GPU Deck code) costs on this machine *right now*.

The target is < 1.5 ms added at p50 (after the serving middlewares went
pure ASGI). A loaded machine slows every hop alike, so a round also passes when the
API layer costs at most ``HOP_RATIO`` bare hops — measured in the same round, so a real
regression (blocking call, extra round trip, buffering middleware) still fails: it
adds milliseconds to the API layer but not to the relay. A round never passes when
the API layer costs more than ``HARD_CEILING_MS`` (gross regression, any load).

Auth and the IP checker are stubbed (no DB / Redis on the measured path) and
metering enqueue is captured in memory, matching the production hot path
where both are cache hits / off-path.
"""

from __future__ import annotations

import asyncio
import json
import socket
import statistics
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from starlette.responses import StreamingResponse

pytestmark = [pytest.mark.integration, pytest.mark.slow]

N_WARMUP = 30
N_SAMPLES = 300
TARGET_P50_MS = 1.5
HOP_RATIO = 2.5
HARD_CEILING_MS = 10.0
MAX_ROUNDS = 3


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fake_engine() -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()

        async def frames():
            for word in ("Hello", " there", "!"):
                chunk = {
                    "id": "c",
                    "object": "chat.completion.chunk",
                    "model": body["model"],
                    "choices": [{"index": 0, "delta": {"content": word}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}\n\n".encode()
                await asyncio.sleep(0.002)
            if (body.get("stream_options") or {}).get("include_usage"):
                usage = {
                    "id": "c",
                    "choices": [],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 3},
                }
                yield f"data: {json.dumps(usage)}\n\n".encode()
            yield b"data: [DONE]\n\n"

        return StreamingResponse(frames(), media_type="text/event-stream")

    return app


def _relay_app(upstream_base: str):
    """Bare pass-through proxy (pure ASGI + httpx streaming): the cost of one extra hop."""
    state: dict = {}

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        client = state.get("client")
        if client is None:  # created on the server's own loop
            client = state["client"] = httpx.AsyncClient(base_url=upstream_base, timeout=10)
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        payload = json.loads(body)
        payload["model"] = "bench-engine"  # the same body rewrite the API layer does
        request = client.build_request(
            "POST",
            scope["path"],
            content=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
        )
        response = await client.send(request, stream=True)
        try:
            await send(
                {
                    "type": "http.response.start",
                    "status": response.status_code,
                    "headers": [(b"content-type", b"text/event-stream")],
                }
            )
            async for chunk in response.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            await response.aclose()

    return app


def round_passes(direct_p50: float, relay_p50: float, proxied_p50: float) -> bool:
    """Absolute target on an idle machine, else bounded by the same-round hop cost."""
    overhead = proxied_p50 - direct_p50
    hop = max(relay_p50 - direct_p50, 0.0)
    if overhead >= HARD_CEILING_MS:
        return False
    return overhead < TARGET_P50_MS or overhead < HOP_RATIO * hop


def test_round_passes_rules():
    assert round_passes(1.0, 1.4, 2.2)  # 1.2 ms < 1.5 ms target
    assert round_passes(3.0, 5.0, 7.5)  # loaded: 4.5 ms overhead, hop 2.0 ms
    assert not round_passes(1.0, 1.5, 4.0)  # idle hop 0.5 ms, API layer +3 ms: regression
    assert not round_passes(1.0, 9.0, 12.0)  # beyond the hard ceiling


class _Server(threading.Thread):
    def __init__(self, app, port: int) -> None:
        super().__init__(daemon=True)
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            lifespan="off",
            access_log=False,
        )
        self.server = uvicorn.Server(config)

    def run(self) -> None:
        self.server.run()

    def start_and_wait(self) -> None:
        self.start()
        deadline = time.time() + 30  # a loaded CI box can be slow to start uvicorn
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.01)

    def stop(self) -> None:
        self.server.should_exit = True
        self.join(timeout=10)


def _ttfb_ms(client: httpx.Client, url: str, model: str) -> float:
    body = {
        "model": model,
        "stream": True,
        "messages": [{"role": "user", "content": "hi"}],
    }
    start = time.perf_counter()
    with client.stream("POST", url, json=body, headers={"Authorization": "Bearer gd-test"}) as r:
        assert r.status_code == 200
        chunks = r.iter_raw()
        next(chunks)
        first = time.perf_counter()
        for _ in chunks:
            pass
    return (first - start) * 1000


def test_proxy_ttfb_overhead_p50_under_target(monkeypatch):
    from app.api_layer_main import app as serving_app
    from app.services.serving import auth, upstream
    from app.services.serving.auth import ApiKeyContext
    from app.services.serving.routing import get_routing_table

    engine_port, api_port = _free_port(), _free_port()
    upstream.set_transport_factory(None)
    get_routing_table().load_from_dict(
        {
            "version": 1,
            "models": {
                "bench": {
                    "type": "chat",
                    "deployment_id": None,
                    "traffic": {
                        "strategy": "least_sessions",
                        "failover": False,
                        "max_retries": 0,
                    },
                    "instances": [
                        {
                            "instance_id": "bench-1",
                            "node_id": "local",
                            "proxy_url": f"http://127.0.0.1:{engine_port}",
                            "served_model_name": "bench-engine",
                            "direct": True,
                        }
                    ],
                }
            },
        }
    )
    auth.set_verifiers_for_tests(lambda key: ApiKeyContext(id="k", name="bench"), None)

    class Allowed:
        allowed = True
        reason = "bench"

    async def allow(ip, scope):
        return Allowed()

    monkeypatch.setattr("app.middlewares.ip_rules.check_ip", allow)
    captured = []
    monkeypatch.setattr("app.workers.tasks.metering_tasks.enqueue_usage_event", captured.append)

    relay_port = _free_port()
    engine_srv = _Server(_fake_engine(), engine_port)
    relay_srv = _Server(_relay_app(f"http://127.0.0.1:{engine_port}"), relay_port)
    api_srv = _Server(serving_app, api_port)
    engine_srv.start_and_wait()
    relay_srv.start_and_wait()
    api_srv.start_and_wait()
    try:
        direct_url = f"http://127.0.0.1:{engine_port}/v1/chat/completions"
        relay_url = f"http://127.0.0.1:{relay_port}/v1/chat/completions"
        proxy_url = f"http://127.0.0.1:{api_port}/v1/chat/completions"
        with httpx.Client(timeout=10) as client:
            for _ in range(N_WARMUP):
                _ttfb_ms(client, direct_url, "bench-engine")
                _ttfb_ms(client, relay_url, "bench")
                _ttfb_ms(client, proxy_url, "bench")
            rounds = []
            for _ in range(MAX_ROUNDS):
                direct, relayed, proxied = [], [], []
                for _ in range(N_SAMPLES):  # interleaved so drift affects all three equally
                    direct.append(_ttfb_ms(client, direct_url, "bench-engine"))
                    relayed.append(_ttfb_ms(client, relay_url, "bench"))
                    proxied.append(_ttfb_ms(client, proxy_url, "bench"))
                d50, r50, p50 = (statistics.median(x) for x in (direct, relayed, proxied))
                p95 = statistics.quantiles(proxied, n=20)[18]
                rounds.append((round(p50 - d50, 3), round(r50 - d50, 3)))
                print(
                    f"\nTTFB direct p50={d50:.3f} ms | relay p50={r50:.3f} ms | API layer "
                    f"p50={p50:.3f} ms p95={p95:.3f} ms | overhead p50={p50 - d50:.3f} ms "
                    f"(bare hop {r50 - d50:.3f} ms)"
                )
                if round_passes(d50, r50, p50):
                    break
            else:
                pytest.fail(
                    f"proxy TTFB overhead p50 (overhead, bare hop) per round: {rounds}; "
                    f"target < {TARGET_P50_MS} ms or < {HOP_RATIO} x bare hop"
                )
        assert len(captured) >= N_SAMPLES  # metering ran off the hot path for every request
    finally:
        api_srv.stop()
        relay_srv.stop()
        engine_srv.stop()
        auth.set_verifiers_for_tests(None, None)
        auth._resolved = False
        upstream.set_transport_factory(None)
