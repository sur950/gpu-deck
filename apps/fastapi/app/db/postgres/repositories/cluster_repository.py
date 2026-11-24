"""
Cluster repository: CRUD on ``clusters`` plus the per-cluster counts the
UI shows (nodes, online nodes, devices, instances, VRAM).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import Clusters, Devices, Instances, Nodes
from app.db.postgres.repositories.base_repository import BaseRepository
from app.db.postgres.repositories.node_repository import ONLINE_STATUSES
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

SORTABLE = {"name", "type", "status", "region", "created_at", "updated_at"}


class ClusterRepository(BaseRepository[Clusters]):
    def __init__(self, session: AsyncSession):
        super().__init__(Clusters, session)

    async def get_active(self, cluster_id: uuid.UUID) -> Optional[Clusters]:
        result = await self.session.execute(
            select(Clusters).where(Clusters.id == cluster_id, Clusters.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str, exclude_id: Optional[uuid.UUID] = None):
        query = select(Clusters).where(
            func.lower(Clusters.name) == name.lower(), Clusters.deleted_at.is_(None)
        )
        if exclude_id:
            query = query.where(Clusters.id != exclude_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list(
        self,
        page: int,
        page_size: int,
        search: Optional[str] = None,
        sort: Optional[str] = None,
        type_: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Tuple[List[Clusters], int]:
        query = select(Clusters).where(Clusters.deleted_at.is_(None))
        if search:
            query = query.where(Clusters.name.ilike(f"%{search}%"))
        if type_:
            query = query.where(Clusters.type == type_)
        if status:
            query = query.where(Clusters.status == status)
        total = (
            await self.session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        query = query.order_by(_sort_clause(sort)).offset((page - 1) * page_size).limit(page_size)
        rows = (await self.session.execute(query)).scalars().all()
        return list(rows), total

    async def list_all_active(self, cluster_id: Optional[uuid.UUID] = None) -> List[Clusters]:
        query = select(Clusters).where(Clusters.deleted_at.is_(None)).order_by(Clusters.name)
        if cluster_id:
            query = query.where(Clusters.id == cluster_id)
        return list((await self.session.execute(query)).scalars().all())

    async def soft_delete(self, cluster_id: uuid.UUID) -> None:
        await self.update(cluster_id, deleted_at=datetime.now(timezone.utc), status="disabled")

    async def count_nodes(self, cluster_id: uuid.UUID, include_removed: bool = False) -> int:
        query = select(func.count()).where(
            Nodes.cluster_id == cluster_id, Nodes.deleted_at.is_(None)
        )
        if not include_removed:
            query = query.where(Nodes.status != "removed")
        return (await self.session.execute(query)).scalar_one()

    async def counts_for(self, cluster_ids: List[uuid.UUID]) -> Dict[uuid.UUID, Dict[str, Any]]:
        """Counts per cluster in two queries (nodes/devices/vram, instances)."""
        if not cluster_ids:
            return {}
        node_q = (
            select(
                Nodes.cluster_id,
                func.count(func.distinct(Nodes.id)).label("nodes"),
                func.count(func.distinct(Nodes.id))
                # connected nodes: a drained node is still online (drain = no new work)
                .filter(Nodes.status.in_(ONLINE_STATUSES)).label("nodes_online"),
                func.count(Devices.id).label("devices"),
                func.coalesce(func.sum(Devices.vram_total_bytes), 0).label("vram_total"),
            )
            .outerjoin(Devices, (Devices.node_id == Nodes.id) & (Devices.deleted_at.is_(None)))
            .where(
                Nodes.cluster_id.in_(cluster_ids),
                Nodes.deleted_at.is_(None),
                Nodes.status != "removed",
            )
            .group_by(Nodes.cluster_id)
        )
        inst_q = (
            select(Nodes.cluster_id, func.count(Instances.id))
            .join(Nodes, Nodes.id == Instances.node_id)
            .where(Nodes.cluster_id.in_(cluster_ids), Instances.state != "stopped")
            .group_by(Nodes.cluster_id)
        )
        out: Dict[uuid.UUID, Dict[str, Any]] = {
            cid: {
                "nodes": 0,
                "nodes_online": 0,
                "devices": 0,
                "instances": 0,
                "vram_total_bytes": 0,
                "vram_used_bytes": 0,
            }
            for cid in cluster_ids
        }
        for row in (await self.session.execute(node_q)).all():
            out[row[0]].update(
                nodes=row[1],
                nodes_online=row[2],
                devices=row[3],
                vram_total_bytes=int(row[4]),
            )
        for row in (await self.session.execute(inst_q)).all():
            out[row[0]]["instances"] = row[1]
        return out

    async def vram_used_for(self, cluster_ids: List[uuid.UUID]) -> Dict[uuid.UUID, int]:
        """Sum of active slice reservations per cluster (static view of VRAM use)."""
        if not cluster_ids:
            return {}
        sql = text("""
            SELECT n.cluster_id, COALESCE(SUM(s.vram_bytes), 0)
            FROM device_slices s
            JOIN devices d ON d.id = s.device_id
            JOIN nodes n ON n.id = d.node_id
            WHERE s.status <> 'released' AND n.cluster_id = ANY(:ids)
            GROUP BY n.cluster_id
            """)
        rows = (await self.session.execute(sql, {"ids": list(cluster_ids)})).all()
        return {row[0]: int(row[1]) for row in rows}


def _sort_clause(sort: Optional[str]):
    field = (sort or "-created_at").strip()
    desc = field.startswith("-")
    field = field.lstrip("-")
    if field not in SORTABLE:
        field = "created_at"
        desc = True
    column = getattr(Clusters, field)
    return column.desc() if desc else column.asc()
