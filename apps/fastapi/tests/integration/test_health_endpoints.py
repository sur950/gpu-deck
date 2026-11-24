"""
Health endpoints against the real local Postgres and Redis (from apps/fastapi/.env).

Route file: app/api/v1/routes/health.py (+ /health on the serving app).
"""

import pytest
from app.db.redis.client import get_redis, redis_key

pytestmark = [pytest.mark.integration, pytest.mark.db]


@pytest.mark.asyncio
async def test_admin_health_reports_dependencies(async_client):
    r = await async_client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    data = body["data"]
    assert data["process"] == "admin"
    assert data["status"] in ("healthy", "degraded"), data
    assert data["checks"]["database"]["status"] == "healthy", data["checks"]["database"]
    assert data["checks"]["redis"]["status"] == "healthy", data["checks"]["redis"]
    assert "cpu_percent" in data["checks"]["system"]


@pytest.mark.asyncio
async def test_serving_health_reports_dependencies(async_serving_client):
    r = await async_serving_client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["process"] == "serving"
    assert data["checks"]["database"]["status"] == "healthy"
    assert data["checks"]["redis"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_redis_acl_allows_prefixed_keys_only():
    client = await get_redis()
    key = redis_key("tests", "probe")
    assert await client.set(key, "1", ex=30)
    assert await client.get(key) == "1"
    await client.delete(key)
    with pytest.raises(Exception):
        await client.set("unprefixed:probe", "1", ex=30)
