"""
Redis: CacheService (prefixed, JSON, graceful degradation) and the raw client.
"""

from app.db.redis.cache_service import CacheService, get_cache_service
from app.db.redis.client import close_redis, get_redis, redis_key

__all__ = ["CacheService", "get_cache_service", "get_redis", "close_redis", "redis_key"]
