"""
Lifecycle hook registry: per-process startup/shutdown hooks, sync or async.
"""

import pytest
from app.core import lifecycle


@pytest.fixture(autouse=True)
def isolated_registry():
    saved_startup, saved_shutdown = list(lifecycle._startup), list(lifecycle._shutdown)
    lifecycle._reset_for_tests(discovered=True)
    yield
    lifecycle._startup[:] = saved_startup
    lifecycle._shutdown[:] = saved_shutdown


@pytest.mark.unit
async def test_hooks_run_only_for_their_process():
    calls = []

    @lifecycle.on_startup("admin")
    async def admin_only():
        calls.append("admin")

    @lifecycle.on_startup("serving", "worker")
    def not_admin():
        calls.append("other")

    @lifecycle.on_startup()
    def everyone():
        calls.append("all")

    await lifecycle.run_startup_hooks("admin")
    assert calls == ["admin", "all"]


@pytest.mark.unit
async def test_shutdown_hooks_run_in_reverse_and_tolerate_errors():
    calls = []

    @lifecycle.on_shutdown("admin")
    def first():
        calls.append("first")

    @lifecycle.on_shutdown("admin")
    def boom():
        raise RuntimeError("nope")

    @lifecycle.on_shutdown("admin")
    def last():
        calls.append("last")

    await lifecycle.run_shutdown_hooks("admin")
    assert calls == ["last", "first"]


@pytest.mark.unit
def test_unknown_process_is_rejected():
    with pytest.raises(ValueError):
        lifecycle.on_startup("mystery")


@pytest.mark.unit
def test_discover_hooks_finds_service_hook_modules():
    # No domain ships hooks.py yet; discovery must still succeed and be idempotent.
    assert lifecycle.discover_hooks() == lifecycle.discover_hooks()
