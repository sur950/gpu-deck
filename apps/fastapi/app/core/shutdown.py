"""
Process shutdown flag for long-lived responses (SSE streams).

uvicorn waits for open responses before it runs the lifespan shutdown, so an
endless ``/live/events`` or ``/logs/stream`` response held the admin API in
"Waiting for connections to close" (``--reload`` hung for minutes while a
browser or the desktop app kept a stream open). ``install_signal_hooks``
chains a handler in front of uvicorn's SIGINT / SIGTERM handler that only sets
the flag; streaming generators poll ``shutting_down()`` (at least once a
second) and end, so the connection completes and shutdown goes on.
``bootstrap.dev.sh`` also passes ``--timeout-graceful-shutdown`` as a backstop.
"""

from __future__ import annotations

import os
import signal
import threading
from typing import Any

from app.core.logger import get_logger

logger = get_logger(__name__)

_shutting_down = threading.Event()
_HOOK_ATTR = "_gpudeck_shutdown_hook"


def shutting_down() -> bool:
    """True once the process was asked to stop."""
    return _shutting_down.is_set()


def mark_shutting_down() -> None:
    _shutting_down.set()


def clear() -> None:
    """Startup (and tests): a fresh process start is not shutting down."""
    _shutting_down.clear()


def _chain(previous: Any):
    def handler(signum: int, frame: Any) -> None:
        mark_shutting_down()
        if callable(previous):
            previous(signum, frame)
            return
        if previous == signal.SIG_IGN:
            return
        # Default disposition: restore it and re-deliver the signal.
        signal.signal(signum, signal.SIG_DFL if previous is None else previous)
        os.kill(os.getpid(), signum)

    setattr(handler, _HOOK_ATTR, True)
    return handler


def install_signal_hooks() -> bool:
    """Chain the flag in front of the current SIGINT / SIGTERM handlers (main thread only)."""
    if threading.current_thread() is not threading.main_thread():
        return False
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous = signal.getsignal(sig)
        if getattr(previous, _HOOK_ATTR, False):
            continue
        try:
            signal.signal(sig, _chain(previous))
        except (ValueError, OSError) as exc:  # pragma: no cover - exotic runtimes
            logger.debug(f"shutdown hook for {sig} not installed: {exc}")
            return False
    return True
