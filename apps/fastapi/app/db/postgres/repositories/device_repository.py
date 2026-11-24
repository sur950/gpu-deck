"""
Device and device-slice repositories: accelerator inventory reads used by
the devices, nodes and topology endpoints.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import (
    Devices,
    DeviceSlices,
    Instances,
    Nodes,
)
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

DEVICE_SORTABLE = {
    "name",
    "vendor",
    "index",
    "health",
    "vram_total_bytes",
    "created_at",
}


class DeviceRepository(BaseRepository[Devices]):
    def __init__(self, session: AsyncSession):
        super().__init__(Devices, session)

    async def get_active(self, device_id: uuid.UUID) -> Optional[Devices]:
        result = await self.session.execute(
            select(Devices).where(Devices.id == device_id, Devices.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def list_for_node(self, node_id: uuid.UUID) -> List[Devices]:
        result = await self.session.execute(
            select(Devices)
            .where(Devices.node_id == node_id, Devices.deleted_at.is_(None))
            .order_by(Devices.index)
        )
        return list(result.scalars().all())

    async def list_for_nodes(self, node_ids: List[uuid.UUID]) -> List[Devices]:
        if not node_ids:
            return []
        result = await self.session.execute(
            select(Devices)
            .where(Devices.node_id.in_(node_ids), Devices.deleted_at.is_(None))
            .order_by(Devices.node_id, Devices.index)
        )
        return list(result.scalars().all())

    async def list(
        self,
        page: int,
        page_size: int,
        sort: Optional[str] = None,
        cluster_id: Optional[uuid.UUID] = None,
        node_id: Optional[uuid.UUID] = None,
        vendor: Optional[str] = None,
        health: Optional[str] = None,
        free_vram_min_bytes: Optional[int] = None,
    ) -> Tuple[List[Tuple[Devices, Nodes]], int]:
        query = (
            select(Devices, Nodes)
            .join(Nodes, Nodes.id == Devices.node_id)
            .where(Devices.deleted_at.is_(None), Nodes.deleted_at.is_(None))
        )
        if cluster_id:
            query = query.where(Nodes.cluster_id == cluster_id)
        if node_id:
            query = query.where(Devices.node_id == node_id)
        if vendor:
            query = query.where(Devices.vendor == vendor)
        if health:
            query = query.where(Devices.health == health)
        if free_vram_min_bytes is not None:
            reserved = (
                select(func.coalesce(func.sum(DeviceSlices.vram_bytes), 0))
                .where(
                    DeviceSlices.device_id == Devices.id,
                    DeviceSlices.status != "released",
                )
                .scalar_subquery()
            )
            query = query.where(Devices.vram_total_bytes - reserved >= free_vram_min_bytes)
        total = (
            await self.session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        field = (sort or "-created_at").strip()
        desc = field.startswith("-")
        field = field.lstrip("-")
        if field not in DEVICE_SORTABLE:
            field, desc = "created_at", True
        column = getattr(Devices, field)
        query = query.order_by(column.desc() if desc else column.asc(), Devices.index)
        rows = (
            await self.session.execute(query.offset((page - 1) * page_size).limit(page_size))
        ).all()
        return [(row[0], row[1]) for row in rows], total

    async def reserved_vram_for(self, device_ids: List[uuid.UUID]) -> Dict[uuid.UUID, int]:
        if not device_ids:
            return {}
        result = await self.session.execute(
            select(
                DeviceSlices.device_id,
                func.coalesce(func.sum(DeviceSlices.vram_bytes), 0),
            )
            .where(
                DeviceSlices.device_id.in_(device_ids),
                DeviceSlices.status != "released",
            )
            .group_by(DeviceSlices.device_id)
        )
        return {row[0]: int(row[1]) for row in result.all()}

    async def instance_ids_for(
        self, device_ids: List[uuid.UUID]
    ) -> Dict[uuid.UUID, List[uuid.UUID]]:
        """Reverse lookup through instances.device_ids (GIN indexed)."""
        out: Dict[uuid.UUID, List[uuid.UUID]] = {d: [] for d in device_ids}
        if not device_ids:
            return out
        sql = text("""
            SELECT d.device_id, i.id
            FROM instances i, UNNEST(i.device_ids) AS d(device_id)
            WHERE i.state <> 'stopped' AND d.device_id = ANY(:ids)
            """)
        for device_id, instance_id in (await self.session.execute(sql, {"ids": device_ids})).all():
            out.setdefault(device_id, []).append(instance_id)
        return out

    async def count_all(self) -> Tuple[int, int]:
        """(devices, unhealthy devices) across the fleet."""
        total = (
            await self.session.execute(select(func.count()).where(Devices.deleted_at.is_(None)))
        ).scalar_one()
        bad = (
            await self.session.execute(
                select(func.count()).where(
                    Devices.deleted_at.is_(None),
                    Devices.health.in_(("unhealthy", "degraded")),
                )
            )
        ).scalar_one()
        return total, bad

    async def vram_total(self) -> int:
        return int(
            (
                await self.session.execute(
                    select(func.coalesce(func.sum(Devices.vram_total_bytes), 0)).where(
                        Devices.deleted_at.is_(None)
                    )
                )
            ).scalar_one()
        )


class DeviceSliceRepository(BaseRepository[DeviceSlices]):
    def __init__(self, session: AsyncSession):
        super().__init__(DeviceSlices, session)

    async def list_for_device(
        self, device_id: uuid.UUID, include_released: bool = False
    ) -> List[DeviceSlices]:
        query = select(DeviceSlices).where(DeviceSlices.device_id == device_id)
        if not include_released:
            query = query.where(DeviceSlices.status != "released")
        result = await self.session.execute(query.order_by(DeviceSlices.created_at))
        return list(result.scalars().all())

    async def list_for_devices(
        self, device_ids: List[uuid.UUID]
    ) -> Dict[uuid.UUID, List[DeviceSlices]]:
        out: Dict[uuid.UUID, List[DeviceSlices]] = {d: [] for d in device_ids}
        if not device_ids:
            return out
        result = await self.session.execute(
            select(DeviceSlices)
            .where(
                DeviceSlices.device_id.in_(device_ids),
                DeviceSlices.status != "released",
            )
            .order_by(DeviceSlices.created_at)
        )
        for row in result.scalars().all():
            out.setdefault(row.device_id, []).append(row)
        return out

    async def list_active(
        self,
        page: int,
        page_size: int,
        deployment_id: Optional[uuid.UUID] = None,
        node_id: Optional[uuid.UUID] = None,
    ) -> Tuple[List[DeviceSlices], int]:
        query = select(DeviceSlices).where(DeviceSlices.status != "released")
        if deployment_id:
            query = query.where(DeviceSlices.deployment_id == deployment_id)
        if node_id:
            query = query.join(Devices, Devices.id == DeviceSlices.device_id).where(
                Devices.node_id == node_id
            )
        total = (
            await self.session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        rows = (
            await self.session.execute(
                query.order_by(DeviceSlices.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
        return list(rows.all()), total

    async def release_for_instances(self, instance_ids: List[uuid.UUID]) -> int:
        if not instance_ids:
            return 0
        result = await self.session.execute(
            text(
                "UPDATE device_slices SET status = 'released', released_at = :now "
                "WHERE instance_id = ANY(:ids) AND status <> 'released'"
            ),
            {"now": datetime.now(timezone.utc), "ids": instance_ids},
        )
        return result.rowcount


__all__ = ["DeviceRepository", "DeviceSliceRepository", "Instances"]
