"""
Security jobs, run by the scheduler (jobs) process:

- ``security.repair_limiter_ttls`` (every 5 minutes): give any rate-limit
  counter under ``gpu-deck:LIMITS:`` that has no TTL its window as TTL. A
  fixed-window counter without a TTL never resets and keeps its endpoint at
  429 forever; the limiter storage already re-arms the TTL on every hit, this
  catches keys nobody hits (and runs at admin startup too).
"""

from app.core.logger import get_logger
from app.workers.scheduler import run_async, scheduled_job

logger = get_logger(__name__)

QUEUE = "security"


async def _repair_limiter_ttls() -> int:
    from app.core.rate_limiter import repair_limiter_ttls

    return await repair_limiter_ttls()


@scheduled_job("security.repair_limiter_ttls", "interval", minutes=5, job_opts={"coalesce": True})
def repair_limiter_ttls_job() -> None:
    """Set a TTL on TTL-less rate-limit counters."""
    from app.core.settings import settings

    if not settings.RATE_LIMIT_ENABLED:
        return
    try:
        fixed = run_async(_repair_limiter_ttls())
        if fixed:
            logger.warning(f"rate limiter: set a TTL on {fixed} TTL-less key(s)")
    except Exception as exc:  # noqa: BLE001 - next run retries
        logger.error(f"rate limiter TTL repair failed: {exc}")
