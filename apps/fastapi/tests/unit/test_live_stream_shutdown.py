"""
SSE streams end when the process shuts down (FULL-TEST #13 / MON-TEST: ``uvicorn --reload``
hung for minutes in "Waiting for connections to close" while a browser held
``/live/events`` open).
"""

from __future__ import annotations

import asyncio
import signal
import threading

from app.core import shutdown


async def test_live_event_stream_returns_once_shutting_down(monkeypatch):
    from app.services.metrics import live

    shutdown.clear()
    flt = live.LiveFilter(topics={"heartbeat"})
    frames = []

    async def consume():
        async for event in live.live_event_stream(flt, interval_seconds=1):
            frames.append(event)

    task = asyncio.ensure_future(consume())
    await asyncio.sleep(0.2)
    assert not task.done()  # an open stream waits for events
    shutdown.mark_shutting_down()
    try:
        await asyncio.wait_for(task, timeout=2.5)  # polls the flag at least once a second
    finally:
        shutdown.clear()
    assert task.done() and not task.cancelled()


def test_signal_hook_sets_the_flag_and_chains_the_previous_handler():
    assert threading.current_thread() is threading.main_thread()
    seen = []
    original = signal.getsignal(signal.SIGTERM)
    original_int = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, lambda signum, frame: seen.append(signum))
    try:
        shutdown.clear()
        assert shutdown.install_signal_hooks()
        assert shutdown.install_signal_hooks()  # idempotent: not chained twice
        signal.raise_signal(signal.SIGTERM)
        assert shutdown.shutting_down() and seen == [signal.SIGTERM]
    finally:
        signal.signal(signal.SIGTERM, original)
        signal.signal(signal.SIGINT, original_int)
        shutdown.clear()


def test_signal_hooks_are_skipped_outside_the_main_thread():
    out = []
    t = threading.Thread(target=lambda: out.append(shutdown.install_signal_hooks()))
    t.start()
    t.join()
    assert out == [False]
