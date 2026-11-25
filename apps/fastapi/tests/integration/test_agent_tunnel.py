"""
API layer <- agent outbound tunnel (agent protocol §13), real sockets, no database.

A fake agent (asyncio TLS client + ``h2`` server state machine, like the agent's
``tunnel.rs``) dials the API layer's ``TunnelServer`` with a node certificate from a throwaway
internal CA. Checks:

- the tunnel is authenticated (node certificate, ALPN h2), probed and registered;
- the serving upstream client routes ``tunnel://<node>`` URLs over it, SSE frames stream
  through unbuffered and concurrent requests are multiplexed on the one connection;
- a services certificate, a certificate from another CA, no certificate and a revoked node
  certificate are all refused before anything is registered;
- a dead tunnel is dropped by the keep-alive probe and requests then fail with
  ``httpx.ConnectError`` (what the serving pipeline fails over on).
"""

from __future__ import annotations

import asyncio
import hashlib
import ssl
import time
import uuid
from typing import Dict, Optional, Tuple

import h2.config
import h2.connection
import h2.events
import httpx
import pytest
from cryptography.hazmat.primitives import serialization

pytestmark = [pytest.mark.integration]

GAP = 0.2


def _write(path, data: bytes) -> str:
    path.write_bytes(data)
    return str(path)


def _key_pem(key) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def _pki(tmp_path) -> Dict[str, str]:
    from app.services.pki import ca as cax

    ca_key, ca_cert = cax.generate_ca("ecdsa_p256")
    other_key, other_cert = cax.generate_ca("ecdsa_p256")
    node_id = str(uuid.uuid4())
    out: Dict[str, str] = {"node_id": node_id}
    out["ca"] = _write(tmp_path / "ca.pem", ca_cert.public_bytes(serialization.Encoding.PEM))

    def issue(name, signer, csr_key, kind):
        key = cax.generate_private_key("ecdsa_p256")
        if kind == "node":
            cert = cax.sign_node_csr(
                signer[0], signer[1], cax.build_csr(key, "pending", cax.NODE_ORG), node_id, 30
            )
        else:
            cert = cax.sign_service_csr(
                signer[0],
                signer[1],
                cax.build_csr(key, "api-layer", cax.SERVICE_ORG),
                "api_layer",
                None,
                [],
                30,
            )
        out[f"{name}_crt"] = _write(
            tmp_path / f"{name}.crt", cert.public_bytes(serialization.Encoding.PEM)
        )
        out[f"{name}_key"] = _write(tmp_path / f"{name}.key", _key_pem(key))
        out[f"{name}_der_sha"] = hashlib.sha256(
            cert.public_bytes(serialization.Encoding.DER)
        ).hexdigest()

    issue("api", (ca_key, ca_cert), None, "service")
    issue("node", (ca_key, ca_cert), None, "node")
    issue("svc", (ca_key, ca_cert), None, "service")
    issue("foreign", (other_key, other_cert), None, "node")
    return out


