"""
Fleet jobs, run by the jobs process next to the hub's own sweeper so
fleet housekeeping keeps going when either process is down (every step is
idempotent, see ``app/services/nodes/maintenance.py``):

- ``fleet.maintenance`` (every 15 s): offline detection, command expiry,
  drained detection, superseded-cert revocation, live cache cleanup.
- ``fleet.cert_rotation`` (hourly): ``rotate_cert`` for node certs expiring
  within ``NODE_CERT_ROTATE_BEFORE_DAYS``.
"""

import asyncio
from typing import Any, Dict

from app.core.logger import get_logger
from app.workers.celery_app import celery_app
from app.workers.scheduler import run_async, scheduled_job
from app.workers.tasks.base_task import BaseTaskWithRetry

logger = get_logger(__name__)

QUEUE = "fleet"

BEAT_SCHEDULE: Dict[str, Any] = {}


async def _maintenance() -> bool:
    from app.services.nodes.maintenance import run_hub_maintenance

    return await run_hub_maintenance()


async def _cert_rotation() -> int:
    from app.services.nodes.maintenance import request_cert_rotations

    return await request_cert_rotations()


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(
    name="app.workers.tasks.fleet_tasks.maintenance", base=BaseTaskWithRetry, bind=True
)
def maintenance(self) -> bool:
    return _run(_maintenance())


@celery_app.task(
    name="app.workers.tasks.fleet_tasks.cert_rotation",
    base=BaseTaskWithRetry,
    bind=True,
)
def cert_rotation(self) -> int:
    return _run(_cert_rotation())


@scheduled_job(
    "fleet.maintenance",
    "interval",
    seconds=15,
    job_opts={"max_instances": 1, "coalesce": True},
)
def scheduled_maintenance() -> None:
    try:
        run_async(_maintenance())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"fleet maintenance failed: {exc}")


@scheduled_job(
    "fleet.cert_rotation",
    "interval",
    hours=1,
    job_opts={"max_instances": 1, "coalesce": True},
)
def scheduled_cert_rotation() -> None:
    try:
        count = run_async(_cert_rotation())
        if count:
            logger.info(f"requested certificate rotation on {count} node(s)")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"certificate rotation sweep failed: {exc}")
