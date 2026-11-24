"""
Pooled httpx clients towards the node-agent proxies (protocol.md §6).

One persistent ``httpx.AsyncClient`` per node proxy origin
(``scheme://host:port``), all sharing one TLS context with the API layer's mTLS
client certificate (``API_LAYER_CLIENT_CERT_PATH/KEY_PATH``) and the CA bundle
from the routing table (or ``API_LAYER_CA_BUNDLE_PATH``). Every client is
rebuilt when the CA bundle changes. In dev mode (``SERVING_DEV_ENGINE_URL``) the
engine is dialled directly over plain HTTP.

Connection reuse matters on long links: a new connection costs a TCP connect
(plus an SSH channel when the proxy sits behind ``ssh -L``) and a TLS 1.3
handshake — about two extra round trips before the request is even sent. So:

- idle connections stay pooled for ``SERVING_UPSTREAM_KEEPALIVE_SECONDS`` (httpx
  drops them after 5 s by default, which made almost every request pay a fresh
  handshake);
- the pools of routed nodes are pre-warmed with ``SERVING_UPSTREAM_WARM_CONNECTIONS``
  connections when a node first appears in the routing table, and kept warm by a
  ``GET /_health`` every ``SERVING_UPSTREAM_KEEPWARM_SECONDS`` (which also drops a
  connection a middlebox silently killed before a client request finds it);
- pools of nodes that left the routing table are closed.

HTTP/2: the clients offer ALPN ``h2`` + ``http/1.1`` (``SERVING_UPSTREAM_HTTP2``, needs the
``h2`` package). The node-agent proxy negotiates ``h2``, so one TLS connection per node
multiplexes every concurrent request (the pool opens more only when the proxy's
``max_concurrent_streams`` is reached); a proxy that only speaks ``http/1.1`` (older agents,
plain-HTTP dev engines) gets HTTP/1.1 keep-alive connections as before. The agent pings idle
h2 connections every 30 s and closes one that does not answer within 20 s; httpx only
answers while a request is in flight, so the keep-warm request (every 20 s by default, which
also keeps the agent from pinging at all) is what keeps an idle HTTP/2 connection open.
httpcore checks an idle HTTP/1.1 connection for a server close before reusing it but not an
HTTP/2 one, so ``_NodeProxyTransport`` does: an idle h2 connection with unread data (GOAWAY
after an agent restart or an unanswered ping, or a FIN) is closed before the next request
would be sent on it and the request opens a new connection instead of failing.

Tests can inject a transport with ``set_transport_factory``.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import importlib.util
import os
import ssl
import tempfile
from typing import AsyncIterator, Callable, Dict, Iterable, Optional, Set
from urllib.parse import urlsplit

import httpx
from app.core.logger import get_logger
from app.core.settings import settings

logger = get_logger(__name__)

_transport_factory: Optional[Callable[[], httpx.AsyncBaseTransport]] = None
# Jobs process: each Celery task runs on its own event loop (thread pool on macOS), so it
# gets its own client through this context variable instead of the process-wide pools.
_scoped_client: contextvars.ContextVar[Optional[httpx.AsyncClient]] = contextvars.ContextVar(
    "gpudeck_upstream_client", default=None
)

_DEFAULT_ORIGIN = "-"
_h2_missing_logged = False


def http2_enabled() -> bool:
    """Offer HTTP/2 to node proxies: on by setting and when the ``h2`` package is installed."""
    global _h2_missing_logged
    if not settings.SERVING_UPSTREAM_HTTP2:
        return False
    if importlib.util.find_spec("h2") is None:
        if not _h2_missing_logged:
            _h2_missing_logged = True
            logger.warning("SERVING_UPSTREAM_HTTP2 is on but the h2 package is missing - HTTP/1.1")
        return False
    return True


class _NodeProxyTransport(httpx.AsyncHTTPTransport):
    """httpx transport that drops idle HTTP/2 connections the proxy has already closed."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await _drop_stale_h2(self._pool)
        return await super().handle_async_request(request)


