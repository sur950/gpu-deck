"""
Integration tests against the real local Postgres and Redis.

Isolation (other agents share gpu_deck_dev and the gpu-deck Redis user):

- Postgres: a scratch schema ``b1test_<hex>`` inside gpu_deck_dev, built from
  001_init/up.sql, used through a dedicated engine whose search_path points at
  it, and dropped (CASCADE) at the end. Nothing touches the public schema.
- Redis: REDIS_KEY_PREFIX becomes ``gpu-deck:b1test:<hex>:`` for the module
  (still inside the ACL namespace) and every key under it is deleted at the end.
- Audit events go to an in-memory sink (no Celery broker traffic).
- slowapi is disabled so repeated logins do not trip the 10/minute limit.

The app under test is assembled from the core domain's route modules with the same
middleware/exception wiring as app.main, so it does not depend on other
other domains' routes importing cleanly.
"""

import asyncio
import csv
import importlib
import io
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import asyncpg
import pytest
import pytest_asyncio
from app.core.settings import settings
from httpx import ASGITransport, AsyncClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.db,
    pytest.mark.asyncio(loop_scope="session"),
]

UP_SQL = Path(__file__).resolve().parents[2] / "app/db/postgres/alembic/migrations/001_init/up.sql"
CORE_ROUTES = ("auth", "api_keys", "vault", "audit", "ip_rules", "branding", "health")
TEST_PASSWORD = "core-integration-Passw0rd"
BASE_URL = "http://core.test"


def _plain_dsn() -> str:
    return settings.get_database_url().replace("postgresql+asyncpg://", "postgresql://", 1)


