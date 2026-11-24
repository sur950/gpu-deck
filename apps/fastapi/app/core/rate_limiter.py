"""
Rate limiting using SlowAPI (admin API only — put any AI gateway in front of the
serving API for per-user quotas and rate limits).

Usage::

    @router.post("/auth/login")
    @rate_limit("10/minute")
    @handle_exceptions
    async def login(request: Request, ...):   # `request: Request` is mandatory

Counters use the fixed-window strategy on Redis under ``gpu-deck:LIMITS:``.
Every counter must carry a TTL: a fixed-window counter without one never
resets, so once it passes the limit the endpoint answers 429 forever. The
stock ``limits`` script (``incr_expire.lua``) only sets the TTL when the
increment *created* the key (``current == amount``); a counter that exists
without a TTL (an EXPIRE that failed after the INCRBY in the same script, Redis
scripts do not roll back; a key restored or rewritten without its TTL) is never
repaired. ``TTLSafeRedisStorage`` re-arms the TTL whenever the key has none,
on every hit, and ``repair_limiter_ttls`` (admin startup + the
``security.repair_limiter_ttls`` job) fixes stray TTL-less keys out of band.
"""

import re
from typing import Any, Callable, Optional

from app.core.exceptions import RateLimitExceededError
from app.core.logger import get_logger
from app.core.settings import settings
from fastapi import Request, Response
from limits.storage import RedisStorage
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

logger = get_logger(__name__)

STORAGE_SCHEME = "gpudeck+"
# Fallback TTL for a TTL-less limiter key whose window cannot be parsed from its name.
FALLBACK_TTL_SECONDS = 3600

# INCRBY and (re)arm the TTL whenever the key has none: TTL is read *before* the increment,
# so -2 (new key) and -1 (no expiry, however it got there) both set it; an existing TTL is
# left alone so the fixed window does not slide.
_INCR_EXPIRE_ALWAYS_TTL = b"""
local ttl = redis.call("ttl", KEYS[1])
local current = redis.call("incrby", KEYS[1], tonumber(ARGV[2]))
if ttl < 0 then
    redis.call("expire", KEYS[1], ARGV[1])
end
return current
"""


class TTLSafeRedisStorage(RedisStorage):
    """``limits`` Redis storage whose counters always end up with a TTL."""

    STORAGE_SCHEME = [f"{STORAGE_SCHEME}redis", f"{STORAGE_SCHEME}rediss"]
    SCRIPT_INCR_EXPIRE = _INCR_EXPIRE_ALWAYS_TTL

    def __init__(self, uri: str, **options: Any) -> None:
        if uri.startswith(STORAGE_SCHEME):
            uri = uri[len(STORAGE_SCHEME) :]
        super().__init__(uri, **options)

    # The prefix follows settings.REDIS_KEY_PREFIX at call time (not import time), so a
    # test that swaps the prefix gets its own counters instead of sharing the dev stack's.
    @property  # type: ignore[override]
    def key_prefix(self) -> str:
        return limiter_key_prefix()

    @key_prefix.setter
    def key_prefix(self, _value: str) -> None:
        """Ignored: always derived from settings (see the getter)."""


def get_user_id(request: Request) -> str:
    """Key by authenticated user when present, else by client IP.

    The IP honours X-Forwarded-For / X-Real-IP only from TRUSTED_PROXIES
    (``resolve_client_ip``): behind Caddy every request would otherwise share
    the proxy's bucket, and a direct caller cannot forge its way out of one.
    """
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return f"user:{user_id}"
    from app.services.security.client_ip import resolve_client_ip

    return resolve_client_ip(request.scope) or get_remote_address(request)


def limiter_key_prefix() -> str:
    """``gpu-deck:LIMITS`` (``limits`` adds ``:`` before each counter key)."""
    return settings.redis_key("LIMITS")


limiter = Limiter(
    key_func=get_user_id,
    storage_uri=(
        STORAGE_SCHEME + settings.get_redis_url() if settings.RATE_LIMIT_ENABLED else "memory://"
    ),
    strategy="fixed-window",
    # Every key lives under REDIS_KEY_PREFIX (the dev Redis ACL enforces it).
    storage_options=({"key_prefix": limiter_key_prefix()} if settings.RATE_LIMIT_ENABLED else {}),
    default_limits=(
        [f"{settings.RATE_LIMIT_PER_MINUTE}/minute"] if settings.RATE_LIMIT_ENABLED else []
    ),
    enabled=settings.RATE_LIMIT_ENABLED,
)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """Convert slowapi's exception into the envelope 429."""
    raise RateLimitExceededError(detail=f"Rate limit exceeded: {exc.detail}")


def rate_limit(limit: str):
    """Decorator to apply a custom rate limit to an endpoint, e.g. @rate_limit("5/minute")."""

    def decorator(func: Callable):
        return limiter.limit(limit)(func)

    return decorator


_GRANULARITY_SECONDS = {
    "second": 1,
    "minute": 60,
    "hour": 3600,
    "day": 86400,
    "month": 30 * 86400,
    "year": 365 * 86400,
}
# limits' fixed-window key: LIMITER/<identifiers...>/<amount>/<multiples>/<granularity>
_WINDOW_RE = re.compile(r"/(\d+)/(\d+)/(second|minute|hour|day|month|year)s?$")


def window_seconds(key: str) -> Optional[int]:
    """The window length encoded at the end of a ``limits`` counter key, if any."""
    m = _WINDOW_RE.search(key)
    if not m:
        return None
    return int(m.group(2)) * _GRANULARITY_SECONDS[m.group(3)]


async def repair_limiter_ttls(client: Any = None, prefix: Optional[str] = None) -> int:
    """
    Give every TTL-less rate-limit key under ``gpu-deck:LIMITS:`` its window as TTL.

    Safe to run any time (only keys with TTL -1 are touched); returns the number fixed.
    """
    if client is None:
        from app.db.redis.client import get_redis

        client = await get_redis()
    pattern = f"{prefix or limiter_key_prefix()}:*"
    fixed = 0
    async for raw in client.scan_iter(match=pattern, count=500):
        key = raw.decode() if isinstance(raw, bytes) else str(raw)
        if await client.ttl(key) != -1:
            continue
        ttl = window_seconds(key) or FALLBACK_TTL_SECONDS
        if await client.expire(key, ttl, nx=True):
            fixed += 1
            logger.warning(f"rate limiter key had no TTL, set {ttl}s: {key}")
    return fixed
