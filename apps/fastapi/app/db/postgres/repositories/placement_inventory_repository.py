"""
Read-only inventory queries for placement and compatibility checks:
nodes with their devices, and what live instances / GPU slices already hold
on each device. Turns rows into the scheduler's pure ``NodeView`` snapshot.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import (
    Deployments,
    Devices,
    DeviceSlices,
    Instances,
    Models,
    Nodes,
)
from app.services.scheduler.core import DeviceView, InstanceUse, NodeView
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# Instances that hold (or are about to hold) device memory.
HOLDING_STATES = ("pending", "loading", "ready", "draining", "unloading")


class PlacementInventoryRepository:
    """Nodes, devices and current reservations (no writes)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def release_connection(self) -> None:
        await self.session.commit()

    async def node_with_devices(self, node_id: uuid.UUID) -> Tuple[Optional[Nodes], List[Devices]]:
        node = (
            await self.session.execute(
                select(Nodes).where(Nodes.id == node_id, Nodes.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if node is None:
            return None, []
        return node, (await self.devices_for_nodes([node_id])).get(node_id, [])

    async def online_nodes_with_devices(
        self, cluster_id: Optional[uuid.UUID] = None
    ) -> List[Tuple[Nodes, List[Devices]]]:
        conditions = [Nodes.deleted_at.is_(None), Nodes.status == "online"]
        if cluster_id is not None:
            conditions.append(Nodes.cluster_id == cluster_id)
        nodes = list(
            (
                await self.session.execute(
                    select(Nodes).where(and_(*conditions)).order_by(Nodes.name)
                )
            )
            .scalars()
            .all()
        )
        devices = await self.devices_for_nodes([n.id for n in nodes])
        return [(n, devices.get(n.id, [])) for n in nodes]

    async def devices_for_nodes(
        self, node_ids: Sequence[uuid.UUID]
    ) -> Dict[uuid.UUID, List[Devices]]:
        if not node_ids:
            return {}
        rows = (
            await self.session.execute(
                select(Devices)
                .where(Devices.node_id.in_(list(node_ids)), Devices.deleted_at.is_(None))
                .order_by(Devices.node_id, Devices.index)
            )
        ).scalars()
        out: Dict[uuid.UUID, List[Devices]] = {}
        for d in rows:
            out.setdefault(d.node_id, []).append(d)
        return out

    async def devices_by_ids(self, device_ids: Sequence[uuid.UUID]) -> List[Devices]:
        if not device_ids:
            return []
        rows = (
            await self.session.execute(
                select(Devices).where(
                    Devices.id.in_(list(device_ids)), Devices.deleted_at.is_(None)
                )
            )
        ).scalars()
        by_id = {d.id: d for d in rows}
        return [by_id[d] for d in device_ids if d in by_id]

    async def nodes_by_ids(self, node_ids: Sequence[uuid.UUID]) -> Dict[uuid.UUID, Nodes]:
        if not node_ids:
            return {}
        rows = (
            await self.session.execute(select(Nodes).where(Nodes.id.in_(list(set(node_ids)))))
        ).scalars()
        return {n.id: n for n in rows}

    async def nodes_in(self, cluster_id: Optional[uuid.UUID] = None) -> List[Nodes]:
        conditions = [Nodes.deleted_at.is_(None), Nodes.status != "removed"]
        if cluster_id is not None:
            conditions.append(Nodes.cluster_id == cluster_id)
        result = await self.session.execute(select(Nodes).where(and_(*conditions)))
        return list(result.scalars().all())

    async def snapshot(
        self,
        cluster_id: Optional[uuid.UUID] = None,
        exclude_instance_ids: Sequence[uuid.UUID] = (),
        observed_used: Optional[Dict[str, int]] = None,
    ) -> List[NodeView]:
        """
        Every non-removed node (status filtering happens in the scheduler so
        previews can explain why a node was skipped) with per-device
        reservations from live instances and active slices.
        """
        conditions = [Nodes.deleted_at.is_(None), Nodes.status != "removed"]
        if cluster_id is not None:
            conditions.append(Nodes.cluster_id == cluster_id)
        nodes = list(
            (await self.session.execute(select(Nodes).where(and_(*conditions)))).scalars().all()
        )
        if not nodes:
            return []
        node_ids = [n.id for n in nodes]
        devices = await self.devices_for_nodes(node_ids)
        excluded = set(exclude_instance_ids)

        inst_rows = (
            await self.session.execute(
                select(
                    Instances.id,
                    Instances.node_id,
                    Instances.deployment_id,
                    Instances.device_ids,
                    Instances.resources,
                    Instances.state,
                    Instances.last_request_at,
                    Deployments.priority_override,
                    Models.priority,
                )
                .join(Deployments, Deployments.id == Instances.deployment_id)
                .join(Models, Models.id == Deployments.model_id)
                .where(Instances.node_id.in_(node_ids), Instances.state.in_(HOLDING_STATES))
            )
        ).all()
        slice_rows = (
            await self.session.execute(
                select(
                    DeviceSlices.device_id,
                    DeviceSlices.instance_id,
                    DeviceSlices.vram_bytes,
                    DeviceSlices.compute_pct,
                ).where(DeviceSlices.status != "released")
            )
        ).all()
        slices_by_instance: Dict[Any, Dict[uuid.UUID, Tuple[int, int]]] = {}
        orphan_slices: Dict[uuid.UUID, List[Tuple[int, int]]] = {}
        for device_id, instance_id, vram, pct in slice_rows:
            if instance_id is None:
                orphan_slices.setdefault(device_id, []).append((int(vram), int(pct)))
            else:
                slices_by_instance.setdefault(instance_id, {})[device_id] = (
                    int(vram),
                    int(pct),
                )

        views: Dict[uuid.UUID, DeviceView] = {}
        out: List[NodeView] = []
        observed_used = observed_used or {}
        for node in nodes:
            dev_views = []
            for d in devices.get(node.id, []):
                view = DeviceView(
                    id=d.id,
                    node_id=node.id,
                    index=int(d.index),
                    vendor=d.vendor,
                    vram_total_bytes=int(d.vram_total_bytes or 0),
                    name=d.name,
                    health=d.health,
                    numa_node=d.numa_node,
                    is_unified_memory=bool(d.is_unified_memory),
                    observed_used_bytes=observed_used.get(str(d.id)),
                )
                for vram, pct in orphan_slices.get(d.id, []):
                    view.reserved_bytes += vram
                    view.compute_reserved_pct += pct
                views[d.id] = view
                dev_views.append(view)
            out.append(
                NodeView(
                    id=node.id,
                    name=node.name,
                    status=node.status,
                    cluster_id=node.cluster_id,
                    os=node.os,
                    arch=node.arch,
                    labels=dict(node.labels or {}),
                    devices=dev_views,
                )
            )
        counts: Dict[uuid.UUID, int] = {}
        for row in inst_rows:
            (
                iid,
                node_id,
                dep_id,
                dev_ids,
                resources,
                state,
                last_req,
                prio_override,
                prio,
            ) = row
            counts[node_id] = counts.get(node_id, 0) + 1
            if iid in excluded:
                continue
            res = resources or {}
            per_dev = int(res.get("vram_bytes_per_device") or 0)
            inst_slices = slices_by_instance.get(iid, {})
            for dev_id in dev_ids or []:
                view = views.get(dev_id)
                if view is None:
                    continue
                if dev_id in inst_slices:
                    vram, pct = inst_slices[dev_id]
                else:
                    vram, pct = per_dev, 0
                view.reserved_bytes += vram
                view.compute_reserved_pct += pct
                view.instances.append(
                    InstanceUse(
                        instance_id=iid,
                        deployment_id=dep_id,
                        priority=prio_override or prio or "normal",
                        vram_bytes=vram,
                        compute_pct=pct,
                        last_request_at=last_req,
                        state=state,
                    )
                )
        for nv in out:
            nv.instance_count = counts.get(nv.id, 0)
        return out