def build_b1_app():
    """FastAPI app with the core domain's routers and app.main's middleware/handler wiring."""
    from app.core.exceptions import (
        AuthenticationError,
        AuthorizationError,
        BaseAPIException,
        base_exception_handler,
        generic_exception_handler,
        http_exception_handler,
        validation_exception_handler,
    )
    from app.core.rate_limiter import limiter, rate_limit_exceeded_handler
    from app.core.request_context import set_request_context_dep
    from app.middlewares import install_middlewares
    from fastapi import Depends, FastAPI
    from fastapi.exceptions import RequestValidationError
    from slowapi.errors import RateLimitExceeded
    from starlette.exceptions import HTTPException as StarletteHTTPException

    app = FastAPI()
    app.router.dependencies.append(Depends(set_request_context_dep))
    app.state.limiter = limiter
    install_middlewares(app, "admin")
    app.add_exception_handler(AuthenticationError, base_exception_handler)
    app.add_exception_handler(AuthorizationError, base_exception_handler)
    app.add_exception_handler(BaseAPIException, base_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_exception_handler(Exception, generic_exception_handler)
    for name in CORE_ROUTES:
        module = importlib.import_module(f"app.api.v1.routes.{name}")
        app.include_router(module.router, prefix=settings.API_V1_PREFIX)
    return app


class B1Env:
    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:10]
        self.schema = f"b1test_{self.tag}"
        self.prefix = f"gpu-deck:b1test:{self.tag}:"
        self.events: List[Dict[str, Any]] = []
        self.password = TEST_PASSWORD
        self.app = None
        self.engine = None
        self._saved: Dict[str, Any] = {}

    def client(self) -> AsyncClient:
        return AsyncClient(transport=ASGITransport(app=self.app), base_url=BASE_URL)

    async def login(self, client: AsyncClient, password: str = None) -> str:
        r = await client.post(
            f"{settings.API_V1_PREFIX}/auth/login",
            json={"username": "admin", "password": password or self.password},
        )
        assert r.status_code == 200, r.text
        return r.json()["data"]["csrf_token"]

    def events_of(self, event_type: str) -> List[Dict[str, Any]]:
        return [e for e in self.events if e["event_type"] == event_type]


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def platform():
    from app.core.rate_limiter import limiter
    from app.db.postgres import connection
    from app.db.postgres.seed import seed_if_empty
    from app.db.redis import client as redis_client_module
    from app.db.redis.cache_service import CacheService
    from app.services.api_keys.service import verifier
    from app.services.audit import audit
    from app.services.security.ip_rules import checker
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    env = B1Env()

    # --- scratch schema ---
    conn = await asyncpg.connect(_plain_dsn())
    try:
        await conn.execute(f'CREATE SCHEMA "{env.schema}"')
        await conn.execute(f'SET search_path TO "{env.schema}", public')
        sql = "\n".join(
            line
            for line in UP_SQL.read_text().splitlines()
            if not line.strip().startswith("VACUUM")
        )
        await conn.execute(sql)
    except Exception:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{env.schema}" CASCADE')
        await conn.close()
        raise
    await conn.close()

    env.engine = create_async_engine(
        settings.get_database_url(),
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{env.schema},public"}},
    )

    # --- swap globals ---
    env._saved = {
        "engine": connection.engine,
        "REDIS_KEY_PREFIX": settings.REDIS_KEY_PREFIX,
        "ADMIN_USERNAME": settings.ADMIN_USERNAME,
        "ADMIN_PASSWORD": settings.ADMIN_PASSWORD,
        "AUTH_LOCKOUT_ATTEMPTS": settings.AUTH_LOCKOUT_ATTEMPTS,
        "IP_ALLOWLIST": settings.IP_ALLOWLIST,
        "IP_BLOCKLIST": settings.IP_BLOCKLIST,
        "GATEWAY_IP_ALLOWLIST": settings.GATEWAY_IP_ALLOWLIST,
        "TRUSTED_PROXIES": settings.TRUSTED_PROXIES,
        "API_KEY_PREFIX": settings.API_KEY_PREFIX,
        "limiter_enabled": limiter.enabled,
    }
    connection.engine = env.engine
    settings.REDIS_KEY_PREFIX = env.prefix
    settings.ADMIN_USERNAME = "admin"
    settings.ADMIN_PASSWORD = env.password
    settings.AUTH_LOCKOUT_ATTEMPTS = 3
    settings.IP_ALLOWLIST = ""
    settings.IP_BLOCKLIST = ""
    settings.GATEWAY_IP_ALLOWLIST = ""
    settings.TRUSTED_PROXIES = "127.0.0.1/32,::1/128"
    settings.API_KEY_PREFIX = "gd-"
    limiter.enabled = False
    await CacheService.reset()
    await redis_client_module.close_redis()
    checker.reset()
    verifier.reset()
    audit.set_sink(env.events.append)

    async with connection.get_session_maker()() as session:
        assert await seed_if_empty(session) == "created"

    env.app = build_b1_app()
    try:
        yield env
    finally:
        audit.set_sink(None)
        await verifier.flush_pending()
        verifier.reset()
        checker.reset()
        try:
            redis = await redis_client_module.get_redis()
            keys = [k async for k in redis.scan_iter(match=f"{env.prefix}*", count=500)]
            if keys:
                await redis.delete(*keys)
        finally:
            await CacheService.reset()
            await redis_client_module.close_redis()
            connection.engine = env._saved.pop("engine")
            limiter.enabled = env._saved.pop("limiter_enabled")
            for name, value in env._saved.items():
                setattr(settings, name, value)
            await env.engine.dispose()
            conn = await asyncpg.connect(_plain_dsn())
            await conn.execute(f'DROP SCHEMA IF EXISTS "{env.schema}" CASCADE')
            await conn.close()


async def _scalar(env: B1Env, sql: str, *args):
    conn = await asyncpg.connect(_plain_dsn(), server_settings={"search_path": env.schema})
    try:
        return await conn.fetchval(sql, *args)
    finally:
        await conn.close()


