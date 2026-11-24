"""
Cloud VM jobs:

- ``cloud.reconcile`` (every ``CLOUD_RECONCILE_SECONDS``): advance in-flight
  cloud VMs (create, boot/join, drain, stop, terminate), re-read running /
  stopped VMs from the provider, apply the idle auto-terminate option.
- Celery task ``cloud_tasks.advance(id)`` (queue ``cloud``): one lifecycle step
  for one VM right after an API action, so users do not wait for the tick.
"""

from typing import Any, Dict, Optional

from app.core.logger import get_logger
from app.core.settings import settings
from app.workers.celery_app import celery_app
from app.workers.scheduler import run_async, scheduled_job
from app.workers.tasks.base_task import BaseTaskWithRetry

logger = get_logger(__name__)

QUEUE = "cloud"

BEAT_SCHEDULE: Dict[str, Any] = {}


async def _reconcile() -> Dict[str, int]:
    from app.db.postgres.connection import get_session_maker
    from app.services.cloud.lifecycle import CloudLifecycle

    async with get_session_maker()() as session:
        summary = await CloudLifecycle(session).tick()
        await session.commit()
        return summary


async def _advance(row_id: str) -> Optional[str]:
    import uuid

    from app.db.postgres.connection import get_session_maker
    from app.services.cloud.lifecycle import CloudLifecycle

    async with get_session_maker()() as session:
        result = await CloudLifecycle(session).advance(uuid.UUID(row_id))
        await session.commit()
        return result


@celery_app.task(name="app.workers.tasks.cloud_tasks.advance", base=BaseTaskWithRetry, bind=True)
def advance(self, row_id: str) -> Optional[str]:
    return run_async(_advance(row_id))


@scheduled_job(
    "cloud.reconcile",
    "interval",
    seconds=int(settings.CLOUD_RECONCILE_SECONDS),
    job_opts={"max_instances": 1, "coalesce": True},
)
def scheduled_reconcile() -> None:
    try:
        summary = run_async(_reconcile())
        if any(summary.values()):
            logger.info(f"cloud reconcile: {summary}")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"cloud reconcile failed: {exc}")
