"""
Agent update jobs (agent protocol §14), run by the scheduler:

- ``agent_updates.tick`` (every ``AGENT_UPDATE_TICK_SECONDS``): advance update runs (stage,
  apply, deadlines) and rolling updates, start automatic rollouts inside maintenance windows,
  pre-stage the target version on nodes that can update themselves. Every step is idempotent and
  also triggered by the hub (agent events, command results) and the admin API.
"""

from typing import Any, Dict

from app.core.logger import get_logger
from app.core.settings import settings
from app.workers.celery_app import celery_app
from app.workers.scheduler import run_async, scheduled_job
from app.workers.tasks.base_task import BaseTaskWithRetry

logger = get_logger(__name__)

QUEUE = "fleet"

BEAT_SCHEDULE: Dict[str, Any] = {}


async def _tick() -> Dict[str, int]:
    from app.services.agent_updates.orchestrator import tick

    return await tick()


@celery_app.task(
    name="app.workers.tasks.agent_updates_tasks.tick", base=BaseTaskWithRetry, bind=True
)
def tick(self) -> Dict[str, int]:
    return run_async(_tick())


@scheduled_job(
    "agent_updates.tick",
    "interval",
    seconds=settings.AGENT_UPDATE_TICK_SECONDS,
    job_opts={"max_instances": 1, "coalesce": True},
)
def scheduled_tick() -> None:
    try:
        out = run_async(_tick())
        if any(out.values()):
            logger.debug(f"agent updates tick: {out}")
    except Exception as exc:  # noqa: BLE001 - the next tick retries
        logger.error(f"agent updates tick failed: {exc}")
