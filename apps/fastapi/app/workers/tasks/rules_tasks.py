"""
Rules engine jobs: the evaluator tick (leader of ``rules-evaluator``
only) and the daily rule_runs retention.
"""

from datetime import datetime, timedelta, timezone

from app.core.logger import get_logger
from app.core.settings import settings
from app.workers.scheduler import run_async, scheduled_job

logger = get_logger(__name__)

QUEUE = "default"


async def _tick() -> dict:
    from app.services.ha.service import RULES_EVALUATOR, get_lease_manager
    from app.services.rules.service import evaluate_due_rules

    leases = get_lease_manager()
    fence = leases.fence(RULES_EVALUATOR)
    if fence is None:
        logger.debug("rules tick skipped: not the rules-evaluator leader")
        return {}
    counts = await evaluate_due_rules(fence=fence)
    if counts:
        logger.info(f"rules tick: {counts}")
    return counts


@scheduled_job(
    "rules.tick",
    "interval",
    seconds=settings.RULES_TICK_SECONDS,
    job_opts={"max_instances": 1, "coalesce": True, "misfire_grace_time": 5},
)
def rules_tick() -> None:
    """Evaluate every due rule (leader only)."""
    try:
        run_async(_tick())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"rules tick failed: {exc}")


async def _purge_rule_runs() -> int:
    from app.db.postgres.connection import get_session_maker
    from app.db.postgres.repositories.rule_repository import RuleRunRepository
    from app.services.ha.service import JOBS_LEADER, get_lease_manager

    days = int(settings.RULE_RUNS_RETENTION_DAYS)
    if days <= 0 or not get_lease_manager().is_leader(JOBS_LEADER):
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with get_session_maker()() as session:
        deleted = await RuleRunRepository(session).delete_older_than(cutoff)
        await session.commit()
    if deleted:
        logger.info(f"rule_runs retention: deleted {deleted} rows older than {days} days")
    return deleted


@scheduled_job("rules.purge_runs", "cron", hour=1, minute=35)
def purge_rule_runs() -> None:
    """Daily rule_runs retention (RULE_RUNS_RETENTION_DAYS)."""
    try:
        run_async(_purge_rule_runs())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"rule_runs retention failed: {exc}")
