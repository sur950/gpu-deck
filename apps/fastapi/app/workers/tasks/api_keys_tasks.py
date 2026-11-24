"""
API key jobs, run by the scheduler (jobs) process:

- ``api_keys.flush_last_used`` (every minute): move the ``last_used_at``
  timestamps the serving API buffers in Redis (``apikeys:last_used`` hash)
  into Postgres, so the request path never writes to the database.
- ``api_keys.expire`` (every 5 minutes): flip active keys past ``expires_at``
  to ``expired`` and invalidate their verification cache entries. The verifier
  already rejects an expired key on its own; this keeps the stored status and
  the admin UI truthful.
"""

from app.core.logger import get_logger
from app.workers.scheduler import run_async, scheduled_job

logger = get_logger(__name__)

QUEUE = "api_keys"


async def _flush_last_used() -> int:
    from app.services.api_keys.service import flush_last_used

    return await flush_last_used()


async def _expire_keys() -> int:
    from app.services.api_keys.service import expire_due_keys

    return await expire_due_keys()


@scheduled_job("api_keys.flush_last_used", "interval", minutes=1, job_opts={"coalesce": True})
def flush_api_key_last_used() -> None:
    """Redis last_used buffer -> api_keys.last_used_at."""
    try:
        updated = run_async(_flush_last_used())
        if updated:
            logger.debug(f"api key last_used flushed: {updated} row(s)")
    except Exception as exc:  # noqa: BLE001 - next run retries; the buffer is kept on DB errors
        logger.error(f"api key last_used flush failed: {exc}")


@scheduled_job("api_keys.expire", "interval", minutes=5, job_opts={"coalesce": True})
def expire_api_keys() -> None:
    """Mark keys past expires_at as expired."""
    try:
        run_async(_expire_keys())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"api key expiry job failed: {exc}")
