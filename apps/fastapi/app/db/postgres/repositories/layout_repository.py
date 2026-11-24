"""
Repositories for GPU layouts: ``layouts`` and ``layout_items``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import LayoutItems, Layouts
from app.db.postgres.repositories.base_repository import BaseRepository
from app.db.postgres.repositories.rule_repository import parse_sort
from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

_SORTABLE = {"name", "created_at", "updated_at", "applied_at", "is_active"}


class LayoutRepository(BaseRepository[Layouts]):
    """CRUD for layouts and their items."""

    def __init__(self, session: AsyncSession):
        super().__init__(Layouts, session)

    async def get_active(self, layout_id: uuid.UUID) -> Optional[Layouts]:
        result = await self.session.execute(
            select(Layouts).where(Layouts.id == layout_id, Layouts.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def name_exists(self, name: str, exclude_id: Optional[uuid.UUID] = None) -> bool:
        query = select(Layouts.id).where(
            func.lower(Layouts.name) == name.lower(), Layouts.deleted_at.is_(None)
        )
        if exclude_id is not None:
            query = query.where(Layouts.id != exclude_id)
        return (await self.session.execute(query.limit(1))).scalar_one_or_none() is not None

    async def list(
        self,
        page: int = 1,
        page_size: int = 50,
        search: Optional[str] = None,
        sort: Optional[str] = None,
    ) -> Tuple[List[Layouts], int]:
        conditions = [Layouts.deleted_at.is_(None)]
        if search:
            conditions.append(Layouts.name.ilike(f"%{search}%"))
        where = and_(*conditions)
        total = (
            await self.session.execute(select(func.count()).select_from(Layouts).where(where))
        ).scalar_one()
        field, desc = parse_sort(sort, _SORTABLE)
        column = getattr(Layouts, field)
        query = (
            select(Layouts)
            .where(where)
            .order_by(column.desc() if desc else column.asc(), Layouts.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list((await self.session.execute(query)).scalars().all()), total

    async def items_for(self, layout_id: uuid.UUID) -> List[LayoutItems]:
        query = (
            select(LayoutItems)
            .where(LayoutItems.layout_id == layout_id)
            .order_by(LayoutItems.position.asc())
        )
        return list((await self.session.execute(query)).scalars().all())

    async def items_for_many(
        self, layout_ids: List[uuid.UUID]
    ) -> Dict[uuid.UUID, List[LayoutItems]]:
        if not layout_ids:
            return {}
        query = (
            select(LayoutItems)
            .where(LayoutItems.layout_id.in_(layout_ids))
            .order_by(LayoutItems.layout_id, LayoutItems.position.asc())
        )
        out: Dict[uuid.UUID, List[LayoutItems]] = {lid: [] for lid in layout_ids}
        for item in (await self.session.execute(query)).scalars().all():
            out.setdefault(item.layout_id, []).append(item)
        return out

    async def replace_items(self, layout_id: uuid.UUID, items: List[Dict[str, Any]]) -> None:
        """Delete every item of the layout and insert the new list (positions must be unique)."""
        await self.session.execute(delete(LayoutItems).where(LayoutItems.layout_id == layout_id))
        await self.session.flush()
        for item in items:
            self.session.add(LayoutItems(id=uuid.uuid4(), layout_id=layout_id, **item))
        await self.session.flush()

    async def soft_delete(self, layout_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            update(Layouts)
            .where(Layouts.id == layout_id, Layouts.deleted_at.is_(None))
            .values(deleted_at=datetime.now(timezone.utc), is_active=False)
        )
        await self.session.flush()
        return result.rowcount > 0

    async def mark_applied(self, layout_id: uuid.UUID, at: datetime) -> None:
        """The applied layout becomes the single active one."""
        await self.session.execute(
            update(Layouts).where(Layouts.is_active.is_(True)).values(is_active=False)
        )
        await self.session.execute(
            update(Layouts).where(Layouts.id == layout_id).values(is_active=True, applied_at=at)
        )
        await self.session.flush()
