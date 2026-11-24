"""
Per-instance in-flight request counters.

Kept in memory on the hot path (one dict increment per request) and flushed
to Redis every ``SERVING_INFLIGHT_FLUSH_MS`` so the swap manager can wait
for zero in-flight requests on a draining instance across all API-layer
processes:

    HSET gpu-deck:serving:inflight:{instance_id} {process_id} <count>
    HSET gpu-deck:serving:inflight:procs {process_id} <unix ts>   (liveness)

``read_inflight(instance_id)`` sums the counts of processes seen in the last
``STALE_AFTER_SECONDS``.
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
from typing import Dict, Optional

from app.core.logger import get_logger
from app.core.settings import settings

logger = get_logger(__name__)

STALE_AFTER_SECONDS = 15
PROCESS_ID = f"{socket.gethostname()}:{os.getpid()}"


class InflightTracker:
    def __init__(self) -> None:
        self.counts: Dict[str, int] = {}
        self.total: int = 0
        self._dirty: set = set()
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    def acquire(self, instance_id: str) -> int:
        """Increment and return the count *before* this request (concurrency at start)."""
        before = self.counts.get(instance_id, 0)
        self.counts[instance_id] = before + 1
        self.total += 1
        self._dirty.add(instance_id)
        return before

    def release(self, instance_id: str) -> None:
        current = self.counts.get(instance_id, 0)
        if current <= 1:
            self.counts.pop(instance_id, None)
        else:
            self.counts[instance_id] = current - 1
        self.total = max(0, self.total - 1)
        self._dirty.add(instance_id)

    def snapshot(self) -> Dict[str, int]:
        return dict(self.counts)

    async def flush(self, final: bool = False) -> None:
        from app.db.redis.client import get_redis, redis_key

        # Dirty instances plus every instance with requests still open: a long stream on an
        # otherwise idle instance must keep its key alive past the TTL.
        dirty = list(self._dirty | set(self.counts))
        self._dirty.clear()
        try:
            client = await get_redis()
            pipe = client.pipeline(transaction=False)
            now = int(time.time())
            if final:
                pipe.hdel(redis_key("serving", "inflight", "procs"), PROCESS_ID)
            else:
                pipe.hset(redis_key("serving", "inflight", "procs"), PROCESS_ID, now)
            for instance_id in dirty:
                key = redis_key("serving", "inflight", instance_id)
                count = self.counts.get(instance_id, 0)
                if count:
                    pipe.hset(key, PROCESS_ID, count)
                else:
                    pipe.hdel(key, PROCESS_ID)
                pipe.expire(key, STALE_AFTER_SECONDS * 4)
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"inflight flush skipped: {exc}")

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="inflight-flusher")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        # Final flush so a shutting-down process does not leave stale counts: every instance
        # it still counted is written as 0 (hdel) and the process leaves the liveness hash.
        self._dirty.update(self.counts)
        self.counts.clear()
        self.total = 0
        await self.flush(final=True)

    async def _run(self) -> None:
        interval = max(0.05, settings.SERVING_INFLIGHT_FLUSH_MS / 1000.0)
        while not self._stop.is_set():
            await asyncio.sleep(interval)
            if self._dirty or int(time.time()) % 5 == 0:
                await self.flush()


async def read_inflight(instance_id: str) -> int:
    """Sum of live API-layer processes' in-flight counts for an instance (for the swap manager)."""
    from app.db.redis.client import get_redis, redis_key

    client = await get_redis()
    procs = await client.hgetall(redis_key("serving", "inflight", "procs"))
    counts = await client.hgetall(redis_key("serving", "inflight", instance_id))
    cutoff = time.time() - STALE_AFTER_SECONDS
    total = 0
    for proc, value in (counts or {}).items():
        try:
            seen = float((procs or {}).get(proc, 0))
        except (TypeError, ValueError):
            seen = 0
        if seen >= cutoff:
            total += int(value)
    return total


_tracker: Optional[InflightTracker] = None


def get_inflight_tracker() -> InflightTracker:
    global _tracker
    if _tracker is None:
        _tracker = InflightTracker()
    return _tracker