async def _drop_stale_h2(pool) -> None:
    """
    Close idle HTTP/2 connections whose socket is readable.

    An idle h2 connection only receives data when the proxy is closing it (GOAWAY / FIN) or
    pinging it; either way it is about to go away, and a request written onto it would fail
    with ``ReadError`` instead of being retried. The pool forgets closed connections on the
    next assignment, so the request goes out on a fresh (or another idle) connection.
    """
    try:
        connections = list(pool.connections)
    except Exception:  # noqa: BLE001 - httpcore internals changed: keep default behaviour
        return
    for conn in connections:
        inner = getattr(conn, "_connection", None)
        if type(inner).__name__ != "AsyncHTTP2Connection":
            continue
        try:
            if not conn.is_idle():
                continue
            stream = getattr(inner, "_network_stream", None)
            if stream is None or not stream.get_extra_info("is_readable"):
                continue
            logger.debug("upstream h2 connection closed by the node proxy - reconnecting")
            await conn.aclose()
        except Exception:  # noqa: BLE001
            continue


def _client(verify: ssl.SSLContext | bool, limits: httpx.Limits) -> httpx.AsyncClient:
    """A client towards node proxies: HTTP/1.1 always, HTTP/2 when enabled (ALPN)."""
    transport = _NodeProxyTransport(verify=verify, limits=limits, http1=True, http2=http2_enabled())
    return httpx.AsyncClient(transport=transport, follow_redirects=False)


def origin_of(url: Optional[str]) -> str:
    """``scheme://host:port`` of a URL (the pool key); ``-`` when unknown."""
    if not url:
        return _DEFAULT_ORIGIN
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname:
        return _DEFAULT_ORIGIN
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"{parts.scheme}://{parts.hostname}:{port}"


class UpstreamPools:
    """Per-node-origin persistent clients sharing one TLS context."""

    def __init__(self) -> None:
        self.clients: Dict[str, httpx.AsyncClient] = {}
        self.ca_hash: Optional[str] = None
        self._ssl: ssl.SSLContext | bool | None = None
        self._closing: Set[asyncio.Task] = set()

    def _limits(self) -> httpx.Limits:
        per_node = max(1, int(settings.SERVING_UPSTREAM_MAX_CONNECTIONS_PER_NODE))
        return httpx.Limits(
            max_connections=per_node,
            max_keepalive_connections=min(
                per_node, max(1, int(settings.SERVING_UPSTREAM_MAX_KEEPALIVE_PER_NODE))
            ),
            keepalive_expiry=float(settings.SERVING_UPSTREAM_KEEPALIVE_SECONDS),
        )

    def _make(self, ca_bundle_pem: Optional[str]) -> httpx.AsyncClient:
        if _transport_factory is not None:
            return httpx.AsyncClient(transport=_transport_factory(), limits=self._limits())
        if self._ssl is None:
            self._ssl = _ssl_context(ca_bundle_pem)
        return _client(self._ssl, self._limits())

    def get(self, ca_bundle_pem: Optional[str], url: Optional[str]) -> httpx.AsyncClient:
        ca_hash = hashlib.sha256((ca_bundle_pem or "").encode()).hexdigest()
        if self.ca_hash is not None and ca_hash != self.ca_hash and _transport_factory is None:
            logger.info("routing table CA bundle changed — rebuilding upstream pools")
            self.discard(list(self.clients))
            self._ssl = None
        self.ca_hash = ca_hash
        key = origin_of(url)
        client = self.clients.get(key)
        if client is None or client.is_closed:
            client = self._make(ca_bundle_pem)
            self.clients[key] = client
        return client

    def discard(self, origins: Iterable[str]) -> None:
        """Drop the pools of these origins; their clients close in the background."""
        for origin in list(origins):
            client = self.clients.pop(origin, None)
            if client is None:
                continue
            try:
                task = asyncio.get_running_loop().create_task(client.aclose())
            except RuntimeError:
                continue
            self._closing.add(task)
            task.add_done_callback(self._closing.discard)

    async def close(self) -> None:
        clients, self.clients = list(self.clients.values()), {}
        for client in clients:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass
        self._ssl = None
        self.ca_hash = None


_pools = UpstreamPools()


