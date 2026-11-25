"""
Seed / rotate the single admin from .env on every boot.

Runs from the admin API lifespan and from ``bootstrap.dev.sh seed``.
Rotation rules:

1. No admin row: insert one from ADMIN_USERNAME / ADMIN_PASSWORD / ADMIN_NAME
   with an Argon2id hash.
2. ``env_secret_hash`` = ``scrypt:`` + scrypt(ADMIN_PASSWORD, salt derived from
   SECRET_KEY) — a memory-hard, peppered fingerprint, so neither the database
   alone nor the database plus SECRET_KEY gives a fast offline guess of the
   password (the login hash itself is Argon2id in ``password_hash``).
3. On every boot the username (and display name) are synced; the password is
   re-applied ONLY when the fingerprint differs from the stored one. A password
   set in the UI therefore survives restarts until ADMIN_PASSWORD changes in
   .env, at which point the new .env value wins and every session is revoked.
4. Upgrade from 1.0.x (a 64-hex HMAC-SHA256 fingerprint, no ``scrypt:`` prefix):
   the old scheme is no longer computed. The row's fingerprint is rewritten to
   the scrypt form of the current ADMIN_PASSWORD without touching the password
   or sessions, so a UI-set password is never reverted. If .env no longer
   matches the current login password a warning says so; change ADMIN_PASSWORD
   again (or use the UI) to apply a new one.
"""

import hashlib
import hmac
import uuid
from datetime import datetime, timezone

from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.repositories.admin_user_repository import AdminUserRepository
from app.utils.password import PasswordHasher
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


FINGERPRINT_PREFIX = "scrypt:"
# scrypt cost: 16 MiB, ~50 ms, once per boot. 28-byte digest -> 7 + 56 = 63 chars
# (admin_users.env_secret_hash is VARCHAR(64)).
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 28}


def _fingerprint_salt(secret_key: str) -> bytes:
    """Per-deployment salt (pepper): HMAC-SHA256(SECRET_KEY, fixed label)."""
    return hmac.new(secret_key.encode(), b"gpu-deck:admin-env-secret:v2", hashlib.sha256).digest()


def env_secret_fingerprint(password: str | None = None, secret_key: str | None = None) -> str:
    """``scrypt:<56 hex>`` of ADMIN_PASSWORD, salted from SECRET_KEY (fits env_secret_hash)."""
    secret = (password if password is not None else settings.ADMIN_PASSWORD).encode()
    salt = _fingerprint_salt(secret_key or settings.SECRET_KEY)
    return FINGERPRINT_PREFIX + hashlib.scrypt(secret, salt=salt, **_SCRYPT).hex()


def is_legacy_fingerprint(stored: str | None) -> bool:
    """A 1.0.x HMAC fingerprint (64 hex, no ``scrypt:`` prefix)."""
    return bool(stored) and not str(stored).startswith(FINGERPRINT_PREFIX)


async def seed_if_empty(session: AsyncSession) -> str:
    """
    Ensure the admin row matches .env. Returns what happened:
    ``"created"``, ``"password_rotated"``, ``"synced"``, ``"fingerprint_upgraded"`` or
    ``"unchanged"``.
    Commits on its own (it runs outside any request).
    """
    repo = AdminUserRepository(session)
    fingerprint = env_secret_fingerprint()
    username = settings.ADMIN_USERNAME.strip()
    display_name = settings.ADMIN_NAME or None

    admin = await repo.get_singleton()
    if admin is None:
        now = datetime.now(timezone.utc)
        try:
            await repo.create(
                id=uuid.uuid4(),
                username=username,
                display_name=display_name,
                password_hash=PasswordHasher.hash(settings.ADMIN_PASSWORD),
                password_changed_at=now,
                env_secret_hash=fingerprint,
                env_secret_applied_at=now,
                is_active=True,
            )
            await session.commit()
        except IntegrityError:
            # Another process seeded first (multi-worker boot); fall through to sync.
            await session.rollback()
            admin = await repo.get_singleton()
            if admin is None:
                raise
        else:
            logger.info(f"Admin seeded from .env: username={username}")
            _audit("auth.admin_seeded", username)
            return "created"

    if is_legacy_fingerprint(admin.env_secret_hash):
        return await _upgrade_legacy_fingerprint(session, repo, admin, fingerprint)

    rotate = not hmac.compare_digest(admin.env_secret_hash or "", fingerprint)
    sync_only = admin.username != username or (admin.display_name or None) != display_name

    if rotate:
        await repo.apply_env_seed(
            admin.id,
            username=username,
            display_name=display_name,
            password_hash=PasswordHasher.hash(settings.ADMIN_PASSWORD),
            env_secret_hash=fingerprint,
        )
        revoked = await _revoke_sessions(session, admin.id)
        await session.commit()
        logger.warning(
            f"ADMIN_PASSWORD changed in .env: password re-applied, {revoked} session(s) revoked"
        )
        _audit("auth.admin_password_rotated", username, {"sessions_revoked": revoked})
        return "password_rotated"

    if sync_only:
        await repo.apply_env_seed(
            admin.id,
            username=username,
            display_name=display_name,
            password_hash=None,
            env_secret_hash=None,
        )
        await session.commit()
        logger.info(f"Admin profile synced from .env: username={username}")
        return "synced"

    logger.info(f"Admin seed unchanged (username={username})")
    return "unchanged"


async def _upgrade_legacy_fingerprint(session: AsyncSession, repo, admin, fingerprint: str) -> str:
    """Rule 4: rewrite a 1.0.x fingerprint; never changes the password or sessions."""
    await repo.set_env_secret_hash(admin.id, fingerprint)
    await session.commit()
    if not PasswordHasher.verify(settings.ADMIN_PASSWORD, admin.password_hash):
        logger.warning(
            "Admin seed fingerprint upgraded; ADMIN_PASSWORD in .env differs from the current "
            "admin password, which was kept. Change ADMIN_PASSWORD again (or use the UI) to "
            "apply a new password."
        )
    else:
        logger.info("Admin seed fingerprint upgraded to scrypt")
    return "fingerprint_upgraded"


async def _revoke_sessions(session: AsyncSession, admin_id: uuid.UUID) -> int:
    from app.services.auth.session_manager import SessionManager

    try:
        return await SessionManager(session).revoke_all_sessions(admin_id)
    except Exception as exc:  # noqa: BLE001 - Redis down at boot must not block the seed
        logger.warning(f"Could not revoke sessions after password rotation: {exc}")
        return 0


def _audit(event: str, username: str, metadata: dict | None = None) -> None:
    try:
        from app.services.audit import audit

        audit.record(
            event,
            actor_type="system",
            resource_type="admin",
            resource_name=username,
            metadata=metadata or {},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"seed audit skipped: {exc}")
