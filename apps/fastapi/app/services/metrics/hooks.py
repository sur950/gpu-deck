"""
Admin process lifecycle for the live streams: end SSE responses promptly on
shutdown (``app.core.shutdown``) so ``uvicorn --reload`` and restarts do not
wait on browsers holding ``/live/events`` or ``/logs/stream`` open.
"""

from __future__ import annotations

from app.core.lifecycle import on_shutdown, on_startup
from app.core.logger import get_logger
from app.core.shutdown import clear, install_signal_hooks, mark_shutting_down

logger = get_logger(__name__)


@on_startup("admin")
async def hook_shutdown_signals() -> None:
    clear()
    if install_signal_hooks():
        logger.debug("live streams end on SIGINT / SIGTERM")


@on_shutdown("admin")
async def end_live_streams() -> None:
    mark_shutting_down()