def get_pools() -> UpstreamPools:
    return _pools


def set_transport_factory(
    factory: Optional[Callable[[], httpx.AsyncBaseTransport]],
) -> None:
    """Tests: route upstream calls through a custom transport (e.g. ASGITransport)."""
    global _transport_factory, _pools
    _transport_factory = factory
    _pools = UpstreamPools()


def upstream_headers(
    request_id: str, api_key_id: Optional[str], deployment_id: Optional[str], fmt: str
) -> Dict[str, str]:
    headers = {
        "X-Request-ID": request_id,
        "X-GPUDeck-Format": fmt,
    }
    if api_key_id:
        headers["X-GPUDeck-Api-Key-Id"] = str(api_key_id)
    if deployment_id:
        headers["X-GPUDeck-Deployment"] = str(deployment_id)
    return headers


def timeouts(stream: bool) -> httpx.Timeout:
    read = (
        settings.SERVING_STREAM_IDLE_TIMEOUT_SECONDS
        if stream
        else settings.SERVING_REQUEST_TIMEOUT_SECONDS
    )
    return httpx.Timeout(
        connect=settings.SERVING_CONNECT_TIMEOUT_SECONDS,
        read=read,
        write=settings.SERVING_CONNECT_TIMEOUT_SECONDS * 2,
        pool=settings.SERVING_CONNECT_TIMEOUT_SECONDS,
    )


def _ssl_context(ca_bundle_pem: Optional[str]) -> ssl.SSLContext | bool:
    """
    Node-proxy TLS: chain verified against the internal CA, identity by
    ``CN == node id`` (``app.services.pki.proxy_tls``) instead of a hostname
    check — proxies are dialled by IP / proxy_address the cert may not list.
    """
    if not settings.SERVING_UPSTREAM_VERIFY_TLS:
        return False
    from app.services.pki.proxy_tls import node_proxy_ssl_context

    ctx = node_proxy_ssl_context()
    if settings.API_LAYER_CA_BUNDLE_PATH and os.path.exists(settings.API_LAYER_CA_BUNDLE_PATH):
        ctx.load_verify_locations(cafile=settings.API_LAYER_CA_BUNDLE_PATH)
    elif ca_bundle_pem:
        ctx.load_verify_locations(cadata=ca_bundle_pem)
    cert = settings.API_LAYER_CLIENT_CERT_PATH
    key = settings.API_LAYER_CLIENT_KEY_PATH
    if cert and key and os.path.exists(cert) and os.path.exists(key):
        ctx.load_cert_chain(certfile=cert, keyfile=key)
    else:
        logger.warning("API_LAYER_CLIENT_CERT_PATH/KEY_PATH not set — proxies will refuse mTLS")
    return ctx


def get_upstream_client(
    ca_bundle_pem: Optional[str] = None, url: Optional[str] = None
) -> httpx.AsyncClient:
    """
    The persistent client for the node proxy serving ``url`` (one pool per origin).

    Rebuilt when the CA bundle from the routing table changes. Inside
    ``scoped_upstream_client`` the job's own client is returned instead.
    """
    scoped = _scoped_client.get()
    if scoped is not None:
        return scoped
    return _pools.get(ca_bundle_pem, url)


def _new_client(ca_bundle_pem: Optional[str]) -> httpx.AsyncClient:
    limits = httpx.Limits(
        max_connections=256,
        max_keepalive_connections=64,
        keepalive_expiry=float(settings.SERVING_UPSTREAM_KEEPALIVE_SECONDS),
    )
    if _transport_factory is not None:
        return httpx.AsyncClient(transport=_transport_factory(), limits=limits)
    return _client(_ssl_context(ca_bundle_pem), limits)


@contextlib.asynccontextmanager
async def scoped_upstream_client(
    ca_bundle_pem: Optional[str] = None,
) -> AsyncIterator[httpx.AsyncClient]:
    """A client bound to the current event loop for the duration of a job."""
    client = _new_client(ca_bundle_pem)
    token = _scoped_client.set(client)
    try:
        yield client
    finally:
        _scoped_client.reset(token)
        await client.aclose()


