"""
Celery application configuration with task auto-discovery.

Extension point
---------------
Drop ``app/workers/tasks/<domain>_tasks.py``; it is imported automatically
(``conf.imports`` + an eager import for producers) so its ``@celery_app.task``
decorators register. Declare the module knobs ABOVE the first task::

    QUEUE = "metrics"                      # route every task in the module to this queue
                                           # (queue is created if missing); default "default"
    BEAT_SCHEDULE = {                      # merged into celery beat
        "rollup-metrics-minutely": {
            "task": "app.workers.tasks.metrics_tasks.rollup_minute",
            "schedule": crontab(),         # or a number of seconds
        },
    }

The knobs are read when the module's first ``@celery_app.task`` decorator
runs, so they take effect in every Celery process regardless of import order
(spawned pool/beat children import the task module before this one).

Task shape::

    async def _impl(...):                                   # async work on a fresh session
        async with get_session_maker()() as session: ...

    @celery_app.task(name="app.workers.tasks.x_tasks.do_x", base=BaseTaskWithRetry, bind=True)
    def do_x(self, ...):                                    # sync Celery entry
        run_async(_impl(...))

    def enqueue_do_x(...) -> None:                          # called from the API; never raises
        try: do_x.apply_async(kwargs={...})
        except Exception as exc: logger.warning(f"enqueue_do_x failed: {exc}")

Keep heavy imports inside functions: every task module is imported by every
process that imports ``app.workers.celery_app`` (the APIs enqueue through it).

Redis keys: every key lives under REDIS_KEY_PREFIX, so ``global_keyprefix`` is
set on broker and backend (the dev Redis ACL allows keys under ``gpu-deck:``
only; pub/sub channels are unrestricted).
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from typing import Any, Callable, Dict, List, Set

from app.core.logger import get_logger
from app.core.settings import settings
from celery import Celery, signals

logger = get_logger(__name__)

TASKS_PACKAGE = "app.workers.tasks"
TASK_MODULE_SUFFIX = "_tasks"

_transport_options = {
    "global_keyprefix": settings.REDIS_KEY_PREFIX,
}


def discover_task_modules() -> List[str]:
    """Module paths of every ``app/workers/tasks/*_tasks.py``, sorted."""
    package = importlib.import_module(TASKS_PACKAGE)
    names = []
    for module_info in pkgutil.iter_modules(package.__path__):
        name = module_info.name
        if name.endswith(TASK_MODULE_SUFFIX) and not name.startswith("_"):
            names.append(f"{TASKS_PACKAGE}.{name}")
    return sorted(names)


def _queue_entry(name: str) -> Dict[str, str]:
    return {"exchange": name, "routing_key": name}


class GpuDeckCelery(Celery):
    """Celery app whose ``task`` decorator also wires the module's QUEUE / BEAT_SCHEDULE."""

    _configured_modules: Set[str]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._configured_modules = set()

    def register_task_module(self, module_name: str, from_decorator: bool = False) -> None:
        """Apply QUEUE and BEAT_SCHEDULE declared by ``module_name``. Idempotent."""
        if module_name in self._configured_modules:
            return
        module = sys.modules.get(module_name)
        if module is None:
            return
        if not from_decorator and getattr(getattr(module, "__spec__", None), "_initializing", 0):
            # Still executing its top level (it imported this module first, which then
            # imports every task module): QUEUE may not be defined yet. Its @celery_app.task
            # decorators register it when they run (QUEUE is declared above them).
            return
        self._configured_modules.add(module_name)

        queue = getattr(module, "QUEUE", "default")
        task_queues = dict(self.conf.task_queues or {})
        task_queues.setdefault("default", _queue_entry("default"))
        task_queues.setdefault(queue, _queue_entry(queue))
        self.conf.task_queues = task_queues

        task_routes = dict(self.conf.task_routes or {})
        task_routes[f"{module_name}.*"] = {"queue": queue}
        self.conf.task_routes = task_routes

        beat_schedule = dict(self.conf.beat_schedule or {})
        for key, entry in (getattr(module, "BEAT_SCHEDULE", None) or {}).items():
            if key in beat_schedule and beat_schedule[key].get("task") != entry.get("task"):
                raise RuntimeError(f"Duplicate beat schedule key {key!r} in {module_name}")
            entry = dict(entry)
            entry.setdefault("options", {}).setdefault("queue", queue)
            beat_schedule[key] = entry
        self.conf.beat_schedule = beat_schedule
        logger.debug(f"Celery module registered: {module_name} queue={queue}")

    def task(self, *args: Any, **opts: Any) -> Any:  # type: ignore[override]
        # Bare ``@celery_app.task``
        if len(args) == 1 and callable(args[0]) and not opts:
            self.register_task_module(args[0].__module__, from_decorator=True)
            return super().task(args[0])

        inner = super().task(*args, **opts)

        def decorator(fun: Callable[..., Any]) -> Any:
            self.register_task_module(fun.__module__, from_decorator=True)
            return inner(fun)

        return decorator


celery_app = GpuDeckCelery(
    "gpu_deck_workers",
    broker=settings.get_celery_broker_url(),
    backend=settings.get_celery_result_backend(),
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone=settings.TZ,
    enable_utc=True,
    task_track_started=settings.CELERY_TASK_TRACK_STARTED,
    task_time_limit=settings.CELERY_TASK_TIME_LIMIT,
    task_soft_time_limit=max(settings.CELERY_TASK_TIME_LIMIT - 30, 30),
    worker_prefetch_multiplier=4,
    worker_max_tasks_per_child=1000,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_queue="default",
    worker_send_task_events=settings.CELERY_SEND_EVENTS,
    task_send_sent_event=settings.CELERY_SEND_EVENTS,
    # Nothing reads task results. Keeping them makes every enqueue subscribe to
    # the result backend first; with Redis unreachable that stalls the caller.
    task_ignore_result=True,
    result_expires=settings.CELERY_TASK_RESULT_EXPIRES,
    result_extended=True,
    broker_transport_options=_transport_options,
    result_backend_transport_options=_transport_options,
    beat_schedule_filename="celerybeat-schedule",
    # Worker/beat processes import these on init (no circular import at module load).
    imports=tuple(discover_task_modules()),
    task_queues={"default": _queue_entry("default")},
    task_routes={},
    beat_schedule={},
)


def register_task_modules() -> List[str]:
    """Import every task module so its decorators run (producers need this). Idempotent."""
    modules = discover_task_modules()
    for module_name in modules:
        importlib.import_module(module_name)
        celery_app.register_task_module(module_name)
    logger.info(
        f"Celery: {len(modules)} task module(s), queues={sorted(celery_app.conf.task_queues)}, "
        f"beat entries={len(celery_app.conf.beat_schedule)}"
    )
    return modules


def queue_names() -> List[str]:
    """All queues a worker should consume (for `celery worker -Q`)."""
    return sorted(celery_app.conf.task_queues or {"default": None})


# Import every task module so its @celery_app.task decorators run.
register_task_modules()

logger.info("Celery app configured successfully")


@signals.worker_init.connect
def _start_worker_metrics(**_kwargs) -> None:
    """Prometheus listener in the worker's main process (threads pool: tasks run here too)."""
    from app.core.observability import start_process_metrics_server

    start_process_metrics_server(settings.WORKER_METRICS_PORT, "worker")
