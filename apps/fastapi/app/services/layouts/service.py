"""
GPU layouts (rules/layout.schema.json, admin.yaml Layouts tag): CRUD, capture
the current placement, preview the diff against what is running, apply it as
ordered swap operations and prefetch weights for an upcoming layout.

Diff per deployment (items may list one deployment several times, e.g. one
item per node):

- live instances that match an item (node / devices / slice) are **kept**
  (traffic weight changes become a ``reweight`` swap);
- missing replicas are placed (pinned node/devices, else the placement
  planner) after simulating the VRAM freed by this layout's unloads;
- surplus instances are paired with missing replicas as ``move`` swaps, the
  rest are unloaded;
- ``replicas: 0`` unloads the deployment; ``unlisted_policy: unload`` unloads
  running deployments not in the layout. ``always_on`` deployments are never
  unloaded by a layout (``protected``).

``apply`` creates one swap per instance change: unloads first, then reweights,
then loads / moves ordered so a swap comes after every swap that frees a device
it lands on, and depends on those swaps. Moves whose target devices are freed by
each other form a cycle (two models trading two full GPUs): no order works, since
each move loads on its target before unloading its source. Such moves are split
into an unload and a load (all unloads first, each load depending on the unload
that frees its device), at the price of a short gap for those deployments; the
diff warns about it. Independent swaps run in parallel up to
``RULES_MAX_CONCURRENT_SWAPS``; one failure does not roll back the others.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.exceptions import (
    ConflictError,
    DuplicateResourceError,
    ResourceNotFoundError,
    ValidationError,
)
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_db
from app.schemas.layouts_schema import (
    LayoutApplyResult,
    LayoutCaptureRequest,
    LayoutCreate,
    LayoutDiff,
    LayoutDiffEntry,
    LayoutItemInput,
    LayoutItemOut,
    LayoutOut,
    LayoutUpdate,
)
from app.services.deployments.external import ensure_deployment_managed
from app.services.rules.inputs import (
    DeploymentSnapshot,
    DeviceSnapshot,
    InstanceSnapshot,
)
from app.services.swap.placement import estimate_vram_need, plan_placement
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

SECONDS_PER_UNLOAD = 30
SECONDS_PER_LOAD = 90


@dataclass
class PlannedSwap:
    key: str
    spec: Dict[str, Any]
    deployment_id: uuid.UUID
    frees_devices: List[str] = field(default_factory=list)
    uses_devices: List[str] = field(default_factory=list)
    prefetch: Optional[Tuple[uuid.UUID, uuid.UUID]] = None  # (node_id, model_version_id)


@dataclass
class LayoutPlan:
    diff: LayoutDiff
    swaps: List[PlannedSwap]
    prefetch: List[Tuple[uuid.UUID, uuid.UUID]]


def _waits_for(swaps: List[PlannedSwap]) -> Dict[int, List[int]]:
    """i -> the swaps (indexes) that free a device swap i lands on (never itself)."""
    return {
        i: [
            j
            for j, other in enumerate(swaps)
            if j != i and set(other.frees_devices) & set(swap.uses_devices)
        ]
        for i, swap in enumerate(swaps)
    }


def _cyclic(graph: Dict[int, List[int]]) -> set:
    """Nodes on a cycle (strongly connected components with more than one node; Tarjan)."""
    index: Dict[int, int] = {}
    low: Dict[int, int] = {}
    stack: List[int] = []
    on_stack: set = set()
    out: set = set()
    counter = [0]

    def visit(v: int) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in graph.get(v, []):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            component = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                component.append(w)
                if w == v:
                    break
            if len(component) > 1:
                out.update(component)

    for v in graph:
        if v not in index:
            visit(v)
    return out


def split_move_cycles(
    swaps: List[PlannedSwap],
) -> Tuple[List[PlannedSwap], List[uuid.UUID]]:
    """
    Moves that wait for each other (each lands on a device the other frees) cannot run as
    moves: split each into ``scale_down`` of its source instance + ``scale_up`` on its target.
    Returns the new list and the deployments affected (in order, unique).
    """
    moves = [s for s in swaps if s.spec.get("kind") == "move"]
    cyclic = {id(moves[i]) for i in _cyclic(_waits_for(moves))}
    if not cyclic:
        return swaps, []
    out: List[PlannedSwap] = []
    affected: List[uuid.UUID] = []
    for swap in swaps:
        if id(swap) not in cyclic:
            out.append(swap)
            continue
        source = swap.spec["instance_id"]
        reason = swap.spec.get("reason")
        out.append(
            PlannedSwap(
                key=f"unload:{source}",
                deployment_id=swap.deployment_id,
                spec={
                    "kind": "scale_down",
                    "instance_id": source,
                    "deployment_id": swap.deployment_id,
                    "reason": reason,
                },
                frees_devices=list(swap.frees_devices),
            )
        )
        load_spec = {k: v for k, v in swap.spec.items() if k != "instance_id"}
        load_spec["kind"] = "scale_up"
        out.append(
            PlannedSwap(
                key=swap.key,
                deployment_id=swap.deployment_id,
                spec=load_spec,
                uses_devices=list(swap.uses_devices),
                prefetch=swap.prefetch,
            )
        )
        if swap.deployment_id not in affected:
            affected.append(swap.deployment_id)
    return out, affected


def apply_order(swaps: List[PlannedSwap]) -> List[PlannedSwap]:
    """
    Unloads, then reweights, then loads / moves with every swap after the swaps that free a
    device it lands on (stable otherwise). Cycles must have been split already
    (:func:`split_move_cycles`); any left keep their plan order.
    """
    rank = {"unload": 0, "scale_down": 0, "reweight": 1}
    head = sorted(
        (s for s in swaps if s.spec.get("kind") in rank),
        key=lambda s: rank[s.spec["kind"]],
    )
    rest = [s for s in swaps if s.spec.get("kind") not in rank]
    waits = _waits_for(rest)
    done: set = set()
    ordered: List[PlannedSwap] = []
    while len(ordered) < len(rest):
        ready = [i for i in range(len(rest)) if i not in done and all(j in done for j in waits[i])]
        if not ready:  # a cycle that was not split: keep the remaining plan order
            ready = [i for i in range(len(rest)) if i not in done]
        for i in ready:
            done.add(i)
            ordered.append(rest[i])
    return head + ordered


def _slice_of(inst: InstanceSnapshot, dep: DeploymentSnapshot) -> Optional[int]:
    """Instance slice: the deployment's slice (instances do not override it in v1)."""
    return dep.slice_vram_bytes


