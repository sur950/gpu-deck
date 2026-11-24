"""
Unit tests: API key generation, the serving-side ``verify_api_key``
(local cache -> Redis -> Postgres, all mocked here), invalidation, and the
cached-path microbenchmark (< 1 ms per call).
"""

import hashlib
import re
import time
import uuid
from datetime import datetime, timezone

import pytest
from app.core.settings import settings
from app.db.postgres.repositories.api_key_repository import (
    ApiKeyRepository,
    hash_api_key,
)
from app.services.api_keys import service as svc
from app.services.api_keys.service import (
    ApiKeyPrincipal,
    ApiKeyRejected,
    verify_api_key,
)

pytestmark = [pytest.mark.unit, pytest.mark.auth]


def test_generated_key_format(monkeypatch):
    from app.core.settings.security import SecuritySettings

    # serving.md §3: "Key format gd- + 48 hex" (the default; API_KEY_PREFIX may override it).
    assert SecuritySettings.model_fields["API_KEY_PREFIX"].default == "gd-"
    monkeypatch.setattr(settings, "API_KEY_PREFIX", "gd-")
    full, prefix, key_hash = ApiKeyRepository.generate_key_pair()
    assert re.fullmatch(r"gd-[0-9a-f]{48}", full)
    assert prefix == full[:12]
    assert key_hash == hashlib.sha256(full.encode()).hexdigest() == hash_api_key(full)
    assert ApiKeyRepository.generate_key_pair()[0] != full


class FakeBackends:
    """Stands in for Redis and Postgres behind the verifier."""

    def __init__(self):
        self.db = {}
        self.redis = {}
        self.db_reads = 0
        self.redis_reads = 0

    def add(self, raw, status="active", expires_at=None):
        key_id = str(uuid.uuid4())
        self.db[hash_api_key(raw)] = {
            "id": key_id,
            "name": f"key-{key_id[:4]}",
            "status": status,
            "expires_at": expires_at,
            "metadata": {"team": "a"},
        }
        return key_id

    async def lookup_db(self, key_hash):
        self.db_reads += 1
        return self.db.get(key_hash)

    async def lookup_redis(self, key_hash):
        self.redis_reads += 1
        return self.redis.get(key_hash)

    async def store_redis(self, key_hash, data, ttl):
        self.redis[key_hash] = data


@pytest.fixture
def backends(monkeypatch):
    fake = FakeBackends()
    verifier = svc._Verifier()
    monkeypatch.setattr(verifier, "_lookup_db", fake.lookup_db)
    monkeypatch.setattr(verifier, "_lookup_redis", fake.lookup_redis)
    monkeypatch.setattr(verifier, "_store_redis", fake.store_redis)
    monkeypatch.setattr(verifier, "_touch", lambda key_id: None)

    async def invalidate(hashes):
        for h in hashes:
            verifier.drop_local(h)
            fake.redis.pop(h, None)

    monkeypatch.setattr(verifier, "invalidate", invalidate)
    monkeypatch.setattr(svc, "verifier", verifier)
    monkeypatch.setattr(settings, "API_KEY_LOCAL_CACHE_SECONDS", 60.0)
    fake.verifier = verifier
    return fake


@pytest.mark.asyncio
async def test_verify_valid_key_and_cache_layers(backends):
    raw = "sk-" + "a" * 48
    key_id = backends.add(raw)
    principal = await verify_api_key(raw)
    assert isinstance(principal, ApiKeyPrincipal)
    assert (principal.id, principal.status, principal.metadata) == (
        key_id,
        "active",
        {"team": "a"},
    )
    assert backends.db_reads == 1 and hash_api_key(raw) in backends.redis

    await verify_api_key(raw)
    assert backends.db_reads == 1 and backends.redis_reads == 1  # local cache hit

    backends.verifier.reset()
    await verify_api_key(raw)
    assert backends.db_reads == 1 and backends.redis_reads == 2  # Redis hit, no DB


@pytest.mark.asyncio
async def test_unknown_key_is_negatively_cached(backends):
    for _ in range(3):
        with pytest.raises(ApiKeyRejected) as exc:
            await verify_api_key("gd-unknown")
        assert exc.value.wire_code == "invalid_api_key"
        assert exc.value.status_code == 401
    assert backends.db_reads == 1


@pytest.mark.asyncio
async def test_missing_key_rejected(backends):
    with pytest.raises(ApiKeyRejected):
        await verify_api_key("")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["revoked", "suspended", "expired"])
async def test_non_active_status_rejected_with_wire_code(backends, status):
    raw = f"gd-{status}"
    backends.add(raw, status=status)
    with pytest.raises(ApiKeyRejected) as exc:
        await verify_api_key(raw)
    assert exc.value.wire_code == f"api_key_{status}"
    assert exc.value.error_code == f"API_KEY_{status.upper()}"


@pytest.mark.asyncio
async def test_expiry_enforced_even_when_status_is_active(backends):
    raw = "gd-old"
    backends.add(raw, expires_at=time.time() - 5)
    with pytest.raises(ApiKeyRejected) as exc:
        await verify_api_key(raw)
    assert exc.value.wire_code == "api_key_expired"


@pytest.mark.asyncio
async def test_principal_exposes_aware_datetime(backends):
    raw = "gd-future"
    future = time.time() + 3600
    backends.add(raw, expires_at=future)
    principal = await verify_api_key(raw)
    assert principal.expires_at == datetime.fromtimestamp(future, tz=timezone.utc)
    assert principal.as_dict()["expires_at"] == future


def test_principal_accepts_iso_expiry():
    p = ApiKeyPrincipal.from_dict(
        {
            "id": "x",
            "name": "n",
            "status": "active",
            "expires_at": "2030-01-01T00:00:00",
        }
    )
    assert p.expires_at == datetime(2030, 1, 1, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_invalidation_drops_cached_entry(backends):
    raw = "gd-revoke-me"
    backends.add(raw)
    await verify_api_key(raw)
    backends.db[hash_api_key(raw)]["status"] = "revoked"
    await verify_api_key(raw)  # still served from cache
    await svc.verifier.invalidate([hash_api_key(raw)])
    with pytest.raises(ApiKeyRejected):
        await verify_api_key(raw)


@pytest.mark.asyncio
async def test_serving_adapter_accepts_principal(backends):
    """the API-layer domain's serving auth adapter consumes ApiKeyPrincipal (contract check)."""
    from app.services.serving import auth as serving_auth

    raw = "gd-adapter"
    future = time.time() + 600
    key_id = backends.add(raw, expires_at=future)
    ctx = serving_auth._ctx_from_any(await verify_api_key(raw))
    assert ctx.id == key_id and ctx.status == "active" and not ctx.expired


@pytest.mark.asyncio
async def test_verify_api_key_cached_path_under_one_millisecond(backends):
    raw = "sk-" + "b" * 48
    backends.add(raw)
    await verify_api_key(raw)  # warm
    n = 20000
    start = time.perf_counter()
    for _ in range(n):
        await verify_api_key(raw)
    per_call_ms = (time.perf_counter() - start) * 1000 / n
    print(f"\nverify_api_key cached path: {per_call_ms * 1000:.2f} us/call")
    assert per_call_ms < 1.0
