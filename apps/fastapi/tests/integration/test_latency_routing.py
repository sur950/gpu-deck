"""
Network latency over simulated links (integration: real sockets, Redis, Postgres).

Two fake node proxies (TLS 1.3, HTTP/1.1 keep-alive, asyncio) sit behind latency relays that
add half an RTT per direction per chunk and one RTT before a new connection is relayed, as in
``test_upstream_pooling``: a "near" node at 10 ms and a "far" node at 80 ms. The proxies
answer ``/_health`` at once and engine paths after ``ENGINE_DELAY``, reporting their side in
``x-gpudeck-proxy-timing`` like the Rust agent. No GPU and no real agent: the fakes stand in
for both, which is what these tests are about (the network between the API layer and nodes).

Checks:

- the RTT prober measures each path (warm connection, not the handshake);
- routing picks the near node under equal load and spills to the far one under load;
- ``Server-Timing`` values add up (upstream ~ RTT + engine, net ~ RTT, connect 0 on a warm
  connection and > 0 on a cold one) and the node's header is not passed on;
- RTT and per-region request counts reach Redis, the admin API's live view and history
  (scratch schema), and placement then prefers the node near the traffic;
- the request-timing aggregates and the inference series labels of an external deployment.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ssl
import time
import uuid
from typing import Dict, List, Optional

import asyncpg
import pytest
import pytest_asyncio
from app.core.settings import settings
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.asyncio(loop_scope="session"),
]

NEAR_RTT = 0.010
FAR_RTT = 0.080
ENGINE_DELAY = 0.030
NEAR = str(uuid.uuid5(uuid.NAMESPACE_DNS, "latency-near"))
FAR = str(uuid.uuid5(uuid.NAMESPACE_DNS, "latency-far"))


def _ssl_ctx(tmp_path) -> ssl.SSLContext:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "node-test")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(hours=1))
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "proxy.pem", tmp_path / "proxy.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_cert_chain(str(cert_path), str(key_path))
    return ctx


class FakeNodeProxy:
    """Keep-alive HTTP/1.1 over TLS: /_health at once, engine paths after ``delay``."""

    def __init__(self, ssl_ctx: ssl.SSLContext, delay: float = ENGINE_DELAY) -> None:
        self.ssl_ctx = ssl_ctx
        self.delay = delay
        self.connections = 0
        self.requests: List[str] = []
        self.server: Optional[asyncio.base_events.Server] = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0, ssl=self.ssl_ctx)
        return self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                started = time.perf_counter()
                lines = head.decode().split("\r\n")
                path = lines[0].split(" ")[1]
                length = 0
                for line in lines[1:]:
                    if line.lower().startswith("content-length:"):
                        length = int(line.split(":", 1)[1])
                if length:
                    await reader.readexactly(length)
                extra = b""
                if path == "/_health":
                    body = b'{"status":"ok"}'
                else:
                    self.requests.append(path)
                    engine_started = time.perf_counter()
                    await asyncio.sleep(self.delay)
                    engine_ms = (time.perf_counter() - engine_started) * 1000
                    node_ms = (time.perf_counter() - started) * 1000
                    body = b'{"id":"x","object":"chat.completion","choices":[]}'
                    extra = (
                        b"x-gpudeck-instance: %s\r\n"
                        b"x-gpudeck-proxy-timing: queue;dur=0.010, engine;dur=%.3f, "
                        b"node;dur=%.3f\r\n" % (path.split("/")[2].encode(), engine_ms, node_ms)
                    )
                writer.write(
                    b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n%s"
                    b"content-length: %d\r\n\r\n%s" % (extra, len(body), body)
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass
        finally:
            writer.close()

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()


class LatencyRelay:
    """TCP relay adding ``rtt``/2 per direction and one ``rtt`` per new connection."""

    def __init__(self, upstream_port: int, rtt: float) -> None:
        self.upstream_port = upstream_port
        self.rtt = rtt
        self.server: Optional[asyncio.base_events.Server] = None
        self.tasks: set = set()

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _pipe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        queue: asyncio.Queue = asyncio.Queue()

        async def deliver() -> None:
            while True:
                due, data = await queue.get()
                if data is None:
                    writer.close()
                    return
                delay = due - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                writer.write(data)
                await writer.drain()

        sender = asyncio.ensure_future(deliver())
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                queue.put_nowait((time.monotonic() + self.rtt / 2, data))
        except ConnectionError:
            pass
        finally:
            queue.put_nowait((0, None))
            try:
                await sender
            except Exception:  # noqa: BLE001
                pass

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(self.rtt)
        up_reader, up_writer = await asyncio.open_connection("127.0.0.1", self.upstream_port)
        for coro in (self._pipe(reader, up_writer), self._pipe(up_reader, writer)):
            task = asyncio.ensure_future(coro)
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


def _table_dict(near_base: str, far_base: str, deployment_id: str) -> dict:
    return {
        "version": 1,
        "models": {
            "m": {
                "type": "chat",
                "deployment_id": deployment_id,
                "traffic": {"strategy": "least_sessions", "failover": True, "max_retries": 1},
                "instances": [
                    {
                        "instance_id": "i-far",
                        "node_id": FAR,
                        "proxy_url": far_base,
                        "served_model_name": "m",
                        "engine": "vllm",
                    },
                    {
                        "instance_id": "i-near",
                        "node_id": NEAR,
                        "proxy_url": near_base,
                        "served_model_name": "m",
                        "engine": "vllm",
                    },
                ],
            }
        },
        "nodes": {
            NEAR: {"name": "near-node", "proxy_url": near_base, "status": "online"},
            FAR: {"name": "far-node", "proxy_url": far_base, "status": "online"},
        },
        "ca_bundle_pem": None,
    }


def _ctx():
    from app.services.serving.context import ServingContext

    request = type("R", (), {"state": type("S", (), {})(), "headers": {}, "client": None})()
    return ServingContext(
        request=request, fmt="openai", endpoint="chat/completions", api_key=None
    )  # type: ignore[arg-type]


def _parse_server_timing(value: str) -> Dict[str, float]:
    out = {}
    for item in value.split(","):
        name, _, rest = item.strip().partition(";")
        if rest.startswith("dur="):
            out[name] = float(rest[4:])
    return out


class Links:
    """The two simulated nodes plus a fresh RTT tracker and routing table."""

    def __init__(self) -> None:
        self.deployment_id = str(uuid.uuid4())

    async def start(self, tmp_path, monkeypatch) -> "Links":
        from app.services.serving import latency, upstream
        from app.services.serving.routing import RoutingTable

        monkeypatch.setattr(settings, "SERVING_UPSTREAM_VERIFY_TLS", False)
        monkeypatch.setattr(settings, "SERVING_SERVER_TIMING", "generic")
        monkeypatch.setattr(settings, "SERVING_LATENCY_ROUTING", True)
        monkeypatch.setattr(settings, "SERVING_LATENCY_LOAD_PENALTY_MS", 200.0)
        monkeypatch.setattr(settings, "SERVING_LATENCY_DEFAULT_CAPACITY", 8)
        monkeypatch.setattr(latency, "_tracker", latency.LatencyTracker())
        upstream.set_transport_factory(None)
        ctx = _ssl_ctx(tmp_path)
        self.near = FakeNodeProxy(ctx)
        self.far = FakeNodeProxy(ctx)
        self.near_relay = LatencyRelay(await self.near.start(), NEAR_RTT)
        self.far_relay = LatencyRelay(await self.far.start(), FAR_RTT)
        self.near_base = f"https://127.0.0.1:{await self.near_relay.start()}"
        self.far_base = f"https://127.0.0.1:{await self.far_relay.start()}"
        self.table = RoutingTable()
        self.table.load_from_dict(_table_dict(self.near_base, self.far_base, self.deployment_id))
        self.prober = latency.LatencyProber(self.table)
        return self

    def engine(self):
        from app.services.serving.inflight import InflightTracker
        from app.services.serving.pipeline import ProxyEngine

        engine = ProxyEngine()
        engine.table = self.table
        engine.inflight = InflightTracker()
        return engine

    async def stop(self) -> None:
        from app.services.serving import upstream

        await self.prober.stop()
        await upstream.close_upstream_client()
        upstream.set_transport_factory(None)
        for relay in (self.near_relay, self.far_relay):
            await relay.stop()
        for proxy in (self.near, self.far):
            await proxy.stop()


@pytest_asyncio.fixture(loop_scope="session")
async def links(tmp_path, monkeypatch):
    env = await Links().start(tmp_path, monkeypatch)
    try:
        yield env
    finally:
        await env.stop()


async def test_probes_measure_each_path_and_routing_prefers_the_near_node(links: Links):
    from app.services.serving.latency import get_latency_tracker, rtt_ms

    await links.prober.probe_all()
    await links.prober.probe_all()
    near, far = rtt_ms(NEAR), rtt_ms(FAR)
    print(f"\nprobed RTT near {near:.1f} ms, far {far:.1f} ms")
    # One round trip each (the relay adds rtt/2 per direction); the first probe's connect
    # and TLS handshake (several RTT) are not part of the sample.
    assert NEAR_RTT * 1000 * 0.8 <= near < NEAR_RTT * 1000 + 30
    assert FAR_RTT * 1000 * 0.8 <= far < FAR_RTT * 1000 + 40
    assert get_latency_tracker().modes[NEAR] == "direct"

    engine = links.engine()
    targets = []
    for _ in range(5):
        ctx = _ctx()
        result = await engine.open(ctx, "v1/chat/completions", {"model": "m", "messages": []})
        assert "x-gpudeck-proxy-timing" not in result.headers  # consumed, not passed on
        await result.read_bytes()
        targets.append(ctx.target.instance.node_id)
    assert set(targets) == {NEAR}
    assert len(links.far.requests) == 0

    # Server-Timing of the last request: warm connection, values add up.
    st = _parse_server_timing(ctx.response_headers()["Server-Timing"])
    print(f"Server-Timing {st}")
    assert set(st) >= {"route", "connect", "rtt", "net", "engine", "upstream", "total"}
    assert st["connect"] == 0.0
    assert st["route"] < 5
    assert ENGINE_DELAY * 1000 * 0.9 <= st["engine"] < ENGINE_DELAY * 1000 + 40
    assert st["upstream"] >= st["engine"] + NEAR_RTT * 1000 * 0.8
    assert NEAR_RTT * 1000 * 0.8 <= st["net"] < NEAR_RTT * 1000 + 30
    assert abs(st["rtt"] - near) < 15
    assert st["total"] >= st["upstream"]
    for secret in ("127.0.0.1", "near-node", NEAR):
        assert secret not in ctx.response_headers()["Server-Timing"]

    # The breakdown kept with the request (usage metadata) has ttft and total too.
    record = ctx.timing_breakdown()
    assert record["ttft_ms"] >= record["upstream_ms"] - 1


async def test_routing_spills_to_the_far_node_under_load(links: Links):
    await links.prober.probe_all()
    await links.prober.probe_all()
    links.near.delay = links.far.delay = 0.4  # long requests: they overlap
    engine = links.engine()

    async def one() -> str:
        ctx = _ctx()
        result = await engine.open(ctx, "v1/chat/completions", {"model": "m", "messages": []})
        await result.read_bytes()
        return ctx.target.instance.node_id

    tasks = []
    for _ in range(10):
        tasks.append(asyncio.ensure_future(one()))
        await asyncio.sleep(0.005)  # requests arrive one after another
    nodes = await asyncio.gather(*tasks)
    near, far = nodes.count(NEAR), nodes.count(FAR)
    print(f"\n10 overlapping requests: near {near}, far {far}")
    assert nodes[0] == NEAR and nodes[1] == NEAR and nodes[2] == NEAR
    assert far >= 2 and near > far


async def test_cold_connection_shows_connect_time(links: Links):
    from app.services.serving import upstream

    await links.prober.probe_all()
    await upstream.close_upstream_client()  # drop the warm pools, keep the RTT
    ctx = _ctx()
    result = await links.engine().open(ctx, "v1/chat/completions", {"model": "m", "messages": []})
    await result.read_bytes()
    st = _parse_server_timing(ctx.response_headers()["Server-Timing"])
    assert ctx.target.instance.node_id == NEAR
    assert st["connect"] >= NEAR_RTT * 1000 * 0.8  # relay connect + TLS handshake
    assert st["net"] < st["connect"] + NEAR_RTT * 1000 + 30


# ---------------------------------------------------------------------------
# Redis view, admin API and placement (scratch schema + scratch Redis prefix)
# ---------------------------------------------------------------------------


def _dsn() -> str:
    return settings.get_database_url().replace("postgresql+asyncpg://", "postgresql://", 1)


@pytest_asyncio.fixture(loop_scope="session")
async def scratch():
    from app.db.postgres import connection
    from app.db.redis import client as redis_client_module
    from app.db.redis.cache_service import CacheService
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    from tests.schema_helpers import create_scratch_schema, drop_scratch_schema

    tag = uuid.uuid4().hex[:10]
    schema = f"lattest_{tag}"
    prefix = f"gpu-deck:lattest:{tag}:"
    conn = await asyncpg.connect(_dsn())
    try:
        await create_scratch_schema(conn, schema)
    finally:
        await conn.close()
    engine = create_async_engine(
        settings.get_database_url(),
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema}},
    )
    saved = {"engine": connection.engine, "prefix": settings.REDIS_KEY_PREFIX}
    connection.engine = engine
    settings.REDIS_KEY_PREFIX = prefix
    await CacheService.reset()
    await redis_client_module.close_redis()

    async def sql(query: str, *args):
        c = await asyncpg.connect(_dsn(), server_settings={"search_path": schema})
        try:
            return await c.fetch(query, *args)
        finally:
            await c.close()

    try:
        cluster = uuid.uuid4()
        await sql("INSERT INTO clusters (id, name) VALUES ($1, 'latency')", cluster)
        for node_id, name in ((NEAR, "near-node"), (FAR, "far-node")):
            await sql(
                "INSERT INTO nodes (id, cluster_id, name, status, os, arch) "
                "VALUES ($1, $2, $3, 'online', 'linux', 'x86_64')",
                uuid.UUID(node_id),
                cluster,
                name,
            )
        yield sql
    finally:
        try:
            redis = await redis_client_module.get_redis()
            keys = [k async for k in redis.scan_iter(match=f"{prefix}*", count=500)]
            if keys:
                await redis.delete(*keys)
        finally:
            await CacheService.reset()
            await redis_client_module.close_redis()
            connection.engine = saved["engine"]
            settings.REDIS_KEY_PREFIX = saved["prefix"]
            await engine.dispose()
            conn = await asyncpg.connect(_dsn())
            try:
                await drop_scratch_schema(conn, schema)
            finally:
                await conn.close()


async def test_rtt_and_traffic_reach_the_admin_api_and_placement(
    links: Links, scratch, monkeypatch
):
    from app.db.postgres.connection import get_session_maker
    from app.services.latency import store
    from app.services.latency.placement import load_context
    from app.services.latency.service import LatencyService, purge, snapshot_to_db
    from app.services.scheduler.core import DeviceView, NodeView, PlacementRequest, place
    from sqlalchemy import text

    monkeypatch.setattr(settings, "API_LAYER_ID", "edge-test-1")
    monkeypatch.setattr(settings, "API_LAYER_REGION", "region-a")
    engine = links.engine()
    await links.prober.probe_all()
    for _ in range(3):
        ctx = _ctx()
        result = await engine.open(ctx, "v1/chat/completions", {"model": "m", "messages": []})
        await result.read_bytes()
    await links.prober.run_once()  # probe + publish RTT and the request counts

    live = await store.read_live()
    assert live["api_layers"]["edge-test-1"]["region"] == "region-a"
    assert set(live["api"]["edge-test-1"]) == {NEAR, FAR}
    assert await store.read_traffic(links.deployment_id) == {"region-a": 3}
    assert await store.read_traffic(None) == {"region-a": 3}
    # The hub publishes its ping / pong RTT the same way.
    await store.publish_hub(FAR, {"rtt_ms": 41.0, "jitter_ms": 2.0, "ts": time.time()})

    async with get_session_maker()() as session:
        service = LatencyService(session)
        view = await service.live()
        by_node = {n["node_id"]: n for n in view["nodes"]}
        assert by_node[NEAR]["node_name"] == "near-node"
        layer = by_node[NEAR]["api_layers"][0]
        assert layer["api_layer_id"] == "edge-test-1" and layer["region"] == "region-a"
        assert layer["mode"] == "direct" and layer["rtt_ms"] < by_node[FAR]["best_rtt_ms"]
        assert by_node[FAR]["hub"]["rtt_ms"] == 41.0 and by_node[NEAR]["hub"] is None
        one = await service.live(uuid.UUID(NEAR))
        assert [n["node_id"] for n in one["nodes"]] == [NEAR]

        # Two minutes of history.
        assert await snapshot_to_db(session) == 3
        now = dt.datetime.now(dt.timezone.utc)
        from app.services.latency.service import snapshot_rows

        await session.execute(
            text(
                "INSERT INTO node_latency_samples (ts, node_id, source, api_layer_id, region, "
                "mode, rtt_ms) VALUES (:ts, :node, 'api_layer', 'edge-test-1', 'region-a', "
                "'direct', 30.0)"
            ),
            {"ts": now - dt.timedelta(minutes=3), "node": uuid.UUID(NEAR)},
        )
        assert len(snapshot_rows(await store.read_live())) == 3
        await session.commit()
        history = await service.history([uuid.UUID(NEAR)], "-15m", None, "1m")
        rtt = [s for s in history["series"] if s["metric"] == "rtt_ms"]
        assert len(rtt) == 1 and rtt[0]["labels"]["node"] == "near-node"
        assert rtt[0]["labels"]["region"] == "region-a"
        assert sum(1 for _, v in rtt[0]["points"] if v is not None) == 2
        summary = history["summaries"][0]
        assert summary["points"] == 2 and summary["max_ms"] >= 30.0
        assert summary["min_ms"] <= summary["p50_ms"] <= summary["p95_ms"] <= summary["max_ms"]
        assert await purge(session, now + dt.timedelta(days=30)) == 4
        await session.commit()

    # Placement: the deployment's traffic comes from region-a, near is close to it.
    context = await load_context(uuid.UUID(links.deployment_id))
    assert context is not None and context.source == "deployment" and context.active

    def node(node_id: str, name: str) -> NodeView:
        nid = uuid.UUID(node_id)
        dev = DeviceView(uuid.uuid5(nid, "gpu0"), nid, 0, "nvidia", 80 << 30)
        return NodeView(id=nid, name=name, devices=[dev])

    nodes = [node(FAR, "a-far"), node(NEAR, "b-near")]  # far wins a tie on name
    req = PlacementRequest(
        need_bytes_per_device=10 << 30, deployment_id=uuid.UUID(links.deployment_id)
    )
    assert place(nodes, req).assignments[0].node_id == uuid.UUID(FAR)
    req.latency = context
    plan = place(nodes, req)
    assert plan.assignments[0].node_id == uuid.UUID(NEAR)
    assert "region-a 100%" in plan.assignments[0].reasons[0]


async def test_request_timing_aggregates_and_external_series_labels(scratch):
    from app.db.postgres.connection import get_session_maker
    from app.services.latency.service import LatencyService
    from app.services.metrics.service import MetricsService

    now = dt.datetime.now(dt.timezone.utc)
    for i, (ttft, net) in enumerate(((60.0, 10.0), (100.0, 30.0))):
        await scratch(
            "INSERT INTO usage_events (ts, request_id, model_name, format, endpoint, stream, "
            "status, http_status, latency_ms, ttft_ms, metadata) VALUES "
            "($1, $2, 'm', 'openai', 'chat/completions', true, 'success', 200, 900, $3, "
            "$4::jsonb)",
            now - dt.timedelta(minutes=2 + i),
            uuid.uuid4(),
            int(ttft),
            '{"timing": {"route_ms": 0.2, "queue_ms": 0, "connect_ms": 0, "rtt_ms": %s, '
            '"net_ms": %s, "engine_ms": 20, "upstream_ms": %s, "ttft_ms": %s, '
            '"total_ms": 900}, "api_layer": {"id": "edge-1", "region": "region-a"}}'
            % (net, net, net + 25, ttft),
        )
    async with get_session_maker()() as session:
        data = await LatencyService(session).request_timing({}, "-30m", None, "5m")
        summary = data["summary"]
        assert summary["requests"] == 2
        assert summary["net_ms"] == pytest.approx(20.0)
        assert summary["ttft_p50_ms"] == pytest.approx(80.0)
        # The parts of the time to first byte add up (per request, so on average too).
        assert summary["part_first_token_ms"] == pytest.approx(((60 - 35.2) + (100 - 55.2)) / 2)
        assert summary["part_node_ms"] == pytest.approx(5.0)  # upstream - net - engine
        parts = sum(v for k, v in summary.items() if k.startswith("part_"))
        assert parts == pytest.approx(summary["ttft_ms"])
        assert data["regions"][0]["region"] == "region-a" and data["regions"][0]["requests"] == 2
        requests = next(s for s in data["series"] if s["metric"] == "requests")
        assert sum(v for _, v in requests["points"] if v) == 2
        filtered = await LatencyService(session).request_timing(
            {"region": "elsewhere"}, "-30m", None, "5m"
        )
        assert filtered["summary"]["requests"] == 0

    # Inference series of an observed (external) deployment carry its name.
    node, dep, inst = uuid.UUID(NEAR), uuid.uuid4(), uuid.uuid4()
    await scratch(
        "INSERT INTO deployments (id, name, origin, status) "
        "VALUES ($1, 'main@near-node', 'external', 'running')",
        dep,
    )
    await scratch(
        "INSERT INTO instances (id, deployment_id, node_id, served_model_name, state, origin) "
        "VALUES ($1, $2, $3, 'qwen-test', 'ready', 'external')",
        inst,
        dep,
        node,
    )
    series = [{"labels": {"instance_id": str(inst), "deployment_id": str(dep)}}]
    async with get_session_maker()() as session:
        await MetricsService(session)._label_instances(series)
    labels = series[0]["labels"]
    assert labels["deployment"] == "main@near-node"
    assert labels["model"] == "qwen-test" and labels["served_model"] == "qwen-test"
    assert labels["node"] == "near-node"


# ---------------------------------------------------------------------------
# Agent hub: WebSocket ping / pong over a simulated link
# ---------------------------------------------------------------------------


async def test_hub_measures_agent_rtt_with_websocket_pings(monkeypatch):
    """A real uvicorn server with the hub's protocol class, an agent-like client behind a
    50 ms relay: the hub's keep-alive pings yield the RTT (no protocol frame needed)."""
    import socket

    import uvicorn
    import websockets
    from app.services.agent_hub import latency as hub_latency
    from app.services.agent_hub.ws_protocol import MTLSWebSocketProtocol

    node = str(uuid.uuid4())
    # Plain ws here (no client certificate): stand in for the certificate CN.
    monkeypatch.setattr(hub_latency, "node_id_from_der", lambda der: node)
    published: Dict[str, dict] = {}

    async def fake_publish(node_id, snapshot):
        published[node_id] = snapshot

    async def fake_drop(node_id):
        published.pop(node_id, None)

    monkeypatch.setattr("app.services.latency.store.publish_hub", fake_publish)
    monkeypatch.setattr("app.services.latency.store.drop_hub", fake_drop)

    async def app(scope, receive, send):
        if scope["type"] != "websocket":
            return
        await receive()
        await send({"type": "websocket.accept"})
        while True:
            message = await receive()
            if message["type"] == "websocket.disconnect":
                return

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        ws=MTLSWebSocketProtocol,
        ws_ping_interval=0.2,
        ws_ping_timeout=2.0,
        lifespan="off",
        log_level="warning",
    )
    server = uvicorn.Server(config)
    serve = asyncio.ensure_future(server.serve())
    relay = None
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.02)
        relay = LatencyRelay(port, 0.05)
        relay_port = await relay.start()
        async with websockets.connect(f"ws://127.0.0.1:{relay_port}/agent/v1/ws"):
            await asyncio.sleep(1.6)
            stats = hub_latency.stats(node)
            assert stats is not None and stats.samples >= 3
            print(f"\nhub RTT over a 50 ms link: {stats.ewma_ms:.1f} ms ({stats.samples} pings)")
            assert 45.0 <= stats.ewma_ms < 120.0
            assert published[node]["rtt_ms"] == pytest.approx(stats.ewma_ms, abs=0.01)
        for _ in range(50):
            if node not in published:
                break
            await asyncio.sleep(0.02)
        assert hub_latency.stats(node) is None and node not in published  # forgotten on close
    finally:
        server.should_exit = True
        await asyncio.wait_for(serve, 5)
        if relay is not None:
            await relay.stop()
