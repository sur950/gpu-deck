"""
AuthService: login with lockout, logout, current admin, change password,
session listing/revocation. Route layer stays HTTP-only.

Lockout: Redis counter ``login_failures:{username}`` (TTL AUTH_LOCKOUT_MINUTES)
is the hot path; ``admin_users.failed_login_attempts`` / ``locked_until`` is the
durable mirror, committed explicitly before the 401 is raised (get_db rolls
back on 4xx).
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.exceptions import (
    AccountLockedError,
    AuthorizationError,
    InvalidCredentialsError,
    ResourceNotFoundError,
    ValidationError,
)
from app.core.logger import get_logger
from app.core.request_context import get_client_ip_from_request
from app.core.settings import settings
from app.db.postgres.connection import get_db
from app.db.postgres.models.generated_models import AdminUsers
from app.db.postgres.repositories.admin_user_repository import AdminUserRepository
from app.db.redis.cache_service import CacheService
from app.services.audit import audit
from app.services.auth.csrf import generate_csrf_token
from app.services.auth.session_manager import SessionManager
from app.utils.password import PasswordHasher
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

LOGIN_FAILURES_KEY = "login_failures:{username}"


def _locked_error(minutes_left: int) -> AccountLockedError:
    # admin.yaml: "Lockout ... (401 ACCOUNT_LOCKED)"; core's AccountLockedError is 403.
    exc = AccountLockedError(f"Account is locked. Try again in {max(minutes_left, 1)} minutes.")
    exc.status_code = 401
    return exc


class AuthService:
    """Login/logout/session business logic for the single admin."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.admin_repo = AdminUserRepository(db)
        self.sessions = SessionManager(db)

    @staticmethod
    async def as_dependency(request: Request, db: AsyncSession = Depends(get_db)) -> "AuthService":
        return AuthService(db)

    # ---------- lockout counters ----------

    @staticmethod
    async def _cache() -> Optional[CacheService]:
        try:
            return await CacheService.get_instance()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Lockout cache unavailable: {exc}")
            return None

    @staticmethod
    def _failures_key(username: str) -> str:
        return LOGIN_FAILURES_KEY.format(username=username.lower())

    async def _failure_count(self, username: str) -> int:
        cache = await self._cache()
        if cache is None:
            return 0
        value = await cache.get(self._failures_key(username))
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    async def _record_failure(self, admin: Optional[AdminUsers], username: str) -> int:
        """Increment Redis + DB counters; returns the current count."""
        ttl = int(settings.AUTH_LOCKOUT_MINUTES) * 60
        cache = await self._cache()
        count = await cache.incr(self._failures_key(username), ttl=ttl) if cache else 0
        if admin is not None:
            lock_until = None
            db_count = int(admin.failed_login_attempts or 0) + 1
            if max(count, db_count) >= int(settings.AUTH_LOCKOUT_ATTEMPTS):
                lock_until = datetime.now(timezone.utc) + timedelta(
                    minutes=int(settings.AUTH_LOCKOUT_MINUTES)
                )
            db_count = await self.admin_repo.record_login_failure(admin.id, lock_until)
            await self.db.commit()  # survive the 401 rollback
            count = max(count, db_count)
        return count

    async def _clear_failures(self, admin: AdminUsers, username: str) -> None:
        cache = await self._cache()
        if cache is not None:
            await cache.delete(self._failures_key(username))

    async def _check_locked(self, admin: Optional[AdminUsers], username: str) -> None:
        attempts = int(settings.AUTH_LOCKOUT_ATTEMPTS)
        if attempts <= 0:
            return
        now = datetime.now(timezone.utc)
        if admin is not None and admin.locked_until and admin.locked_until > now:
            raise _locked_error(int((admin.locked_until - now).total_seconds() // 60) + 1)
        if await self._failure_count(username) >= attempts:
            raise _locked_error(int(settings.AUTH_LOCKOUT_MINUTES))

    # ---------- login / logout ----------

    async def login(self, request: Request, username: str, password: str) -> Dict[str, Any]:
        ip = get_client_ip_from_request(request) or None
        user_agent = request.headers.get("user-agent")
        username = (username or "").strip()
        admin = await self.admin_repo.get_by_username(username) if username else None

        await self._check_locked(admin, username)

        if admin is None or not PasswordHasher.verify(password, admin.password_hash):
            if admin is None:
                PasswordHasher.dummy_verify(password)  # same timing as a wrong password
            count = await self._record_failure(admin, username)
            audit.record(
                "auth.login",
                actor_type="admin",
                actor_id=admin.id if admin else None,
                actor_name=username[:255],
                outcome="failure",
                message="Invalid credentials",
                metadata={"failed_attempts": count},
                ip_address=ip,
                user_agent=user_agent,
            )
            if count >= int(settings.AUTH_LOCKOUT_ATTEMPTS) > 0:
                audit.record(
                    "auth.locked_out",
                    actor_type="admin",
                    actor_id=admin.id if admin else None,
                    actor_name=username[:255],
                    outcome="failure",
                    message=f"Locked for {settings.AUTH_LOCKOUT_MINUTES} minutes",
                    ip_address=ip,
                    user_agent=user_agent,
                )
                raise _locked_error(int(settings.AUTH_LOCKOUT_MINUTES))
            raise InvalidCredentialsError("Invalid username or password")

        if not admin.is_active:
            raise AuthorizationError("Account is disabled")

        if PasswordHasher.needs_update(admin.password_hash):
            await self.admin_repo.set_password(admin.id, PasswordHasher.hash(password))

        result = await self.sessions.create_session(admin.id, ip, user_agent)
        await self.admin_repo.record_login_success(admin.id, ip)
        await self._clear_failures(admin, username)

        audit.record(
            "auth.login",
            actor_type="admin",
            actor_id=admin.id,
            actor_name=admin.username,
            resource_type="session",
            resource_id=result["session_id"],
            ip_address=ip,
            user_agent=user_agent,
        )
        logger.info(f"Admin login: id={admin.id} session={result['session_id']}")
        result["csrf_token"] = generate_csrf_token(result["token_hash"])
        result["admin"] = admin
        result["must_change_password"] = self.must_change_password(admin)
        return result

    @staticmethod
    def must_change_password(admin: AdminUsers) -> bool:
        """True while the seed password is in use and AUTH_REQUIRE_PASSWORD_CHANGE is on."""
        if not settings.AUTH_REQUIRE_PASSWORD_CHANGE:
            return False
        return admin.password_changed_at <= admin.env_secret_applied_at

    async def logout(self, admin: AdminUsers, session: Dict[str, Any]) -> None:
        session_id = uuid.UUID(session["session_id"])
        await self.sessions.revoke_session(session_id, admin.id)
        audit.record(
            "auth.logout",
            actor_type="admin",
            actor_id=admin.id,
            actor_name=admin.username,
            resource_type="session",
            resource_id=session_id,
        )

    async def logout_all(self, admin: AdminUsers, session: Dict[str, Any]) -> int:
        current = uuid.UUID(session["session_id"])
        count = await self.sessions.revoke_all_sessions(admin.id, exclude_session_id=current)
        audit.record(
            "auth.logout_all",
            actor_type="admin",
            actor_id=admin.id,
            actor_name=admin.username,
            metadata={"revoked": count},
        )
        return count

    # ---------- password ----------

    async def change_password(
        self,
        admin: AdminUsers,
        session: Dict[str, Any],
        current_password: str,
        new_password: str,
    ) -> None:
        min_len = int(settings.AUTH_PASSWORD_MIN_LENGTH)
        if len(new_password or "") < min_len:
            raise ValidationError(f"New password must be at least {min_len} characters")
        if not PasswordHasher.verify(current_password, admin.password_hash):
            audit.record(
                "auth.password_changed",
                actor_type="admin",
                actor_id=admin.id,
                actor_name=admin.username,
                outcome="failure",
                message="Current password incorrect",
            )
            raise InvalidCredentialsError("Current password is incorrect")
        if current_password == new_password:
            raise ValidationError("New password must differ from the current password")

        await self.admin_repo.set_password(admin.id, PasswordHasher.hash(new_password))
        revoked = await self.sessions.revoke_all_sessions(
            admin.id, exclude_session_id=uuid.UUID(session["session_id"])
        )
        audit.record(
            "auth.password_changed",
            actor_type="admin",
            actor_id=admin.id,
            actor_name=admin.username,
            metadata={"other_sessions_revoked": revoked},
        )
        logger.info(f"Admin password changed: id={admin.id} revoked_sessions={revoked}")

    # ---------- sessions ----------

    async def list_sessions(
        self, admin: AdminUsers, session: Dict[str, Any], page: int, page_size: int
    ) -> Tuple[List[Dict[str, Any]], int]:
        rows, total = await self.sessions.list_sessions(admin.id, page, page_size)
        current = session.get("session_id")
        items = [
            {
                "id": row.id,
                "ip_address": str(row.ip_address) if row.ip_address else None,
                "user_agent": row.user_agent,
                "created_at": row.created_at,
                "last_active_at": row.last_active_at,
                "expires_at": row.expires_at,
                "is_current": str(row.id) == current,
            }
            for row in rows
        ]
        return items, total

    async def revoke_session(self, admin: AdminUsers, session_id: uuid.UUID) -> None:
        if not await self.sessions.revoke_session(session_id, admin.id):
            raise ResourceNotFoundError(resource_type="Session")
        audit.record(
            "auth.session_revoked",
            actor_type="admin",
            actor_id=admin.id,
            actor_name=admin.username,
            resource_type="session",
            resource_id=session_id,
        )

    # ---------- current admin ----------

    @staticmethod
    def admin_to_dict(admin: AdminUsers) -> Dict[str, Any]:
        return {
            "id": admin.id,
            "username": admin.username,
            "display_name": admin.display_name,
            "password_changed_at": admin.password_changed_at,
            "last_login_at": admin.last_login_at,
            "last_login_ip": str(admin.last_login_ip) if admin.last_login_ip else None,
            "created_at": admin.created_at,
            "updated_at": admin.updated_at,
        }
