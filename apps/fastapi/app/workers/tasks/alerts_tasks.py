"""
Alert delivery: Celery queue ``alerts`` sends the first attempt right
after ``alerts.emit``; the leader's ``alerts.retry_deliveries`` job retries
failed attempts with exponential backoff; daily retention of alert_events.
"""

import asyncio
from datetime import datetime, timedelta, timezone

from app.core.logger import get_logger
from app.core.settings import settings
from app.workers.celery_app import celery_app
from app.workers.scheduler import run_async, scheduled_job
from app.workers.tasks.base_task import BaseTaskWithRetry

logger = get_logger(__name__)

QUEUE = "alerts"


async def _deliver_event(event_id: str) -> dict:
    import uuid

    from app.services.alerts.service import deliver_event as deliver

    return await deliver(uuid.UUID(event_id))


@celery_app.task(
    name="app.workers.tasks.alerts_tasks.deliver_event",
    base=BaseTaskWithRetry,
    bind=True,
)
def deliver_event(self, event_id: str) -> dict:
    """First delivery attempt for every pending delivery of one alert event."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_deliver_event(event_id))
    finally:
        loop.close()


async def _retry_deliveries() -> dict:
    from app.services.alerts.service import retry_pending
    from app.services.ha.service import JOBS_LEADER, get_lease_manager

    if not get_lease_manager().is_leader(JOBS_LEADER):
        return {}
    counts = await retry_pending()
    if counts:
        logger.info(f"alert retries: {counts}")
    return counts


@scheduled_job(
    "alerts.retry_deliveries",
    "interval",
    seconds=30,
    job_opts={"max_instances": 1, "coalesce": True},
)
def retry_deliveries() -> None:
    """Retry pending alert deliveries whose backoff elapsed (leader only)."""
    try:
        run_async(_retry_deliveries())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"alert retry job failed: {exc}")


async def _purge_alert_events() -> int:
    from app.db.postgres.connection import get_session_maker
    from app.db.postgres.repositories.alert_repository import AlertEventRepository
    from app.services.ha.service import JOBS_LEADER, get_lease_manager

    days = int(settings.ALERT_EVENTS_RETENTION_DAYS)
    if days <= 0 or not get_lease_manager().is_leader(JOBS_LEADER):
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with get_session_maker()() as session:
        deleted = await AlertEventRepository(session).delete_older_than(cutoff)
        await session.commit()
    if deleted:
        logger.info(f"alert retention: deleted {deleted} events older than {days} days")
    return deleted


@scheduled_job("alerts.purge_events", "cron", hour=1, minute=45)
def purge_alert_events() -> None:
    """Daily alert_events retention (ALERT_EVENTS_RETENTION_DAYS)."""
    try:
        run_async(_purge_alert_events())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"alert retention failed: {exc}")
