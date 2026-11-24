"""
Anthropic Message Batch tasks.

- ``process_batch`` (Celery): run a batch's pending items through the same
  ``/v1/messages`` pipeline as live traffic, ``SERVING_BATCH_CONCURRENCY`` at
  a time; items are claimed with SKIP LOCKED so several workers may share a
  batch. Each item writes its own usage event.
- ``batches.dispatch`` (every 15 s): (re)enqueue ``process_batch`` for every
  batch with work left — picks up batches whose worker died, items requeued
  after ``model_not_ready``/busy answers, and expiry.
- ``batches.retention`` (daily): archive ended batches older than
  ``SERVING_BATCH_RESULTS_RETENTION_DAYS`` (their items are deleted).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict

from app.core.logger import get_logger
from app.core.settings import settings
from app.workers.celery_app import celery_app
from app.workers.scheduler import run_async, scheduled_job
from app.workers.tasks.base_task import BaseTaskWithRetry

logger = get_logger(__name__)

QUEUE = "default"

DISPATCH_DEDUPE_SECONDS = 60


async def _process(batch_id: str) -> Dict[str, int]:
    from app.services.serving.batches import process_batch as run

    try:
        return await run(batch_id)
    finally:
        await _release_dispatch(batch_id)


@celery_app.task(
    name="app.workers.tasks.batches_tasks.process_batch",
    base=BaseTaskWithRetry,
    bind=True,
)
def process_batch(self, batch_id: str) -> Dict[str, int]:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        stats = loop.run_until_complete(_process(batch_id))
        logger.info(f"batch {batch_id}: {stats}")
        return stats
    finally:
        loop.close()


def enqueue_batch(batch_id: str) -> None:
    """Called by the serving API after a batch is created. Never raises."""
    try:
        process_batch.apply_async(kwargs={"batch_id": str(batch_id)}, queue=QUEUE)
    except Exception as exc:  # noqa: BLE001 - the dispatcher picks it up within 15 s
        logger.warning(f"enqueue_batch failed ({exc}); dispatcher will retry")


async def _claim_dispatch(batch_id: str) -> bool:
    from app.db.redis.client import get_redis, redis_key

    try:
        client = await get_redis()
        return bool(
            await client.set(
                redis_key("batches", "dispatch", str(batch_id)),
                "1",
                nx=True,
                ex=DISPATCH_DEDUPE_SECONDS,
            )
        )
    except Exception:  # noqa: BLE001
        return True


async def _release_dispatch(batch_id: str) -> None:
    from app.db.redis.client import get_redis, redis_key

    try:
        client = await get_redis()
        await client.delete(redis_key("batches", "dispatch", str(batch_id)))
    except Exception:  # noqa: BLE001
        pass


async def _dispatch() -> int:
    from app.db.postgres.connection import get_session_maker
    from app.db.postgres.repositories.batch_repository import BatchRepository

    async with get_session_maker()() as session:
        ids = await BatchRepository(session).active_ids()
        await session.rollback()
    sent = 0
    for batch_id in ids:
        if await _claim_dispatch(str(batch_id)):
            enqueue_batch(str(batch_id))
            sent += 1
    return sent


@scheduled_job(
    "batches.dispatch",
    "interval",
    seconds=15,
    job_opts={"max_instances": 1, "coalesce": True},
)
def dispatch_batches() -> None:
    try:
        run_async(_dispatch())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"batch dispatch failed: {exc}")


async def _archive_batches() -> int:
    from app.db.postgres.connection import get_session_maker
    from app.db.postgres.repositories.batch_repository import BatchRepository

    cutoff = datetime.now(timezone.utc) - timedelta(
        days=int(settings.SERVING_BATCH_RESULTS_RETENTION_DAYS)
    )
    async with get_session_maker()() as session:
        archived = await BatchRepository(session).archive_older_than(cutoff)
        await session.commit()
    return archived


@scheduled_job("batches.retention", "cron", hour=2, minute=10)
def archive_batches() -> None:
    try:
        archived = run_async(_archive_batches())
        if archived:
            logger.info(f"batch retention: archived {archived} batch(es)")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"batch retention failed: {exc}")
