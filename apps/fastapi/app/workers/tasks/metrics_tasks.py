"""
Metrics jobs, run by the jobs process (APScheduler) and available as
Celery tasks:

- ``metrics.rollup_minutes`` (every minute): 1-minute rollups of
  ``metric_samples_gpu`` / ``metric_samples_engine`` into
  ``metric_rollups_*_1m``. Window = from the last rolled minute (Redis
  ``gpu-deck:metrics:rollup:last``, capped at ``METRICS_ROLLUP_CATCHUP_MINUTES``)
  to the last complete minute ending 30 s ago; the previous 2 minutes are always
  recomputed (upserts) so late batches are counted.
- ``metrics.create_partitions`` (hourly + at start): ``create_daily_partitions``
  for today + ``PARTITIONS_DAYS_AHEAD`` days (no DEFAULT partition exists).
- ``metrics.retention`` (hourly): ``drop_old_partitions`` per table with the
  retention windows from env.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.core.logger import get_logger
from app.core.settings import settings
from app.workers.celery_app import celery_app
from app.workers.scheduler import run_async, scheduled_job
from app.workers.tasks.base_task import BaseTaskWithRetry

logger = get_logger(__name__)

QUEUE = "metrics"

BEAT_SCHEDULE: Dict[str, Any] = {}

LATE_SECONDS = 30
RECOMPUTE_MINUTES = 2


def _floor_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def rollup_window(now: datetime, last: Optional[datetime], catchup_minutes: int) -> Optional[tuple]:
    """[start, end) of minutes to (re)compute; None when nothing is complete yet."""
    end = _floor_minute(now - timedelta(seconds=LATE_SECONDS))
    floor = end - timedelta(minutes=max(1, catchup_minutes))
    start = end - timedelta(minutes=RECOMPUTE_MINUTES)
    if last is not None and last < start:
        start = max(last, floor)
    if start >= end:
        return None
    return start, end


async def _rollup() -> Dict[str, Any]:
    from app.db.postgres.connection import get_session_maker
    from app.db.postgres.repositories.metric_repository import MetricRepository
    from app.db.redis.client import get_redis, redis_key

    key = redis_key("metrics", "rollup", "last")
    last: Optional[datetime] = None
    try:
        client = await get_redis()
        raw = await client.get(key)
        last = datetime.fromisoformat(raw) if raw else None
    except Exception as exc:  # noqa: BLE001
        client = None
        logger.debug(f"rollup marker unavailable: {exc}")
    window = rollup_window(
        datetime.now(timezone.utc), last, settings.METRICS_ROLLUP_CATCHUP_MINUTES
    )
    if window is None:
        return {"rolled": False}
    start, end = window
    async with get_session_maker()() as session:
        repo = MetricRepository(session)
        await repo.create_daily_partitions(settings.PARTITIONS_DAYS_AHEAD, days_back=1)
        await session.commit()
        await repo.rollup_minutes(start, end, settings.METRICS_INTERVAL_SECONDS)
        await session.commit()
    if client is not None:
        await client.set(key, end.isoformat(), ex=7 * 86400)
    logger.debug(f"metrics rollup {start.isoformat()} -> {end.isoformat()}")
    return {"rolled": True, "start": start.isoformat(), "end": end.isoformat()}


async def _create_partitions() -> int:
    from app.db.postgres.connection import get_session_maker
    from app.db.postgres.repositories.metric_repository import MetricRepository

    async with get_session_maker()() as session:
        created = await MetricRepository(session).create_daily_partitions(
            settings.PARTITIONS_DAYS_AHEAD, days_back=0
        )
        await session.commit()
    if created:
        logger.info(f"created {created} daily partition(s)")
    return created


def retention_plan() -> Dict[str, int]:
    """Days to keep per partitioned table (raw hours rounded up to whole days + today)."""
    raw_days = max(1, -(-settings.METRICS_RAW_RETENTION_HOURS // 24))
    return {
        "metric_samples_gpu": raw_days,
        "metric_samples_engine": raw_days,
        "metric_samples_session": raw_days,
        "metric_rollups_gpu_1m": settings.METRICS_ROLLUP_RETENTION_DAYS,
        "metric_rollups_engine_1m": settings.METRICS_ROLLUP_RETENTION_DAYS,
        "log_chunks": settings.LOGS_RETENTION_DAYS,
        "traces": settings.TRACES_RETENTION_DAYS,
    }


async def _retention() -> Dict[str, int]:
    from app.db.postgres.connection import get_session_maker
    from app.db.postgres.repositories.metric_repository import MetricRepository

    dropped: Dict[str, int] = {}
    async with get_session_maker()() as session:
        repo = MetricRepository(session)
        for table, days in retention_plan().items():
            dropped[table] = await repo.drop_old_partitions(table, days)
        await session.commit()
    if any(dropped.values()):
        logger.info(f"retention dropped partitions: {dropped}")
    return dropped


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="app.workers.tasks.metrics_tasks.rollup", base=BaseTaskWithRetry, bind=True)
def rollup(self) -> Dict[str, Any]:
    return _run(_rollup())


@celery_app.task(
    name="app.workers.tasks.metrics_tasks.create_partitions",
    base=BaseTaskWithRetry,
    bind=True,
)
def create_partitions(self) -> int:
    return _run(_create_partitions())


@celery_app.task(
    name="app.workers.tasks.metrics_tasks.retention", base=BaseTaskWithRetry, bind=True
)
def retention(self) -> Dict[str, int]:
    return _run(_retention())


@scheduled_job(
    "metrics.rollup_minutes",
    "interval",
    minutes=1,
    job_opts={"max_instances": 1, "coalesce": True},
)
def scheduled_rollup() -> None:
    try:
        run_async(_rollup())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"metrics rollup failed: {exc}")


@scheduled_job(
    "metrics.create_partitions",
    "interval",
    hours=1,
    job_opts={
        "max_instances": 1,
        "coalesce": True,
        "next_run_time": datetime.now(timezone.utc),
    },
)
def scheduled_partitions() -> None:
    try:
        run_async(_create_partitions())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"partition pre-creation failed: {exc}")


@scheduled_job(
    "metrics.retention",
    "interval",
    hours=1,
    job_opts={"max_instances": 1, "coalesce": True},
)
def scheduled_retention() -> None:
    try:
        run_async(_retention())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"metrics retention failed: {exc}")
