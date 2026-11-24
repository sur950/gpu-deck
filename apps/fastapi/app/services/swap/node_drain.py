"""
Node drain with "move instances": one ``move`` swap per instance on the node.

Hook called by ``NodeService.drain`` (``move_instances=true``). Each live
instance gets a ``move`` swap to a device on another node (load the copy
first when it lands on other devices, route it, then drain and unload the
original), so the deployment keeps serving while the node empties. An
instance with no room elsewhere gets an ``unload`` swap instead (same drain
semantics as before). The node's own devices are never candidates; the node
is already ``draining`` when this runs. Rows are created in the caller's
session (flush only); the swap driver in the jobs process executes them.
"""

from __future__ import annotations

import uuid
from typing import List, Optional, Sequence

from app.core.logger import get_logger

logger = get_logger(__name__)

_LIVE = ("pending", "loading", "ready")


async def plan_node_drain(
    session,
    node_id: uuid.UUID,
    instance_ids: Sequence[uuid.UUID],
    *,
    timeout_seconds: int,
    reason: Optional[str] = None,
    actor_id: Optional[uuid.UUID] = None,
) -> List[uuid.UUID]:
    """Create the swaps that empty ``node_id``; returns their ids."""
    from app.db.postgres.repositories.cluster_state_repository import (
        ClusterStateRepository,
    )
    from app.db.postgres.repositories.swap_operation_repository import (
        SwapOperationRepository,
    )
    from app.services.rules.inputs import DbRuleInputs
    from app.services.swap.placement import plan_placement
    from app.services.swap.planner import SwapPlanner

    state = ClusterStateRepository(session)
    swaps = SwapOperationRepository(session)
    planner = SwapPlanner(session)
    inputs = DbRuleInputs(session)
    deployments = {str(d.id): d for d in await inputs.deployments()}
    devices = [d for d in await inputs.devices() if str(d.node_id) != str(node_id)]
    why = f"node drain: {reason}" if reason else "node drain"
    created: List[uuid.UUID] = []
    for instance_id in instance_ids:
        inst = await state.get_instance(instance_id)
        if inst is None or inst.state not in _LIVE:
            continue
        if await swaps.active_for_instance(inst.id):
            continue  # a running swap already owns this instance
        spec = {
            "kind": "unload",
            "instance_id": inst.id,
            "deployment_id": inst.deployment_id,
            "drain_timeout_seconds": int(timeout_seconds),
            "force_after_timeout": True,
            "reason": why,
        }
        dep = deployments.get(str(inst.deployment_id))
        if dep is not None:
            placed = plan_placement(dep, devices, count=1)
            if placed.placements:
                target = placed.placements[0]
                spec.update(
                    kind="move",
                    node_id=target.node_id,
                    device_ids=target.device_ids,
                    slice=target.slice,
                )
            else:
                logger.warning(
                    f"node drain {node_id}: no room for instance {inst.id} elsewhere "
                    f"({placed.reason}); unloading it"
                )
        row = await planner.create(
            spec, trigger="api", deployment_id=inst.deployment_id, created_by=actor_id
        )
        created.append(row.id)
    logger.info(f"node drain {node_id}: {len(created)} swap(s) planned")
    return created
