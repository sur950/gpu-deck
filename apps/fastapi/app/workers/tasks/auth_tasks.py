"""
Auth jobs, run by the scheduler (jobs) process:

- ``auth.purge_sessions`` (daily): delete session rows that expired or were
  revoked more than SESSION_PURGE_GRACE_DAYS (7) days ago. Live sessions are untouched;
  Redis entries expire on their own TTL.
"""

from datetime import datetime, timedelta, timezone

from app.core.logger import get_logger
from app.workers.scheduler import run_async, scheduled_job

logger = get_logger(__name__)

QUEUE = "auth"

SESSION_PURGE_GRACE_DAYS = 7


async def _purge_sessions(grace_days: int = SESSION_PURGE_GRACE_DAYS) -> int:
    from app.db.postgres.connection import get_session_maker
    from app.db.postgres.repositories.session_repository import SessionRepository

    cutoff = datetime.now(timezone.utc) - timedelta(days=grace_days)
    async with get_session_maker()() as session:
        deleted = await SessionRepository(session).purge_expired(cutoff)
        await session.commit()
    if deleted:
        logger.info(f"auth: purged {deleted} expired/revoked session row(s)")
    return deleted


@scheduled_job("auth.purge_sessions", "cron", hour=1, minute=30)
def purge_expired_sessions() -> None:
    """Daily cleanup of dead session rows."""
    try:
        run_async(_purge_sessions())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"session purge failed: {exc}")
