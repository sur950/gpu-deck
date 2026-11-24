"""
Live event stream: the feed behind ``GET /live/events`` (SSE),
``GET /live/ws`` (WebSocket) and ``GET /logs/stream``.

Producers call ``publish_live_event(event, data)`` from any process; it
appends to the Redis stream ``gpu-deck:live:events`` (``XADD MAXLEN ~ 5000``,
never raises)::

    from app.services.metrics.live import publish_live_event
    await publish_live_event("swap", {"swap_id": ..., "state": "loading", "node_id": ...})

``event`` is one of ``LiveEvent.event`` (metrics, instance, node, device, swap,
rule_run, alert, log, heartbeat). Put ``node_id`` / ``instance_id`` /
``deployment_id`` / ``cluster_id`` into ``data`` when they apply so the
streams can filter. The hub publishes node/instance/device/log events; the automation
publishes swap/rule_run/alert.

Consumers: one background reader per process (``LiveFanout``) does a blocking
``XREAD`` and hands every entry to the subscribed queues; the stream ids are
the SSE ids, so ``Last-Event-ID`` replays what a reconnecting client missed
(window ``LIVE_EVENTS_REPLAY_SECONDS``). ``metrics`` frames are not stored in
the stream: each connection builds them from the live cache every
``interval_seconds``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Set, Tuple

from app.core.logger import get_logger
from app.core.settings import settings
from app.core.shutdown import shutting_down
from app.db.redis.client import get_redis, redis_key
from app.services.metrics import live_cache

logger = get_logger(__name__)

STREAM_MAXLEN = 5000
TOPICS = (
    "metrics",
    "instance",
    "node",
    "device",
    "swap",
    "rule_run",
    "alert",
    "log",
    "heartbeat",
)
QUEUE_SIZE = 1000


def stream_key() -> str:
    return redis_key("live", "events")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _dumps(value: Any) -> str:
    from app.services.agent_hub.protocol import dumps

    return dumps(value)


async def publish_live_event(event: str, data: Dict[str, Any], ts: Optional[str] = None) -> None:
    """Append one LiveEvent to the stream. Best effort: never raises."""
    if event not in TOPICS:
        logger.debug(f"live: unknown event {event}")
        return
    try:
        client = await get_redis()
        await client.xadd(
            stream_key(),
            {"event": event, "ts": ts or now_iso(), "data": _dumps(data)},
            maxlen=STREAM_MAXLEN,
            approximate=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"live publish {event} failed: {exc}")


def _entry_to_event(entry_id: str, fields: Dict[str, str]) -> Dict[str, Any]:
    try:
        data = json.loads(fields.get("data") or "{}")
    except (TypeError, ValueError):
        data = {}
    return {
        "id": entry_id,
        "event": fields.get("event"),
        "ts": fields.get("ts"),
        "data": data,
    }


def _now_stream_id() -> str:
    return f"{int(datetime.now(timezone.utc).timestamp() * 1000) - 1}-0"


def _stream_id_ms(entry_id: str) -> int:
    try:
        return int(str(entry_id).split("-", 1)[0])
    except ValueError:
        return 0


async def replay_since(last_id: str, max_age_seconds: Optional[int] = None) -> List[Dict[str, Any]]:
    """Entries after ``last_id`` that are not older than the replay window."""
    window = max_age_seconds if max_age_seconds is not None else settings.LIVE_EVENTS_REPLAY_SECONDS
    floor_ms = int((datetime.now(timezone.utc).timestamp() - window) * 1000)
    try:
        client = await get_redis()
        rows = await client.xrange(stream_key(), min=f"({last_id}", max="+", count=STREAM_MAXLEN)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"live replay failed: {exc}")
        return []
    return [_entry_to_event(i, f) for i, f in rows if _stream_id_ms(i) >= floor_ms]


class LiveFanout:
    """Per-process XREAD loop feeding subscriber queues."""

    def __init__(self) -> None:
        self._queues: Set[asyncio.Queue] = set()
        self._task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _ensure(self) -> None:
        loop = asyncio.get_running_loop()
        if self._task is not None and not self._task.done() and self._loop is loop:
            return
        self._loop = loop
        self._task = loop.create_task(self._run(_now_stream_id()), name="live-fanout")

    def subscribe(self) -> asyncio.Queue:
        self._ensure()
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._queues.discard(queue)

    @property
    def subscribers(self) -> int:
        return len(self._queues)

    async def _run(self, last_id: str) -> None:
        # Start from "now" (not "$") so entries published between subscribe() and
        # the first XREAD are not lost.
        while True:
            if not self._queues:
                await asyncio.sleep(0.5)
                last_id = _now_stream_id()
                continue
            try:
                client = await get_redis()
                result = await client.xread({stream_key(): last_id}, block=5000, count=200)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"live fanout read failed: {exc}")
                await asyncio.sleep(1.0)
                continue
            for _stream, entries in result or []:
                for entry_id, fields in entries:
                    last_id = entry_id
                    event = _entry_to_event(entry_id, fields)
                    for queue in list(self._queues):
                        try:
                            queue.put_nowait(event)
                        except asyncio.QueueFull:
                            pass  # slow consumer: drop (live view, not a log)


fanout = LiveFanout()


# ==========================================
# Filtering and metrics frames
# ==========================================


@dataclass
class LiveFilter:
    topics: Set[str] = field(default_factory=lambda: set(TOPICS))
    cluster_id: Optional[uuid.UUID] = None
    node_id: Optional[uuid.UUID] = None
    deployment_id: Optional[uuid.UUID] = None
    instance_id: Optional[uuid.UUID] = None
    node_ids: Optional[Set[str]] = None  # resolved from cluster_id / node_id

    def scoped(self) -> bool:
        return any((self.cluster_id, self.node_id, self.deployment_id, self.instance_id))

    def matches(self, event: Dict[str, Any]) -> bool:
        if event.get("event") not in self.topics:
            return False
        if not self.scoped():
            return True
        data = event.get("data") or {}
        if self.instance_id and str(data.get("instance_id")) != str(self.instance_id):
            return False
        if self.deployment_id and str(data.get("deployment_id")) != str(self.deployment_id):
            return False
        if self.cluster_id and str(data.get("cluster_id")) == str(self.cluster_id):
            return True
        if self.node_ids is not None and (self.cluster_id or self.node_id):
            node = data.get("node_id")
            if node is None or str(node) not in self.node_ids:
                return False
        return True


async def resolve_node_scope(flt: LiveFilter) -> None:
    """Fill ``node_ids`` for cluster/node filters (one small query)."""
    if flt.node_id:
        flt.node_ids = {str(flt.node_id)}
        return
    if flt.cluster_id:
        from app.db.postgres.connection import get_session_maker
        from app.db.postgres.repositories.node_repository import NodeRepository

        async with get_session_maker()() as session:
            nodes = await NodeRepository(session).list_active(flt.cluster_id)
        flt.node_ids = {str(n.id) for n in nodes}


def _sample_matches(sample: Dict[str, Any], flt: LiveFilter, kind: str) -> bool:
    if flt.node_ids is not None and str(sample.get("node_id")) not in flt.node_ids:
        return False
    if kind == "engine":
        if flt.instance_id and str(sample.get("instance_id")) != str(flt.instance_id):
            return False
        if flt.deployment_id and str(sample.get("deployment_id")) != str(flt.deployment_id):
            return False
    return True


async def metrics_frame(flt: LiveFilter, rollup: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """``{gpu: [...], engine: [...], overview: OverviewLive}`` from the live cache."""
    gpu = [
        s
        for s in (await live_cache.get_all("gpu")).values()
        if live_cache.is_fresh(s) and _sample_matches(s, flt, "gpu")
    ]
    engine = [
        s
        for s in (await live_cache.get_all("engine")).values()
        if live_cache.is_fresh(s) and _sample_matches(s, flt, "engine")
    ]
    if flt.instance_id or flt.deployment_id:
        gpu = []  # instance-scoped views carry engine samples only
    rollup = rollup or {}
    overview = live_cache.overview_live(
        gpu,
        engine,
        vram_total_bytes=int(rollup.get("vram_total_bytes") or 0),
        requests_per_minute=rollup.get("requests_per_minute"),
        error_rate_pct=rollup.get("error_rate_pct"),
    )
    return {"gpu": gpu, "engine": engine, "overview": overview}


async def live_event_stream(
    flt: LiveFilter,
    interval_seconds: float = 2.0,
    last_event_id: Optional[str] = None,
    is_disconnected=None,
) -> AsyncIterator[Dict[str, Any]]:
    """
    Async generator of LiveEvent dicts (``{"id"?, "event", "ts", "data"}``):
    replayed entries, live stream entries, periodic ``metrics`` frames and
    ``heartbeat`` frames. ``is_disconnected`` is an async callable checked
    between frames.
    """
    from app.services.metrics.service import fleet_rollup_snapshot

    await resolve_node_scope(flt)
    interval = max(1.0, min(float(interval_seconds or 2.0), 60.0))
    heartbeat_every = max(1, int(settings.LIVE_HEARTBEAT_SECONDS))
    queue = fanout.subscribe()
    loop = asyncio.get_running_loop()
    try:
        if last_event_id:
            for event in await replay_since(last_event_id):
                if flt.matches(event):
                    yield event
        next_metrics = loop.time()
        next_heartbeat = loop.time() + heartbeat_every
        rollup: Dict[str, Any] = {}
        next_rollup = loop.time()
        while True:
            if shutting_down():  # process stopping: end the response so uvicorn can exit
                return
            if is_disconnected is not None and await is_disconnected():
                return
            now = loop.time()
            if "metrics" in flt.topics and now >= next_metrics:
                if now >= next_rollup:
                    rollup = await fleet_rollup_snapshot(flt)
                    next_rollup = now + 30
                yield {
                    "event": "metrics",
                    "ts": now_iso(),
                    "data": await metrics_frame(flt, rollup),
                }
                next_metrics = now + interval
            if now >= next_heartbeat:
                if "heartbeat" in flt.topics:
                    yield {"event": "heartbeat", "ts": now_iso(), "data": {}}
                next_heartbeat = now + heartbeat_every
            deadline = min(
                next_metrics if "metrics" in flt.topics else now + heartbeat_every,
                next_heartbeat,
            )
            timeout = max(0.05, min(deadline - loop.time(), 1.0))
            try:
                event = await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                continue
            if flt.matches(event):
                yield event
    finally:
        fanout.unsubscribe(queue)


def sse_format(event: Dict[str, Any]) -> str:
    """One SSE frame: optional ``id``, ``event`` and ``data`` (the event's data JSON)."""
    lines: List[str] = []
    if event.get("id"):
        lines.append(f"id: {event['id']}")
    lines.append(f"event: {event['event']}")
    lines.append(f"data: {_dumps(event.get('data') or {})}")
    return "\n".join(lines) + "\n\n"


def ws_format(event: Dict[str, Any]) -> str:
    """WebSocket frame: the whole LiveEvent object."""
    return _dumps(
        {
            "event": event["event"],
            "ts": event.get("ts") or now_iso(),
            "data": event.get("data") or {},
        }
    )


def parse_topics(values: Optional[List[str]]) -> Tuple[Set[str], List[str]]:
    """Accept repeated or comma-separated topics; returns (valid, unknown)."""
    if not values:
        return set(TOPICS), []
    picked: Set[str] = set()
    unknown: List[str] = []
    for value in values:
        for part in str(value).split(","):
            part = part.strip()
            if not part:
                continue
            if part in TOPICS:
                picked.add(part)
            else:
                unknown.append(part)
    return picked or set(TOPICS), unknown
