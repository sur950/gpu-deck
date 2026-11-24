"""
Swap manager job: the ``jobs-leader`` starts a driver thread for every
non-terminal swap operation (bounded by RULES_MAX_CONCURRENT_SWAPS); swaps
resume from their persisted step after a restart or a leader change.
"""

from app.core.logger import get_logger
from app.core.settings import settings
from app.workers.scheduler import run_async, scheduled_job

logger = get_logger(__name__)

QUEUE = "default"


async def _drive() -> list:
    from app.services.ha.service import JOBS_LEADER, get_lease_manager
    from app.services.swap.service import swap_manager

    fence = get_lease_manager().fence(JOBS_LEADER)
    if fence is None:
        logger.debug("swap drive skipped: not the jobs leader")
        return []
    ids = await swap_manager.active_swap_ids()
    started = swap_manager.start(ids, fence)
    if started:
        logger.info(f"swap manager: driving {started}")
    return started


@scheduled_job(
    "swap.drive",
    "interval",
    seconds=settings.SWAP_DRIVE_INTERVAL_SECONDS,
    job_opts={"max_instances": 1, "coalesce": True, "misfire_grace_time": 5},
)
def drive_swaps() -> None:
    """Pick up pending / resumable swaps (leader only)."""
    try:
        run_async(_drive())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"swap drive failed: {exc}")
