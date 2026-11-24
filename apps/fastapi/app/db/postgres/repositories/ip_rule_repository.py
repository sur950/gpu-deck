"""
Repository for IP allow/block rules (ip_rules).
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import IpRules
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

SORTABLE = {
    "created_at",
    "updated_at",
    "action",
    "scope",
    "cidr",
    "enabled",
    "expires_at",
}


class IpRuleRepository(BaseRepository[IpRules]):
    """CRUD + per-scope snapshots for ip_rules."""

    def __init__(self, session: AsyncSession):
        super().__init__(IpRules, session)

    async def get_active(self, rule_id: uuid.UUID) -> Optional[IpRules]:
        result = await self.session.execute(
            select(IpRules).where(IpRules.id == rule_id, IpRules.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def find_duplicate(
        self, scope: str, action: str, cidr: str, exclude_id: Optional[uuid.UUID] = None
    ) -> Optional[IpRules]:
        query = select(IpRules).where(
            IpRules.scope == scope,
            IpRules.action == action,
            IpRules.cidr == cidr,
            IpRules.deleted_at.is_(None),
        )
        if exclude_id is not None:
            query = query.where(IpRules.id != exclude_id)
        return (await self.session.execute(query)).scalar_one_or_none()

    async def search(
        self,
        *,
        skip: int,
        limit: int,
        scope: Optional[str] = None,
        action: Optional[str] = None,
        enabled: Optional[bool] = None,
        sort: Optional[str] = None,
    ) -> Tuple[List[IpRules], int]:
        conds = [IpRules.deleted_at.is_(None)]
        if scope:
            conds.append(IpRules.scope == scope)
        if action:
            conds.append(IpRules.action == action)
        if enabled is not None:
            conds.append(IpRules.enabled.is_(enabled))
        total = (
            await self.session.execute(select(func.count()).select_from(IpRules).where(*conds))
        ).scalar_one()
        rows = await self.session.execute(
            select(IpRules).where(*conds).order_by(*_order(sort)).offset(skip).limit(limit)
        )
        return list(rows.scalars().all()), int(total)

    async def snapshot(self, scope: str) -> List[Dict[str, Any]]:
        """Enabled, unexpired, non-deleted rules of a scope as plain dicts (for Redis)."""
        now = datetime.now(timezone.utc)
        rows = await self.session.execute(
            select(IpRules)
            .where(
                IpRules.scope == scope,
                IpRules.enabled.is_(True),
                IpRules.deleted_at.is_(None),
                (IpRules.expires_at.is_(None)) | (IpRules.expires_at > now),
            )
            .order_by(IpRules.created_at)
        )
        return [self.to_dict(r) for r in rows.scalars().all()]

    async def soft_delete(self, rule_id: uuid.UUID) -> None:
        await self.session.execute(
            update(IpRules)
            .where(IpRules.id == rule_id)
            .values(deleted_at=datetime.now(timezone.utc), enabled=False)
        )
        await self.session.flush()

    @staticmethod
    def to_dict(row: IpRules) -> Dict[str, Any]:
        return {
            "id": str(row.id),
            "action": row.action,
            "scope": row.scope,
            "cidr": str(row.cidr),
            "description": row.description,
            "enabled": bool(row.enabled),
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


def _order(sort: Optional[str]):
    sort = sort or "-created_at"
    desc = sort.startswith("-")
    field = sort.lstrip("-+")
    if field not in SORTABLE:
        field, desc = "created_at", True
    column = getattr(IpRules, field)
    return [
        column.desc().nulls_last() if desc else column.asc().nulls_last(),
        IpRules.id,
    ]
