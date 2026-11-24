"""
Raw async Redis client (singleton per process and event loop).

Use this when you need pipelines, pub/sub or data structures CacheService does
not wrap. ALWAYS build keys with ``redis_key(...)``: the dev Redis ACL
rejects any key outside ``gpu-deck:*`` with NOPERM.

Worker/scheduler tasks run each job on a fresh event loop — and the jobs
process runs several jobs at once, each on its own thread and loop. A
redis.asyncio client is bound to the loop that created it, so there is one
client per live event loop; a client is never closed from a foreign loop (that
used to strand the other thread's in-flight Redis call forever).
"""

import asyncio
import threading
import weakref
from typing import Optional

import redis.asyncio as redis
from app.core.logger import get_logger
from app.core.settings import settings

logger = get_logger(__name__)

_client: Optional[redis.Redis] = None  # client of the most recently used loop
_client_loop: Optional[asyncio.AbstractEventLoop] = None
_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, redis.Redis]" = (
    weakref.WeakKeyDictionary()
)
_lock = threading.Lock()


def redis_key(*parts: str) -> str:
    """Prefixed key: redis_key("routing", "table") -> 'gpu-deck:routing:table'."""
    return settings.redis_key(*parts)


async def get_redis() -> redis.Redis:
    """redis.asyncio client for the current event loop (decode_responses=True)."""
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    with _lock:
        if _client is not None and _client_loop is loop:
            return _client
        client = _clients.get(loop)
        for old in [lp for lp in list(_clients.keys()) if lp.is_closed()]:
            _clients.pop(old, None)  # loop finished: its sockets died with it
    if client is None:
        client = redis.from_url(
            settings.get_redis_url(),
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
        )
        await client.ping()
        with _lock:
            _clients[loop] = client
        logger.debug("Redis connection established")
    with _lock:
        _client, _client_loop = client, loop
    return client


async def close_redis() -> None:
    """Close the singleton. A client created on another (possibly closed) event
    loop cannot be awaited from this one; it is dropped instead (its sockets die
    with that loop)."""
    global _client, _client_loop
    client, client_loop = _client, _client_loop
    _client = None
    _client_loop = None
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover - called outside a loop
        running = None
    with _lock:
        own = _clients.pop(running, None) if running is not None else None
    if own is not None and own is not client:
        try:
            await own.aclose()
        except Exception as exc:  # noqa: BLE001 - shutdown must not raise
            logger.debug(f"Redis close failed: {exc}")
    if client is None:
        return
    try:
        current = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover - called outside a loop
        current = None
    if client_loop is not None and (client_loop is not current or client_loop.is_closed()):
        logger.debug("Dropping Redis client bound to a previous event loop")
        return
    try:
        await client.aclose()
    except Exception as exc:  # noqa: BLE001 - shutdown must not raise
        logger.debug(f"Redis close failed: {exc}")
    logger.info("Redis connection closed")
