"""
On-demand load: the API layer publishes ``{"deployment_id"}`` on
``gpu-deck:deployments:start-requested`` when a request names a deployment
with no ready replica (``SERVING_ON_DEMAND_LOAD``). The jobs process
subscribes and starts the deployment (``DeploymentService.start``: desired
running, replicas >= max(1, min_replicas), reconcile now).

Guards: one start per deployment per ``DEBOUNCE_SECONDS`` fleet-wide (Redis
``SET NX``), deleted deployments are ignored, deployments that already have a
loading/ready replica are left alone.

Runs on its own thread + event loop + Redis connection (the scheduler process
is synchronous; jobs run on per-job loops), started/stopped by
``hooks.py`` lifecycle hooks.
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from typing import Any, Dict, Optional

from app.core.logger import get_logger
from app.core.settings import settings

logger = get_logger(__name__)

CHANNEL_PARTS = ("deployments", "start-requested")
DEBOUNCE_SECONDS = 30


def channel() -> str:
    return settings.redis_key(*CHANNEL_PARTS)


def parse_message(data: Any) -> Optional[uuid.UUID]:
    try:
        payload = json.loads(data) if isinstance(data, (str, bytes)) else dict(data)
        return uuid.UUID(str(payload.get("deployment_id")))
    except (ValueError, TypeError, AttributeError):
        return None


async def handle_start_request(
    deployment_id: uuid.UUID, redis_client: Any = None, hub: Any = None
) -> str:
    """Start one deployment on demand. Returns what happened (for logs / tests)."""
    if redis_client is not None:
        key = settings.redis_key("deployments", "on-demand", str(deployment_id))
        try:
            if not await redis_client.set(key, "1", nx=True, ex=DEBOUNCE_SECONDS):
                return "debounced"
        except Exception as exc:  # noqa: BLE001 - no debounce without Redis
            logger.debug(f"on-demand debounce unavailable: {exc}")
    from app.db.postgres.connection import get_session_maker
    from app.services.deployments.service import DeploymentService

    async with get_session_maker()() as session:
        svc = DeploymentService.for_session(session, hub=hub)
        dep = await svc.deployments.get_active(deployment_id)
        if dep is None:
            return "unknown"
        live = await svc.instances.for_deployment(dep.id, ("loading", "ready"))
        if live and dep.desired_running:
            return "already_running"
        await svc.start(dep.id)
        await session.commit()
    logger.info(f"on-demand load: started deployment {deployment_id}")
    return "started"


class OnDemandSubscriber:
    """Background thread: SUBSCRIBE and start deployments as requests arrive."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=lambda: asyncio.run(self._run()), name="on-demand-load", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None

    async def _run(self) -> None:
        import redis.asyncio as redis

        while not self._stop.is_set():
            client = None
            try:
                client = redis.from_url(
                    settings.get_redis_url(), encoding="utf-8", decode_responses=True
                )
                pubsub = client.pubsub()
                await pubsub.subscribe(channel())
                logger.info(f"on-demand load: subscribed to {channel()}")
                while not self._stop.is_set():
                    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if not msg:
                        continue
                    dep_id = parse_message(msg.get("data"))
                    if dep_id is None:
                        continue
                    try:
                        await handle_start_request(dep_id, client)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"on-demand load of {dep_id} failed: {exc}")
                await pubsub.unsubscribe()
            except Exception as exc:  # noqa: BLE001 - reconnect after Redis hiccups
                logger.warning(f"on-demand subscriber error: {exc}; retrying")
                await asyncio.sleep(2)
            finally:
                if client is not None:
                    try:
                        await client.aclose()
                    except Exception:  # noqa: BLE001
                        pass


_subscriber = OnDemandSubscriber()


def get_subscriber() -> OnDemandSubscriber:
    return _subscriber


def summary() -> Dict[str, Any]:
    thread = _subscriber._thread
    return {"channel": channel(), "running": bool(thread and thread.is_alive())}
