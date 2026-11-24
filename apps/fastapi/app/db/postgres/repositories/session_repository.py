"""
Repository for admin sessions (sessions table). Only the SHA-256 of the raw
cookie token is stored.
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import Sessions
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class SessionRepository(BaseRepository[Sessions]):
    """CRUD for sessions."""

    def __init__(self, session: AsyncSession):
        super().__init__(Sessions, session)

    async def get_active_by_hash(self, token_hash: str) -> Optional[Sessions]:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(Sessions).where(
                Sessions.token_hash == token_hash,
                Sessions.is_active.is_(True),
                Sessions.expires_at > now,
            )
        )
        return result.scalar_one_or_none()

    async def get_active_by_id(
        self, session_id: uuid.UUID, admin_id: uuid.UUID
    ) -> Optional[Sessions]:
        result = await self.session.execute(
            select(Sessions).where(
                Sessions.id == session_id,
                Sessions.admin_user_id == admin_id,
                Sessions.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def list_active(
        self, admin_id: uuid.UUID, skip: int = 0, limit: int = 50
    ) -> Tuple[List[Sessions], int]:
        now = datetime.now(timezone.utc)
        cond = and_(
            Sessions.admin_user_id == admin_id,
            Sessions.is_active.is_(True),
            Sessions.expires_at > now,
        )
        total = (
            await self.session.execute(select(func.count()).select_from(Sessions).where(cond))
        ).scalar_one()
        rows = await self.session.execute(
            select(Sessions)
            .where(cond)
            .order_by(Sessions.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(rows.scalars().all()), int(total)

    async def touch(self, session_id: uuid.UUID, at: Optional[datetime] = None) -> None:
        await self.session.execute(
            update(Sessions)
            .where(Sessions.id == session_id, Sessions.is_active.is_(True))
            .values(last_active_at=at or datetime.now(timezone.utc))
        )
        await self.session.flush()

    async def revoke(self, session_id: uuid.UUID, admin_id: Optional[uuid.UUID] = None) -> bool:
        cond = [Sessions.id == session_id, Sessions.is_active.is_(True)]
        if admin_id is not None:
            cond.append(Sessions.admin_user_id == admin_id)
        result = await self.session.execute(
            update(Sessions)
            .where(*cond)
            .values(is_active=False, revoked_at=datetime.now(timezone.utc))
        )
        await self.session.flush()
        return result.rowcount > 0

    async def revoke_all(
        self, admin_id: uuid.UUID, exclude_session_id: Optional[uuid.UUID] = None
    ) -> List[str]:
        """Revoke every active session of the admin; returns the token hashes revoked."""
        cond = [Sessions.admin_user_id == admin_id, Sessions.is_active.is_(True)]
        if exclude_session_id is not None:
            cond.append(Sessions.id != exclude_session_id)
        result = await self.session.execute(
            update(Sessions)
            .where(*cond)
            .values(is_active=False, revoked_at=datetime.now(timezone.utc))
            .returning(Sessions.token_hash)
        )
        await self.session.flush()
        return [row[0] for row in result.all()]

    async def purge_expired(self, older_than: datetime) -> int:
        """Delete sessions that expired or were revoked before ``older_than``."""
        result = await self.session.execute(
            delete(Sessions).where(
                (Sessions.expires_at < older_than)
                | (Sessions.is_active.is_(False) & (Sessions.revoked_at < older_than))
            )
        )
        await self.session.flush()
        return result.rowcount
