"""
SSH GPU instance repository: ``gpu_instances`` rows and their device
reservations in ``device_slices`` (``gpu_instance_id``). Flushes, never commits.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import DeviceSlices, GpuInstances, Nodes
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import func, or_, select, update

logger = get_logger(__name__)

LIVE_STATES = ("requested", "placing", "starting", "running", "stopping")
HOLDING_STATES = ("placing", "starting", "running", "stopping")
TERMINAL_STATES = ("stopped", "error")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GpuInstanceRepository(BaseRepository[GpuInstances]):
    def __init__(self, session):
        super().__init__(GpuInstances, session)

    async def get_active(self, instance_id: uuid.UUID) -> Optional[GpuInstances]:
        result = await self.session.execute(
            select(GpuInstances).where(
                GpuInstances.id == instance_id, GpuInstances.deleted_at.is_(None)
            )
        )
        return result.scalar_one_or_none()

    async def get_for_update(self, instance_id: uuid.UUID) -> Optional[GpuInstances]:
        result = await self.session.execute(
            select(GpuInstances)
            .where(GpuInstances.id == instance_id, GpuInstances.deleted_at.is_(None))
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Optional[GpuInstances]:
        result = await self.session.execute(
            select(GpuInstances).where(
                func.lower(GpuInstances.name) == name.lower(),
                GpuInstances.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        page: int,
        page_size: int,
        *,
        state: Optional[Sequence[str]] = None,
        node_id: Optional[uuid.UUID] = None,
        cluster_id: Optional[uuid.UUID] = None,
        search: Optional[str] = None,
        include_stopped: bool = True,
    ) -> Tuple[List[GpuInstances], int]:
        query = select(GpuInstances).where(GpuInstances.deleted_at.is_(None))
        if state:
            query = query.where(GpuInstances.state.in_(list(state)))
        elif not include_stopped:
            query = query.where(GpuInstances.state.in_(LIVE_STATES))
        if node_id is not None:
            query = query.where(GpuInstances.node_id == node_id)
        if cluster_id is not None:
            query = query.where(GpuInstances.cluster_id == cluster_id)
        if search:
            like = f"%{search.lower()}%"
            query = query.where(
                or_(
                    func.lower(GpuInstances.name).like(like),
                    func.lower(GpuInstances.image).like(like),
                )
            )
        total = (
            await self.session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        rows = (
            await self.session.execute(
                query.order_by(GpuInstances.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
        return list(rows.all()), int(total)

    async def in_states(self, states: Iterable[str]) -> List[GpuInstances]:
        result = await self.session.execute(
            select(GpuInstances)
            .where(GpuInstances.deleted_at.is_(None), GpuInstances.state.in_(list(states)))
            .order_by(GpuInstances.created_at)
        )
        return list(result.scalars().all())

    async def for_node(
        self, node_id: uuid.UUID, states: Iterable[str] = LIVE_STATES
    ) -> List[GpuInstances]:
        result = await self.session.execute(
            select(GpuInstances).where(
                GpuInstances.node_id == node_id,
                GpuInstances.deleted_at.is_(None),
                GpuInstances.state.in_(list(states)),
            )
        )
        return list(result.scalars().all())

    async def by_start_or_stop_command(self, command_id: uuid.UUID) -> Optional[GpuInstances]:
        result = await self.session.execute(
            select(GpuInstances).where(
                or_(
                    GpuInstances.start_command_id == command_id,
                    GpuInstances.stop_command_id == command_id,
                )
            )
        )
        return result.scalar_one_or_none()

    async def set_values(self, instance_id: uuid.UUID, **values: Any) -> None:
        if "state" in values:
            values.setdefault("state_changed_at", utcnow())
        await self.session.execute(
            update(GpuInstances).where(GpuInstances.id == instance_id).values(**values)
        )
        await self.session.flush()

    async def count_running_on_node(self, node_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(GpuInstances)
            .where(
                GpuInstances.node_id == node_id,
                GpuInstances.deleted_at.is_(None),
                GpuInstances.state.in_(HOLDING_STATES),
            )
        )
        return int(result.scalar_one())

    async def node(self, node_id: Optional[uuid.UUID]) -> Optional[Nodes]:
        if node_id is None:
            return None
        result = await self.session.execute(select(Nodes).where(Nodes.id == node_id))
        return result.scalar_one_or_none()

    # ---------- device reservations ----------

    async def reserve(
        self,
        instance_id: uuid.UUID,
        name: str,
        reservations: Sequence[Tuple[uuid.UUID, int, int]],
    ) -> List[uuid.UUID]:
        """One ``device_slices`` row per (device_id, vram_bytes, compute_pct)."""
        ids = []
        for device_id, vram, pct in reservations:
            row = DeviceSlices(
                id=uuid.uuid4(),
                device_id=device_id,
                gpu_instance_id=instance_id,
                name=name[:255],
                vram_bytes=max(1, int(vram)),
                compute_pct=max(1, min(100, int(pct))),
                status="reserved",
            )
            self.session.add(row)
            ids.append(row.id)
        await self.session.flush()
        return ids

    async def adopt_slice(self, slice_id: uuid.UUID, instance_id: uuid.UUID) -> bool:
        """Bind a free direct slice (no instance, no GPU instance) to this GPU instance."""
        result = await self.session.execute(
            update(DeviceSlices)
            .where(
                DeviceSlices.id == slice_id,
                DeviceSlices.status != "released",
                DeviceSlices.instance_id.is_(None),
                DeviceSlices.gpu_instance_id.is_(None),
                DeviceSlices.deployment_id.is_(None),
            )
            .values(gpu_instance_id=instance_id)
        )
        await self.session.flush()
        return (result.rowcount or 0) == 1

    async def slices_for(self, instance_id: uuid.UUID) -> List[DeviceSlices]:
        result = await self.session.execute(
            select(DeviceSlices).where(
                DeviceSlices.gpu_instance_id == instance_id,
                DeviceSlices.status != "released",
            )
        )
        return list(result.scalars().all())

    async def set_slices_status(
        self,
        instance_id: uuid.UUID,
        status: str,
        *,
        return_slice_ids: Sequence[uuid.UUID] = (),
    ) -> None:
        """
        Change the reservations' status. On release, slices listed in
        ``return_slice_ids`` (direct slices the instance adopted) go back to the
        pool as unbound ``reserved`` slices instead of being released.
        """
        if status == "released" and return_slice_ids:
            await self.session.execute(
                update(DeviceSlices)
                .where(
                    DeviceSlices.gpu_instance_id == instance_id,
                    DeviceSlices.id.in_(list(return_slice_ids)),
                    DeviceSlices.status != "released",
                )
                .values(gpu_instance_id=None, status="reserved")
            )
        values: Dict[str, Any] = {"status": status}
        if status == "released":
            values["released_at"] = utcnow()
        await self.session.execute(
            update(DeviceSlices)
            .where(
                DeviceSlices.gpu_instance_id == instance_id,
                DeviceSlices.status != "released",
            )
            .values(**values)
        )
        await self.session.flush()
