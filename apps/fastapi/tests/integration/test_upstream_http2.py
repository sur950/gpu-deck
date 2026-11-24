"""
API layer -> node proxy over HTTP/2 (ALPN ``h2``) with HTTP/1.1 fallback.

A fake node proxy (asyncio + ``h2``, TLS 1.3, mTLS against a throwaway internal CA,
node certificate ``CN=<node id>, O=gpudeck-nodes``) answers on HTTP/2 when ALPN picks
``h2`` and on HTTP/1.1 otherwise, like the agent's ``proxy/mod.rs``. Checks:

- the per-node pool negotiates h2 through the real ``NodeProxySSLContext`` (chain + CN
  check + client certificate) and multiplexes concurrent requests on one connection;
- pre-warming opens a single multiplexed connection, the serving pipeline reuses it and
  SSE chunks stream through;
- a proxy that only offers ``http/1.1`` (older agents) and ``SERVING_UPSTREAM_HTTP2=false``
  both fall back to HTTP/1.1 keep-alive;
- a connection the proxy closed with GOAWAY while idle (the agent's unanswered keep-alive
  ping) is replaced transparently: the next request succeeds on a new connection.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ssl
import time
import uuid
from typing import Dict, List, Optional, Tuple

import h2.config
import h2.connection
import h2.events
import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

pytestmark = [pytest.mark.integration]

DELAY = 0.25  # server-side think time per request
SSE = [b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n', b"data: [DONE]\n\n"]


# ---------------------------------------------------------------------------
# PKI: throwaway CA, node server certificate, API-layer client certificate
# ---------------------------------------------------------------------------


def _write(path, data: bytes) -> str:
    path.write_bytes(data)
    return str(path)


def _key_pem(key) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def _pki(tmp_path) -> Dict[str, str]:
    from app.services.pki.ca import NODE_ORG
    from app.services.pki.proxy_tls import NODE_URI_PREFIX

    now = dt.datetime.now(dt.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-internal-ca")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    def leaf(subject: x509.Name, san: Optional[x509.SubjectAlternativeName]):
        key = ec.generate_private_key(ec.SECP256R1())
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=1))
            .not_valid_after(now + dt.timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        )
        if san is not None:
            builder = builder.add_extension(san, critical=False)
        return builder.sign(ca_key, hashes.SHA256()), key

    node_id = str(uuid.uuid4())
    node_cert, node_key = leaf(
        x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, node_id),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, NODE_ORG),
            ]
        ),
        x509.SubjectAlternativeName(
            [x509.UniformResourceIdentifier(f"{NODE_URI_PREFIX}{node_id}")]
        ),
    )
    client_cert, client_key = leaf(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "api-layer")]), None
    )
    pem = serialization.Encoding.PEM
    return {
        "node_id": node_id,
        "ca": _write(tmp_path / "ca.pem", ca.public_bytes(pem)),
        "node_cert": _write(tmp_path / "node.pem", node_cert.public_bytes(pem)),
        "node_key": _write(tmp_path / "node.key", _key_pem(node_key)),
        "client_cert": _write(tmp_path / "client.pem", client_cert.public_bytes(pem)),
        "client_key": _write(tmp_path / "client.key", _key_pem(client_key)),
    }


def _server_ctx(pki: Dict[str, str], alpn: List[str]) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_cert_chain(pki["node_cert"], pki["node_key"])
    ctx.load_verify_locations(cafile=pki["ca"])
    ctx.verify_mode = ssl.CERT_REQUIRED  # mTLS like the agent proxy
    ctx.set_alpn_protocols(alpn)
    return ctx


# ---------------------------------------------------------------------------
# Fake node proxy speaking h2 or HTTP/1.1
# ---------------------------------------------------------------------------


class FakeNodeProxy:
    def __init__(self, ssl_ctx: ssl.SSLContext) -> None:
        self.ssl_ctx = ssl_ctx
        self.protocols: List[str] = []  # negotiated protocol per accepted connection
        self.paths: List[str] = []
        self.active = 0
        self.max_active = 0
        self.server: Optional[asyncio.base_events.Server] = None
        self._h2: List[Tuple[h2.connection.H2Connection, asyncio.StreamWriter]] = []
        self._tasks: set = set()

    @property
    def connections(self) -> int:
        return len(self.protocols)

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0, ssl=self.ssl_ctx)
        return self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    def goaway_all(self) -> None:
        """Close every h2 connection the way hyper does after an unanswered keep-alive ping."""
        for conn, writer in self._h2:
            conn.close_connection()
            writer.write(conn.data_to_send())
            writer.close()
        self._h2.clear()

    def _body(self, path: str) -> List[bytes]:
        if path == "/_health":
            return [b'{"status":"ok"}']
        if path.endswith("/stream"):
            return list(SSE)
        return [b'{"choices":[]}']

    async def _work(self, path: str) -> None:
        self.paths.append(path)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if path != "/_health":
                await asyncio.sleep(DELAY)
        finally:
            self.active -= 1

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        proto = writer.get_extra_info("ssl_object").selected_alpn_protocol() or "http/1.1"
        self.protocols.append(proto)
        try:
            if proto == "h2":
                await self._serve_h2(reader, writer)
            else:
                await self._serve_h1(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass
        finally:
            writer.close()

    async def _serve_h1(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
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
            await self._work(path)
            body = b"".join(self._body(path))
            writer.write(
                b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n"
                b"content-length: %d\r\n\r\n%s" % (len(body), body)
            )
            await writer.drain()

    async def _serve_h2(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = h2.connection.H2Connection(
            config=h2.config.H2Configuration(client_side=False, header_encoding="utf-8")
        )
        conn.initiate_connection()
        writer.write(conn.data_to_send())
        self._h2.append((conn, writer))
        paths: Dict[int, str] = {}

        async def respond(stream_id: int, path: str) -> None:
            await self._work(path)
            chunks = self._body(path)
            ctype = "text/event-stream" if len(chunks) > 1 else "application/json"
            conn.send_headers(stream_id, [(":status", "200"), ("content-type", ctype)])
            writer.write(conn.data_to_send())
            for i, chunk in enumerate(chunks):
                conn.send_data(stream_id, chunk, end_stream=i == len(chunks) - 1)
                writer.write(conn.data_to_send())
                await writer.drain()
                if i < len(chunks) - 1:
                    await asyncio.sleep(0.02)

        while True:
            data = await reader.read(65536)
            if not data:
                return
            for event in conn.receive_data(data):
                if isinstance(event, h2.events.RequestReceived):
                    paths[event.stream_id] = dict(event.headers)[":path"]
                elif isinstance(event, h2.events.DataReceived):
                    conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                elif isinstance(event, h2.events.StreamEnded):
                    task = asyncio.ensure_future(respond(event.stream_id, paths[event.stream_id]))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
                elif isinstance(event, h2.events.ConnectionTerminated):
                    return
            writer.write(conn.data_to_send())
            await writer.drain()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _table(proxy_url: str, node_id: str) -> dict:
    return {
        "version": 1,
        "models": {
            "m": {
                "type": "chat",
                "deployment_id": None,
                "traffic": {"strategy": "least_sessions", "failover": False, "max_retries": 0},
                "instances": [
                    {
                        "instance_id": "i-1",
                        "node_id": node_id,
                        "proxy_url": proxy_url,
                        "served_model_name": "m",
                        "engine": "vllm",
                    }
                ],
            }
        },
        "nodes": {node_id: {"proxy_url": proxy_url}},
        "ca_bundle_pem": None,
    }


@pytest.fixture
def pki(tmp_path, monkeypatch):
    from app.core.settings import settings
    from app.services.serving import upstream

    paths = _pki(tmp_path)
    monkeypatch.setattr(settings, "SERVING_UPSTREAM_VERIFY_TLS", True)
    monkeypatch.setattr(settings, "SERVING_UPSTREAM_HTTP2", True)
    monkeypatch.setattr(settings, "API_LAYER_CA_BUNDLE_PATH", paths["ca"])
    monkeypatch.setattr(settings, "API_LAYER_CLIENT_CERT_PATH", paths["client_cert"])
    monkeypatch.setattr(settings, "API_LAYER_CLIENT_KEY_PATH", paths["client_key"])
    upstream.set_transport_factory(None)
    yield paths
    upstream.set_transport_factory(None)


async def _post(client: httpx.AsyncClient, url: str) -> httpx.Response:
    resp = await client.post(url, json={"model": "m", "messages": []}, timeout=10)
    assert resp.status_code == 200
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_h2_negotiated_and_multiplexed(pki, monkeypatch):
    from app.core.settings import settings
    from app.services.serving import upstream
    from app.services.serving.context import ServingContext
    from app.services.serving.pipeline import ProxyEngine
    from app.services.serving.routing import RoutingTable

    monkeypatch.setattr(settings, "SERVING_UPSTREAM_WARM_CONNECTIONS", 2)
    proxy = FakeNodeProxy(_server_ctx(pki, ["h2", "http/1.1"]))
    base = f"https://127.0.0.1:{await proxy.start()}"
    url = f"{base}/instances/i-1/v1/chat/completions"
    table = RoutingTable()
    warmer = upstream.PoolWarmer(table)
    try:
        table.on_update(warmer.on_table_update)
        table.load_from_dict(_table(base, pki["node_id"]))
        await asyncio.gather(*list(warmer._pending))
        # Two warm requests, one multiplexed h2 connection (verified chain + CN + mTLS).
        assert proxy.protocols == ["h2"]
        assert proxy.paths == ["/_health", "/_health"]

        client = upstream.get_upstream_client(None, url)
        start = time.perf_counter()
        responses = await asyncio.gather(*(_post(client, url) for _ in range(8)))
        elapsed = time.perf_counter() - start
        assert {r.http_version for r in responses} == {"HTTP/2"}
        assert proxy.connections == 1
        assert proxy.max_active == 8  # all eight in flight at once on one connection
        assert elapsed < 4 * DELAY, elapsed

        # The serving pipeline uses the same pool; SSE chunks stream through h2.
        engine = ProxyEngine()
        engine.table = table
        request = type("R", (), {"state": type("S", (), {})(), "headers": {}})()
        ctx = ServingContext(
            request=request, fmt="openai", endpoint="chat/completions", api_key=None
        )  # type: ignore[arg-type]
        result = await engine.open(ctx, "v1/stream", {"model": "m", "stream": True}, stream=True)
        chunks = [chunk async for chunk in result.aiter_raw()]
        assert b"".join(chunks) == b"".join(SSE)
        assert result.response.http_version == "HTTP/2"
        assert proxy.connections == 1

        # Keep-warm rides the same connection.
        await warmer.keep_warm_once()
        assert proxy.connections == 1
    finally:
        await warmer.stop()
        await upstream.close_upstream_client()
        await proxy.stop()


async def test_falls_back_to_http1_when_proxy_has_no_h2(pki):
    from app.services.serving import upstream

    proxy = FakeNodeProxy(_server_ctx(pki, ["http/1.1"]))
    base = f"https://127.0.0.1:{await proxy.start()}"
    url = f"{base}/instances/i-1/v1/chat/completions"
    try:
        client = upstream.get_upstream_client(None, url)
        responses = [await _post(client, url) for _ in range(3)]
        assert {r.http_version for r in responses} == {"HTTP/1.1"}
        assert proxy.protocols == ["http/1.1"]  # keep-alive reuse still applies
    finally:
        await upstream.close_upstream_client()
        await proxy.stop()


async def test_http2_setting_off_uses_http1(pki, monkeypatch):
    from app.core.settings import settings
    from app.services.serving import upstream

    monkeypatch.setattr(settings, "SERVING_UPSTREAM_HTTP2", False)
    upstream.set_transport_factory(None)
    proxy = FakeNodeProxy(_server_ctx(pki, ["h2", "http/1.1"]))
    base = f"https://127.0.0.1:{await proxy.start()}"
    url = f"{base}/instances/i-1/v1/chat/completions"
    try:
        resp = await _post(upstream.get_upstream_client(None, url), url)
        assert resp.http_version == "HTTP/1.1"
        assert proxy.protocols == ["http/1.1"]
        async with upstream.scoped_upstream_client() as job_client:
            assert (await _post(job_client, url)).http_version == "HTTP/1.1"
    finally:
        await upstream.close_upstream_client()
        await proxy.stop()


async def test_jobs_client_uses_h2(pki):
    from app.services.serving import upstream

    proxy = FakeNodeProxy(_server_ctx(pki, ["h2", "http/1.1"]))
    base = f"https://127.0.0.1:{await proxy.start()}"
    url = f"{base}/instances/i-1/v1/chat/completions"
    try:
        async with upstream.scoped_upstream_client() as job_client:
            assert upstream.get_upstream_client(None, url) is job_client
            assert (await _post(job_client, url)).http_version == "HTTP/2"
    finally:
        await proxy.stop()


async def test_goaway_on_idle_connection_is_retried_on_a_new_one(pki):
    from app.services.serving import upstream

    proxy = FakeNodeProxy(_server_ctx(pki, ["h2", "http/1.1"]))
    base = f"https://127.0.0.1:{await proxy.start()}"
    url = f"{base}/instances/i-1/v1/chat/completions"
    try:
        client = upstream.get_upstream_client(None, url)
        assert (await _post(client, url)).http_version == "HTTP/2"
        proxy.goaway_all()  # idle connection closed by the proxy
        await asyncio.sleep(0.05)
        resp = await _post(client, url)
        assert resp.http_version == "HTTP/2"
        assert proxy.connections == 2
    finally:
        await upstream.close_upstream_client()
        await proxy.stop()


def test_http2_needs_the_h2_package(monkeypatch):
    from app.core.settings import settings
    from app.services.serving import upstream

    monkeypatch.setattr(settings, "SERVING_UPSTREAM_HTTP2", True)
    assert upstream.http2_enabled() is True
    monkeypatch.setattr(upstream.importlib.util, "find_spec", lambda name: None)
    assert upstream.http2_enabled() is False
    monkeypatch.setattr(settings, "SERVING_UPSTREAM_HTTP2", False)
    assert upstream.http2_enabled() is False
