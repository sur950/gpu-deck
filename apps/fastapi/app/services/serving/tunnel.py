"""
Outbound serving tunnels, API-layer side (protocol.md §13).

In the default serving mode (``tunnel``) a GPU machine listens on no port. Its agent dials
this listener (``AGENT_TUNNEL_BIND:AGENT_TUNNEL_PORT``, one port per worker process, see
below) over TLS 1.3 with its node certificate; the API layer presents its own certificate (the
key every agent pins) and requires a node certificate chained to the internal CA, with
``CN = <node id>``, ``O = gpudeck-nodes``, the matching ``URI:gpudeck://node/<id>`` SAN and no
CRL entry. ALPN must be ``h2``. On that connection the roles flip: the API layer is the
HTTP/2 client, the agent serves the same handler as its direct-mode proxy.

- ``TunnelRegistry`` keeps every authenticated connection per node; requests pick the open
  connection with the fewest in-flight streams. No open tunnel → ``httpx.ConnectError``, so
  the serving pipeline fails over / ejects exactly as with an unreachable direct proxy.
- The routing table addresses tunnel nodes as ``tunnel://<node id>``;
  ``upstream.get_upstream_client`` hands such origins a client built on ``TunnelTransport``
  (responses stream frame by frame, nothing is buffered).
- A keep-alive probe (``GET /_health``) runs on every connection every
  ``AGENT_TUNNEL_KEEPALIVE_SECONDS``; a connection that fails it is closed (the agent redials)
  and the probe is what keeps the agent's idle timer from closing a quiet tunnel.
- Unauthenticated handshakes are capped (``AGENT_TUNNEL_MAX_PENDING_HANDSHAKES``) and time out
  after 10 s; rejected peers are counted in ``gpudeck_serving_tunnel_rejected_total``.

Worker processes: every worker of a multi-worker API layer binds its own port from
``AGENT_TUNNEL_PORT`` .. ``AGENT_TUNNEL_PORT + AGENT_TUNNEL_LISTENERS - 1`` and the hub lists
every port as an endpoint, so each agent keeps tunnels to every worker (a worker can only send
requests over tunnels it accepted).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import ssl
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, List, Optional, Tuple

import httpcore
import httpx
from app.core.logger import get_logger
from app.core.settings import settings
from httpx._transports.default import AsyncResponseStream, map_httpcore_exceptions

logger = get_logger(__name__)

TUNNEL_SCHEME = "tunnel"
HANDSHAKE_TIMEOUT_SECONDS = 10.0
PROBE_TIMEOUT_SECONDS = 5.0


def tunnel_node(url: Optional[str]) -> Optional[str]:
    """Node id of a ``tunnel://<node id>[/...]`` URL, else None."""
    if not url or not url.startswith(f"{TUNNEL_SCHEME}://"):
        return None
    rest = url[len(TUNNEL_SCHEME) + 3 :]
    node = rest.split("/", 1)[0].strip()
    return node or None


def _origin(node_id: str) -> httpcore.Origin:
    return httpcore.Origin(b"https", f"{node_id}.tunnel.gpudeck".encode(), 443)


