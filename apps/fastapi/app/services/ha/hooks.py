"""
HA lifecycle: the jobs process takes its leases right at start-up (no
waiting for the first ``ha.leases`` tick) and releases them on shutdown so a
standby takes over immediately instead of after HA_LEASE_TTL_SECONDS.
"""

from app.core.lifecycle import on_shutdown, on_startup
from app.core.logger import get_logger

logger = get_logger(__name__)


@on_startup("scheduler")
async def acquire_leases() -> None:
    from app.services.ha.service import get_lease_manager

    held = await get_lease_manager().tick()
    logger.info(f"ha: start-up lease state {held}")


@on_shutdown("scheduler")
async def release_leases() -> None:
    from app.services.ha.service import MANAGED_LEASES, get_lease_manager

    manager = get_lease_manager()
    for name in MANAGED_LEASES:
        if manager.state.get(name) is not None:
            await manager.release(name)
