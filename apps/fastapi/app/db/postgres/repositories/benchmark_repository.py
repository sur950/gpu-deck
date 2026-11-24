"""
Repositories for ``benchmarks`` and ``benchmark_runs``. Flush only.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import (
    BenchmarkRuns,
    Benchmarks,
    Deployments,
)
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

BENCHMARK_SORT_FIELDS = {"name", "created_at", "updated_at"}
ACTIVE_RUN_STATES = ("pending", "running")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BenchmarkRepository(BaseRepository[Benchmarks]):
    """``benchmarks`` table."""

    def __init__(self, session: AsyncSession):
        super().__init__(Benchmarks, session)

    async def get_active(self, benchmark_id: uuid.UUID) -> Optional[Benchmarks]:
        result = await self.session.execute(
            select(Benchmarks).where(Benchmarks.id == benchmark_id, Benchmarks.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Optional[Benchmarks]:
        result = await self.session.execute(
            select(Benchmarks).where(
                func.lower(Benchmarks.name) == name.lower(),
                Benchmarks.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list_benchmarks(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        search: Optional[str] = None,
        sort: Optional[str] = None,
        deployment_id: Optional[uuid.UUID] = None,
    ) -> Tuple[List[Benchmarks], int]:
        conditions = [Benchmarks.deleted_at.is_(None)]
        if search:
            conditions.append(Benchmarks.name.ilike(f"%{search}%"))
        if deployment_id:
            conditions.append(Benchmarks.deployment_id == deployment_id)
        base = select(Benchmarks).where(and_(*conditions))
        total = (
            await self.session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        order = []
        for part in (sort or "-created_at").split(","):
            name = part.strip().lstrip("-")
            if name in BENCHMARK_SORT_FIELDS:
                col = getattr(Benchmarks, name)
                order.append(col.desc() if part.strip().startswith("-") else col.asc())
        order.append(Benchmarks.id)
        rows = (
            await self.session.execute(
                base.order_by(*order).offset((page - 1) * page_size).limit(page_size)
            )
        ).scalars()
        return list(rows), int(total)

    async def soft_delete(self, benchmark_id: uuid.UUID) -> None:
        await self.session.execute(
            delete(BenchmarkRuns).where(BenchmarkRuns.benchmark_id == benchmark_id)
        )
        await self.session.execute(
            update(Benchmarks).where(Benchmarks.id == benchmark_id).values(deleted_at=_now())
        )
        await self.session.flush()

    async def deployment_names(self, deployment_ids: Sequence[uuid.UUID]) -> Dict[uuid.UUID, str]:
        if not deployment_ids:
            return {}
        result = await self.session.execute(
            select(Deployments.id, Deployments.name).where(
                Deployments.id.in_(list(set(deployment_ids)))
            )
        )
        return {row[0]: row[1] for row in result.all()}


class BenchmarkRunRepository(BaseRepository[BenchmarkRuns]):
    """``benchmark_runs`` table."""

    def __init__(self, session: AsyncSession):
        super().__init__(BenchmarkRuns, session)

    async def list_for_benchmark(
        self, benchmark_id: uuid.UUID, page: int = 1, page_size: int = 50
    ) -> Tuple[List[BenchmarkRuns], int]:
        base = select(BenchmarkRuns).where(BenchmarkRuns.benchmark_id == benchmark_id)
        total = (
            await self.session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        rows = (
            await self.session.execute(
                base.order_by(BenchmarkRuns.created_at.desc(), BenchmarkRuns.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
        return list(rows), int(total)

    async def counts_and_last(
        self, benchmark_ids: Sequence[uuid.UUID]
    ) -> Tuple[Dict[uuid.UUID, int], Dict[uuid.UUID, BenchmarkRuns]]:
        if not benchmark_ids:
            return {}, {}
        counts = {
            row[0]: int(row[1])
            for row in (
                await self.session.execute(
                    select(BenchmarkRuns.benchmark_id, func.count())
                    .where(BenchmarkRuns.benchmark_id.in_(list(benchmark_ids)))
                    .group_by(BenchmarkRuns.benchmark_id)
                )
            ).all()
        }
        last: Dict[uuid.UUID, BenchmarkRuns] = {}
        newest = (
            select(
                BenchmarkRuns.benchmark_id.label("bid"),
                func.max(BenchmarkRuns.created_at).label("newest"),
            )
            .where(BenchmarkRuns.benchmark_id.in_(list(benchmark_ids)))
            .group_by(BenchmarkRuns.benchmark_id)
            .subquery()
        )
        rows = (
            await self.session.execute(
                select(BenchmarkRuns)
                .join(
                    newest,
                    and_(
                        BenchmarkRuns.benchmark_id == newest.c.bid,
                        BenchmarkRuns.created_at == newest.c.newest,
                    ),
                )
                .order_by(BenchmarkRuns.id)
            )
        ).scalars()
        for r in rows:
            last[r.benchmark_id] = r
        return counts, last

    async def active_for_benchmark(self, benchmark_id: uuid.UUID) -> Optional[BenchmarkRuns]:
        result = await self.session.execute(
            select(BenchmarkRuns)
            .where(
                BenchmarkRuns.benchmark_id == benchmark_id,
                BenchmarkRuns.status.in_(ACTIVE_RUN_STATES),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def active_for_deployment(self, deployment_id: uuid.UUID) -> Optional[BenchmarkRuns]:
        result = await self.session.execute(
            select(BenchmarkRuns)
            .join(Benchmarks, Benchmarks.id == BenchmarkRuns.benchmark_id)
            .where(
                Benchmarks.deployment_id == deployment_id,
                BenchmarkRuns.status.in_(ACTIVE_RUN_STATES),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def set_fields(self, run_id: uuid.UUID, **values: Any) -> None:
        values["updated_at"] = _now()
        await self.session.execute(
            update(BenchmarkRuns).where(BenchmarkRuns.id == run_id).values(**values)
        )
        await self.session.flush()

    async def status_of(self, run_id: uuid.UUID) -> Optional[str]:
        result = await self.session.execute(
            select(BenchmarkRuns.status).where(BenchmarkRuns.id == run_id)
        )
        return result.scalar_one_or_none()

    async def stale_running(self, before: datetime) -> List[BenchmarkRuns]:
        result = await self.session.execute(
            select(BenchmarkRuns).where(
                BenchmarkRuns.status.in_(ACTIVE_RUN_STATES),
                BenchmarkRuns.updated_at < before,
            )
        )
        return list(result.scalars().all())