class FakeAgent:
    """Dials the tunnel and serves HTTP/2 on the client socket (the agent's role)."""

    def __init__(self, port: int, ca: str, cert: Optional[str], key: Optional[str]) -> None:
        self.port, self.ca, self.cert, self.key = port, ca, cert, key
        self.requests = 0
        self.closed = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    async def start(self) -> None:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.check_hostname = False
        ctx.load_verify_locations(self.ca)
        if self.cert:
            ctx.load_cert_chain(self.cert, self.key)
        ctx.set_alpn_protocols(["h2"])
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port, ssl=ctx)
        self._writer = writer
        self._task = asyncio.create_task(self._serve(reader, writer))

    async def stop(self) -> None:
        if self._writer is not None:
            self._writer.close()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _serve(self, reader, writer) -> None:
        conn = h2.connection.H2Connection(h2.config.H2Configuration(client_side=False))
        conn.initiate_connection()
        writer.write(conn.data_to_send())
        paths: Dict[int, str] = {}

        def flush() -> None:
            data = conn.data_to_send()
            if data:
                writer.write(data)

        async def respond(sid: int, path: str) -> None:
            self.requests += 1
            if path == "/_health":
                conn.send_headers(sid, [(":status", "200"), ("content-type", "application/json")])
                conn.send_data(sid, b'{"status":"ok"}', end_stream=True)
                flush()
                return
            conn.send_headers(sid, [(":status", "200"), ("content-type", "text/event-stream")])
            flush()
            for i in range(3):
                conn.send_data(sid, f"data: {i}\n\n".encode())
                flush()
                await writer.drain()
                await asyncio.sleep(GAP)
            conn.send_data(sid, b"data: [DONE]\n\n", end_stream=True)
            flush()

        try:
            while True:
                data = await reader.read(65535)
                if not data:
                    break
                for ev in conn.receive_data(data):
                    if isinstance(ev, h2.events.RequestReceived):
                        headers = {
                            k.decode() if isinstance(k, bytes) else k: v for k, v in ev.headers
                        }
                        p = headers.get(":path")
                        paths[ev.stream_id] = p.decode() if isinstance(p, bytes) else p
                    elif isinstance(ev, h2.events.DataReceived):
                        conn.acknowledge_received_data(ev.flow_controlled_length, ev.stream_id)
                    if getattr(ev, "stream_ended", None) is not None or isinstance(
                        ev, h2.events.StreamEnded
                    ):
                        sid = ev.stream_id
                        if sid in paths:
                            asyncio.create_task(respond(sid, paths.pop(sid)))
                flush()
                await writer.drain()
        except (ConnectionError, ssl.SSLError, OSError):
            pass
        finally:
            self.closed.set()


@pytest.fixture
async def tunnel(tmp_path):
    from app.services.serving import upstream
    from app.services.serving.tunnel import TunnelRegistry, TunnelServer, server_ssl_context

    pki = _pki(tmp_path)
    ctx = server_ssl_context(pki["api_crt"], pki["api_key"], open(pki["ca"]).read())
    registry = TunnelRegistry()
    server = TunnelServer(registry)
    port = await server.start(host="127.0.0.1", ports=[0], ctx=ctx)
    assert port
    saved_registry = upstream_registry_swap(registry)
    upstream.set_transport_factory(None)
    try:
        yield server, registry, pki, port
    finally:
        upstream_registry_swap(saved_registry)
        await server.stop()
        await upstream.close_upstream_client()


def upstream_registry_swap(registry):
    from app.services.serving import tunnel as tunnel_mod

    saved = tunnel_mod._registry
    tunnel_mod._registry = registry
    return saved