API = settings.API_V1_PREFIX


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def test_login_sets_cookies_and_me_works(platform):
    async with platform.client() as client:
        r = await client.post(
            f"{API}/auth/login",
            json={"username": "ADMIN", "password": platform.password},
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["user"]["username"] == "admin"
        assert data["expires_in"] > 0 and data["csrf_token"]
        set_cookie = "\n".join(r.headers.get_list("set-cookie"))
        assert f"{settings.SESSION_COOKIE_NAME}=" in set_cookie
        session_line = next(
            line
            for line in r.headers.get_list("set-cookie")
            if line.startswith(settings.SESSION_COOKIE_NAME)
        )
        assert "HttpOnly" in session_line and "SameSite=lax" in session_line
        csrf_line = next(
            line
            for line in r.headers.get_list("set-cookie")
            if line.startswith(settings.CSRF_COOKIE_NAME)
        )
        assert "HttpOnly" not in csrf_line

        me = await client.get(f"{API}/auth/me")
        assert me.status_code == 200
        assert me.json()["data"]["last_login_ip"] == "127.0.0.1"

        csrf = await client.get(f"{API}/auth/csrf")
        assert csrf.json()["data"]["csrf_token"] == data["csrf_token"]

    assert platform.events_of("auth.login")[-1]["outcome"] == "success"


async def test_unauthenticated_requests_get_401(platform):
    async with platform.client() as client:
        r = await client.get(f"{API}/auth/me")
        assert r.status_code == 401
        assert r.json()["success"] is False
        bad = await client.get(f"{API}/auth/me", headers={"authorization": "Bearer nope"})
        assert bad.status_code == 401


async def test_csrf_enforced_on_cookie_sessions(platform):
    async with platform.client() as client:
        token = await platform.login(client)
        missing = await client.post(f"{API}/auth/logout-all")
        assert missing.status_code == 403
        assert missing.json()["error"]["code"] == "CSRF_ERROR"
        wrong = await client.post(
            f"{API}/auth/logout-all", headers={settings.CSRF_HEADER_NAME: "f" * 64}
        )
        assert wrong.status_code == 403
        ok = await client.post(f"{API}/auth/logout-all", headers={settings.CSRF_HEADER_NAME: token})
        assert ok.status_code == 204


async def test_bearer_token_clients_skip_csrf(platform):
    async with platform.client() as client:
        await platform.login(client)
        raw = client.cookies.get(settings.SESSION_COOKIE_NAME)
    async with platform.client() as cli:
        r = await cli.post(
            f"{API}/api-keys",
            json={"name": f"cli-{uuid.uuid4().hex[:6]}"},
            headers={"authorization": f"Bearer {raw}"},
        )
        assert r.status_code == 201, r.text


async def test_session_list_and_revoke_other_session(platform):
    async with platform.client() as a, platform.client() as b:
        token_a = await platform.login(a)
        await platform.login(b)
        sessions = (await a.get(f"{API}/auth/sessions")).json()["data"]
        assert sum(1 for s in sessions if s["is_current"]) == 1
        me_b = (await b.get(f"{API}/auth/sessions")).json()["data"]
        b_id = next(s["id"] for s in me_b if s["is_current"])

        r = await a.delete(
            f"{API}/auth/sessions/{b_id}", headers={settings.CSRF_HEADER_NAME: token_a}
        )
        assert r.status_code == 204
        assert (await b.get(f"{API}/auth/me")).status_code == 401
        assert (await a.get(f"{API}/auth/me")).status_code == 200

        gone = await a.delete(
            f"{API}/auth/sessions/{b_id}", headers={settings.CSRF_HEADER_NAME: token_a}
        )
        assert gone.status_code == 404


async def test_logout_revokes_session(platform):
    async with platform.client() as client:
        token = await platform.login(client)
        raw = client.cookies.get(settings.SESSION_COOKIE_NAME)
        r = await client.post(f"{API}/auth/logout", headers={settings.CSRF_HEADER_NAME: token})
        assert r.status_code == 204
    async with platform.client() as other:
        r = await other.get(f"{API}/auth/me", headers={"authorization": f"Bearer {raw}"})
        assert r.status_code == 401


async def test_change_password_revokes_other_sessions_and_seed_rule(platform):
    from app.db.postgres import connection
    from app.db.postgres.seed import seed_if_empty

    new_password = "ui-chosen-Passw0rd-1"
    async with platform.client() as a, platform.client() as b:
        token_a = await platform.login(a)
        await platform.login(b)

        short = await a.post(
            f"{API}/auth/change-password",
            json={"current_password": platform.password, "new_password": "short"},
            headers={settings.CSRF_HEADER_NAME: token_a},
        )
        assert short.status_code == 422
        wrong = await a.post(
            f"{API}/auth/change-password",
            json={"current_password": "not-it-at-all", "new_password": new_password},
            headers={settings.CSRF_HEADER_NAME: token_a},
        )
        assert wrong.status_code == 401

        r = await a.post(
            f"{API}/auth/change-password",
            json={"current_password": platform.password, "new_password": new_password},
            headers={settings.CSRF_HEADER_NAME: token_a},
        )
        assert r.status_code == 204, r.text
        assert (await a.get(f"{API}/auth/me")).status_code == 200  # current session kept
        assert (await b.get(f"{API}/auth/me")).status_code == 401  # others revoked

    # Restart with .env unchanged: the UI password survives.
    async with connection.get_session_maker()() as session:
        assert await seed_if_empty(session) == "unchanged"
    async with platform.client() as c:
        await platform.login(c, new_password)
        bad = await c.post(
            f"{API}/auth/login",
            json={"username": "admin", "password": platform.password},
        )
        assert bad.status_code == 401

    # ADMIN_PASSWORD changes in .env: the .env value wins, every session is revoked.
    rotated = "env-rotated-Passw0rd-2"
    settings.ADMIN_PASSWORD = rotated
    async with platform.client() as live:
        await platform.login(live, new_password)
        async with connection.get_session_maker()() as session:
            assert await seed_if_empty(session) == "password_rotated"
        assert (await live.get(f"{API}/auth/me")).status_code == 401
    platform.password = rotated
    async with platform.client() as c:
        await platform.login(c, rotated)
        old = await c.post(
            f"{API}/auth/login", json={"username": "admin", "password": new_password}
        )
        assert old.status_code == 401
    assert platform.events_of("auth.password_changed")
    assert platform.events_of("auth.admin_password_rotated")


async def test_login_lockout_then_recovery(platform):
    from app.db.postgres import connection
    from app.db.postgres.repositories.admin_user_repository import AdminUserRepository
    from app.db.redis.cache_service import CacheService

    async with platform.client() as client:
        codes = []
        for _ in range(int(settings.AUTH_LOCKOUT_ATTEMPTS)):
            r = await client.post(
                f"{API}/auth/login",
                json={"username": "admin", "password": "wrong-password"},
            )
            codes.append((r.status_code, r.json()["error"]["code"]))
        assert codes[0] == (401, "INVALID_CREDENTIALS")
        assert codes[-1] == (401, "ACCOUNT_LOCKED")

        # Even the right password is refused while locked.
        r = await client.post(
            f"{API}/auth/login",
            json={"username": "admin", "password": platform.password},
        )
        assert (r.status_code, r.json()["error"]["code"]) == (401, "ACCOUNT_LOCKED")
        locked_until = await _scalar(platform, "SELECT locked_until FROM admin_users LIMIT 1")
        assert locked_until > datetime.now(timezone.utc)
        assert platform.events_of("auth.locked_out")

        # Unlock (what an expired lockout window amounts to) and log in again.
        async with connection.get_session_maker()() as session:
            repo = AdminUserRepository(session)
            admin = await repo.get_singleton()
            await repo.clear_lockout(admin.id)
            await session.commit()
        cache = await CacheService.get_instance()
        await cache.delete("login_failures:admin")
        await platform.login(client)
        assert await _scalar(platform, "SELECT failed_login_attempts FROM admin_users LIMIT 1") == 0


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


async def test_api_key_lifecycle_and_cache_invalidation(platform):
    from app.services.api_keys.service import ApiKeyRejected, verifier, verify_api_key

    async with platform.client() as client:
        token = await platform.login(client)
        hdr = {settings.CSRF_HEADER_NAME: token}
        r = await client.post(
            f"{API}/api-keys",
            json={
                "name": "team-a",
                "description": "integration",
                "metadata": {"team": "a"},
            },
            headers=hdr,
        )
        assert r.status_code == 201, r.text
        created = r.json()["data"]
        key, key_id = created["key"], created["id"]
        assert re.fullmatch(r"gd-[0-9a-f]{48}", key)
        assert created["key_prefix"] == key[:12]

        got = (await client.get(f"{API}/api-keys/{key_id}")).json()["data"]
        assert "key" not in got
        stored = await _scalar(
            platform, "SELECT key_hash FROM api_keys WHERE id = $1", uuid.UUID(key_id)
        )
        assert stored != key and len(stored) == 64

        dup = await client.post(f"{API}/api-keys", json={"name": "TEAM-A"}, headers=hdr)
        assert dup.status_code == 409

        principal = await verify_api_key(key)
        assert principal.id == key_id and principal.metadata == {"team": "a"}
        await verify_api_key(key)  # now warm in the local cache

        # Revoke through the admin API: rejected on the very next call.
        r = await client.post(f"{API}/api-keys/{key_id}/revoke", headers=hdr)
        assert r.status_code == 200 and r.json()["data"]["status"] == "revoked"
        with pytest.raises(ApiKeyRejected) as exc:
            await verify_api_key(key)
        assert exc.value.wire_code == "api_key_revoked"

        # Rotate a second key: old secret dies, new one works, same id.
        r2 = (await client.post(f"{API}/api-keys", json={"name": "team-b"}, headers=hdr)).json()
        key2, key2_id = r2["data"]["key"], r2["data"]["id"]
        await verify_api_key(key2)
        rot = await client.post(f"{API}/api-keys/{key2_id}/rotate", headers=hdr)
        assert rot.status_code == 200
        new_key = rot.json()["data"]["key"]
        assert rot.json()["data"]["id"] == key2_id and new_key != key2
        with pytest.raises(ApiKeyRejected):
            await verify_api_key(key2)
        assert (await verify_api_key(new_key)).id == key2_id

        # Suspend / activate.
        assert (
            await client.post(f"{API}/api-keys/{key2_id}/suspend", headers=hdr)
        ).status_code == 200
        with pytest.raises(ApiKeyRejected) as exc:
            await verify_api_key(new_key)
        assert exc.value.wire_code == "api_key_suspended"
        assert (
            await client.post(f"{API}/api-keys/{key2_id}/activate", headers=hdr)
        ).status_code == 200
        await verify_api_key(new_key)
        bad_transition = await client.post(f"{API}/api-keys/{key_id}/suspend", headers=hdr)
        assert bad_transition.status_code == 409

        # Update, list, stats.
        patched = await client.patch(
            f"{API}/api-keys/{key2_id}", json={"description": "renamed"}, headers=hdr
        )
        assert patched.json()["data"]["description"] == "renamed"
        listing = await client.get(f"{API}/api-keys", params={"status": "active", "search": "team"})
        assert listing.json()["meta"]["total"] >= 1
        stats = (await client.get(f"{API}/api-keys/stats")).json()["data"]
        assert stats["revoked"] >= 1 and stats["active"] >= 1

        # Delete.
        assert (await client.delete(f"{API}/api-keys/{key2_id}", headers=hdr)).status_code == 204
        assert (await client.get(f"{API}/api-keys/{key2_id}")).status_code == 404
        with pytest.raises(ApiKeyRejected):
            await verify_api_key(new_key)

    assert {
        "api_key.created",
        "api_key.revoked",
        "api_key.rotated",
        "api_key.deleted",
    } <= {e["event_type"] for e in platform.events}
    assert all(
        "key" not in (e.get("after_state") or {}) for e in platform.events_of("api_key.created")
    )
    verifier.reset()


async def test_api_key_expiry_and_last_used_flush(platform):
    from app.services.api_keys.service import (
        ApiKeyRejected,
        expire_due_keys,
        flush_last_used,
        verifier,
        verify_api_key,
    )

    async with platform.client() as client:
        token = await platform.login(client)
        hdr = {settings.CSRF_HEADER_NAME: token}
        soon = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
        created = (
            await client.post(
                f"{API}/api-keys",
                json={"name": "short-lived", "expires_at": soon},
                headers=hdr,
            )
        ).json()["data"]
        key, key_id = created["key"], created["id"]

        await verify_api_key(key)
        await verifier.flush_pending()
        assert await flush_last_used() >= 1
        last_used = await _scalar(
            platform,
            "SELECT last_used_at FROM api_keys WHERE id = $1",
            uuid.UUID(key_id),
        )
        assert last_used is not None

        await asyncio.sleep(2.2)
        with pytest.raises(ApiKeyRejected) as exc:
            await verify_api_key(key)
        assert exc.value.wire_code == "api_key_expired"
        assert await expire_due_keys() >= 1
        status = (await client.get(f"{API}/api-keys/{key_id}")).json()["data"]["status"]
        assert status == "expired"


async def test_revocation_reaches_other_processes_via_pubsub(platform):
    """A second verifier (another serving replica) drops its warm entry on revoke."""
    from app.services.api_keys import service as svc

    replica = svc._Verifier()
    await replica.start_subscriber()
    try:
        async with platform.client() as client:
            token = await platform.login(client)
            hdr = {settings.CSRF_HEADER_NAME: token}
            created = (
                await client.post(f"{API}/api-keys", json={"name": "replica"}, headers=hdr)
            ).json()["data"]
            await asyncio.sleep(0.3)  # let the subscriber attach
            await replica.resolve(created["key"])
            key_hash = svc.hash_api_key(created["key"])
            assert key_hash in replica._local
            await client.post(f"{API}/api-keys/{created['id']}/revoke", headers=hdr)
            for _ in range(50):
                if key_hash not in replica._local:
                    break
                await asyncio.sleep(0.05)
            assert key_hash not in replica._local
            with pytest.raises(svc.ApiKeyRejected):
                await replica.resolve(created["key"])
    finally:
        await replica.stop_subscriber()
        await replica.flush_pending()


async def test_verify_api_key_latency(platform):
    """Microbenchmark: process-local cached path < 1 ms; Redis path reported."""
    from app.services.api_keys.service import verifier, verify_api_key

    async with platform.client() as client:
        token = await platform.login(client)
        key = (
            await client.post(
                f"{API}/api-keys",
                json={"name": "bench"},
                headers={settings.CSRF_HEADER_NAME: token},
            )
        ).json()["data"]["key"]

    await verify_api_key(key)
    n = 5000
    start = time.perf_counter()
    for _ in range(n):
        await verify_api_key(key)
    cached_ms = (time.perf_counter() - start) * 1000 / n

    verifier.local_cache_enabled = False
    try:
        m = 300
        start = time.perf_counter()
        for _ in range(m):
            await verify_api_key(key)
        redis_ms = (time.perf_counter() - start) * 1000 / m
    finally:
        verifier.local_cache_enabled = True
    print(f"\nverify_api_key: cached {cached_ms * 1000:.1f} us/call, redis {redis_ms:.3f} ms/call")
    assert cached_ms < 1.0
    assert redis_ms < 20.0


# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------


async def test_vault_crud_reveal_and_rotation(platform):
    from app.db.postgres import connection
    from app.services.vault import VaultService
    from app.services.vault.crypto import decrypt_value
    from cryptography.fernet import Fernet

    secret = "hf_" + uuid.uuid4().hex
    async with platform.client() as client:
        token = await platform.login(client)
        hdr = {settings.CSRF_HEADER_NAME: token}
        r = await client.post(
            f"{API}/vault",
            json={
                "name": "hf-token",
                "type": "token",
                "provider": "huggingface",
                "value": secret,
            },
            headers=hdr,
        )
        assert r.status_code == 201, r.text
        cred = r.json()["data"]
        assert secret not in r.text and cred["masked_value"].endswith(secret[-4:])
        ct = await _scalar(
            platform,
            "SELECT encrypted_value FROM credentials WHERE id = $1",
            uuid.UUID(cred["id"]),
        )
        assert secret not in ct and decrypt_value(ct) == secret

        listing = await client.get(f"{API}/vault", params={"provider": "huggingface"})
        assert listing.json()["meta"]["total"] == 1 and secret not in listing.text
        meta = (await client.get(f"{API}/vault/metadata")).json()["data"]
        assert "ca_key" in meta["types"] and "internal_ca" in meta["providers"]
        stats = (await client.get(f"{API}/vault/stats")).json()["data"]
        assert stats["total"] >= 1

        revealed = await client.post(f"{API}/vault/{cred['id']}/reveal", headers=hdr)
        assert revealed.status_code == 200
        assert revealed.json()["data"]["value"] == secret
        usage = await _scalar(
            platform,
            "SELECT count(*) FROM credential_usage_history WHERE credential_id = $1 "
            "AND purpose = 'reveal'",
            uuid.UUID(cred["id"]),
        )
        assert usage == 1
        reveal_events = platform.events_of("credential.revealed")
        assert reveal_events and secret not in str(reveal_events)

        # Other domains: programmatic access by name / provider, gateway-shaped calls.
        async with connection.get_session_maker()() as session:
            vault = VaultService(session)
            assert await vault.get_secret("hf-token", purpose="hf_download") == secret
            assert await vault.get_secret_by_provider("huggingface") == secret
            ca = await vault.upsert_secret(
                "internal-ca", "PEM-1", type="ca_key", provider="internal_ca"
            )
            ca2 = await vault.upsert_secret(
                "internal-ca", "PEM-2", type="ca_key", provider="internal_ca"
            )
            assert ca["id"] == ca2["id"]
            assert await vault.get_cached_credential_value(ca["id"]) == "PEM-2"
            from app.db.postgres.repositories.credential_repository import (
                CredentialRepository,
            )

            compat = VaultService(CredentialRepository(session))  # gateway-shaped constructor
            record = await compat.create_credential(
                name="smtp-password", type="smtp", value="mail-secret", created_by=None
            )
            assert record.id and record["masked_value"]
            await session.commit()

        # Key rotation: prepend a new key, re-encrypt, retire the old key.
        old_keys = settings.VAULT_KEYS
        new_key = Fernet.generate_key().decode()
        settings.VAULT_KEYS = f"{new_key},{old_keys}"
        try:
            async with connection.get_session_maker()() as session:
                rotated = await VaultService(session).rotate_keys()
                await session.commit()
            assert rotated >= 3
            settings.VAULT_KEYS = new_key
            again = await client.post(f"{API}/vault/{cred['id']}/reveal", headers=hdr)
            assert again.json()["data"]["value"] == secret
        finally:
            settings.VAULT_KEYS = old_keys
            async with connection.get_session_maker()() as session:
                # Put rows back on the original key so teardown/other tests stay consistent.
                settings.VAULT_KEYS = f"{old_keys},{new_key}"
                await VaultService(session).rotate_keys()
                await session.commit()
                settings.VAULT_KEYS = old_keys

        upd = await client.patch(
            f"{API}/vault/{cred['id']}", json={"value": secret + "-v2"}, headers=hdr
        )
        assert upd.status_code == 200 and upd.json()["data"]["masked_value"].endswith("-v2")
        assert (await client.delete(f"{API}/vault/{cred['id']}", headers=hdr)).status_code == 204
        assert (await client.get(f"{API}/vault/{cred['id']}")).status_code == 404
        async with connection.get_session_maker()() as session:
            assert await VaultService(session).get_secret("hf-token", required=False) is None

    assert platform.events_of("vault.keys_rotated")


async def test_vault_expiry_job(platform):
    from app.db.postgres import connection
    from app.services.vault import VaultService

    async with connection.get_session_maker()() as session:
        vault = VaultService(session)
        cred = await vault.create_credential(
            {
                "name": "expiring",
                "type": "api_key",
                "value": "will-expire-123",
                "expires_at": datetime.now(timezone.utc) - timedelta(minutes=1),
            },
            created_by=None,
        )
        await session.commit()
    async with connection.get_session_maker()() as session:
        assert await VaultService(session).expire_credentials() >= 1
        await session.commit()
    status = await _scalar(platform, "SELECT status FROM credentials WHERE id = $1", cred["id"])
    assert status == "expired"


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


async def test_audit_write_search_and_csv_export(platform):
    from app.services.audit.service import write_audit_event_standalone

    written = 0
    for event in list(platform.events):
        if await write_audit_event_standalone(event):
            written += 1
    assert written == len(platform.events) > 0

    async with platform.client() as client:
        await platform.login(client)
        r = await client.get(f"{API}/audit", params={"event_type": "api_key.created"})
        assert r.status_code == 200
        items = r.json()["data"]
        assert items and all(i["event_type"] == "api_key.created" for i in items)
        assert all(i["actor_type"] == "admin" for i in items)

        failures = await client.get(
            f"{API}/audit", params={"outcome": "failure", "event_type": "auth.login"}
        )
        assert failures.json()["meta"]["total"] >= 1

        one = await client.get(f"{API}/audit/{items[0]['id']}")
        assert one.status_code == 200 and one.json()["data"]["id"] == items[0]["id"]

        export = await client.get(f"{API}/audit/export", params={"actor_type": "admin"})
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("text/csv")
        rows = list(csv.DictReader(io.StringIO(export.text)))
        assert rows and {"event_type", "actor_type", "created_at"} <= set(rows[0])
        assert all(row["actor_type"] == "admin" for row in rows)


# ---------------------------------------------------------------------------
# IP rules
# ---------------------------------------------------------------------------


async def test_ip_rules_crud_self_lockout_and_enforcement(platform):
    from app.services.security.ip_rules import check_ip_allowed

    async with platform.client() as client:
        token = await platform.login(client)
        hdr = {settings.CSRF_HEADER_NAME: token}

        # Blocking our own address on the admin scope is refused.
        lock = await client.post(
            f"{API}/ip-rules",
            json={"action": "block", "scope": "admin", "cidr": "127.0.0.0/8"},
            headers=hdr,
        )
        assert lock.status_code == 409 and lock.json()["error"]["code"] == "SELF_LOCKOUT"
        # So is an allow-list that does not include us.
        lock2 = await client.post(
            f"{API}/ip-rules",
            json={"action": "allow", "scope": "admin", "cidr": "10.0.0.0/8"},
            headers=hdr,
        )
        assert lock2.status_code == 409

        # api_layer scope: block a range; the serving checker sees it at once.
        assert await check_ip_allowed("198.51.100.20", "api_layer") is True
        blk = await client.post(
            f"{API}/ip-rules",
            json={"action": "block", "scope": "api_layer", "cidr": "198.51.100.0/24"},
            headers=hdr,
        )
        assert blk.status_code == 201, blk.text
        block_id = blk.json()["data"]["id"]
        assert await check_ip_allowed("198.51.100.20", "api_layer") is False
        assert await check_ip_allowed("203.0.113.1", "api_layer") is True
        dup = await client.post(
            f"{API}/ip-rules",
            json={"action": "block", "scope": "api_layer", "cidr": "198.51.100.0/24"},
            headers=hdr,
        )
        assert dup.status_code == 409

        chk = await client.post(
            f"{API}/ip-rules/check",
            json={"ip": "198.51.100.20", "scope": "api_layer"},
            headers=hdr,
        )
        result = chk.json()["data"]
        assert result["allowed"] is False and result["reason"] == "blocked_by_rule"
        assert result["matched_rule"]["id"] == block_id

        # Admin allow-list: include ourselves first, then a private range.
        me = await client.post(
            f"{API}/ip-rules",
            json={"action": "allow", "scope": "admin", "cidr": "127.0.0.1"},
            headers=hdr,
        )
        assert me.status_code == 201
        assert me.json()["data"]["cidr"] == "127.0.0.1/32"
        lan = await client.post(
            f"{API}/ip-rules",
            json={"action": "allow", "scope": "admin", "cidr": "10.0.0.0/8"},
            headers=hdr,
        )
        assert lan.status_code == 201
        me_id = me.json()["data"]["id"]

        # Middleware: Caddy (trusted, loopback) forwards the real client address.
        inside = await client.get(f"{API}/auth/me", headers={"x-forwarded-for": "10.1.2.3"})
        assert inside.status_code == 200
        outside = await client.get(f"{API}/auth/me", headers={"x-forwarded-for": "192.0.2.44"})
        assert outside.status_code == 403
        assert outside.json()["error"]["code"] == "IP_BLOCKED"
        live = await client.get(f"{API}/health/live", headers={"x-forwarded-for": "192.0.2.44"})
        assert live.status_code == 200

        # Removing our own allow rule would lock us out.
        rm = await client.delete(f"{API}/ip-rules/{me_id}", headers=hdr)
        assert rm.status_code == 409

        # Disable via PATCH is guarded the same way; disabling the LAN rule is fine.
        lan_id = lan.json()["data"]["id"]
        patched = await client.patch(
            f"{API}/ip-rules/{lan_id}", json={"enabled": False}, headers=hdr
        )
        assert patched.status_code == 200 and patched.json()["data"]["enabled"] is False
        assert (
            await client.get(f"{API}/auth/me", headers={"x-forwarded-for": "10.1.2.3"})
        ).status_code == 403

        listing = await client.get(f"{API}/ip-rules", params={"scope": "admin"})
        assert listing.json()["meta"]["total"] == 2

        # Clean up: delete LAN + block, then our own allow (no other allow left -> open mode).
        for rule_id in (lan_id, block_id, me_id):
            assert (
                await client.delete(f"{API}/ip-rules/{rule_id}", headers=hdr)
            ).status_code == 204
        assert await check_ip_allowed("198.51.100.20", "api_layer") is True
        assert (
            await client.get(f"{API}/auth/me", headers={"x-forwarded-for": "192.0.2.44"})
        ).status_code == 200

    assert platform.events_of("ip_rule.created") and platform.events_of("ip_rule.deleted")


# ---------------------------------------------------------------------------
# Branding, health, system info
# ---------------------------------------------------------------------------


async def test_branding_public_and_health(platform):
    async with platform.client() as client:
        br = await client.get(f"{API}/branding")
        assert br.status_code == 200
        assert br.headers["cache-control"] == "public, max-age=300"
        assert br.json()["data"]["app_name"]

        live = await client.get(f"{API}/health/live")
        assert live.json() == {"status": "ok"}

        health = await client.get(f"{API}/health")
        data = health.json()["data"]
        assert data["checks"]["database"]["status"] == "healthy"
        assert data["checks"]["redis"]["status"] == "healthy"

        ready = await client.get(f"{API}/health/ready")
        assert ready.json()["data"]["checks"]["seed"]["status"] == "healthy"

        assert (await client.get(f"{API}/system/info")).status_code == 401
        await platform.login(client)
        info = (await client.get(f"{API}/system/info")).json()["data"]
        assert info["limits"]["auth_lockout_attempts"] == settings.AUTH_LOCKOUT_ATTEMPTS