class _TunnelStream(httpcore.AsyncNetworkStream):
    """httpcore network stream over an accepted (server-side TLS) asyncio connection."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer

    async def read(self, max_bytes: int, timeout: Optional[float] = None) -> bytes:
        try:
            return await asyncio.wait_for(self._reader.read(max_bytes), timeout)
        except asyncio.TimeoutError as exc:
            raise httpcore.ReadTimeout(str(exc)) from exc
        except (OSError, ssl.SSLError) as exc:
            raise httpcore.ReadError(str(exc)) from exc

    async def write(self, buffer: bytes, timeout: Optional[float] = None) -> None:
        if not buffer:
            return
        try:
            self._writer.write(buffer)
            await asyncio.wait_for(self._writer.drain(), timeout)
        except asyncio.TimeoutError as exc:
            raise httpcore.WriteTimeout(str(exc)) from exc
        except (OSError, ssl.SSLError) as exc:
            raise httpcore.WriteError(str(exc)) from exc

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            self._writer.close()
            await asyncio.wait_for(self._writer.wait_closed(), 2.0)

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):  # type: ignore
        raise RuntimeError("tunnel streams are already TLS")

    def get_extra_info(self, info: str):  # type: ignore[override]
        if info == "ssl_object":
            return self._writer.get_extra_info("ssl_object")
        if info in ("client_addr", "server_addr"):
            return self._writer.get_extra_info("peername")
        return None


@dataclass
class TunnelConnection:
    node_id: str
    peer: str
    conn: httpcore.AsyncHTTP2Connection
    opened_at: float = field(default_factory=time.monotonic)
    active: int = 0
    probes_failed: int = 0
    #: sha256 of the node certificate (DER), checked against every new CRL.
    fingerprint: str = ""

    @property
    def usable(self) -> bool:
        return not self.conn.is_closed() and self.conn.is_available()


class TunnelRegistry:
    """Authenticated tunnel connections of this process, per node."""

    def __init__(self) -> None:
        self.by_node: Dict[str, List[TunnelConnection]] = {}
        self.accepted_total = 0
        self.rejected_total: Dict[str, int] = {}

    def add(self, tc: TunnelConnection) -> None:
        self.by_node.setdefault(tc.node_id, []).append(tc)
        self.accepted_total += 1

    def remove(self, tc: TunnelConnection) -> None:
        conns = self.by_node.get(tc.node_id) or []
        if tc in conns:
            conns.remove(tc)
        if not conns:
            self.by_node.pop(tc.node_id, None)

    def connections(self, node_id: str) -> List[TunnelConnection]:
        return list(self.by_node.get(node_id) or [])

    def pick(
        self, node_id: str, exclude: Optional[List[TunnelConnection]] = None
    ) -> Optional[TunnelConnection]:
        skip = exclude or []
        live = [c for c in self.by_node.get(node_id) or [] if c.usable and c not in skip]
        if not live:
            return None
        return min(live, key=lambda c: c.active)

    def counts(self) -> Dict[str, int]:
        return {node: sum(1 for c in conns if c.usable) for node, conns in self.by_node.items()}

    def reject(self, reason: str) -> None:
        self.rejected_total[reason] = self.rejected_total.get(reason, 0) + 1

    async def close_all(self) -> None:
        for conns in list(self.by_node.values()):
            for tc in list(conns):
                with contextlib.suppress(Exception):
                    await tc.conn.aclose()
        self.by_node.clear()


_registry = TunnelRegistry()


def get_tunnel_registry() -> TunnelRegistry:
    return _registry


class _CountingStream:
    """Response body: streams parts as they arrive, releases the connection slot on close."""

    def __init__(self, stream, tc: TunnelConnection) -> None:
        self._stream = stream
        self._tc = tc
        self._done = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for part in self._stream:
                yield part
        finally:
            self._release()

    def _release(self) -> None:
        if not self._done:
            self._done = True
            self._tc.active = max(0, self._tc.active - 1)

    async def aclose(self) -> None:
        try:
            if hasattr(self._stream, "aclose"):
                await self._stream.aclose()
        finally:
            self._release()


async def send_over_tunnel(
    registry: TunnelRegistry, node_id: str, request: httpcore.Request
) -> httpcore.Response:
    """Send ``request`` over the least-busy tunnel of ``node_id`` (httpcore level)."""
    tried: List[TunnelConnection] = []
    while True:
        tc = registry.pick(node_id, tried)
        if tc is None:
            raise httpcore.ConnectError(f"no open tunnel to node {node_id}")
        tried.append(tc)
        origin = _origin(node_id)
        req = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=origin.scheme, host=origin.host, port=origin.port, target=request.url.target
            ),
            headers=request.headers,
            content=request.stream,
            extensions=request.extensions,
        )
        tc.active += 1
        try:
            resp = await tc.conn.handle_async_request(req)
        except httpcore.ConnectionNotAvailable:
            tc.active = max(0, tc.active - 1)
            continue
        except BaseException:
            tc.active = max(0, tc.active - 1)
            raise
        return httpcore.Response(
            status=resp.status,
            headers=resp.headers,
            content=_CountingStream(resp.stream, tc),
            extensions=resp.extensions,
        )


class TunnelTransport(httpx.AsyncBaseTransport):
    """httpx transport for ``tunnel://<node id>/...`` URLs (the node is the URL host)."""

    def __init__(self, registry: Optional[TunnelRegistry] = None) -> None:
        self._registry = registry

    @property
    def registry(self) -> TunnelRegistry:
        return self._registry or get_tunnel_registry()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert isinstance(request.stream, httpx.AsyncByteStream)
        node_id = request.url.host
        req = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=b"https",
                host=request.url.raw_host,
                port=443,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        with map_httpcore_exceptions():
            resp = await send_over_tunnel(self.registry, node_id, req)
        return httpx.Response(
            status_code=resp.status,
            headers=resp.headers,
            stream=AsyncResponseStream(resp.stream),
            extensions=resp.extensions,
        )

    async def aclose(self) -> None:  # connections belong to the registry
        return None


