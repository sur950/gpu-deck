"""
Runs message batches inside the API layer.

Nodes in serving mode ``tunnel`` (protocol.md §13) are reachable only over the outbound tunnels
API-layer processes hold, so the jobs process hands every batch to the API layer: it publishes
the batch id on ``gpu-deck:batches:run`` and this subscriber runs
``services.serving.batches.process_batch`` (the same code the Celery task used; items are
claimed with SKIP LOCKED, so several API-layer processes may share a batch). With no API layer
subscribed the jobs process runs the batch itself (direct-mode nodes only).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Optional, Set

from app.core.logger import get_logger

logger = get_logger(__name__)


class BatchRunner:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running: Set[str] = set()
        self._jobs: Set[asyncio.Task] = set()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._listen(), name="batch-runner")

    async def stop(self) -> None:
        for task in [self._task, *self._jobs]:
            if task is not None:
                task.cancel()
        for task in [self._task, *self._jobs]:
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._task = None

    async def _listen(self) -> None:
        from app.db.redis.client import get_redis, redis_key

        channel = redis_key("batches", "run")
        while True:
            pubsub = None
            try:
                client = await get_redis()
                pubsub = client.pubsub()
                await pubsub.subscribe(channel)
                while True:
                    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
                    if msg and msg.get("data"):
                        data = msg["data"]
                        self.run(data.decode() if isinstance(data, bytes) else str(data))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - resubscribe
                logger.warning(f"batch runner subscription failed: {exc}")
                await asyncio.sleep(2)
            finally:
                if pubsub is not None:
                    with contextlib.suppress(Exception):
                        await pubsub.close()

    def run(self, batch_id: str) -> None:
        if batch_id in self._running:
            return
        self._running.add(batch_id)
        task = asyncio.get_running_loop().create_task(self._run(batch_id))
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)

    async def _run(self, batch_id: str) -> None:
        from app.services.serving.batches import process_batch
        from app.workers.tasks.batches_tasks import _release_dispatch

        try:
            stats = await process_batch(batch_id)
            logger.info(f"batch {batch_id} (API layer): {stats}")
        except Exception as exc:  # noqa: BLE001 - the dispatcher retries within 15 s
            logger.error(f"batch {batch_id} failed in the API layer: {exc}")
        finally:
            self._running.discard(batch_id)
            await _release_dispatch(batch_id)


_runner: Optional[BatchRunner] = None


async def start_batch_runner() -> None:
    global _runner
    _runner = BatchRunner()
    await _runner.start()


async def stop_batch_runner() -> None:
    global _runner
    if _runner is not None:
        await _runner.stop()
        _runner = None
