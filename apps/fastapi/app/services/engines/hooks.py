"""
Engine registry lifecycle hooks: seed the built-in engine versions on admin
API startup (``001_init`` only seeds the engines themselves).
"""

from app.core.lifecycle import on_startup
from app.core.logger import get_logger

logger = get_logger(__name__)


@on_startup("admin")
async def seed_engine_versions() -> None:
    """Insert pinned engine versions for engines that have none (idempotent)."""
    from app.db.postgres.connection import get_session_maker
    from app.services.engines.service import EngineService

    try:
        async with get_session_maker()() as session:
            created = await EngineService.for_session(session).seed_builtin_versions()
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - a seed failure must not block boot
        logger.error(f"engine version seed failed: {type(exc).__name__}: {exc}")
        return
    logger.info(f"engine versions seed done (created={created})")
