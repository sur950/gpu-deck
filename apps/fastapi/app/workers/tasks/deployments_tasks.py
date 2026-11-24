"""
Deployment jobs, all in the jobs process (APScheduler):

* ``deployments.reconcile`` — desired replicas -> instances, command outcomes,
  timeouts (``app/services/deployments/reconcile.py``); a Redis lock keeps one
  reconciler active across jobs processes.
* ``routing.publish`` — refresh the API layer routing table (live sessions).
* ``deployments.prune_instances`` — delete stopped instance rows past retention.
"""

from datetime import datetime, timedelta, timezone

from app.core.logger import get_logger
from app.core.settings import settings
from app.workers.scheduler import run_async, scheduled_job

logger = get_logger(__name__)

QUEUE = "deployments"


async def _reconcile() -> dict:
    from app.services.deployments.reconcile import run_reconcile_tick

    summary = await run_reconcile_tick()
    if summary.get("placed") or summary.get("stopped") or summary.get("applied"):
        logger.info(f"deployments reconcile: {summary}")
    return summary


@scheduled_job(
    "deployments.reconcile",
    "interval",
    seconds=settings.DEPLOYMENT_RECONCILE_INTERVAL_SECONDS,
    job_opts={"max_instances": 1, "coalesce": True, "misfire_grace_time": 10},
)
def reconcile_deployments() -> None:
    """One reconciliation tick."""
    try:
        run_async(_reconcile())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"deployments reconcile failed: {exc}")


async def _publish() -> None:
    from app.services.deployments.routing_publisher import publish_routing_table

    await publish_routing_table()


@scheduled_job(
    "routing.publish",
    "interval",
    seconds=settings.ROUTING_PUBLISH_INTERVAL_SECONDS,
    job_opts={"max_instances": 1, "coalesce": True, "misfire_grace_time": 2},
)
def publish_routing() -> None:
    """Rewrite the routing table with live session counts."""
    try:
        run_async(_publish())
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"routing publish failed: {exc}")


async def _prune() -> int:
    from app.db.postgres.connection import get_session_maker
    from app.db.postgres.repositories.instance_repository import InstanceRepository

    before = datetime.now(timezone.utc) - timedelta(
        days=settings.DEPLOYMENT_STOPPED_INSTANCE_RETENTION_DAYS
    )
    async with get_session_maker()() as session:
        removed = await InstanceRepository(session).prune_stopped(before)
        await session.commit()
    return removed


@scheduled_job("deployments.prune_instances", "cron", hour=3, minute=17)
def prune_instances() -> None:
    """Delete stopped instances older than the retention window."""
    try:
        removed = run_async(_prune())
        if removed:
            logger.info(f"pruned {removed} stopped instance row(s)")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"instance prune failed: {exc}")
