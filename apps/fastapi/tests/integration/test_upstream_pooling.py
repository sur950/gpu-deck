"""
API layer -> node proxy connection reuse over a high-latency link (FULL-TEST #15).

A fake node proxy (TLS 1.3, HTTP/1.1 keep-alive, asyncio) sits behind a latency
relay that adds ``RTT`` per round trip: half an RTT per direction per chunk and one
RTT before a new connection is relayed (the SSH channel open of ``ssh -L``). A new
connection therefore costs connect + TLS handshake + request ~ 3 RTT; a reused one
~ 1 RTT. Over the 295 ms RTT link of the H200 test, TTFT went from 118 ms direct to
900 ms through the API layer because idle connections were dropped after httpx's
default 5 s keep-alive.

Checks: the per-node pool is pre-warmed when the node appears in the routing table,
requests (also after > 5 s idle) cost ~1 RTT and open no new connection, the pipeline
uses the per-node pool, and nodes leaving the table lose their pool. The default
httpx client is measured side by side as the baseline.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ssl
import time
from typing import List, Optional

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

pytestmark = [pytest.mark.integration, pytest.mark.slow]

RTT = 0.12


def _self_signed(tmp_path) -> ssl.SSLContext:
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


class FakeProxy:
    """Minimal keep-alive HTTP/1.1 server over TLS; counts accepted connections."""

    def __init__(self, ssl_ctx: ssl.SSLContext) -> None:
        self.ssl_ctx = ssl_ctx
        self.connections = 0
        self.paths: List[str] = []
        self.server: Optional[asyncio.base_events.Server] = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0, ssl=self.ssl_ctx)
        return self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                lines = head.decode().split("\r\n")
                path = lines[0].split(" ")[1]
                length = 0
                for line in lines[1:]:
                    if line.lower().startswith("content-length:"):
                        length = int(line.split(":", 1)[1])
                if length:
                    await reader.readexactly(length)
                self.paths.append(path)
                body = b'{"status":"ok"}' if path == "/_health" else b'{"choices":[]}'
                writer.write(
                    b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n"
                    b"x-gpudeck-instance: i-1\r\ncontent-length: %d\r\n\r\n%s" % (len(body), body)
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
    """TCP relay adding RTT/2 per direction and one RTT per new connection."""

    def __init__(self, upstream_port: int) -> None:
        self.upstream_port = upstream_port
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
                queue.put_nowait((time.monotonic() + RTT / 2, data))
        except ConnectionError:
            pass
        finally:
            queue.put_nowait((0, None))
            try:
                await sender
            except Exception:  # noqa: BLE001
                pass

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(RTT)  # channel open over the slow link
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


async def _timed_post(client: httpx.AsyncClient, url: str) -> float:
    start = time.perf_counter()
    resp = await client.post(url, json={"model": "m", "messages": []}, timeout=10)
    assert resp.status_code == 200
    return time.perf_counter() - start


def _table(proxy_url: str, instance_id: str = "i-1") -> dict:
    return {
        "version": 1,
        "models": {
            "m": {
                "type": "chat",
                "deployment_id": None,
                "traffic": {"strategy": "least_sessions", "failover": False, "max_retries": 0},
                "instances": [
                    {
                        "instance_id": instance_id,
                        "node_id": "node-test",
                        "proxy_url": proxy_url,
                        "served_model_name": "m",
                        "engine": "vllm",
                    }
                ],
            }
        },
        "nodes": {},
        "ca_bundle_pem": None,
    }


async def test_node_pool_reused_over_high_latency_link(tmp_path, monkeypatch):
    from app.core.settings import settings
    from app.services.serving import upstream
    from app.services.serving.context import ServingContext
    from app.services.serving.pipeline import ProxyEngine
    from app.services.serving.routing import RoutingTable

    monkeypatch.setattr(settings, "SERVING_UPSTREAM_VERIFY_TLS", False)
    monkeypatch.setattr(settings, "SERVING_UPSTREAM_WARM_CONNECTIONS", 2)
    upstream.set_transport_factory(None)

    proxy = FakeProxy(_self_signed(tmp_path))
    relay = LatencyRelay(await proxy.start())
    base = f"https://127.0.0.1:{await relay.start()}"
    url = f"{base}/instances/i-1/v1/chat/completions"
    table = RoutingTable()
    warmer = upstream.PoolWarmer(table)
    baseline = httpx.AsyncClient(verify=False)  # httpx defaults: 5 s keep-alive expiry
    try:
        table.on_update(warmer.on_table_update)
        table.load_from_dict(_table(base))
        await asyncio.gather(*list(warmer._pending))
        assert proxy.connections == 2  # pre-warmed through /_health
        assert proxy.paths == ["/_health", "/_health"]

        client = upstream.get_upstream_client(None, url)
        assert client is upstream.get_upstream_client(None, base + "/other")  # one pool per node
        warm = [await _timed_post(client, url) for _ in range(5)]
        cold = await _timed_post(baseline, url)  # first request of a fresh client
        assert proxy.connections == 3  # pooled requests opened nothing new
        assert max(warm) < 1.8 * RTT, warm
        assert cold > 2.5 * RTT, cold

        await asyncio.sleep(5.3)  # longer than httpx's default keepalive_expiry
        after_idle = await _timed_post(client, url)
        baseline_after_idle = await _timed_post(baseline, url)
        assert after_idle < 1.8 * RTT, after_idle
        assert baseline_after_idle > 2.5 * RTT, baseline_after_idle  # the H200 symptom
        assert proxy.connections == 4  # only the baseline reconnected
        print(
            f"\nRTT {RTT * 1000:.0f} ms: pooled {after_idle * 1000:.0f} ms after idle, "
            f"default httpx {baseline_after_idle * 1000:.0f} ms (fresh connection)"
        )

        # The serving pipeline goes through the same per-node pool.
        engine = ProxyEngine()
        engine.table = table
        request = type("R", (), {"state": type("S", (), {})(), "headers": {}})()
        ctx = ServingContext(
            request=request, fmt="openai", endpoint="chat/completions", api_key=None
        )  # type: ignore[arg-type]
        start = time.perf_counter()
        result = await engine.open(ctx, "v1/chat/completions", {"model": "m", "messages": []})
        await result.read_bytes()
        assert time.perf_counter() - start < 1.8 * RTT
        assert proxy.connections == 4

        # A node that leaves the routing table loses its pool.
        table.load_from_dict(_table("https://127.0.0.2:1", "i-2"))
        assert upstream.origin_of(base) not in upstream.get_pools().clients
        for task in list(warmer._pending):
            task.cancel()
    finally:
        await warmer.stop()
        await baseline.aclose()
        await upstream.close_upstream_client()
        upstream.set_transport_factory(None)
        await relay.stop()
        await proxy.stop()


async def test_keep_warm_replaces_a_killed_connection(tmp_path, monkeypatch):
    """A connection dropped by a middlebox is replaced by the keep-warm ping, not a request."""
    from app.core.settings import settings
    from app.services.serving import upstream
    from app.services.serving.routing import RoutingTable

    monkeypatch.setattr(settings, "SERVING_UPSTREAM_VERIFY_TLS", False)
    monkeypatch.setattr(settings, "SERVING_UPSTREAM_WARM_CONNECTIONS", 1)
    upstream.set_transport_factory(None)
    proxy = FakeProxy(_self_signed(tmp_path))
    relay = LatencyRelay(await proxy.start())
    base = f"https://127.0.0.1:{await relay.start()}"
    table = RoutingTable()
    warmer = upstream.PoolWarmer(table)
    try:
        table.on_update(warmer.on_table_update)
        table.load_from_dict(_table(base))
        await asyncio.gather(*list(warmer._pending))
        assert proxy.connections == 1
        for task in list(relay.tasks):  # the tunnel drops every open connection
            task.cancel()
        await asyncio.sleep(0.05)
        await warmer.keep_warm_once()
        assert proxy.connections == 2
        client = upstream.get_upstream_client(None, base)
        elapsed = await _timed_post(client, f"{base}/instances/i-1/v1/chat/completions")
        assert elapsed < 1.8 * RTT, elapsed
        assert proxy.connections == 2
    finally:
        await warmer.stop()
        await upstream.close_upstream_client()
        upstream.set_transport_factory(None)
        await relay.stop()
        await proxy.stop()
