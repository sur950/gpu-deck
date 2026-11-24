"""
Billing report repository (``billing_reports``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import BillingReports
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

SORTABLE = {
    "name",
    "created_at",
    "period_start",
    "period_end",
    "status",
    "generated_at",
}


class BillingReportRepository(BaseRepository[BillingReports]):
    def __init__(self, session: AsyncSession):
        super().__init__(BillingReports, session)

    def _active(self):
        return select(BillingReports).where(BillingReports.deleted_at.is_(None))

    async def get_active(self, report_id: uuid.UUID) -> Optional[BillingReports]:
        result = await self.session.execute(self._active().where(BillingReports.id == report_id))
        return result.scalar_one_or_none()

    async def list_reports(
        self,
        page: int,
        page_size: int,
        search: Optional[str] = None,
        sort: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Tuple[List[BillingReports], int]:
        conditions = [BillingReports.deleted_at.is_(None)]
        if search:
            conditions.append(func.lower(BillingReports.name).like(f"%{search.lower()}%"))
        if status:
            conditions.append(BillingReports.status == status)
        field, desc = (sort or "-created_at"), False
        if field.startswith("-"):
            field, desc = field[1:], True
        if field not in SORTABLE:
            field, desc = "created_at", True
        column = getattr(BillingReports, field)
        total = (
            await self.session.execute(
                select(func.count()).select_from(BillingReports).where(*conditions)
            )
        ).scalar_one()
        result = await self.session.execute(
            select(BillingReports)
            .where(*conditions)
            .order_by(column.desc() if desc else column.asc(), BillingReports.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), int(total)

    async def find_period(
        self, name: str, period_start: datetime, period_end: datetime
    ) -> Optional[BillingReports]:
        result = await self.session.execute(
            self._active().where(
                BillingReports.name == name,
                BillingReports.period_start == period_start,
                BillingReports.period_end == period_end,
            )
        )
        return result.scalars().first()

    async def soft_delete(self, report_id: uuid.UUID) -> None:
        await self.session.execute(
            update(BillingReports)
            .where(BillingReports.id == report_id)
            .values(deleted_at=datetime.now(timezone.utc))
        )
        await self.session.flush()
