"""
SSH GPU instance jobs, in the jobs process (APScheduler):

* ``gpu_instances.tick`` — queued placements, start/stop command outcomes
  (backstop for the hub hook), TTL / idle auto-stop, lost instances and
  GPU-minute metering (``app/services/gpu_instances/lifecycle.py``).
"""

from app.core.logger import get_logger
from app.services.gpu_instances.config import gpu_instance_settings
from app.workers.scheduler import run_async, scheduled_job

logger = get_logger(__name__)

QUEUE = "deployments"


async def _tick() -> dict:
    from app.services.gpu_instances.lifecycle import run_tick

    summary = await run_tick()
    if any(v for k, v in summary.items() if k != "skipped"):
        logger.info(f"gpu instances tick: {summary}")
    return summary


@scheduled_job(
    "gpu_instances.tick",
    "interval",
    seconds=gpu_instance_settings.GPU_INSTANCE_TICK_SECONDS,
    job_opts={"max_instances": 1, "coalesce": True, "misfire_grace_time": 10},
)
def gpu_instances_tick() -> None:
    """One lifecycle pass over live SSH GPU instances."""
    try:
        run_async(_tick())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"gpu instances tick failed: {exc}")
