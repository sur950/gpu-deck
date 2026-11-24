"""
APScheduler integration with a job registry.

Extension point
---------------
Any ``app/workers/tasks/*_tasks.py`` module registers periodic in-process jobs
with the decorator; nothing else needs editing::

    from app.workers.scheduler import scheduled_job, run_async

    @scheduled_job("rules.evaluate", "interval", seconds=30)
    def evaluate_rules() -> None:
        run_async(_evaluate_rules())          # async impl on a fresh event loop

    @scheduled_job("billing.monthly_report", "cron", day=1, hour=0, minute=5)
    def monthly_report() -> None: ...

Job ids must be unique (``<domain>.<verb>``); the registry raises on duplicates.
``register_scheduled_jobs()`` imports the task modules (through celery_app's
discovery) and adds every registered job with ``replace_existing=True``.

Runs in the dedicated jobs process::

    python -m app.workers.scheduler

or inside the admin API when SCHEDULER_EMBEDDED=true. HA leader
election so only one scheduler instance fires jobs.
"""

from __future__ import annotations

import asyncio
import signal
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.core.logger import get_logger
from app.core.settings import settings
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.redis import RedisJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from pytz import utc

logger = get_logger(__name__)


# ==========================================
# Job registry
# ==========================================


@dataclass
class ScheduledJob:
    job_id: str
    func: Callable[..., Any]
    trigger: str
    trigger_kwargs: Dict[str, Any] = field(default_factory=dict)
    job_kwargs: Dict[str, Any] = field(default_factory=dict)


_REGISTRY: Dict[str, ScheduledJob] = {}


def scheduled_job(job_id: str, trigger: str = "interval", **trigger_kwargs: Any):
    """
    Register ``func`` as a periodic job. ``trigger`` is 'interval', 'cron' or
    'date'; keyword arguments go to the trigger (seconds=, minutes=, hour=, ...).
    Pass ``max_instances``/``misfire_grace_time``/``coalesce`` via ``job_opts={}``.
    """
    job_opts = trigger_kwargs.pop("job_opts", None) or {}

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if job_id in _REGISTRY and _REGISTRY[job_id].func is not func:
            raise RuntimeError(f"Scheduled job id {job_id!r} registered twice")
        _REGISTRY[job_id] = ScheduledJob(
            job_id, func, trigger, dict(trigger_kwargs), dict(job_opts)
        )
        return func

    return decorator


def registered_jobs() -> List[ScheduledJob]:
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def _reset_registry_for_tests() -> None:
    _REGISTRY.clear()


def run_async(coro) -> Any:
    """Run an async implementation from a sync scheduler/Celery entry on a fresh loop."""
    return asyncio.run(coro)


# ==========================================
# Scheduler manager
# ==========================================


class SchedulerManager:
    """Manage scheduled jobs using APScheduler with a Redis job store."""

    def __init__(self):
        self.scheduler: Optional[BackgroundScheduler] = None

    def initialize(self):
        redis_password = settings.REDIS_PASSWORD or None
        # An ACL Redis has no shared password: a password without a username
        # authenticates as 'default' and is rejected.
        redis_username = settings.REDIS_USERNAME or None
        jobstores = {
            "default": RedisJobStore(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                username=redis_username,
                password=redis_password,
                # Keys must live under the ACL'd prefix.
                jobs_key=settings.redis_key("apscheduler", "jobs"),
                run_times_key=settings.redis_key("apscheduler", "run_times"),
            )
        }
        executors = {"default": ThreadPoolExecutor(20)}
        job_defaults = {"coalesce": False, "max_instances": 3, "misfire_grace_time": 60}
        self.scheduler = BackgroundScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults,
            timezone=utc,
        )
        logger.info("Scheduler initialized")

    def start(self):
        if self.scheduler and not self.scheduler.running:
            self.scheduler.start()
            logger.info("Scheduler started")

    def shutdown(self, wait: bool = True):
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=wait)
            logger.info("Scheduler shutdown")

    def add_job(self, func, trigger, **kwargs):
        if self.scheduler:
            kwargs.setdefault("replace_existing", True)
            job = self.scheduler.add_job(func, trigger, **kwargs)
            logger.info(f"Job added: {kwargs.get('id', func.__name__)} ({trigger})")
            return job
        return None

    def remove_job(self, job_id: str):
        if self.scheduler:
            self.scheduler.remove_job(job_id)
            logger.info(f"Job removed: {job_id}")

    def get_jobs(self):
        return self.scheduler.get_jobs() if self.scheduler else []


scheduler = SchedulerManager()


def register_scheduled_jobs() -> List[str]:
    """Import task modules (populating the registry) and add every job to the scheduler."""
    # Importing celery_app imports every *_tasks.py module, which runs the decorators.
    from app.workers.celery_app import discover_task_modules  # noqa: F401

    ids = []
    for job in registered_jobs():
        scheduler.add_job(
            job.func,
            job.trigger,
            id=job.job_id,
            name=job.job_id,
            **job.trigger_kwargs,
            **job.job_kwargs,
        )
        ids.append(job.job_id)
    logger.info(f"Scheduled jobs registered: {ids or 'none'}")
    return ids


def main() -> None:
    """Jobs process: run the scheduler until SIGTERM/SIGINT."""
    from app.core.lifecycle import run_shutdown_hooks_sync, run_startup_hooks_sync

    logger.info(f"Starting {settings.APP_NAME} scheduler process")
    run_startup_hooks_sync("scheduler")
    from app.core.observability import start_process_metrics_server

    start_process_metrics_server(settings.SCHEDULER_METRICS_PORT, "scheduler")
    scheduler.initialize()
    register_scheduled_jobs()
    scheduler.start()

    stop = {"flag": False}

    def _handle(signum, frame):  # noqa: ARG001
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    try:
        while not stop["flag"]:
            time.sleep(1)
    finally:
        scheduler.shutdown()
        run_shutdown_hooks_sync("scheduler")
        logger.info("Scheduler process exited")


if __name__ == "__main__":
    # `python -m app.workers.scheduler` loads this file as __main__; task modules
    # import `app.workers.scheduler` (a second copy with its own registry).
    # Delegate to the real module so both sides share one registry.
    from app.workers.scheduler import main as _main

    _main()