async def close_upstream_client() -> None:
    await _pools.close()


# ---------------------------------------------------------------------------
# Pre-warming / keep-warm of the routed node pools
# ---------------------------------------------------------------------------


def routed_origins(table) -> Dict[str, str]:
    """``origin -> base URL`` of every node proxy that serves a non-stopped instance."""
    out: Dict[str, str] = {}
    for entry in getattr(table, "models", {}).values():
        for inst in entry.instances:
            if inst.state not in ("ready", "loading", "pending"):
                continue
            base = (inst.proxy_url or "").rstrip("/")
            if not base:
                continue
            out.setdefault(origin_of(base), base)
    return out


def _warm_url(base: str, direct: bool) -> str:
    # The node proxy answers /_health itself (no engine round trip); a dev engine may 404,
    # which still leaves a warm connection behind.
    return f"{base}/_health" if not direct else f"{base}/"


class PoolWarmer:
    """Opens connections to newly routed nodes and keeps idle pools alive."""

    def __init__(self, table) -> None:
        self.table = table
        self.warmed: Set[str] = set()
        self._task: Optional[asyncio.Task] = None
        self._pending: Set[asyncio.Task] = set()
        self._stop = asyncio.Event()

    def _direct(self, origin: str) -> bool:
        for entry in self.table.models.values():
            for inst in entry.instances:
                if origin_of(inst.proxy_url) == origin:
                    return bool(inst.direct)
        return False

    async def warm(self, origin: str, base: str, connections: Optional[int] = None) -> int:
        """
        Open up to ``connections`` pooled connections to one node proxy; returns successes.

        Over HTTP/2 the concurrent warm requests share one multiplexed connection.
        """
        n = settings.SERVING_UPSTREAM_WARM_CONNECTIONS if connections is None else connections
        if n <= 0:
            return 0
        client = get_upstream_client(self.table.ca_bundle_pem, base)
        url = _warm_url(base, self._direct(origin))
        timeout = httpx.Timeout(settings.SERVING_CONNECT_TIMEOUT_SECONDS * 2)

        async def one() -> bool:
            try:
                resp = await client.get(url, timeout=timeout)
                await resp.aclose()
                return True
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"upstream pre-warm {origin} failed: {exc}")
                return False

        results = await asyncio.gather(*(one() for _ in range(n)))
        return sum(1 for r in results if r)

    def on_table_update(self, table) -> None:
        """Routing listener: warm new origins, drop pools of nodes that left the table."""
        if not settings.SERVING_UPSTREAM_PREWARM or self._stop.is_set():
            return
        current = routed_origins(table)
        gone = [o for o in _pools.clients if o not in current and o != _DEFAULT_ORIGIN]
        if gone:
            _pools.discard(gone)
        self.warmed &= set(current)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        for origin, base in current.items():
            if origin in self.warmed:
                continue
            self.warmed.add(origin)
            task = loop.create_task(self.warm(origin, base))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)

    async def keep_warm_once(self) -> None:
        current = routed_origins(self.table)
        if current:
            await asyncio.gather(*(self.warm(o, b, connections=1) for o, b in current.items()))

    async def start(self) -> None:
        if not settings.SERVING_UPSTREAM_PREWARM:
            return
        self.table.on_update(self.on_table_update)
        self.on_table_update(self.table)
        if settings.SERVING_UPSTREAM_KEEPWARM_SECONDS > 0:
            self._task = asyncio.create_task(self._run(), name="upstream-keep-warm")

    async def _run(self) -> None:
        interval = max(1, int(settings.SERVING_UPSTREAM_KEEPWARM_SECONDS))
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass
            try:
                await self.keep_warm_once()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"upstream keep-warm failed: {exc}")

    async def stop(self) -> None:
        self._stop.set()
        tasks = list(self._pending) + ([self._task] if self._task else [])
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._task = None


def write_pem_tempfile(pem: str) -> str:
    """Helper for tests/tools that need a CA file path from PEM text."""
    fd, path = tempfile.mkstemp(suffix=".pem")
    with os.fdopen(fd, "w") as fh:
        fh.write(pem)
    return path
