"""
Metering tasks (Celery queue ``metering``).

- ``write_usage_event``: one ``usage_events`` row per serving request,
  enqueued by the API layer after the response (``enqueue_usage_event``,
  never raises, never on the hot path).
- ``metering.rollup`` (every ``METERING_ROLLUP_INTERVAL_MINUTES``): rebuild
  the current and previous hour of ``usage_rollups_hourly`` incl. billable
  GPU-minute allocation (``services/metering/rollup.py``).
- ``metering.retention`` (daily): drop ``usage_events`` partitions older than
  ``USAGE_EVENTS_RETENTION_DAYS``.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict

from app.core.logger import get_logger
from app.core.settings import settings
from app.workers.celery_app import celery_app
from app.workers.scheduler import run_async, scheduled_job
from app.workers.tasks.base_task import BaseTaskWithRetry

logger = get_logger(__name__)

QUEUE = "metering"


async def _write_usage_event(payload: Dict[str, Any]) -> int:
    from app.db.postgres.connection import get_session_maker
    from app.services.metering.writer import write_usage_event as write

    async with get_session_maker()() as session:
        event_id = await write(session, payload)
        await session.commit()
    return event_id


@celery_app.task(
    name="app.workers.tasks.metering_tasks.write_usage_event",
    base=BaseTaskWithRetry,
    bind=True,
)
def write_usage_event(self, payload: Dict[str, Any]) -> int:
    """Insert one usage event (sync Celery entry around the async impl)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_write_usage_event(payload))
    finally:
        loop.close()


def enqueue_usage_event(payload: Dict[str, Any]) -> None:
    """Hand a usage payload to the metering queue. Never raises."""
    try:
        write_usage_event.apply_async(kwargs={"payload": payload}, queue=QUEUE)
    except Exception as exc:  # noqa: BLE001
        # Broker down: keep the event recoverable from the logs rather than lose it silently.
        logger.warning(
            f"enqueue_usage_event failed ({exc}); usage={json.dumps(payload, default=str)}"
        )


async def _rollup_recent() -> Dict[str, int]:
    from app.db.postgres.connection import get_session_maker
    from app.services.metering.rollup import rollup_recent

    async with get_session_maker()() as session:
        written = await rollup_recent(session)
        await session.commit()
    return written


@scheduled_job(
    "metering.rollup",
    "interval",
    minutes=max(1, int(settings.METERING_ROLLUP_INTERVAL_MINUTES)),
    job_opts={"max_instances": 1, "coalesce": True},
)
def rollup_usage() -> None:
    """Hourly usage rollups + GPU-minute allocation for the current and previous hour."""
    try:
        written = run_async(_rollup_recent())
        logger.debug(f"metering rollup: {written}")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"metering rollup failed: {exc}")


async def _purge_usage_events() -> int:
    from app.db.postgres.connection import get_session_maker
    from app.db.postgres.repositories.usage_event_repository import UsageEventRepository

    days = int(settings.USAGE_EVENTS_RETENTION_DAYS)
    if days <= 0:
        return 0
    async with get_session_maker()() as session:
        dropped = await UsageEventRepository(session).drop_old_partitions(days)
        await session.commit()
    if dropped:
        logger.info(f"usage retention: dropped {dropped} usage_events partitions (> {days} days)")
    return dropped


@scheduled_job("metering.retention", "cron", hour=1, minute=40)
def purge_usage_events() -> None:
    """Daily usage_events retention (USAGE_EVENTS_RETENTION_DAYS)."""
    try:
        run_async(_purge_usage_events())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"usage retention failed at {datetime.now(timezone.utc)}: {exc}")
