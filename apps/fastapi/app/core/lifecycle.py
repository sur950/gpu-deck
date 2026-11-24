"""
Process lifecycle hooks — the extension point for startup/shutdown work.

Each process (``admin`` = app.main, ``serving`` = app.api_layer_main,
``worker`` = Celery, ``scheduler`` = APScheduler loop) runs the hooks
registered for it, in registration order, without the entrypoint knowing
which domains exist.

A domain registers hooks by dropping ``app/services/<domain>/hooks.py``::

    from app.core.lifecycle import on_startup, on_shutdown

    @on_startup("admin", "serving")           # processes this hook applies to
    async def warm_routing_table() -> None: ...

    @on_shutdown("admin")
    async def close_clients() -> None: ...

Hooks may be sync or async. A failing startup hook aborts the process (a
half-initialised domain is worse than a crash); a failing shutdown hook is
logged and the rest still run. ``"*"`` registers for every process.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import pkgutil
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional, Sequence, Union

from app.core.logger import get_logger

logger = get_logger(__name__)

PROCESSES = ("admin", "serving", "worker", "scheduler")
HookFn = Callable[[], Union[None, Awaitable[None]]]


@dataclass
class _Hook:
    fn: HookFn
    processes: Sequence[str]
    name: str = field(init=False)

    def __post_init__(self) -> None:
        self.name = f"{self.fn.__module__}.{self.fn.__qualname__}"

    def applies_to(self, process: str) -> bool:
        return "*" in self.processes or process in self.processes


_startup: List[_Hook] = []
_shutdown: List[_Hook] = []
_discovered = False


def _validate(processes: Sequence[str]) -> Sequence[str]:
    if not processes:
        return ("*",)
    for p in processes:
        if p != "*" and p not in PROCESSES:
            raise ValueError(f"Unknown process {p!r}; expected one of {PROCESSES} or '*'")
    return processes


def on_startup(*processes: str) -> Callable[[HookFn], HookFn]:
    """Register a startup hook for the given processes (default: all)."""
    procs = _validate(processes)

    def decorator(fn: HookFn) -> HookFn:
        _startup.append(_Hook(fn, procs))
        return fn

    return decorator


def on_shutdown(*processes: str) -> Callable[[HookFn], HookFn]:
    """Register a shutdown hook for the given processes (default: all)."""
    procs = _validate(processes)

    def decorator(fn: HookFn) -> HookFn:
        _shutdown.append(_Hook(fn, procs))
        return fn

    return decorator


def discover_hooks(package: str = "app.services") -> List[str]:
    """Import every ``<package>.<domain>.hooks`` module that exists. Idempotent."""
    global _discovered
    imported: List[str] = []
    pkg = importlib.import_module(package)
    for module_info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if not module_info.ispkg or module_info.name.startswith("_"):
            continue
        hooks_module = f"{package}.{module_info.name}.hooks"
        try:
            spec = importlib.util.find_spec(hooks_module)
        except ModuleNotFoundError:
            spec = None
        if spec is None:
            continue
        importlib.import_module(hooks_module)
        imported.append(hooks_module)
    _discovered = True
    if imported:
        logger.info(f"Lifecycle hooks discovered: {', '.join(imported)}")
    return imported


async def _call(hook: _Hook) -> None:
    result = hook.fn()
    if inspect.isawaitable(result):
        await result


async def run_startup_hooks(process: str) -> None:
    if not _discovered:
        discover_hooks()
    for hook in [h for h in _startup if h.applies_to(process)]:
        logger.info(f"[{process}] startup hook: {hook.name}")
        await _call(hook)


async def run_shutdown_hooks(process: str) -> None:
    for hook in reversed([h for h in _shutdown if h.applies_to(process)]):
        logger.info(f"[{process}] shutdown hook: {hook.name}")
        try:
            await _call(hook)
        except Exception as exc:  # noqa: BLE001 - keep shutting down
            logger.error(f"Shutdown hook {hook.name} failed: {exc}")


def run_startup_hooks_sync(process: str) -> None:
    """For the Celery worker / scheduler processes (no running event loop)."""
    asyncio.run(run_startup_hooks(process))


def run_shutdown_hooks_sync(process: str) -> None:
    asyncio.run(run_shutdown_hooks(process))


def registered_hooks() -> Dict[str, List[str]]:
    """Introspection for tests and `bootstrap.dev.sh status`."""
    return {
        "startup": [f"{h.name} -> {list(h.processes)}" for h in _startup],
        "shutdown": [f"{h.name} -> {list(h.processes)}" for h in _shutdown],
    }


def _reset_for_tests(discovered: Optional[bool] = False) -> None:
    global _discovered
    _startup.clear()
    _shutdown.clear()
    _discovered = bool(discovered)
