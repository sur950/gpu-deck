"""
Audit tasks: the off-hot-path audit writer (Celery queue ``audit``) and the
retention job that deletes rows older than AUDIT_RETENTION_DAYS.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from app.core.logger import get_logger
from app.core.settings import settings
from app.workers.celery_app import celery_app
from app.workers.scheduler import run_async, scheduled_job
from app.workers.tasks.base_task import BaseTaskWithRetry

logger = get_logger(__name__)

QUEUE = settings.AUDIT_QUEUE


async def _write_audit_log(event: Dict[str, Any]) -> None:
    from app.db.postgres.connection import get_session_maker
    from app.services.audit.service import write_audit_event

    async with get_session_maker()() as session:
        await write_audit_event(session, event)
        await session.commit()


@celery_app.task(
    name="app.workers.tasks.audit_tasks.write_audit_log",
    base=BaseTaskWithRetry,
    bind=True,
)
def write_audit_log(self, event: Dict[str, Any]) -> None:
    """Insert one audit row (sync Celery entry around the async impl)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_write_audit_log(event))
    finally:
        loop.close()


def enqueue_audit_log(event: Dict[str, Any]) -> None:
    """
    Hand an event to the audit queue. Never raises. If the broker is down the
    event is written directly (best effort) so audit never silently drops
    because Redis hiccupped.
    """
    try:
        write_audit_log.apply_async(kwargs={"event": event}, queue=QUEUE)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"enqueue_audit_log failed ({exc}); writing directly")
        try:
            from app.services.audit.service import write_audit_event_standalone

            asyncio.run(write_audit_event_standalone(event))
        except Exception as inner:  # noqa: BLE001
            logger.warning(f"audit direct write failed (event lost): {inner}")


async def _purge_old_audit_logs() -> int:
    from app.db.postgres.connection import get_session_maker
    from app.db.postgres.repositories.audit_log_repository import AuditLogRepository

    days = int(settings.AUDIT_RETENTION_DAYS)
    if days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with get_session_maker()() as session:
        deleted = await AuditLogRepository(session).delete_older_than(cutoff)
        await session.commit()
    if deleted:
        logger.info(f"audit retention: deleted {deleted} rows older than {days} days")
    return deleted


@scheduled_job("audit.purge_old", "cron", hour=1, minute=15)
def purge_old_audit_logs() -> None:
    """Daily audit retention (AUDIT_RETENTION_DAYS, 0 = keep forever)."""
    try:
        run_async(_purge_old_audit_logs())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"audit retention failed: {exc}")
