"""
Seed / rotate the single admin from .env on every boot.

Runs from the admin API lifespan and from ``bootstrap.dev.sh seed``.
Rotation rules:

1. No admin row: insert one from ADMIN_USERNAME / ADMIN_PASSWORD / ADMIN_NAME
   with an Argon2id hash.
2. ``env_secret_hash`` = HMAC-SHA256(SECRET_KEY, ADMIN_PASSWORD) — not a bare
   sha256, so the database never holds an unsalted hash of the password.
3. On every boot the username (and display name) are synced; the password is
   re-applied ONLY when the HMAC differs from the stored one. A password set
   in the UI therefore survives restarts until ADMIN_PASSWORD changes in .env,
   at which point the new .env value wins and every session is revoked.
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


def env_secret_fingerprint(password: str | None = None, secret_key: str | None = None) -> str:
    """HMAC-SHA256(SECRET_KEY, ADMIN_PASSWORD) hex (64 chars, fits env_secret_hash)."""
    return hmac.new(
        (secret_key or settings.SECRET_KEY).encode(),
        (password if password is not None else settings.ADMIN_PASSWORD).encode(),
        hashlib.sha256,
    ).hexdigest()


async def seed_if_empty(session: AsyncSession) -> str:
    """
    Ensure the admin row matches .env. Returns what happened:
    ``"created"``, ``"password_rotated"``, ``"synced"`` or ``"unchanged"``.
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
