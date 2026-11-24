"""
HA lease job: every jobs process tries to take / renew the
``jobs-leader`` and ``rules-evaluator`` leases every HA_LEASE_RENEW_SECONDS.
See app/services/ha/service.py for the election and fencing protocol.
"""

from app.core.logger import get_logger
from app.core.settings import settings
from app.workers.scheduler import run_async, scheduled_job

logger = get_logger(__name__)

QUEUE = "default"


async def _renew_leases() -> dict:
    from app.services.ha.service import get_lease_manager

    return await get_lease_manager().tick()


@scheduled_job(
    "ha.leases",
    "interval",
    seconds=settings.HA_LEASE_RENEW_SECONDS,
    job_opts={"max_instances": 1, "coalesce": True, "misfire_grace_time": 5},
)
def renew_leases() -> None:
    """Acquire or renew this process's leases (never raises)."""
    try:
        run_async(_renew_leases())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"ha lease job failed: {exc}")
