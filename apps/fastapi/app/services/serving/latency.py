"""
API layer -> node round-trip times, measured over the path requests use.

Every ``SERVING_RTT_PROBE_SECONDS`` the ``LatencyProber`` sends one ``GET /_health`` to every
connected node through the same per-node client the serving pipeline uses: the warm HTTP/2
connection of a direct-mode proxy, or the agent's outbound tunnel (``tunnel://<node id>``).
The node proxy answers ``/_health`` itself, so the time from writing the request headers to
reading the response headers (httpcore ``trace`` events) is one network round trip plus a few
microseconds of proxy work. A probe that had to open a connection is timed the same way (the
connect and TLS handshake happen before the request headers are written), so a cold pool does
not inflate the RTT.

``LatencyTracker`` keeps an EWMA plus jitter per node (``app.services.latency.stats``);
``rtt_ms(node)`` is a dictionary lookup used by replica selection and ``Server-Timing`` (no
extra round trip per request). The tracker also counts requests per deployment; both are
published to Redis with ``app.services.latency.store`` after every probe round (RTT per API
layer and region, request counts per region) for the admin API, the per-minute history and
placement.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional, Set

import httpx
from app.core.logger import get_logger
from app.core.settings import settings
from app.services.latency.stats import RttStats

logger = get_logger(__name__)

PROBE_TIMEOUT_SECONDS = 5.0
PROBE_CONCURRENCY = 32
CONNECTED = ("online", "draining", "drained")


class LatencyTracker:
    """Per-node RTT statistics of this process plus request counts per deployment."""

    def __init__(self) -> None:
        self.stats: Dict[str, RttStats] = {}
        self.modes: Dict[str, str] = {}
        self.traffic: Dict[str, int] = {}

    def record(self, node_id: str, rtt_ms: float, mode: str = "direct") -> None:
        stats = self.stats.get(node_id)
        if stats is None:
            stats = self.stats[node_id] = RttStats(settings.SERVING_RTT_EWMA_ALPHA)
        stats.add(rtt_ms)
        self.modes[node_id] = mode

    def failure(self, node_id: str) -> None:
        stats = self.stats.get(node_id)
        if stats is None:
            stats = self.stats[node_id] = RttStats(settings.SERVING_RTT_EWMA_ALPHA)
        stats.fail()

    def max_age_seconds(self) -> float:
        return max(30.0, float(settings.SERVING_RTT_PROBE_SECONDS or 0) * 6.0)

    def rtt_ms(self, node_id: str) -> Optional[float]:
        """Smoothed RTT to ``node_id`` in ms; None when never measured, stale or failing."""
        stats = self.stats.get(node_id)
        if stats is None or stats.ewma_ms is None or stats.consecutive_failures >= 3:
            return None
        if not stats.fresh(self.max_age_seconds()):
            return None
        return stats.ewma_ms

    def forget(self, keep: Set[str]) -> None:
        for node_id in [n for n in self.stats if n not in keep]:
            self.stats.pop(node_id, None)
            self.modes.pop(node_id, None)

    def count_request(self, deployment_id: Optional[str]) -> None:
        if deployment_id:
            key = str(deployment_id)
            self.traffic[key] = self.traffic.get(key, 0) + 1

    def take_traffic(self) -> Dict[str, int]:
        counts, self.traffic = self.traffic, {}
        return counts

    def snapshots(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for node_id, stats in self.stats.items():
            if stats.ewma_ms is None:
                continue
            snap = stats.snapshot()
            snap["mode"] = self.modes.get(node_id, "direct")
            out[node_id] = snap
        return out


_tracker: Optional[LatencyTracker] = None


def get_latency_tracker() -> LatencyTracker:
    global _tracker
    if _tracker is None:
        _tracker = LatencyTracker()
    return _tracker


def rtt_ms(node_id: str) -> Optional[float]:
    """Smoothed RTT from this API layer to a node (None = unknown)."""
    return get_latency_tracker().rtt_ms(node_id)


def probe_targets(table) -> Dict[str, str]:
    """``node id -> proxy base URL`` of every connected node (routing ``nodes`` first, then
    the instances of a table without a node list, e.g. a dev engine)."""
    targets: Dict[str, str] = {}
    for node_id, node in (getattr(table, "nodes", None) or {}).items():
        node = node or {}
        url = str(node.get("proxy_url") or "").rstrip("/")
        if url and node.get("status", "online") in CONNECTED:
            targets[str(node_id)] = url
    for entry in (getattr(table, "models", None) or {}).values():
        for inst in entry.instances:
            if inst.node_id and inst.node_id not in targets and inst.proxy_url:
                targets[inst.node_id] = inst.proxy_url.rstrip("/")
    return targets


def path_mode(base_url: str) -> str:
    return "tunnel" if base_url.startswith("tunnel://") else "direct"


async def probe_once(base_url: str, ca_bundle_pem: Optional[str]) -> float:
    """
    One round trip to a node proxy in ms (request headers written -> response headers read).

    Raises on any transport error. Falls back to the wall time of the whole call when the
    transport emits no trace events (test transports).
    """
    from app.services.serving.upstream import get_upstream_client

    marks: Dict[str, float] = {}

    async def trace(event: str, info: Dict[str, Any]) -> None:
        if event.endswith("send_request_headers.started"):
            marks.setdefault("sent", time.perf_counter())
        elif event.endswith("receive_response_headers.complete"):
            marks["received"] = time.perf_counter()

    client = get_upstream_client(ca_bundle_pem, base_url)
    started = time.perf_counter()
    response = await client.get(
        f"{base_url}/_health",
        timeout=httpx.Timeout(PROBE_TIMEOUT_SECONDS),
        extensions={"trace": trace},
    )
    ended = time.perf_counter()
    await response.aclose()
    if "sent" in marks and "received" in marks:
        return max(0.0, (marks["received"] - marks["sent"]) * 1000.0)
    return (ended - started) * 1000.0


class LatencyProber:
    """Measures RTT to every connected node on a fixed cadence and publishes the results."""

    def __init__(self, table, tracker: Optional[LatencyTracker] = None) -> None:
        self.table = table
        self.tracker = tracker or get_latency_tracker()
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._known: Set[str] = set()
        self._pending: Set[asyncio.Task] = set()

    @property
    def enabled(self) -> bool:
        return float(settings.SERVING_RTT_PROBE_SECONDS or 0) > 0

    async def probe_node(self, node_id: str, base_url: str) -> Optional[float]:
        try:
            rtt = await probe_once(base_url, getattr(self.table, "ca_bundle_pem", None))
        except Exception as exc:  # noqa: BLE001 - unreachable / no tunnel: counted, not raised
            self.tracker.failure(node_id)
            logger.debug(f"RTT probe node={node_id} failed: {exc}")
            return None
        self.tracker.record(node_id, rtt, path_mode(base_url))
        return rtt

    async def probe_all(self) -> Dict[str, Optional[float]]:
        targets = probe_targets(self.table)
        self.tracker.forget(set(targets))
        gate = asyncio.Semaphore(PROBE_CONCURRENCY)

        async def one(node_id: str, url: str) -> Optional[float]:
            async with gate:
                return await self.probe_node(node_id, url)

        results = await asyncio.gather(*(one(n, u) for n, u in targets.items()))
        return dict(zip(targets.keys(), results))

    async def publish(self) -> None:
        from app.services.latency.store import publish_api_layer

        await publish_api_layer(self.tracker.snapshots(), self.tracker.take_traffic())

    async def run_once(self) -> None:
        await self.probe_all()
        await self.publish()

    def on_table_update(self, table) -> None:
        """Probe nodes that just appeared right away, so routing has an RTT within a second."""
        if not self.enabled or self._stop.is_set():
            return
        targets = probe_targets(table)
        new = {n: u for n, u in targets.items() if n not in self._known}
        self._known = set(targets)
        if not new:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        for node_id, url in new.items():
            task = loop.create_task(self.probe_node(node_id, url))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)

    async def start(self) -> None:
        if self.enabled:
            self.table.on_update(self.on_table_update)
            self.on_table_update(self.table)
        # Probing off: still publish the request counts per region (placement input).
        self._task = asyncio.create_task(self._run(), name="latency-prober")

    async def _run(self) -> None:
        probing = self.enabled
        interval = max(1.0, float(settings.SERVING_RTT_PROBE_SECONDS)) if probing else 30.0
        while not self._stop.is_set():
            try:
                if probing:
                    await self.run_once()
                else:
                    await self.publish()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"latency probe round failed: {exc}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass

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
