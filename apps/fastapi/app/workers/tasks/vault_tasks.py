"""
Vault jobs, run by the scheduler (jobs) process:

- ``vault.expire_credentials`` (daily, 00:05 UTC): mark credentials past
  ``expires_at`` as expired and drop their cached ciphertext.
- ``vault.rotate_keys`` (hourly): after a new key is prepended to VAULT_KEYS,
  re-encrypt every credential still on an older key (MultiFernet.rotate). A
  no-op when everything is on the primary key, so the old key can be removed
  from .env once a run reports nothing left to rotate.
"""

from app.core.logger import get_logger
from app.workers.scheduler import run_async, scheduled_job

logger = get_logger(__name__)

QUEUE = "vault"


async def _with_vault(method: str) -> int:
    from app.db.postgres.connection import get_session_maker
    from app.services.vault.service import VaultService

    async with get_session_maker()() as session:
        result = await getattr(VaultService(session), method)()
        await session.commit()
    return int(result or 0)


async def _expire_credentials() -> int:
    return await _with_vault("expire_credentials")


async def _rotate_keys() -> int:
    return await _with_vault("rotate_keys")


@scheduled_job("vault.expire_credentials", "cron", hour=0, minute=5)
def expire_credentials() -> None:
    """Daily credential expiry."""
    try:
        run_async(_expire_credentials())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"credential expiry job failed: {exc}")


@scheduled_job("vault.rotate_keys", "interval", hours=1, job_opts={"coalesce": True})
def rotate_vault_keys() -> None:
    """Re-encrypt credentials still on an old VAULT_KEYS entry."""
    try:
        rotated = run_async(_rotate_keys())
        if rotated:
            logger.info(f"vault rotation: re-encrypted {rotated} credential(s)")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"vault rotation job failed: {exc}")
