"""
Redis and Celery settings.

Every Redis consumer must prefix its keys with ``REDIS_KEY_PREFIX``
(``gpu-deck:<domain>:<event-or-key>``): CacheService does it for you, Celery
uses ``global_keyprefix``, APScheduler gets prefixed ``jobs_key``/``run_times_key``,
slowapi gets a prefixed storage. The dev Redis container
(infra/docker/docker-compose.deps.yml) enforces it with an ACL user limited to
``~gpu-deck:*`` keys, so an unprefixed key fails with NOPERM in development.
"""

from __future__ import annotations

from typing import Optional

from app.core.settings.base import DomainSettings
from pydantic import Field


class RedisSettings(DomainSettings):
    """Redis connection plus the key prefix every consumer must honour."""

    REDIS_HOST: str = Field(default="127.0.0.1", description="Redis host")
    REDIS_PORT: int = Field(default=6379, description="Redis port")
    REDIS_DB: int = Field(default=0, description="Redis database index")
    REDIS_USERNAME: Optional[str] = Field(
        None,
        description="Redis ACL username (the dev Redis container uses 'gpu-deck'; "
        "a password alone authenticates as 'default').",
    )
    REDIS_PASSWORD: Optional[str] = Field(None, description="Redis password")
    REDIS_URL: Optional[str] = Field(None, description="Full Redis URL (wins when set)")
    REDIS_KEY_PREFIX: str = Field(
        default="gpu-deck:",
        description="Prefix for every key and channel this app creates in Redis "
        "(the dev Redis ACL only allows keys under gpu-deck:)",
    )

    # Celery — broker and result backend default to the Redis above. Set these
    # only to point Celery at a different Redis; a copy of REDIS_URL goes stale
    # when the Redis password changes.
    CELERY_BROKER_URL: Optional[str] = Field(None, description="Celery broker URL")
    CELERY_RESULT_BACKEND: Optional[str] = Field(None, description="Celery result backend URL")
    CELERY_TASK_TRACK_STARTED: bool = Field(default=True, description="Track task start")
    CELERY_TASK_TIME_LIMIT: int = Field(default=300, description="Hard task time limit (s)")
    CELERY_MAX_RETRIES: int = Field(default=3, description="Maximum task retries")
    CELERY_TASK_RESULT_EXPIRES: int = Field(default=86400, description="Result TTL (s)")
    CELERY_SEND_EVENTS: bool = Field(
        default=False, description="Emit worker/task events (needed only for Flower)"
    )

    def get_redis_url(self) -> str:
        if self.REDIS_URL:
            return self.REDIS_URL
        if self.REDIS_PASSWORD:
            auth = f"{self.REDIS_USERNAME or ''}:{self.REDIS_PASSWORD}@"
        else:
            auth = ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    def get_celery_broker_url(self) -> str:
        return self.CELERY_BROKER_URL or self.get_redis_url()

    def get_celery_result_backend(self) -> str:
        return self.CELERY_RESULT_BACKEND or self.get_redis_url()

    def redis_key(self, *parts: str) -> str:
        """Build a prefixed Redis key: redis_key("session", h) -> 'gpu-deck:session:h'."""
        return self.REDIS_KEY_PREFIX + ":".join(p.strip(":") for p in parts if p)
