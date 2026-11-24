"""
Regression: every rate-limit counter in Redis carries a TTL.

A fixed-window counter without a TTL never resets, so once it passed its limit
``/api/v1/cloud/nodes`` answered 429 forever. The stock ``limits`` script only
sets the TTL when the increment created the key; ``TTLSafeRedisStorage``
re-arms it whenever the key has none, and ``repair_limiter_ttls`` fixes stray
keys out of band. Runs on the shared dev Redis under a unique
``gpu-deck:rltest:<tag>:`` prefix (``REDIS_KEY_PREFIX`` is swapped and restored).
"""

import threading
import uuid

import httpx
import pytest
import redis
import redis.asyncio as aioredis
from app.core import rate_limiter
from app.core.rate_limiter import (
    STORAGE_SCHEME,
    TTLSafeRedisStorage,
    limiter,
    repair_limiter_ttls,
    window_seconds,
)
from app.core.settings import settings
from fastapi import FastAPI, Request
from limits import parse
from limits.storage import RedisStorage, storage_from_string
from limits.strategies import FixedWindowRateLimiter

pytestmark = pytest.mark.integration


@pytest.fixture
def prefix():
    saved = settings.REDIS_KEY_PREFIX
    tag = uuid.uuid4().hex[:10]
    settings.REDIS_KEY_PREFIX = f"gpu-deck:rltest:{tag}:"
    client = redis.Redis.from_url(settings.get_redis_url())
    try:
        yield settings.REDIS_KEY_PREFIX
    finally:
        for key in client.scan_iter(match=f"{settings.REDIS_KEY_PREFIX}*", count=500):
            client.delete(key)
        client.close()
        settings.REDIS_KEY_PREFIX = saved


@pytest.fixture
def r():
    client = redis.Redis.from_url(settings.get_redis_url(), decode_responses=True)
    yield client
    client.close()


def _storage() -> TTLSafeRedisStorage:
    storage = storage_from_string(STORAGE_SCHEME + settings.get_redis_url())
    assert isinstance(storage, TTLSafeRedisStorage)
    return storage


def test_first_hit_sets_the_ttl(prefix, r):
    storage = _storage()
    item = parse("30/minute")
    assert FixedWindowRateLimiter(storage).hit(item, "127.0.0.1", "/api/v1/cloud/nodes")
    key = f"{prefix}LIMITS:{item.key_for('127.0.0.1', '/api/v1/cloud/nodes')}"
    assert r.get(key) == "1"
    assert 0 < r.ttl(key) <= 60


def test_ttl_less_counter_is_rearmed_on_the_next_hit(prefix, r):
    """The /cloud/nodes failure: a counter at 40 with TTL -1 must not block forever."""
    storage = _storage()
    item = parse("30/minute")
    key = f"{prefix}LIMITS:{item.key_for('127.0.0.1', '/api/v1/cloud/nodes')}"
    r.set(key, 40)  # e.g. the EXPIRE after INCRBY failed (scripts do not roll back)
    assert r.ttl(key) == -1
    assert not FixedWindowRateLimiter(storage).hit(item, "127.0.0.1", "/api/v1/cloud/nodes")
    assert r.get(key) == "41"
    assert 0 < r.ttl(key) <= 60


def test_stock_limits_script_leaves_it_ttl_less(prefix, r):
    """Documents the root cause: upstream incr_expire.lua only expires a key it created."""
    stock = RedisStorage(settings.get_redis_url(), key_prefix=settings.redis_key("LIMITS"))
    key = f"{prefix}LIMITS:stock"
    r.set(key, 5)
    stock.incr("stock", 60)
    assert r.ttl(key) == -1


def test_existing_ttl_is_not_extended(prefix, r):
    storage = _storage()
    key = f"{prefix}LIMITS:fixed"
    r.set(key, 3, ex=20)
    storage.incr("fixed", 60)
    assert 0 < r.ttl(key) <= 20  # the fixed window does not slide


def test_concurrent_first_hits_all_leave_a_ttl(prefix, r):
    storage = _storage()
    keys = [f"race{i}" for i in range(20)]
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        for k in keys:
            storage.incr(k, 60)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for k in keys:
        full = f"{prefix}LIMITS:{k}"
        assert r.get(full) == "8"
        assert 0 < r.ttl(full) <= 60, full


async def test_slowapi_route_counter_has_a_ttl(prefix, r):
    if not limiter.enabled or not isinstance(limiter._storage, TTLSafeRedisStorage):
        pytest.skip("rate limiting disabled in this environment")
    app = FastAPI()
    app.state.limiter = limiter

    @app.get("/limited")
    @rate_limiter.rate_limit("5/minute")
    async def limited(request: Request):
        return {"ok": True}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        for _ in range(2):
            assert (await client.get("/limited")).status_code == 200
    keys = list(r.scan_iter(match=f"{prefix}LIMITS:*", count=500))
    assert keys, "the limiter wrote no key under the swapped prefix"
    for key in keys:
        assert 0 < r.ttl(key) <= 60, key


async def test_repair_sets_ttls_from_the_key_window(prefix, r):
    limits_prefix = f"{prefix}LIMITS"
    minute = f"{limits_prefix}:LIMITER/127.0.0.1//api/v1/cloud/nodes/30/1/minute"
    hour = f"{limits_prefix}:LIMITER/user:1//api/v1/x/100/2/hour"
    odd = f"{limits_prefix}:something-else"
    keep = f"{limits_prefix}:LIMITER/1.2.3.4//y/5/1/minute"
    outside = f"{prefix}other:no-ttl"
    for k in (minute, hour, odd, outside):
        r.set(k, 40)
    r.set(keep, 1, ex=15)
    client = aioredis.from_url(settings.get_redis_url(), decode_responses=True)
    try:
        assert await repair_limiter_ttls(client) == 3
        assert await repair_limiter_ttls(client) == 0  # idempotent
    finally:
        await client.aclose()
    assert 0 < r.ttl(minute) <= 60
    assert 60 < r.ttl(hour) <= 7200
    assert 0 < r.ttl(odd) <= rate_limiter.FALLBACK_TTL_SECONDS
    assert 0 < r.ttl(keep) <= 15
    assert r.ttl(outside) == -1  # only limiter keys are touched


def test_window_seconds():
    assert window_seconds("LIMITER/127.0.0.1//api/v1/cloud/nodes/30/1/minute") == 60
    assert window_seconds("LIMITER/u//p/10/5/seconds") == 5
    assert window_seconds("LIMITER/u//p/1000/1/day") == 86400
    assert window_seconds("nope") is None


def test_repair_runs_at_admin_startup_and_on_a_schedule():
    from app.core import lifecycle
    from app.workers.celery_app import discover_task_modules
    from app.workers.scheduler import registered_jobs

    discover_task_modules()
    assert "security.repair_limiter_ttls" in {j.job_id for j in registered_jobs()}
    lifecycle.discover_hooks()
    names = lifecycle.registered_hooks()["startup"]
    assert any("repair_rate_limit_ttls" in n and "admin" in n for n in names)
