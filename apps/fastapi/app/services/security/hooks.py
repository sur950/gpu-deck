"""
Security lifecycle hooks.

Admin and serving processes: warm the per-scope IP rule snapshots at startup
so the first request does not pay the Postgres read. A failure is logged, not
fatal: the checker loads lazily and fails open on infrastructure errors.

Admin process: give any TTL-less rate-limit counter its window as TTL (a
fixed-window counter without one blocks its endpoint forever); the scheduler
repeats this every 5 minutes (``security.repair_limiter_ttls``).
"""

from app.core.lifecycle import on_startup
from app.core.logger import get_logger
from app.core.rate_limiter import repair_limiter_ttls
from app.core.settings import settings
from app.services.security.ip_rules import warm_cache

logger = get_logger(__name__)


@on_startup("admin", "serving")
async def warm_ip_rules_cache() -> None:
    try:
        await warm_cache()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"ip_rules cache warm-up skipped: {exc}")


@on_startup("admin")
async def repair_rate_limit_ttls() -> None:
    if not settings.RATE_LIMIT_ENABLED:
        return
    try:
        fixed = await repair_limiter_ttls()
        if fixed:
            logger.warning(f"rate limiter: set a TTL on {fixed} TTL-less key(s)")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"rate limiter TTL repair skipped: {exc}")
