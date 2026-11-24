"""
Cross-process hub bus over Redis pub/sub.

Only the hub process holds agent sockets; the admin API and the jobs process
must still issue commands and config pushes. They talk through these channels
(all under ``settings.REDIS_KEY_PREFIX``):

- ``hub:dispatch`` (any process -> hub): ``{"type": "command", "node_id",
  "command_id"}``, ``{"type": "config_push", "node_id"|null, "full",
  "sections"}``, ``{"type": "disconnect", "node_id", "code", "reason"}``,
  ``{"type": "crl_updated", "fingerprints": [...]}``, ``{"type": "routing_rebuild"}``
- ``hub:results`` (hub -> any process): ``{"command_id", "node_id", "state",
  "result", "error_code", "error_message", "progress_pct"}``

Live UI events do not use pub/sub: they go to the Redis stream
``live:events`` (``app/services/metrics/live.py``).

Presence: hash ``hub:connected`` (node_id -> JSON {hub, connected_at,
fingerprint}) maintained by the hub so other processes can answer
"is this node connected" without a socket.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Optional

from app.core.logger import get_logger
from app.db.redis.client import get_redis, redis_key
from app.services.agent_hub.protocol import dumps

logger = get_logger(__name__)

CH_DISPATCH = "hub:dispatch"
CH_RESULTS = "hub:results"
KEY_CONNECTED = "hub:connected"


def channel(name: str) -> str:
    return redis_key(name)


async def publish(name: str, payload: Dict[str, Any]) -> int:
    client = await get_redis()
    return await client.publish(channel(name), dumps(payload))


class Subscription:
    """Background pub/sub reader calling ``handler(payload)`` for every message."""

    def __init__(self, name: str, handler: Callable[[Dict[str, Any]], Awaitable[None]]):
        self.name = name
        self.handler = handler
        self._task: Optional[asyncio.Task] = None
        self._pubsub = None
        self.ready = asyncio.Event()

    async def start(self) -> None:
        client = await get_redis()
        self._pubsub = client.pubsub(ignore_subscribe_messages=True)
        await self._pubsub.subscribe(channel(self.name))
        self._task = asyncio.create_task(self._run(), name=f"bus:{self.name}")
        self.ready.set()

    async def _run(self) -> None:
        assert self._pubsub is not None
        try:
            async for message in self._pubsub.listen():
                if message is None or message.get("type") != "message":
                    continue
                try:
                    payload = json.loads(message["data"])
                except (TypeError, ValueError):
                    continue
                try:
                    await self.handler(payload)
                except Exception as exc:  # noqa: BLE001 - one bad message must not kill the reader
                    logger.error(f"bus {self.name}: handler failed: {exc}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(f"bus {self.name}: reader stopped: {exc}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe(channel(self.name))
                await self._pubsub.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._pubsub = None


async def iter_channel(name: str) -> AsyncIterator[Dict[str, Any]]:
    """Async generator over one channel (SSE / WebSocket fan-out)."""
    client = await get_redis()
    pubsub = client.pubsub(ignore_subscribe_messages=True)
    await pubsub.subscribe(channel(name))
    try:
        async for message in pubsub.listen():
            if message is None or message.get("type") != "message":
                continue
            try:
                yield json.loads(message["data"])
            except (TypeError, ValueError):
                continue
    finally:
        try:
            await pubsub.unsubscribe(channel(name))
            await pubsub.aclose()
        except Exception:  # noqa: BLE001
            pass


# ---------- presence ----------


async def set_connected(node_id: str, info: Dict[str, Any]) -> None:
    client = await get_redis()
    await client.hset(redis_key(KEY_CONNECTED), node_id, dumps(info))


async def set_disconnected(node_id: str) -> None:
    client = await get_redis()
    await client.hdel(redis_key(KEY_CONNECTED), node_id)


async def connected_nodes() -> Dict[str, Dict[str, Any]]:
    client = await get_redis()
    raw = await client.hgetall(redis_key(KEY_CONNECTED))
    out = {}
    for node_id, value in raw.items():
        try:
            out[node_id] = json.loads(value)
        except (TypeError, ValueError):
            out[node_id] = {}
    return out


async def clear_presence_for_hub(hub_id: str) -> int:
    """Hub startup/shutdown: drop stale presence rows written by this hub instance."""
    client = await get_redis()
    raw = await client.hgetall(redis_key(KEY_CONNECTED))
    removed = 0
    for node_id, value in raw.items():
        try:
            if json.loads(value).get("hub") == hub_id:
                await client.hdel(redis_key(KEY_CONNECTED), node_id)
                removed += 1
        except (TypeError, ValueError):
            continue
    return removed
