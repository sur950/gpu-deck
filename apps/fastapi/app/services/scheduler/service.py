"""
Placement scheduler service: builds the inventory snapshot (DB reservations
plus the latest observed GPU memory from the live metrics cache) and runs the
pure core in ``app.services.scheduler.core``.

Used by ``DeploymentService`` (reconciliation, previews, ``place_instance``)
and available to the automation services (rules / layouts / swaps). ``plan`` adds the
network input (RTT per API-layer region and the deployment's traffic per region,
``app.services.latency.placement``) unless the request already carries one::

    plan = await SchedulerService.for_session(db).plan(request, cluster_id=...)
    victims = await SchedulerService.for_session(db).evictions(request, "normal")
"""

from __future__ import annotations

import copy
import uuid
from typing import Dict, List, Optional, Sequence

from app.core.logger import get_logger
from app.db.postgres.repositories.placement_inventory_repository import (
    PlacementInventoryRepository,
)
from app.services.scheduler.core import (
    InstanceUse,
    NodeView,
    PlacementPlan,
    PlacementRequest,
    place,
    plan_evictions,
)
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


async def observed_device_usage(device_ids: Sequence[uuid.UUID]) -> Dict[str, int]:
    """``{device_id: memory_used_bytes}`` from fresh live samples (best effort)."""
    from app.services.deployments import live

    out: Dict[str, int] = {}
    for dev_id, sample in zip(device_ids, await live.samples("gpu", device_ids)):
        if sample and live.is_fresh(sample) and sample.get("memory_used_bytes") is not None:
            out[str(dev_id)] = int(sample["memory_used_bytes"])
    return out


class SchedulerService:
    """Snapshot + plan."""

    def __init__(self, session: AsyncSession, inventory: PlacementInventoryRepository):
        self.session = session
        self.inventory = inventory

    @classmethod
    def for_session(cls, db: AsyncSession) -> "SchedulerService":
        return cls(db, PlacementInventoryRepository(db))

    async def snapshot(
        self,
        cluster_id: Optional[uuid.UUID] = None,
        exclude_instance_ids: Sequence[uuid.UUID] = (),
        use_observed: bool = True,
    ) -> List[NodeView]:
        nodes = await self.inventory.snapshot(cluster_id, exclude_instance_ids)
        if use_observed:
            from app.services.scheduler.usage import device_usage

            device_ids = [d.id for n in nodes for d in n.devices]
            usage = await device_usage(self.session, device_ids)
            for n in nodes:
                for d in n.devices:
                    u = usage.get(str(d.id))
                    d.observed_used_bytes = u.used_bytes if u else None
                    d.external_used_bytes = u.external_bytes if u else None
        return nodes

    async def plan(
        self,
        request: PlacementRequest,
        *,
        exclude_instance_ids: Sequence[uuid.UUID] = (),
        nodes: Optional[List[NodeView]] = None,
    ) -> PlacementPlan:
        snapshot = (
            nodes
            if nodes is not None
            else await self.snapshot(request.cluster_id, exclude_instance_ids)
        )
        if request.latency is None:
            from app.services.latency.placement import load_context

            context = await load_context(request.deployment_id)
            if context is not None:
                request = copy.copy(request)
                request.latency = context
        return place(snapshot, request)

    async def evictions(
        self,
        request: PlacementRequest,
        requester_priority: str,
        nodes: Optional[List[NodeView]] = None,
    ) -> List[InstanceUse]:
        snapshot = nodes if nodes is not None else await self.snapshot(request.cluster_id)
        return plan_evictions(snapshot, request, requester_priority)
