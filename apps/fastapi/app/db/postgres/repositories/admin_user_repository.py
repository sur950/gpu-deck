"""
Repository for the single seeded admin (admin_users).
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import AdminUsers
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class AdminUserRepository(BaseRepository[AdminUsers]):
    """CRUD for admin_users (exactly one row)."""

    def __init__(self, session: AsyncSession):
        super().__init__(AdminUsers, session)

    async def get_singleton(self) -> Optional[AdminUsers]:
        result = await self.session.execute(select(AdminUsers).limit(1))
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> Optional[AdminUsers]:
        result = await self.session.execute(
            select(AdminUsers).where(func.lower(AdminUsers.username) == username.lower())
        )
        return result.scalar_one_or_none()

    async def record_login_success(self, admin_id: uuid.UUID, ip: Optional[str]) -> None:
        now = datetime.now(timezone.utc)
        await self.session.execute(
            update(AdminUsers)
            .where(AdminUsers.id == admin_id)
            .values(
                last_login_at=now,
                last_login_ip=ip or None,
                failed_login_attempts=0,
                locked_until=None,
            )
        )
        await self.session.flush()

    async def record_login_failure(
        self, admin_id: uuid.UUID, lock_until: Optional[datetime] = None
    ) -> int:
        """Increment the durable failure counter; returns the new count."""
        values: dict[str, Any] = {"failed_login_attempts": AdminUsers.failed_login_attempts + 1}
        if lock_until is not None:
            values["locked_until"] = lock_until
        result = await self.session.execute(
            update(AdminUsers)
            .where(AdminUsers.id == admin_id)
            .values(**values)
            .returning(AdminUsers.failed_login_attempts)
        )
        await self.session.flush()
        return int(result.scalar_one())

    async def clear_lockout(self, admin_id: uuid.UUID) -> None:
        await self.session.execute(
            update(AdminUsers)
            .where(AdminUsers.id == admin_id)
            .values(failed_login_attempts=0, locked_until=None)
        )
        await self.session.flush()

    async def set_password(self, admin_id: uuid.UUID, password_hash: str) -> None:
        """UI-set password: password_changed_at moves past env_secret_applied_at."""
        await self.session.execute(
            update(AdminUsers)
            .where(AdminUsers.id == admin_id)
            .values(
                password_hash=password_hash,
                password_changed_at=datetime.now(timezone.utc),
            )
        )
        await self.session.flush()

    async def set_env_secret_hash(self, admin_id: uuid.UUID, env_secret_hash: str) -> None:
        """Rewrite only the .env fingerprint (scheme upgrade; password untouched)."""
        await self.session.execute(
            update(AdminUsers)
            .where(AdminUsers.id == admin_id)
            .values(env_secret_hash=env_secret_hash)
        )
        await self.session.flush()

    async def apply_env_seed(
        self,
        admin_id: uuid.UUID,
        *,
        username: str,
        display_name: Optional[str],
        password_hash: Optional[str],
        env_secret_hash: Optional[str],
    ) -> None:
        """Sync username/display name; re-apply the password only when a hash is given."""
        values: dict[str, Any] = {"username": username, "display_name": display_name}
        if password_hash is not None and env_secret_hash is not None:
            now = datetime.now(timezone.utc)
            values.update(
                password_hash=password_hash,
                env_secret_hash=env_secret_hash,
                env_secret_applied_at=now,
                password_changed_at=now,
                failed_login_attempts=0,
                locked_until=None,
            )
        await self.session.execute(
            update(AdminUsers).where(AdminUsers.id == admin_id).values(**values)
        )
        await self.session.flush()