async def _wait_registered(registry, node_id: str, n: int = 1, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while registry.counts().get(node_id, 0) < n:
        assert time.monotonic() < deadline, f"tunnel not registered: {registry.counts()}"
        await asyncio.sleep(0.02)


async def _stream(client: httpx.AsyncClient, url: str) -> Tuple[float, float, bytes]:
    t0 = time.monotonic()
    first = None
    body = b""
    async with client.stream("POST", url, content=b'{"stream":true}') as r:
        assert r.status_code == 200
        async for chunk in r.aiter_raw():
            if chunk and first is None:
                first = time.monotonic() - t0
            body += chunk
    return first or 0.0, time.monotonic() - t0, body


async def test_tunnel_carries_streamed_and_multiplexed_requests(tunnel):
    from app.services.serving.upstream import get_upstream_client

    server, registry, pki, port = tunnel
    node = pki["node_id"]
    agent = FakeAgent(port, pki["ca"], pki["node_crt"], pki["node_key"])
    await agent.start()
    try:
        await _wait_registered(registry, node)
        client = get_upstream_client(None, f"tunnel://{node}")
        url = f"tunnel://{node}/instances/i-1/v1/chat/completions"
        ttfb, total, body = await _stream(client, url)
        assert body.endswith(b"data: [DONE]\n\n") and b"data: 2" in body
        assert ttfb < GAP, f"first SSE frame must not wait for the stream ({ttfb:.3f}s)"
        assert total >= 3 * GAP
        # Eight concurrent streams share the one tunnel connection.
        t0 = time.monotonic()
        results = await asyncio.gather(*(_stream(client, url) for _ in range(8)))
        assert all(r[2].endswith(b"[DONE]\n\n") for r in results)
        assert time.monotonic() - t0 < 3 * GAP * 3, "requests were multiplexed, not serialised"
        assert len(registry.connections(node)) == 1
        assert registry.connections(node)[0].active == 0, "in-flight slots released"
    finally:
        await agent.stop()


async def test_tunnel_refuses_every_identity_but_a_valid_node(tunnel):
    from app.services.serving.routing import get_routing_table

    server, registry, pki, port = tunnel
    node = pki["node_id"]
    cases = [
        ("services certificate", pki["svc_crt"], pki["svc_key"], "node_identity_rejected"),
        ("other CA", pki["foreign_crt"], pki["foreign_key"], "tls_handshake_failed"),
        ("no certificate", None, None, "tls_handshake_failed"),
    ]
    for what, cert, key, reason in cases:
        before = registry.rejected_total.get(reason, 0)
        agent = FakeAgent(port, pki["ca"], cert, key)
        try:
            await agent.start()
            await asyncio.wait_for(agent.closed.wait(), 5)
        except (ssl.SSLError, ConnectionError, OSError, asyncio.TimeoutError):
            pass
        finally:
            await agent.stop()
        deadline = time.monotonic() + 5
        while registry.rejected_total.get(reason, 0) <= before:
            assert time.monotonic() < deadline, f"{what}: not rejected ({registry.rejected_total})"
            await asyncio.sleep(0.02)
        assert registry.counts() == {}, what
    # A revoked node certificate (routing table CRL) is refused too.
    table = get_routing_table()
    saved = table.crl
    table.crl = {"fingerprints_sha256": [pki["node_der_sha"]]}
    try:
        before = registry.rejected_total.get("node_identity_rejected", 0)
        agent = FakeAgent(port, pki["ca"], pki["node_crt"], pki["node_key"])
        await agent.start()
        await asyncio.wait_for(agent.closed.wait(), 5)
        await agent.stop()
        assert registry.rejected_total["node_identity_rejected"] == before + 1
        assert node not in registry.counts()
    finally:
        table.crl = saved


async def test_dead_tunnel_is_dropped_and_requests_fail_over(tunnel):
    from app.services.serving.upstream import get_upstream_client

    server, registry, pki, port = tunnel
    node = pki["node_id"]
    agent = FakeAgent(port, pki["ca"], pki["node_crt"], pki["node_key"])
    await agent.start()
    await _wait_registered(registry, node)
    await agent.stop()
    await asyncio.sleep(0.1)
    await server.keepalive_once()
    assert node not in registry.counts()
    client = get_upstream_client(None, f"tunnel://{node}")
    with pytest.raises(httpx.ConnectError):
        await client.post(f"tunnel://{node}/instances/i-1/v1/chat/completions", content=b"{}")


async def test_revoking_the_node_certificate_closes_its_tunnels(tunnel):
    from app.services.serving.routing import RoutingTable

    server, registry, pki, port = tunnel
    node = pki["node_id"]
    agent = FakeAgent(port, pki["ca"], pki["node_crt"], pki["node_key"])
    await agent.start()
    try:
        await _wait_registered(registry, node)
        table = RoutingTable()
        table.load_from_dict({"version": 1, "models": {}, "nodes": {}, "crl": {}})
        server.on_table_update(table)
        assert node in registry.counts(), "an unrelated table update keeps the tunnel"
        table.crl = {"fingerprints_sha256": [pki["node_der_sha"]]}
        server.on_table_update(table)
        assert node not in registry.counts()
        await asyncio.wait_for(agent.closed.wait(), 5)
    finally:
        await agent.stop()