def tunnel_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=TunnelTransport(), follow_redirects=False)


# ---------------------------------------------------------------------------
# Listener
# ---------------------------------------------------------------------------


def _ca_pem() -> Optional[str]:
    if settings.API_LAYER_CA_BUNDLE_PATH and os.path.exists(settings.API_LAYER_CA_BUNDLE_PATH):
        with open(settings.API_LAYER_CA_BUNDLE_PATH) as fh:
            return fh.read()
    path = os.path.join(settings.PKI_CA_DIR, "ca.pem")
    if os.path.exists(path):
        with open(path) as fh:
            return fh.read()
    from app.services.serving.routing import get_routing_table

    return get_routing_table().ca_bundle_pem


async def _server_identity() -> Tuple[Optional[str], Optional[str]]:
    """
    Certificate + key the tunnel listener presents: the API layer's own pair when it may serve
    TLS (serverAuth EKU), else the vault-held API-layer pair (a certificate brought with
    ``API_LAYER_CLIENT_CERT_PATH`` may be client-only). Both keys are pinned by agents.
    """
    from app.services.pki import ca as cax

    cert, key = settings.API_LAYER_CLIENT_CERT_PATH, settings.API_LAYER_CLIENT_KEY_PATH
    if cert and key and os.path.exists(cert) and os.path.exists(key):
        try:
            with open(cert) as fh:
                if cax.has_server_auth(cax.cert_from_pem(fh.read())):
                    return cert, key
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"tunnel: unreadable {cert}: {exc}")
        logger.info(
            "tunnel: API_LAYER_CLIENT_CERT_PATH is a client-only certificate; the tunnel "
            "listener uses the vault-held API layer certificate"
        )
    try:
        from app.db.postgres.connection import get_session_maker
        from app.services.pki.service import PkiService

        async with get_session_maker()() as session:
            paths = await PkiService.from_session(session).materialize_api_layer_tls()
            await session.commit()
        return paths["certfile"], paths["keyfile"]
    except Exception as exc:  # noqa: BLE001
        logger.error(f"tunnel: no server certificate: {exc}")
        return None, None


