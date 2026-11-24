"""
System tasks — the reference task module (also exercises the discovery wiring).

Shows every extension hook a ``*_tasks.py`` module can use: a routed queue, a
Celery beat entry, an APScheduler job and the async-impl + sync-entry +
enqueue-helper shape.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict

from app.core.logger import get_logger
from app.workers.celery_app import celery_app
from app.workers.scheduler import run_async, scheduled_job
from app.workers.tasks.base_task import BaseTaskWithRetry

logger = get_logger(__name__)

QUEUE = "system"

BEAT_SCHEDULE = {
    # Proves beat -> worker -> Redis wiring end to end; harmless if it never runs.
    "system-heartbeat": {
        "task": "app.workers.tasks.system_tasks.heartbeat",
        "schedule": 300.0,  # seconds
    },
}


async def _heartbeat() -> Dict[str, Any]:
    """Round-trip to Redis under the project prefix so a misconfigured ACL shows up here first."""
    from app.db.redis.client import get_redis, redis_key

    client = await get_redis()
    now = datetime.now(timezone.utc).isoformat()
    await client.set(redis_key("workers", "heartbeat"), now, ex=900)
    return {"at": now}


@celery_app.task(name="app.workers.tasks.system_tasks.heartbeat", base=BaseTaskWithRetry, bind=True)
def heartbeat(self) -> Dict[str, Any]:
    """Write a heartbeat timestamp to Redis."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(_heartbeat())
    finally:
        loop.close()
    logger.debug(f"worker heartbeat {result['at']}")
    return result


def enqueue_heartbeat() -> None:
    """Fire-and-forget; never raises (the enqueue_* pattern)."""
    try:
        heartbeat.apply_async(queue=QUEUE)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"enqueue_heartbeat failed: {exc}")


@scheduled_job("system.scheduler_heartbeat", "interval", minutes=5)
def scheduler_heartbeat() -> None:
    """APScheduler-side heartbeat (jobs process liveness)."""
    try:
        run_async(_heartbeat())
    except Exception as exc:  # noqa: BLE001
        logger.error(f"scheduler heartbeat failed: {exc}")
