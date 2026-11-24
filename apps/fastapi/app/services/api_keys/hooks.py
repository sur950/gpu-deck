"""
API key lifecycle hooks.

Serving process: start the Redis pub/sub listener that drops process-local
verification cache entries the moment a key is revoked/suspended/rotated in
the admin API, and stop it (plus any buffered last_used writes) on shutdown.
"""

from app.core.lifecycle import on_shutdown, on_startup
from app.core.logger import get_logger
from app.services.api_keys.service import verifier

logger = get_logger(__name__)


@on_startup("serving")
async def start_api_key_invalidation_listener() -> None:
    """Never fails startup: the subscriber reconnects on its own and TTLs bound staleness."""
    try:
        await verifier.start_subscriber()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"api key invalidation listener not started: {exc}")


@on_shutdown("serving")
async def stop_api_key_invalidation_listener() -> None:
    await verifier.stop_subscriber()
    await verifier.flush_pending()