def server_ssl_context(certfile: str, keyfile: str, ca_pem: str) -> ssl.SSLContext:
    """TLS 1.3 only, node client certificate required, ALPN h2."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cadata=ca_pem)
    ctx.load_cert_chain(certfile=certfile, keyfile=keyfile)
    ctx.set_alpn_protocols(["h2"])
    return ctx


def check_peer(der: Optional[bytes], revoked: set) -> str:
    """Node id of an accepted tunnel peer, or raise ``ssl.SSLCertVerificationError``."""
    from app.services.pki.proxy_tls import verify_node_peer

    node_id = verify_node_peer(None, der)
    if der and hashlib.sha256(der).hexdigest() in revoked:
        raise ssl.SSLCertVerificationError("node certificate is revoked")
    return node_id


def _revoked() -> set:
    from app.services.serving.routing import get_routing_table

    crl = get_routing_table().crl or {}
    return {str(f).lower() for f in crl.get("fingerprints_sha256") or []}


class TunnelServer:
    """Accepts agent tunnels, authenticates them and keeps them alive."""

    def __init__(self, registry: Optional[TunnelRegistry] = None) -> None:
        self.registry = registry or get_tunnel_registry()
        self.port: Optional[int] = None
        self._server: Optional[asyncio.AbstractServer] = None
        self._ctx: Optional[ssl.SSLContext] = None
        self._pending = 0
        self._tasks: set = set()
        self._keepalive: Optional[asyncio.Task] = None

    async def start(
        self,
        host: Optional[str] = None,
        ports: Optional[List[int]] = None,
        ctx: Optional[ssl.SSLContext] = None,
    ) -> Optional[int]:
        if ctx is None:
            cert, key = await _server_identity()
            ca = _ca_pem()
            if not (cert and key and ca):
                logger.error("agent tunnel listener not started: no API layer certificate / CA")
                return None
            ctx = server_ssl_context(cert, key, ca)
        self._ctx = ctx
        host = host or settings.AGENT_TUNNEL_BIND
        if ports is None:
            base = int(settings.AGENT_TUNNEL_PORT)
            ports = [base + i for i in range(max(1, int(settings.AGENT_TUNNEL_LISTENERS)))]
        last: Optional[Exception] = None
        for port in ports:
            try:
                self._server = await asyncio.start_server(self._on_tcp, host, port)
                self.port = self._server.sockets[0].getsockname()[1]
                break
            except OSError as exc:  # taken by another worker of this API layer
                last = exc
        if self._server is None:
            logger.error(f"agent tunnel listener not started: no free port in {ports}: {last}")
            return None
        self._keepalive = asyncio.create_task(self._keepalive_loop(), name="tunnel-keepalive")
        logger.info(f"agent tunnel listener on {host}:{self.port} (TLS 1.3, node certificates)")
        return self.port

    async def stop(self) -> None:
        if self._keepalive is not None:
            self._keepalive.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._keepalive
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 2.0)
        for task in list(self._tasks):
            task.cancel()
        await self.registry.close_all()

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _on_tcp(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        peer_s = f"{peer[0]}:{peer[1]}" if peer else "?"
        if self._pending >= int(settings.AGENT_TUNNEL_MAX_PENDING_HANDSHAKES):
            self.registry.reject("too_many_pending_handshakes")
            writer.transport.abort()
            return
        self._pending += 1
        try:
            await asyncio.wait_for(writer.start_tls(self._ctx), HANDSHAKE_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - wrong / no certificate, not TLS, timeout
            self.registry.reject("tls_handshake_failed")
            logger.debug(f"tunnel handshake from {peer_s} failed: {exc}")
            writer.transport.abort()
            return
        finally:
            self._pending -= 1
        ssl_obj = writer.get_extra_info("ssl_object")
        try:
            if ssl_obj is None or ssl_obj.selected_alpn_protocol() != "h2":
                raise ssl.SSLError("ALPN h2 required")
            der = ssl_obj.getpeercert(binary_form=True)
            node_id = check_peer(der, _revoked())
        except (ssl.SSLError, ValueError) as exc:
            self.registry.reject("node_identity_rejected")
            logger.warning(f"tunnel from {peer_s} refused: {exc}")
            writer.transport.abort()
            return
        conn = httpcore.AsyncHTTP2Connection(_origin(node_id), _TunnelStream(reader, writer))
        tc = TunnelConnection(
            node_id=node_id,
            peer=peer_s,
            conn=conn,
            fingerprint=hashlib.sha256(der or b"").hexdigest(),
        )
        if not await self._probe(tc):
            self.registry.reject("probe_failed")
            await conn.aclose()
            return
        self.registry.add(tc)
        logger.info(
            f"tunnel from node={node_id} {peer_s} open "
            f"({len(self.registry.connections(node_id))} for this node)"
        )

    async def _probe(self, tc: TunnelConnection) -> bool:
        """``GET /_health`` over this very connection (HTTP/2 handshake + liveness)."""
        req = httpcore.Request(
            b"GET",
            httpcore.URL(
                scheme=b"https", host=_origin(tc.node_id).host, port=443, target=b"/_health"
            ),
            headers=[(b"host", tc.node_id.encode())],
            extensions={
                "timeout": {
                    "connect": PROBE_TIMEOUT_SECONDS,
                    "read": PROBE_TIMEOUT_SECONDS,
                    "write": PROBE_TIMEOUT_SECONDS,
                    "pool": PROBE_TIMEOUT_SECONDS,
                }
            },
        )
        try:
            resp = await asyncio.wait_for(
                tc.conn.handle_async_request(req), PROBE_TIMEOUT_SECONDS * 2
            )
            try:
                async for _ in resp.stream:  # type: ignore[union-attr]
                    pass
            finally:
                if hasattr(resp.stream, "aclose"):
                    await resp.stream.aclose()  # type: ignore[union-attr]
            return resp.status == 200
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"tunnel probe node={tc.node_id} {tc.peer} failed: {exc}")
            return False

    def on_table_update(self, table) -> None:
        """Routing-table listener: close tunnels of revoked certificates and removed nodes."""
        revoked = {str(f).lower() for f in (table.crl or {}).get("fingerprints_sha256") or []}
        nodes = table.nodes or {}
        for conns in list(self.registry.by_node.values()):
            for tc in list(conns):
                removed = bool(nodes) and (nodes.get(tc.node_id) or {}).get("status") == "removed"
                if tc.fingerprint in revoked or removed:
                    logger.warning(
                        f"tunnel from node={tc.node_id} closed: "
                        f"{'certificate revoked' if tc.fingerprint in revoked else 'node removed'}"
                    )
                    self.registry.remove(tc)
                    try:
                        self._spawn(tc.conn.aclose())
                    except RuntimeError:  # no running loop (tests)
                        pass

    async def keepalive_once(self) -> None:
        conns = [tc for cs in list(self.registry.by_node.values()) for tc in list(cs)]

        async def check(tc: TunnelConnection) -> None:
            if not tc.conn.is_closed() and await self._probe(tc):
                return
            logger.info(f"tunnel from node={tc.node_id} {tc.peer} closed")
            self.registry.remove(tc)
            with contextlib.suppress(Exception):
                await tc.conn.aclose()

        await asyncio.gather(*(check(tc) for tc in conns))

    async def _keepalive_loop(self) -> None:
        interval = max(1.0, float(settings.AGENT_TUNNEL_KEEPALIVE_SECONDS))
        while True:
            await asyncio.sleep(interval)
            try:
                await self.keepalive_once()
                await _publish_presence(self.registry)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"tunnel keep-alive failed: {exc}")


async def _publish_presence(registry: TunnelRegistry) -> None:
    """``gpu-deck:tunnel:<node id>`` = open tunnels of this process (TTL 3 × keep-alive), for
    the admin API's node view. Best effort."""
    try:
        from app.db.redis.client import get_redis, redis_key

        client = await get_redis()
        ttl = int(max(3.0, float(settings.AGENT_TUNNEL_KEEPALIVE_SECONDS) * 3))
        worker = f"{os.uname().nodename}:{os.getpid()}"
        for node, count in registry.counts().items():
            await client.hset(redis_key("tunnel", node), worker, count)
            await client.expire(redis_key("tunnel", node), ttl)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"tunnel presence not published: {exc}")


_server: Optional[TunnelServer] = None


def get_tunnel_server() -> Optional[TunnelServer]:
    return _server


async def start_tunnel_server() -> Optional[TunnelServer]:
    global _server
    if not settings.AGENT_TUNNEL_ENABLED or settings.SERVING_DEV_ENGINE_URL:
        return None
    server = TunnelServer()
    if await server.start() is None:
        return None
    from app.services.serving.routing import get_routing_table

    get_routing_table().on_update(server.on_table_update)
    _server = server
    return server


async def stop_tunnel_server() -> None:
    global _server
    if _server is not None:
        await _server.stop()
        _server = None
