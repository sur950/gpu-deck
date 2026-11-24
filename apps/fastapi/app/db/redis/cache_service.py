"""
Cache service for session storage and general data caching.

- Every key is prefixed with ``settings.REDIS_KEY_PREFIX`` (enforced by the
  dev Redis ACL; keeps a shared Redis tidy in production).
- Graceful degradation: Redis failures log and return defaults so callers can
  fall back to Postgres.
"""

import asyncio
import json
import weakref
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import redis.asyncio as redis
from app.core.logger import get_logger
from app.core.settings import settings

logger = get_logger(__name__)


_per_loop: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, CacheService]" = (
    weakref.WeakKeyDictionary()
)


class CacheService:
    """Redis-based cache service for sessions and general caching."""

    _instance: Optional["CacheService"] = None
    _redis_client: Optional[redis.Redis] = None

    def __init__(self):
        self.redis_url = settings.get_redis_url()
        prefix = settings.REDIS_KEY_PREFIX
        self.session_prefix = f"{prefix}session:"
        self.user_sessions_prefix = f"{prefix}user_sessions:"
        self.cache_prefix = f"{prefix}cache:"
        self.default_ttl = 3600

    _loop: Optional[asyncio.AbstractEventLoop] = None

    @classmethod
    async def get_instance(cls) -> "CacheService":
        """One instance per event loop: the jobs process runs concurrent jobs on separate
        threads/loops, and closing another loop's client strands its in-flight calls."""
        loop = asyncio.get_running_loop()
        if cls._instance is not None and cls._loop is loop:
            return cls._instance
        inst = _per_loop.get(loop)
        for old in [lp for lp in list(_per_loop.keys()) if lp.is_closed()]:
            _per_loop.pop(old, None)
        if inst is None:
            inst = cls()
            await inst.connect()
            _per_loop[loop] = inst
        cls._instance, cls._loop = inst, loop
        return inst

    async def connect(self):
        if self._redis_client is None:
            try:
                self._redis_client = redis.from_url(
                    self.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    max_connections=50,
                )
                await self._redis_client.ping()
                logger.info("Redis cache connection established")
            except Exception as e:
                logger.error(f"Failed to connect to Redis: {e}")
                raise

    async def close(self):
        if self._redis_client:
            await self._redis_client.aclose()
            self._redis_client = None
            logger.info("Redis cache connection closed")

    @classmethod
    async def reset(cls) -> None:
        """Close and forget this loop's instance (process shutdown / tests)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover
            loop = None
        inst = _per_loop.pop(loop, None) if loop is not None else None
        if cls._instance is not None and (cls._loop is loop or cls._loop is None):
            inst = inst or cls._instance
        if inst is not None:
            await inst.close()
        cls._instance = None
        cls._loop = None

    # ========== Session Management ==========

    async def create_session(
        self,
        session_id: str,
        user_id: str,
        session_data: Dict[str, Any],
        ttl: Optional[int] = None,
    ) -> bool:
        try:
            data = {
                "session_id": session_id,
                "user_id": user_id,
                "created_at": self._get_current_timestamp(),
                **session_data,
            }
            key = f"{self.session_prefix}{session_id}"
            user_sessions_key = f"{self.user_sessions_prefix}{user_id}"
            ttl = ttl or (settings.SESSION_EXPIRE_HOURS * 3600)
            await self._redis_client.setex(key, ttl, json.dumps(data))
            await self._redis_client.sadd(user_sessions_key, session_id)
            await self._redis_client.expire(user_sessions_key, ttl)
            return True
        except Exception as e:
            logger.error(f"Failed to create session: {e}")
            return False

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        try:
            data = await self._redis_client.get(f"{self.session_prefix}{session_id}")
            return json.loads(data) if data else None
        except Exception as e:
            logger.error(f"Failed to get session: {e}")
            return None

    async def update_session(
        self, session_id: str, updates: Dict[str, Any], extend_ttl: bool = True
    ) -> bool:
        try:
            key = f"{self.session_prefix}{session_id}"
            current = await self.get_session(session_id)
            if not current:
                return False
            current.update(updates)
            current["updated_at"] = self._get_current_timestamp()
            ttl = settings.SESSION_EXPIRE_HOURS * 3600
            if extend_ttl:
                await self._redis_client.setex(key, ttl, json.dumps(current))
            else:
                remaining = await self._redis_client.ttl(key)
                await self._redis_client.setex(key, max(remaining, 1), json.dumps(current))
            return True
        except Exception as e:
            logger.error(f"Failed to update session: {e}")
            return False

    async def delete_session(self, session_id: str, user_id: Optional[str] = None) -> bool:
        try:
            await self._redis_client.delete(f"{self.session_prefix}{session_id}")
            if user_id:
                await self._redis_client.srem(f"{self.user_sessions_prefix}{user_id}", session_id)
            return True
        except Exception as e:
            logger.error(f"Failed to delete session: {e}")
            return False

    async def delete_user_sessions(self, user_id: str) -> int:
        try:
            user_sessions_key = f"{self.user_sessions_prefix}{user_id}"
            session_ids = await self._redis_client.smembers(user_sessions_key)
            deleted = 0
            for session_id in session_ids:
                await self._redis_client.delete(f"{self.session_prefix}{session_id}")
                deleted += 1
            await self._redis_client.delete(user_sessions_key)
            return deleted
        except Exception as e:
            logger.error(f"Failed to delete user sessions: {e}")
            return 0

    async def list_user_sessions(self, user_id: str) -> List[Dict[str, Any]]:
        try:
            session_ids = await self._redis_client.smembers(f"{self.user_sessions_prefix}{user_id}")
            sessions = []
            for session_id in session_ids:
                data = await self.get_session(session_id)
                if data:
                    sessions.append(data)
            sessions.sort(key=lambda s: s.get("created_at", 0), reverse=True)
            return sessions
        except Exception as e:
            logger.error(f"Failed to list user sessions: {e}")
            return []

    # ========== General Caching ==========

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            await self._redis_client.setex(
                f"{self.cache_prefix}{key}", ttl or self.default_ttl, str(value)
            )
            return True
        except Exception as e:
            logger.error(f"Failed to set cache: {e}")
            return False

    async def get(self, key: str, default: Any = None) -> Any:
        try:
            value = await self._redis_client.get(f"{self.cache_prefix}{key}")
            if value is None:
                return default
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        except Exception as e:
            logger.error(f"Failed to get cache: {e}")
            return default

    async def delete(self, key: str) -> bool:
        try:
            await self._redis_client.delete(f"{self.cache_prefix}{key}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete cache: {e}")
            return False

    async def exists(self, key: str) -> bool:
        try:
            return await self._redis_client.exists(f"{self.cache_prefix}{key}") > 0
        except Exception as e:
            logger.error(f"Failed to check cache existence: {e}")
            return False

    async def incr(self, key: str, ttl: Optional[int] = None) -> int:
        """Atomic counter (login failures, parallel-request counters)."""
        try:
            full = f"{self.cache_prefix}{key}"
            value = await self._redis_client.incr(full)
            if ttl and value == 1:
                await self._redis_client.expire(full, ttl)
            return value
        except Exception as e:
            logger.error(f"Failed to incr cache: {e}")
            return 0

    async def clear_pattern(self, pattern: str) -> int:
        try:
            deleted = 0
            async for key in self._redis_client.scan_iter(match=f"{self.cache_prefix}{pattern}"):
                await self._redis_client.delete(key)
                deleted += 1
            return deleted
        except Exception as e:
            logger.error(f"Failed to clear cache pattern: {e}")
            return 0

    # ========== Utility ==========

    def _get_current_timestamp(self) -> int:
        return int(datetime.now(timezone.utc).timestamp())

    async def ping(self) -> bool:
        try:
            await self._redis_client.ping()
            return True
        except Exception as e:
            logger.error(f"Redis ping failed: {e}")
            return False


async def get_cache_service() -> CacheService:
    """Get cache service singleton."""
    return await CacheService.get_instance()
