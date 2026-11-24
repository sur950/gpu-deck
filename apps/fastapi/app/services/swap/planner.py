"""
Turns an action spec (from a rule evaluator, a layout diff or a manual
``POST /swaps``) into a ``swap_operations`` row with its immutable ``plan``
and the ordered ``steps`` the executor walks.

Spec keys (all optional except ``kind``)::

    {"kind": "unload|scale_down|load|scale_up|move|restart|reweight",
     "instance_id": ..., "deployment_id": ..., "node_id": ..., "device_ids": [...],
     "slice": {"vram_bytes", "compute_pct"}, "traffic_weight": 100,
     "drain_timeout_seconds": 120, "force_after_timeout": false, "force": false,
     "order": "load_first|unload_first", "reason": "...", "reweight": [...]}

``plan.lock_device_ids`` lists every device the swap touches (source and
target) so the executor holds one lock per device for the whole swap.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from app.core.logger import get_logger
from app.core.settings import settings
from app.services.swap.executor import build_steps

logger = get_logger(__name__)

KIND_FOR_SPEC = {
    "unload": "unload",
    "evict": "unload",
    "scale_down": "scale_down",
    "load": "load",
    "scale_up": "scale_up",
    "move": "move",
    "swap": "swap",
    "restart": "restart",
    "reweight": "apply_layout",
    "apply_layout": "apply_layout",
}


def _s(value: Any) -> Optional[str]:
    return str(value) if value not in (None, "") else None


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def build_plan(
    spec: Dict[str, Any],
    source_device_ids: Sequence[Any] = (),
    source_slice: Optional[Dict[str, Any]] = None,
    depends_on: Sequence[Any] = (),
) -> Dict[str, Any]:
    """Pure: spec (+ source instance devices) -> (kind, plan, steps)."""
    raw_kind = spec.get("kind") or spec.get("action")
    kind = KIND_FOR_SPEC.get(raw_kind)
    if kind is None:
        raise ValueError(f"unknown swap action {raw_kind!r}")
    from_id = _s(spec.get("instance_id")) if kind not in ("load", "scale_up") else None
    plan: Dict[str, Any] = {
        "reason": spec.get("reason"),
        "force_after_timeout": bool(spec.get("force_after_timeout", False)),
        "force": bool(spec.get("force", False)),
        "depends_on": [str(d) for d in depends_on],
    }
    target_devices = [str(d) for d in (spec.get("device_ids") or [])]
    has_load = kind in ("load", "scale_up", "move", "swap")
    if from_id:
        plan["unload"] = [from_id]
    if has_load:
        plan["load"] = {
            "deployment_id": _s(spec.get("deployment_id")),
            "node_id": _s(spec.get("node_id")),
            "device_ids": target_devices,
            "slice": spec.get("slice"),
            "traffic_weight": int(spec.get("traffic_weight", 100)),
            "instance_id": None,
        }
        plan["target_weight"] = int(spec.get("traffic_weight", 100))
    if kind == "restart":
        plan["force"] = bool(spec.get("force", True))
    if kind == "apply_layout":
        plan["reweight"] = [
            {
                "instance_id": str(r["instance_id"]),
                "traffic_weight": int(r["traffic_weight"]),
            }
            for r in spec.get("reweight") or []
        ]
    if source_slice:
        plan["previous_slice"] = source_slice
    order = "unload_first"
    if kind in ("move", "swap"):
        overlap = set(target_devices) & {str(d) for d in source_device_ids}
        order = spec.get("order") or (
            "unload_first" if overlap or not target_devices else "load_first"
        )
        if overlap and order == "load_first":
            order = "unload_first"  # the same device cannot hold both copies
    plan["order"] = order
    plan["lock_device_ids"] = sorted({*(str(d) for d in source_device_ids), *target_devices})
    steps = build_steps(kind, order=order, has_from=bool(from_id), has_load=has_load)
    return {
        "kind": kind,
        "plan": _jsonable(plan),
        "steps": steps,
        "from_instance_id": from_id,
    }


class SwapPlanner:
    """Creates swap_operations rows inside the caller's session (flush, no commit)."""

    def __init__(self, session):
        from app.db.postgres.repositories.cluster_state_repository import (
            ClusterStateRepository,
        )
        from app.db.postgres.repositories.swap_operation_repository import (
            SwapOperationRepository,
        )

        self.session = session
        self.repo = SwapOperationRepository(session)
        self.state = ClusterStateRepository(session)

    async def create(
        self,
        spec: Dict[str, Any],
        *,
        trigger: str,
        deployment_id: Optional[uuid.UUID] = None,
        rule_run_id: Optional[uuid.UUID] = None,
        layout_id: Optional[uuid.UUID] = None,
        created_by: Optional[uuid.UUID] = None,
        dry_run: bool = False,
        depends_on: Sequence[uuid.UUID] = (),
    ):
        source_devices: List[Any] = []
        source_slice = None
        node_id = spec.get("node_id")
        instance_id = spec.get("instance_id")
        if instance_id and spec.get("kind") not in ("load", "scale_up"):
            inst = await self.state.get_instance(uuid.UUID(str(instance_id)))
            if inst is None:
                raise ValueError(f"instance {instance_id} not found")
            source_devices = list(inst.device_ids or [])
            deployment_id = deployment_id or inst.deployment_id
            if node_id is None or spec.get("kind") in (
                "unload",
                "scale_down",
                "evict",
                "restart",
            ):
                node_id = inst.node_id
            resources = inst.resources or {}
            if resources.get("slice"):
                source_slice = resources["slice"]
        deployment_id = deployment_id or spec.get("deployment_id")
        if spec.get("kind") in ("load", "scale_up", "move", "swap") and not spec.get(
            "deployment_id"
        ):
            spec = {**spec, "deployment_id": deployment_id}
        built = build_plan(spec, source_devices, source_slice, depends_on)
        drain = spec.get("drain_timeout_seconds")
        if drain is None:
            drain = settings.SWAP_DEFAULT_DRAIN_TIMEOUT_SECONDS
        row = await self.repo.create(
            kind=built["kind"],
            state="pending",
            trigger=trigger,
            rule_run_id=rule_run_id,
            layout_id=layout_id,
            deployment_id=uuid.UUID(str(deployment_id)) if deployment_id else None,
            from_instance_id=(
                uuid.UUID(built["from_instance_id"]) if built["from_instance_id"] else None
            ),
            node_id=uuid.UUID(str(node_id)) if node_id else None,
            plan=built["plan"],
            steps=built["steps"],
            current_step=0,
            rollback={},
            drain_timeout_seconds=int(drain),
            dry_run=bool(dry_run),
            created_by=created_by,
        )
        logger.info(
            f"swap {row.id} created kind={row.kind} trigger={trigger} deployment={deployment_id} "
            f"dry_run={dry_run}"
        )
        return row
