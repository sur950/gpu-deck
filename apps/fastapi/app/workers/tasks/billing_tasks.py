"""
Billing tasks (Celery queue ``billing``).

- ``generate_billing_report``: price a report's rollups with the current
  plans (``POST /billing/reports`` and ``/regenerate`` enqueue it).
- ``billing.monthly_report`` (daily 00:30 UTC): on
  ``BILLING_MONTHLY_REPORT_DAY`` create + generate last month's report once.
"""

import asyncio
from typing import Any, Dict

from app.core.logger import get_logger
from app.workers.celery_app import celery_app
from app.workers.scheduler import run_async, scheduled_job
from app.workers.tasks.base_task import BaseTaskWithRetry

logger = get_logger(__name__)

QUEUE = "billing"


async def _generate(report_id: str) -> Dict[str, Any]:
    from app.db.postgres.connection import get_session_maker
    from app.services.billing.service import generate_report

    async with get_session_maker()() as session:
        return await generate_report(session, report_id)


@celery_app.task(
    name="app.workers.tasks.billing_tasks.generate_billing_report",
    base=BaseTaskWithRetry,
    bind=True,
)
def generate_billing_report(self, report_id: str) -> str:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(_generate(report_id))
        return str(result.get("status"))
    finally:
        loop.close()


def enqueue_billing_report(report_id: str) -> bool:
    """Queue report generation; False when the broker is unavailable (caller runs inline)."""
    try:
        generate_billing_report.apply_async(kwargs={"report_id": str(report_id)}, queue=QUEUE)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"enqueue_billing_report failed: {exc}")
        return False


async def _monthly() -> Any:
    from app.db.postgres.connection import get_session_maker
    from app.services.billing.service import ensure_monthly_report

    async with get_session_maker()() as session:
        return await ensure_monthly_report(session)


@scheduled_job("billing.monthly_report", "cron", hour=0, minute=30)
def monthly_report() -> None:
    try:
        created = run_async(_monthly())
        if created:
            logger.info(f"monthly billing report generated: {created}")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"monthly billing report failed: {exc}")
