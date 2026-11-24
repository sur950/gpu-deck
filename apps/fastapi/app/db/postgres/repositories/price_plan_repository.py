"""
Price plan repository (``price_plans``; attachments live on ``clusters`` /
``nodes.price_plan_id``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import PricePlans
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

SORTABLE = {
    "name",
    "created_at",
    "updated_at",
    "usd_per_gpu_minute",
    "usd_per_1k_input_tokens",
    "usd_per_1k_output_tokens",
    "usd_per_1k_cached_tokens",
}


class PricePlanRepository(BaseRepository[PricePlans]):
    def __init__(self, session: AsyncSession):
        super().__init__(PricePlans, session)

    def _active(self):
        return select(PricePlans).where(PricePlans.deleted_at.is_(None))

    async def get_active(self, plan_id: uuid.UUID) -> Optional[PricePlans]:
        result = await self.session.execute(self._active().where(PricePlans.id == plan_id))
        return result.scalar_one_or_none()

    async def list_plans(
        self, page: int, page_size: int, search: Optional[str], sort: Optional[str]
    ) -> Tuple[List[PricePlans], int]:
        query = self._active()
        count = select(func.count()).select_from(PricePlans).where(PricePlans.deleted_at.is_(None))
        if search:
            like = f"%{search.lower()}%"
            query = query.where(func.lower(PricePlans.name).like(like))
            count = count.where(func.lower(PricePlans.name).like(like))
        field, desc = (sort or "name"), False
        if field.startswith("-"):
            field, desc = field[1:], True
        if field not in SORTABLE:
            field, desc = "name", False
        column = getattr(PricePlans, field)
        query = query.order_by(column.desc() if desc else column.asc(), PricePlans.id.asc())
        total = (await self.session.execute(count)).scalar_one()
        result = await self.session.execute(query.offset((page - 1) * page_size).limit(page_size))
        return list(result.scalars().all()), int(total)

    async def name_taken(self, name: str, exclude_id: Optional[uuid.UUID] = None) -> bool:
        query = self._active().where(func.lower(PricePlans.name) == name.lower())
        if exclude_id is not None:
            query = query.where(PricePlans.id != exclude_id)
        result = await self.session.execute(query.limit(1))
        return result.scalar_one_or_none() is not None

    async def clear_default(self, except_id: Optional[uuid.UUID] = None) -> None:
        stmt = update(PricePlans).where(PricePlans.is_default.is_(True))
        if except_id is not None:
            stmt = stmt.where(PricePlans.id != except_id)
        await self.session.execute(stmt.values(is_default=False))
        await self.session.flush()

    async def soft_delete(self, plan_id: uuid.UUID) -> Dict[str, int]:
        """Soft-delete and detach from clusters and nodes; returns detach counts."""
        await self.session.execute(
            update(PricePlans)
            .where(PricePlans.id == plan_id)
            .values(deleted_at=datetime.now(timezone.utc), is_default=False)
        )
        clusters = await self.session.execute(
            text("UPDATE clusters SET price_plan_id = NULL WHERE price_plan_id = :id"),
            {"id": plan_id},
        )
        nodes = await self.session.execute(
            text("UPDATE nodes SET price_plan_id = NULL WHERE price_plan_id = :id"),
            {"id": plan_id},
        )
        await self.session.flush()
        return {
            "clusters": int(clusters.rowcount or 0),
            "nodes": int(nodes.rowcount or 0),
        }

    async def pricing_snapshot(self) -> Dict[str, Any]:
        """Everything the plan resolver needs: active plans, node and cluster attachments."""
        plans = await self.session.execute(
            text(
                "SELECT id, name, currency, usd_per_gpu_minute, usd_per_1k_input_tokens, "
                "usd_per_1k_output_tokens, usd_per_1k_cached_tokens, is_default "
                "FROM price_plans WHERE deleted_at IS NULL"
            )
        )
        # Deleted nodes are kept: historical usage still resolves to their plan.
        nodes = await self.session.execute(text("SELECT id, cluster_id, price_plan_id FROM nodes"))
        clusters = await self.session.execute(text("SELECT id, price_plan_id FROM clusters"))
        return {
            "plans": [dict(r) for r in plans.mappings().all()],
            "nodes": [dict(r) for r in nodes.mappings().all()],
            "clusters": [dict(r) for r in clusters.mappings().all()],
        }
