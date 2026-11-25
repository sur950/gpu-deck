"""
Latency jobs, run by the jobs process (APScheduler) and available as Celery tasks:

- ``latency.snapshot`` (every minute): copies the live round-trip times from Redis
  (``gpu-deck:latency:*``, written by the agent hub and every API layer) into
  ``node_latency_samples``, one row per (node, source, API layer) and minute.
- ``latency.retention`` (hourly): deletes rows older than ``LATENCY_RETENTION_DAYS``.
"""

from typing import Any, Dict

from app.core.logger import get_logger
from app.workers.celery_app import celery_app
from app.workers.scheduler import run_async, scheduled_job
from app.workers.tasks.base_task import BaseTaskWithRetry

logger = get_logger(__name__)

# Same queue as the other metric jobs (no extra queue for workers to consume).
QUEUE = "metrics"

BEAT_SCHEDULE: Dict[str, Any] = {}


async def _snapshot() -> int:
    from app.db.postgres.connection import get_session_maker
    from app.services.latency.service import snapshot_to_db

    async with get_session_maker()() as session:
        written = await snapshot_to_db(session)
        await session.commit()
    return written


async def _retention() -> int:
    from app.db.postgres.connection import get_session_maker
    from app.services.latency.service import purge

    async with get_session_maker()() as session:
        deleted = await purge(session)
        await session.commit()
    if deleted:
        logger.info(f"latency retention deleted {deleted} row(s)")
    return deleted


@celery_app.task(name="app.workers.tasks.latency_tasks.snapshot", base=BaseTaskWithRetry, bind=True)
def snapshot(self) -> int:
    return run_async(_snapshot())


@celery_app.task(
    name="app.workers.tasks.latency_tasks.retention", base=BaseTaskWithRetry, bind=True
)
def retention(self) -> int:
    return run_async(_retention())


@scheduled_job(
    "latency.snapshot",
    "interval",
    minutes=1,
    job_opts={"max_instances": 1, "coalesce": True},
)
def scheduled_snapshot() -> None:
    try:
        run_async(_snapshot())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"latency snapshot failed: {exc}")


@scheduled_job(
    "latency.retention",
    "interval",
    hours=1,
    job_opts={"max_instances": 1, "coalesce": True},
)
def scheduled_retention() -> None:
    try:
        run_async(_retention())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"latency retention failed: {exc}")
