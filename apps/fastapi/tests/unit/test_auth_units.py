"""
Unit tests: password hashing, session tokens, CSRF tokens + middleware,
cookie->bearer middleware, admin seed rotation rule (mocked repository).

No network: everything below runs against in-memory fakes.
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from app.core.settings import settings
from app.db.postgres import seed as seed_module
from app.middlewares.cookie_auth import CookieAuthMiddleware
from app.middlewares.csrf import CSRFMiddleware
from app.services.auth.csrf import generate_csrf_token, verify_csrf_token
from app.services.auth.token_crypto import (
    decrypt_session_token,
    generate_session_token,
    hash_token,
)
from app.utils.password import PasswordHasher
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

pytestmark = [pytest.mark.unit, pytest.mark.auth]


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def test_password_hash_is_argon2id_and_verifies():
    hashed = PasswordHasher.hash("correct horse battery staple")
    assert hashed.startswith("$argon2id$")
    assert PasswordHasher.verify("correct horse battery staple", hashed)
    assert not PasswordHasher.verify("wrong", hashed)
    assert PasswordHasher.is_usable(hashed)


@pytest.mark.parametrize("bad", [None, "", "not-a-hash", "$2b$12$abcdefghijklmnopqrstuv"])
def test_password_verify_never_raises_on_bad_hash(bad):
    assert PasswordHasher.verify("anything", bad) is False


def test_password_needs_update_for_foreign_hash():
    assert PasswordHasher.needs_update("$2b$12$abc") is True
    assert PasswordHasher.needs_update(PasswordHasher.hash("x" * 12)) is False


# ---------------------------------------------------------------------------
# Session tokens
# ---------------------------------------------------------------------------


def test_session_token_roundtrip_and_hash():
    admin_id, session_id = str(uuid.uuid4()), str(uuid.uuid4())
    raw, token_hash = generate_session_token(admin_id, session_id)
    assert token_hash == hashlib.sha256(raw.encode()).hexdigest() == hash_token(raw)
    payload = decrypt_session_token(raw)
    assert payload == {"u": admin_id, "s": session_id, "v": 1}


@pytest.mark.parametrize("token", ["", "garbage", "gAAAAAB-not-really-fernet"])
def test_session_token_rejects_garbage(token):
    assert decrypt_session_token(token) is None


def test_session_token_rejects_tampering():
    raw, _ = generate_session_token(str(uuid.uuid4()), str(uuid.uuid4()))
    tampered = raw[:-4] + ("AAAA" if raw[-4:] != "AAAA" else "BBBB")
    assert decrypt_session_token(tampered) is None


# ---------------------------------------------------------------------------
# CSRF tokens
# ---------------------------------------------------------------------------


def test_csrf_token_bound_to_session_hash():
    token = generate_csrf_token("hash-a")
    assert token == generate_csrf_token("hash-a")
    assert token != generate_csrf_token("hash-b")
    assert verify_csrf_token("hash-a", token)
    assert not verify_csrf_token("hash-b", token)
    assert not verify_csrf_token("hash-a", "")
    assert not verify_csrf_token("", token)


async def _echo(request: Request):
    return JSONResponse(
        {
            "authorization": request.headers.get("authorization"),
            "via_cookie": (request.scope.get("state") or {}).get("auth_via_cookie", False),
        }
    )


def _csrf_app():
    prefix = settings.API_V1_PREFIX
    app = Starlette(
        routes=[
            Route(f"{prefix}/things", _echo, methods=["GET", "POST", "DELETE"]),
            Route(f"{prefix}/auth/login", _echo, methods=["POST"]),
        ]
    )
    # Same order as the registry: cookie_auth (40) inside csrf (45).
    app.add_middleware(CookieAuthMiddleware)
    app.add_middleware(CSRFMiddleware)
    return app


@pytest.fixture
def session_cookie():
    raw, token_hash = generate_session_token(str(uuid.uuid4()), str(uuid.uuid4()))
    return raw, generate_csrf_token(token_hash)


async def _call(method, path, cookies=None, headers=None):
    app = _csrf_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        cookie_header = "; ".join(f"{k}={v}" for k, v in (cookies or {}).items())
        hdrs = dict(headers or {})
        if cookie_header:
            hdrs["cookie"] = cookie_header
        return await client.request(method, path, headers=hdrs)


@pytest.mark.asyncio
async def test_csrf_blocks_cookie_post_without_header(session_cookie):
    raw, csrf = session_cookie
    r = await _call(
        "POST",
        f"{settings.API_V1_PREFIX}/things",
        cookies={settings.SESSION_COOKIE_NAME: raw, settings.CSRF_COOKIE_NAME: csrf},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CSRF_ERROR"


@pytest.mark.asyncio
async def test_csrf_blocks_mismatched_header(session_cookie):
    raw, csrf = session_cookie
    r = await _call(
        "DELETE",
        f"{settings.API_V1_PREFIX}/things",
        cookies={settings.SESSION_COOKIE_NAME: raw, settings.CSRF_COOKIE_NAME: csrf},
        headers={settings.CSRF_HEADER_NAME: "x" * 64},
    )
    assert r.status_code == 403
    assert "mismatch" in r.json()["error"]["message"]


@pytest.mark.asyncio
async def test_csrf_blocks_token_from_another_session(session_cookie):
    raw, _ = session_cookie
    foreign = generate_csrf_token("some-other-session-hash")
    r = await _call(
        "POST",
        f"{settings.API_V1_PREFIX}/things",
        cookies={settings.SESSION_COOKIE_NAME: raw, settings.CSRF_COOKIE_NAME: foreign},
        headers={settings.CSRF_HEADER_NAME: foreign},
    )
    assert r.status_code == 403
    assert "session" in r.json()["error"]["message"]


@pytest.mark.asyncio
async def test_csrf_allows_double_submit_and_cookie_becomes_bearer(session_cookie):
    raw, csrf = session_cookie
    r = await _call(
        "POST",
        f"{settings.API_V1_PREFIX}/things",
        cookies={settings.SESSION_COOKIE_NAME: raw, settings.CSRF_COOKIE_NAME: csrf},
        headers={settings.CSRF_HEADER_NAME: csrf},
    )
    assert r.status_code == 200
    assert r.json() == {"authorization": f"Bearer {raw}", "via_cookie": True}


@pytest.mark.asyncio
async def test_csrf_skips_safe_methods_exempt_paths_and_bearer_clients(session_cookie):
    raw, _ = session_cookie
    cookies = {settings.SESSION_COOKIE_NAME: raw}
    assert (await _call("GET", f"{settings.API_V1_PREFIX}/things", cookies)).status_code == 200
    login = await _call("POST", f"{settings.API_V1_PREFIX}/auth/login", cookies)
    assert login.status_code == 200
    bearer = await _call(
        "POST",
        f"{settings.API_V1_PREFIX}/things",
        cookies,
        headers={"authorization": "Bearer cli-token"},
    )
    assert bearer.status_code == 200
    assert bearer.json()["authorization"] == "Bearer cli-token"
    no_session = await _call("POST", f"{settings.API_V1_PREFIX}/things")
    assert no_session.status_code == 200  # no cookie -> auth dependency answers 401 later


# ---------------------------------------------------------------------------
# Admin seed (HMAC rotation rule)
# ---------------------------------------------------------------------------


def test_env_secret_fingerprint_is_peppered_scrypt_not_bare_sha256():
    fp = seed_module.env_secret_fingerprint("pw-123456789", "k" * 40)
    assert fp.startswith("scrypt:") and len(fp) <= 64  # admin_users.env_secret_hash VARCHAR(64)
    assert fp != hashlib.sha256(b"pw-123456789").hexdigest()
    assert fp == seed_module.env_secret_fingerprint("pw-123456789", "k" * 40)  # deterministic
    assert fp != seed_module.env_secret_fingerprint("pw-123456789", "j" * 40)
    assert fp != seed_module.env_secret_fingerprint("pw-123456780", "k" * 40)
    assert not seed_module.is_legacy_fingerprint(fp)
    assert seed_module.is_legacy_fingerprint("ab" * 32)


class _FakeAdminRepo:
    admin = None
    calls: list = []

    def __init__(self, session):
        pass

    async def get_singleton(self):
        return _FakeAdminRepo.admin

    async def create(self, **fields):
        _FakeAdminRepo.admin = SimpleNamespace(**fields)
        _FakeAdminRepo.calls.append(("create", fields))
        return _FakeAdminRepo.admin

    async def set_env_secret_hash(self, admin_id, env_secret_hash):
        _FakeAdminRepo.calls.append(("set_env_secret_hash", env_secret_hash))
        _FakeAdminRepo.admin.env_secret_hash = env_secret_hash

    async def apply_env_seed(self, admin_id, **fields):
        _FakeAdminRepo.calls.append(("apply_env_seed", fields))
        for key, value in fields.items():
            if value is not None or key == "display_name":
                setattr(_FakeAdminRepo.admin, key, value)


class _FakeSession:
    async def commit(self):
        pass

    async def rollback(self):
        pass


@pytest.fixture
def fake_seed(monkeypatch):
    _FakeAdminRepo.admin = None
    _FakeAdminRepo.calls = []
    revoked = []

    async def _revoke(session, admin_id):
        revoked.append(admin_id)
        return 2

    monkeypatch.setattr(seed_module, "AdminUserRepository", _FakeAdminRepo)
    monkeypatch.setattr(seed_module, "_revoke_sessions", _revoke)
    monkeypatch.setattr(seed_module, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(settings, "ADMIN_USERNAME", "admin")
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "first-password-123")
    monkeypatch.setattr(settings, "ADMIN_NAME", "Administrator")
    return revoked


@pytest.mark.asyncio
async def test_seed_creates_then_is_stable(fake_seed):
    assert await seed_module.seed_if_empty(_FakeSession()) == "created"
    admin = _FakeAdminRepo.admin
    assert PasswordHasher.verify("first-password-123", admin.password_hash)
    assert admin.env_secret_hash == seed_module.env_secret_fingerprint("first-password-123")
    assert await seed_module.seed_if_empty(_FakeSession()) == "unchanged"


@pytest.mark.asyncio
async def test_seed_keeps_ui_password_until_env_changes(fake_seed, monkeypatch):
    await seed_module.seed_if_empty(_FakeSession())
    admin = _FakeAdminRepo.admin
    admin.id = uuid.uuid4()
    ui_hash = PasswordHasher.hash("changed-in-the-ui-1")
    admin.password_hash = ui_hash  # what change-password does
    admin.password_changed_at = datetime.now(timezone.utc) + timedelta(seconds=1)

    # Restart with the same .env: the UI password survives.
    assert await seed_module.seed_if_empty(_FakeSession()) == "unchanged"
    assert admin.password_hash == ui_hash

    # ADMIN_PASSWORD changes in .env: re-applied, sessions revoked.
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "second-password-456")
    assert await seed_module.seed_if_empty(_FakeSession()) == "password_rotated"
    assert PasswordHasher.verify("second-password-456", admin.password_hash)
    assert fake_seed == [admin.id]


@pytest.mark.asyncio
async def test_seed_syncs_username_without_touching_password(fake_seed, monkeypatch):
    await seed_module.seed_if_empty(_FakeSession())
    admin = _FakeAdminRepo.admin
    admin.id = uuid.uuid4()
    before = admin.password_hash
    monkeypatch.setattr(settings, "ADMIN_USERNAME", "root")
    assert await seed_module.seed_if_empty(_FakeSession()) == "synced"
    assert admin.username == "root"
    assert admin.password_hash == before
    assert fake_seed == []


@pytest.mark.asyncio
async def test_seed_upgrades_legacy_fingerprint_without_touching_password(fake_seed, monkeypatch):
    """1.0.x rows hold a 64-hex HMAC: rewritten to scrypt, UI password and sessions kept."""
    await seed_module.seed_if_empty(_FakeSession())
    admin = _FakeAdminRepo.admin
    admin.id = uuid.uuid4()
    ui_hash = PasswordHasher.hash("changed-in-the-ui-1")
    admin.password_hash = ui_hash
    admin.env_secret_hash = "0f" * 32  # legacy HMAC-SHA256 hex

    assert await seed_module.seed_if_empty(_FakeSession()) == "fingerprint_upgraded"
    assert admin.password_hash == ui_hash
    assert admin.env_secret_hash == seed_module.env_secret_fingerprint("first-password-123")
    assert fake_seed == []  # no session revoked
    assert await seed_module.seed_if_empty(_FakeSession()) == "unchanged"

    # A later .env change is applied as usual.
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "second-password-456")
    assert await seed_module.seed_if_empty(_FakeSession()) == "password_rotated"
    assert PasswordHasher.verify("second-password-456", admin.password_hash)