def _matches(inst: InstanceSnapshot, item: Dict[str, Any], dep: DeploymentSnapshot) -> bool:
    if item.get("node_id") and str(inst.node_id) != str(item["node_id"]):
        return False
    if item.get("device_ids") and {str(d) for d in inst.device_ids} != {
        str(d) for d in item["device_ids"]
    }:
        return False
    want_slice = item.get("slice_vram_bytes")
    if want_slice and int(want_slice) != int(_slice_of(inst, dep) or 0):
        return False
    return True


def _inst_json(inst: InstanceSnapshot) -> Dict[str, Any]:
    return {
        "instance_id": str(inst.id),
        "node_id": str(inst.node_id),
        "device_ids": [str(d) for d in inst.device_ids],
        "traffic_weight": inst.traffic_weight,
    }


class LayoutService:
    """Business logic for layouts (flush only; ``get_db`` commits)."""

    def __init__(self, session: AsyncSession, inputs=None, prefetcher=None):
        from app.db.postgres.repositories.cluster_state_repository import (
            ClusterStateRepository,
        )
        from app.db.postgres.repositories.layout_repository import LayoutRepository
        from app.db.postgres.repositories.rule_repository import RuleRepository

        self.session = session
        self.repo = LayoutRepository(session)
        self.rules = RuleRepository(session)
        self.state = ClusterStateRepository(session)
        self._inputs = inputs
        self._prefetcher = prefetcher

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db, scope="function")
    ) -> "LayoutService":
        return LayoutService(db)

    def inputs(self):
        if self._inputs is None:
            from app.services.rules.inputs import DbRuleInputs

            self._inputs = DbRuleInputs(self.session)
        return self._inputs

    # ------------------------------------------------------------------ output

    async def _out(self, rows: List[Any]) -> List[LayoutOut]:
        from app.db.postgres.models.generated_models import Deployments, Nodes
        from sqlalchemy import select

        items = await self.repo.items_for_many([r.id for r in rows])
        dep_ids = {i.deployment_id for its in items.values() for i in its}
        node_ids = {i.node_id for its in items.values() for i in its if i.node_id}
        dep_names: Dict[Any, str] = {}
        node_names: Dict[Any, str] = {}
        if dep_ids:
            res = await self.session.execute(
                select(Deployments.id, Deployments.name).where(Deployments.id.in_(list(dep_ids)))
            )
            dep_names = {r[0]: r[1] for r in res.all()}
        if node_ids:
            res = await self.session.execute(
                select(Nodes.id, Nodes.name).where(Nodes.id.in_(list(node_ids)))
            )
            node_names = {r[0]: r[1] for r in res.all()}
        out = []
        for row in rows:
            used_by = [r.id for r in await self.rules.rules_referencing_layout(row.id)]
            out.append(
                LayoutOut(
                    id=row.id,
                    name=row.name,
                    description=row.description,
                    unlisted_policy=getattr(row, "unlisted_policy", None) or "keep",
                    is_active=bool(row.is_active),
                    applied_at=row.applied_at,
                    items=[
                        LayoutItemOut(
                            id=i.id,
                            position=i.position,
                            deployment_id=i.deployment_id,
                            deployment_name=dep_names.get(i.deployment_id),
                            node_id=i.node_id,
                            node_name=node_names.get(i.node_id),
                            device_ids=list(i.device_ids or []),
                            replicas=i.replicas,
                            slice_vram_bytes=i.slice_vram_bytes,
                            slice_compute_pct=i.slice_compute_pct,
                            traffic_weight=i.traffic_weight,
                        )
                        for i in items.get(row.id, [])
                    ],
                    used_by_rule_ids=used_by,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
            )
        return out

    async def _get(self, layout_id: uuid.UUID):
        row = await self.repo.get_active(layout_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Layout")
        return row

    async def list_layouts(self, **filters: Any) -> Tuple[List[LayoutOut], int]:
        rows, total = await self.repo.list(**filters)
        return await self._out(rows), total

    async def get_layout(self, layout_id: uuid.UUID) -> LayoutOut:
        return (await self._out([await self._get(layout_id)]))[0]

    # ------------------------------------------------------------------ writes

    async def _item_rows(self, items: List[LayoutItemInput]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        positions = set()
        for index, item in enumerate(items):
            position = item.position if item.position is not None else index
            if position in positions:
                raise ValidationError(f"items[{index}].position {position} is used twice")
            positions.add(position)
            await ensure_deployment_managed(self.session, item.deployment_id)
            dep = await self.state.get_deployment(item.deployment_id)
            if dep is None:
                raise ValidationError(f"items[{index}].deployment_id: deployment not found")
            if item.node_id is not None:
                node = await self.state.get_node(item.node_id)
                if node is None or getattr(node, "deleted_at", None) is not None:
                    raise ValidationError(f"items[{index}].node_id: node not found")
                if item.device_ids:
                    devices = await self.state.get_devices(item.device_ids)
                    if len(devices) != len(item.device_ids) or any(
                        d.node_id != item.node_id for d in devices
                    ):
                        raise ValidationError(
                            f"items[{index}].device_ids must all belong to node_id"
                        )
            if dep.max_replicas is not None and item.replicas > int(dep.max_replicas):
                raise ValidationError(
                    f"items[{index}].replicas {item.replicas} exceeds the deployment's "
                    f"max_replicas "
                    f"{dep.max_replicas}"
                )
            rows.append(
                {
                    "position": position,
                    "deployment_id": item.deployment_id,
                    "node_id": item.node_id,
                    "device_ids": list(item.device_ids),
                    "replicas": item.replicas,
                    "slice_vram_bytes": item.slice.vram_bytes if item.slice else None,
                    "slice_compute_pct": item.slice.compute_pct if item.slice else None,
                    "traffic_weight": item.traffic_weight,
                }
            )
        return rows

    async def create_layout(self, payload: LayoutCreate, created_by) -> LayoutOut:
        from app.services.audit import audit

        if await self.repo.name_exists(payload.name):
            raise DuplicateResourceError(resource_type="Layout", field="name")
        items = await self._item_rows(payload.items)
        row = await self.repo.create(
            name=payload.name,
            description=payload.description,
            unlisted_policy=payload.unlisted_policy.value,
            is_active=False,
            created_by=created_by,
        )
        await self.repo.replace_items(row.id, items)
        out = await self.get_layout(row.id)
        audit.record(
            "layout.created",
            resource_type="layout",
            resource_id=row.id,
            resource_name=row.name,
            after_state=out.model_dump(mode="json"),
        )
        return out

    async def update_layout(self, layout_id: uuid.UUID, payload: LayoutUpdate) -> LayoutOut:
        from app.services.audit import audit

        row = await self._get(layout_id)
        before = (await self._out([row]))[0].model_dump(mode="json")
        data = payload.model_dump(exclude_unset=True)
        changes: Dict[str, Any] = {}
        if data.get("name"):
            if await self.repo.name_exists(data["name"], layout_id):
                raise DuplicateResourceError(resource_type="Layout", field="name")
            changes["name"] = data["name"]
        if "description" in data:
            changes["description"] = data["description"]
        if data.get("unlisted_policy") is not None:
            changes["unlisted_policy"] = payload.unlisted_policy.value
        if payload.items is not None:
            await self.repo.replace_items(layout_id, await self._item_rows(payload.items))
        if changes:
            changes["updated_at"] = datetime.now(timezone.utc)
            await self.repo.update(layout_id, **changes)
        out = await self.get_layout(layout_id)
        audit.record(
            "layout.updated",
            resource_type="layout",
            resource_id=layout_id,
            resource_name=out.name,
            before_state=before,
            after_state=out.model_dump(mode="json"),
        )
        return out

    async def delete_layout(self, layout_id: uuid.UUID) -> None:
        from app.services.audit import audit

        row = await self._get(layout_id)
        users = await self.rules.rules_referencing_layout(layout_id)
        if users:
            raise ConflictError(
                "Layout is used by time_schedule rule(s): " + ", ".join(r.name for r in users)
            )
        await self.repo.soft_delete(layout_id)
        audit.record(
            "layout.deleted",
            resource_type="layout",
            resource_id=layout_id,
            resource_name=row.name,
        )

    async def capture(self, payload: LayoutCaptureRequest, created_by) -> LayoutOut:
        deployments = await self.inputs().deployments()
        items: List[LayoutItemInput] = []
        for dep in deployments:
            if payload.cluster_id and str(dep.cluster_id) != str(payload.cluster_id):
                continue
            groups: Dict[Tuple[str, Tuple[str, ...]], List[InstanceSnapshot]] = {}
            for inst in dep.live_instances:
                key = (
                    str(inst.node_id),
                    tuple(sorted(str(d) for d in inst.device_ids)),
                )
                groups.setdefault(key, []).append(inst)
            for (node_id, device_ids), insts in sorted(groups.items()):
                slice_spec = None
                if dep.slice_vram_bytes:
                    slice_spec = {
                        "vram_bytes": int(dep.slice_vram_bytes),
                        "compute_pct": int(dep.slice_compute_pct or 100),
                    }
                items.append(
                    LayoutItemInput(
                        deployment_id=dep.id,
                        node_id=uuid.UUID(node_id),
                        device_ids=[uuid.UUID(d) for d in device_ids],
                        replicas=len(insts),
                        slice=slice_spec,
                        traffic_weight=max(i.traffic_weight for i in insts),
                        position=len(items),
                    )
                )
        if not items:
            raise ValidationError("Nothing is running to capture")
        return await self.create_layout(
            LayoutCreate(name=payload.name, description=payload.description, items=items),
            created_by,
        )

    # ------------------------------------------------------------------ diff

    async def diff(self, layout_id: uuid.UUID) -> LayoutDiff:
        return (await self.plan(layout_id)).diff

    async def plan(self, layout_id: uuid.UUID) -> LayoutPlan:
        layout = await self._get(layout_id)
        items = await self.repo.items_for(layout_id)
        inputs = self.inputs()
        deployments = {str(d.id): d for d in await inputs.deployments()}
        devices = [copy.deepcopy(d) for d in await inputs.devices()]
        dev_by_id = {str(d.id): d for d in devices}
        policy = getattr(layout, "unlisted_policy", None) or "keep"

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        order: List[str] = []
        for item in sorted(items, key=lambda i: i.position):
            key = str(item.deployment_id)
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(
                {
                    "node_id": item.node_id,
                    "device_ids": [str(d) for d in item.device_ids or []],
                    "replicas": int(item.replicas),
                    "slice_vram_bytes": item.slice_vram_bytes,
                    "slice_compute_pct": item.slice_compute_pct,
                    "traffic_weight": int(item.traffic_weight),
                }
            )
        if policy == "unload":
            for dep in deployments.values():
                if str(dep.id) not in grouped and dep.live_instances:
                    grouped[str(dep.id)] = [
                        {
                            "node_id": None,
                            "device_ids": [],
                            "replicas": 0,
                            "slice_vram_bytes": None,
                            "slice_compute_pct": None,
                            "traffic_weight": 100,
                            "unlisted": True,
                        }
                    ]
                    order.append(str(dep.id))

        entries: List[LayoutDiffEntry] = []
        swaps: List[PlannedSwap] = []
        warnings: List[str] = []
        pending_loads: List[
            Tuple[
                DeploymentSnapshot,
                Dict[str, Any],
                LayoutDiffEntry,
                Optional[InstanceSnapshot],
            ]
        ] = []
        per_entry: Dict[str, Dict[str, Any]] = {}

        # Pass 1: keep / unload / reweight decisions; free VRAM of unloads.
        for dep_key in order:
            dep = deployments.get(dep_key)
            groups = grouped[dep_key]
            if dep is None:
                warnings.append(f"deployment {dep_key} no longer exists; item skipped")
                continue
            current = list(dep.live_instances)
            desired_total = sum(g["replicas"] for g in groups)
            entry = LayoutDiffEntry(
                deployment_id=dep.id,
                deployment_name=dep.name,
                action="noop",
                **{"from": {"instances": [_inst_json(i) for i in current]}},
                to={"items": _jsonable(groups)},
            )
            per_entry[dep_key] = {"loads": 0, "unloads": 0, "moves": 0, "reweights": 0}
            if desired_total == 0 and dep.priority == "always_on":
                entry.protected = True
                entries.append(entry)
                if current:
                    warnings.append(f"{dep.name} is always_on; the layout does not unload it")
                continue
            remaining = list(current)
            reweight: List[Dict[str, Any]] = []
            for group in groups:
                matched = [i for i in remaining if _matches(i, group, dep)][: group["replicas"]]
                for inst in matched:
                    remaining.remove(inst)
                    if inst.traffic_weight != group["traffic_weight"]:
                        reweight.append(
                            {
                                "instance_id": inst.id,
                                "traffic_weight": group["traffic_weight"],
                            }
                        )
                for _ in range(group["replicas"] - len(matched)):
                    pending_loads.append((dep, group, entry, None))
            surplus = sorted(remaining, key=lambda i: (i.active_sessions + i.waiting_sessions))
            # Pair surplus instances with missing replicas of this deployment as moves.
            dep_loads = [p for p in pending_loads if p[0].id == dep.id and p[3] is None]
            for index, inst in enumerate(list(surplus)):
                if index < len(dep_loads):
                    load = dep_loads[index]
                    pending_loads[pending_loads.index(load)] = (
                        load[0],
                        load[1],
                        load[2],
                        inst,
                    )
                    surplus.remove(inst)
            # desired_total >= 1 here, so even an always_on deployment keeps its replicas.
            for inst in surplus:
                self._free(dev_by_id, dep, inst)
                swaps.append(
                    PlannedSwap(
                        key=f"unload:{inst.id}",
                        deployment_id=dep.id,
                        spec={
                            "kind": "scale_down" if desired_total > 0 else "unload",
                            "instance_id": inst.id,
                            "deployment_id": dep.id,
                            "reason": f"layout {layout.name}",
                        },
                        frees_devices=[str(d) for d in inst.device_ids],
                    )
                )
                per_entry[dep_key]["unloads"] += 1
            if reweight:
                swaps.append(
                    PlannedSwap(
                        key=f"reweight:{dep.id}",
                        deployment_id=dep.id,
                        spec={
                            "kind": "reweight",
                            "deployment_id": dep.id,
                            "reweight": reweight,
                            "reason": f"layout {layout.name}",
                        },
                    )
                )
                per_entry[dep_key]["reweights"] += len(reweight)
            entries.append(entry)

        # Moves free their source device as well (for capacity planning of later loads).
        for dep, group, entry, source in pending_loads:
            if source is not None:
                self._free(dev_by_id, dep, source)

        # Pass 2: place missing replicas.
        prefetch: List[Tuple[uuid.UUID, uuid.UUID]] = []
        for dep, group, entry, source in pending_loads:
            slice_bytes = group.get("slice_vram_bytes")
            placement = plan_placement(
                dep,
                devices,
                count=1,
                node_id=group.get("node_id"),
                device_ids=group.get("device_ids") or None,
                slice_bytes=slice_bytes,
            )
            entry.placement = placement.to_json()
            if not placement.placements:
                warnings.append(f"{dep.name}: no capacity for a replica ({placement.reason})")
                continue
            cand = placement.placements[0]
            need = estimate_vram_need(dep, slice_bytes)
            for dev_id in cand.device_ids:
                dev = dev_by_id.get(str(dev_id))
                if dev is not None:
                    dev.reserved_bytes = int(dev.reserved_bytes or 0) + max(need, 1)
            slice_spec = cand.slice
            if slice_bytes:
                slice_spec = {
                    "vram_bytes": int(slice_bytes),
                    "compute_pct": int(group.get("slice_compute_pct") or 100),
                }
            cached = await self.inputs().cached_bytes_on_node(cand.node_id, dep.model_version_id)
            missing = max(0, int(dep.model_size_bytes or 0) - int(cached or 0))
            entry.needs_prefetch_bytes += missing
            if missing > 0 and (cand.node_id, dep.model_version_id) not in prefetch:
                prefetch.append((cand.node_id, dep.model_version_id))
            spec = {
                "kind": (
                    "move" if source is not None else ("scale_up" if dep.live_instances else "load")
                ),
                "deployment_id": dep.id,
                "node_id": cand.node_id,
                "device_ids": cand.device_ids,
                "slice": slice_spec,
                "traffic_weight": group["traffic_weight"],
                "reason": f"layout {entry.deployment_name}",
            }
            frees: List[str] = []
            if source is not None:
                spec["instance_id"] = source.id
                frees = [str(d) for d in source.device_ids]
                per_entry[str(dep.id)]["moves"] += 1
            else:
                per_entry[str(dep.id)]["loads"] += 1
            swaps.append(
                PlannedSwap(
                    key=f"load:{dep.id}:{len(swaps)}",
                    deployment_id=dep.id,
                    spec=spec,
                    frees_devices=frees,
                    uses_devices=[str(d) for d in cand.device_ids],
                    prefetch=((cand.node_id, dep.model_version_id) if missing > 0 else None),
                )
            )

        swaps, split = split_move_cycles(swaps)
        if split:
            names = ", ".join(
                deployments[str(d)].name if str(d) in deployments else str(d) for d in split
            )
            warnings.append(
                f"{names} trade devices that cannot hold both copies: unloaded first, then "
                "loaded on their new devices (short gap while they reload)"
            )

        for entry in entries:
            counts = per_entry.get(str(entry.deployment_id), {})
            if entry.protected and not any(counts.values()):
                entry.action = "noop"
            elif counts.get("moves"):
                entry.action = "move"
            elif counts.get("loads") and not (entry.from_ or {}).get("instances"):
                entry.action = "load"
            elif counts.get("loads") or (
                counts.get("unloads")
                and sum(g["replicas"] for g in grouped[str(entry.deployment_id)]) > 0
            ):
                entry.action = "scale"
            elif counts.get("unloads"):
                entry.action = "unload"
            elif counts.get("reweights"):
                entry.action = "reweight"
        feasible = not any("no capacity" in w for w in warnings)
        n_unload = sum(1 for s in swaps if s.spec["kind"] in ("unload", "scale_down"))
        n_load = sum(1 for s in swaps if s.spec["kind"] in ("load", "scale_up", "move"))
        parallel = max(1, int(settings.RULES_MAX_CONCURRENT_SWAPS))
        estimate = int((n_unload * SECONDS_PER_UNLOAD + n_load * SECONDS_PER_LOAD) / parallel)
        diff = LayoutDiff(
            layout_id=layout.id,
            feasible=feasible,
            entries=entries,
            estimated_seconds=estimate,
            warnings=warnings,
        )
        return LayoutPlan(diff=diff, swaps=swaps, prefetch=prefetch)

    @staticmethod
    def _free(
        dev_by_id: Dict[str, DeviceSnapshot],
        dep: DeploymentSnapshot,
        inst: InstanceSnapshot,
    ) -> None:
        need = estimate_vram_need(dep)
        for dev_id in inst.device_ids:
            dev = dev_by_id.get(str(dev_id))
            if dev is None:
                continue
            dev.reserved_bytes = max(0, int(dev.reserved_bytes or 0) - need)
            if dev.observed_used_bytes is not None:
                dev.observed_used_bytes = max(0, int(dev.observed_used_bytes) - need)

    # ------------------------------------------------------------------ apply / prefetch

    def prefetcher(self):
        if self._prefetcher is None:
            from app.services.swap.adapters import ModelCachePrefetch

            class _SessionPrefetch(ModelCachePrefetch):
                def __init__(inner, session):  # noqa: N805
                    inner.session = session

                async def prefetch(
                    inner, model_version_id, node_id, pin=False, pin_reason=None
                ):  # noqa: N805
                    from app.services.model_cache.service import ModelCacheService

                    return await ModelCacheService.for_session(inner.session).prefetch(
                        node_id, model_version_id, pin=pin, pin_reason=pin_reason
                    )

            self._prefetcher = _SessionPrefetch(self.session)
        return self._prefetcher

    async def _issue_prefetch(self, pairs, pin: bool, reason: Optional[str]) -> List[uuid.UUID]:
        ids: List[uuid.UUID] = []
        for node_id, version_id in pairs:
            try:
                cid = await self.prefetcher().prefetch(
                    version_id, node_id, pin=pin, pin_reason=reason
                )
                if cid is not None:
                    ids.append(cid)
            except Exception as exc:  # noqa: BLE001 - prefetch is best effort
                logger.warning(f"prefetch of {version_id} on node {node_id} failed: {exc}")
        return ids

    async def prefetch(
        self, layout_id: uuid.UUID, pin: bool = True, pin_reason: Optional[str] = None
    ) -> List[uuid.UUID]:
        """Warm caches for every replica the layout would load (time_schedule prefetch)."""
        plan = await self.plan(layout_id)
        return await self._issue_prefetch(plan.prefetch, pin, pin_reason or f"layout {layout_id}")

    async def apply(
        self,
        layout_id: uuid.UUID,
        *,
        drain_timeout_seconds: int = 300,
        prefetch_only: bool = False,
        trigger: str = "layout",
        rule_run_id: Optional[uuid.UUID] = None,
        created_by: Optional[uuid.UUID] = None,
    ) -> LayoutApplyResult:
        from app.services.audit import audit
        from app.services.swap.planner import SwapPlanner

        layout = await self._get(layout_id)
        plan = await self.plan(layout_id)
        command_ids = await self._issue_prefetch(
            plan.prefetch, pin=prefetch_only, reason=f"layout {layout.name}"
        )
        swap_ids: List[uuid.UUID] = []
        if not prefetch_only:
            planner = SwapPlanner(self.session)
            created: Dict[str, Any] = {}
            freeing: List[Tuple[PlannedSwap, uuid.UUID]] = []
            # unloads first (they free capacity), then reweights, then loads / moves after the
            # swaps that free their devices
            for planned in apply_order(plan.swaps):
                depends = [
                    sid for p, sid in freeing if set(p.frees_devices) & set(planned.uses_devices)
                ]
                spec = {**planned.spec, "drain_timeout_seconds": drain_timeout_seconds}
                row = await planner.create(
                    spec,
                    trigger=trigger,
                    deployment_id=planned.deployment_id,
                    rule_run_id=rule_run_id,
                    layout_id=layout.id,
                    created_by=created_by,
                    depends_on=depends,
                )
                created[planned.key] = row.id
                swap_ids.append(row.id)
                if planned.frees_devices:
                    freeing.append((planned, row.id))
            await self.repo.mark_applied(layout.id, datetime.now(timezone.utc))
        audit.record(
            "layout.prefetched" if prefetch_only else "layout.applied",
            actor_type="rule" if trigger == "rule" else None,
            resource_type="layout",
            resource_id=layout.id,
            resource_name=layout.name,
            after_state={
                "swap_operation_ids": [str(s) for s in swap_ids],
                "command_ids": [str(c) for c in command_ids],
            },
            metadata={
                "rule_run_id": str(rule_run_id) if rule_run_id else None,
                "warnings": plan.diff.warnings,
            },
        )
        return LayoutApplyResult(
            layout_id=layout.id,
            swap_operation_ids=swap_ids,
            command_ids=command_ids,
            diff=plan.diff,
        )


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    return value
