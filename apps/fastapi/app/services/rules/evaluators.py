"""
Per-type rule evaluators (rules.md section 2).

Each evaluator is a pure async function over ``RuleInputs``: it returns an
``Evaluation`` listing the targets that matched, the observed values that
explain the decision, the planned actions and any protection that blocked a
target (always_on, min_loaded grace, replica bounds, no capacity ...). It
never executes anything — the engine (engine.py) applies cooldowns, dry-run,
the swap-capacity guard and turns actions into swap operations.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from app.core.logger import get_logger
from app.services.rules.inputs import (
    PRIORITY_RANK,
    DeploymentSnapshot,
    DeviceSnapshot,
    InstanceSnapshot,
    MinuteBucket,
    RuleInputs,
)
from app.services.rules.selectors import select_targets
from app.services.rules.windows import in_time_windows
from app.services.swap.placement import (
    PlacementResult,
    device_free_after_evictions,
    estimate_vram_need,
    plan_placement,
    selector_allows,
)

logger = get_logger(__name__)


@dataclass
class PlannedAction:
    action: str  # unload | load | scale_up | scale_down | move | restart | apply_layout | evict
    target_type: str  # instance | deployment | layout
    target_id: Optional[uuid.UUID]
    target_name: Optional[str]
    deployment_id: Optional[uuid.UUID]
    spec: Dict[str, Any] = field(default_factory=dict)
    swap_operation_id: Optional[uuid.UUID] = None
    status: str = "planned"
    message: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "target_type": self.target_type,
            "target_id": str(self.target_id) if self.target_id else None,
            "target_name": self.target_name,
            "swap_operation_id": (str(self.swap_operation_id) if self.swap_operation_id else None),
            "status": self.status,
            "message": self.message,
            "spec": _jsonable(self.spec),
        }


@dataclass
class TargetMatch:
    deployment_id: uuid.UUID
    deployment_name: str
    observed: Dict[str, Any]
    actions: List[PlannedAction] = field(default_factory=list)
    skipped_reason: Optional[str] = None  # protection that blocked the target


@dataclass
class Evaluation:
    matches: List[TargetMatch]
    decision: Dict[str, Any] = field(default_factory=dict)
    considered: int = 0

    @property
    def actionable(self) -> List[TargetMatch]:
        return [m for m in self.matches if m.actions and not m.skipped_reason]


@dataclass
class EvalContext:
    default_timezone: str = "UTC"
    headroom_pct: int = 10
    force_after_timeout: bool = False
    trigger: str = "interval"  # interval | placement_failure | api


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


def _minutes_since(now: datetime, ts: Optional[datetime]) -> Optional[float]:
    if ts is None:
        return None
    return (now - ts).total_seconds() / 60.0


def _complete(buckets: Sequence[MinuteBucket], minutes: int) -> bool:
    return len(buckets) >= minutes


async def evaluate(
    rule_type: str,
    params: Dict[str, Any],
    target: Dict[str, Any],
    inputs: RuleInputs,
    ctx: EvalContext,
) -> Evaluation:
    """Dispatch to the evaluator for ``rule_type``."""
    fn = _EVALUATORS.get(rule_type)
    if fn is None:
        raise ValueError(f"no evaluator for rule type {rule_type!r}")
    return await fn(params, target, inputs, ctx)


# ---------------------------------------------------------------------------
# idle_unload
# ---------------------------------------------------------------------------


async def evaluate_idle_unload(params, target, inputs: RuleInputs, ctx: EvalContext) -> Evaluation:
    now = inputs.now
    targets = select_targets(target, await inputs.deployments())
    matches: List[TargetMatch] = []
    threshold = int(params["idle_minutes"])
    in_window = in_time_windows(
        now,
        params.get("respect_time_windows") or [],
        params.get("timezone") or ctx.default_timezone,
    )
    for dep in targets:
        ready = dep.ready_instances
        if not ready:
            continue
        reference = dep.last_request_at or min(
            (i.ready_at for i in ready if i.ready_at), default=None
        )
        idle = _minutes_since(now, reference)
        if idle is None:
            continue
        active, waiting = dep.live_sessions
        observed = {
            "idle_minutes": round(idle, 1),
            "threshold": threshold,
            "active_sessions": active,
            "waiting_sessions": waiting,
            "ready_replicas": len(ready),
        }
        if idle < threshold or (active + waiting) > 0:
            continue
        match = TargetMatch(dep.id, dep.name, observed)
        if dep.priority == "always_on":
            match.skipped_reason = "always_on"
        elif not in_window:
            match.skipped_reason = "outside_window"
        else:
            # never below the deployment's own floor, or the deployments reconciler would reload it
            keep = max(int(params.get("keep_replicas") or 0), int(dep.min_replicas or 0))
            observed["keep_replicas_effective"] = keep
            grace = int(params.get("min_loaded_minutes") or 0)
            # unload newest first, keep the oldest `keep` replicas
            ordered = sorted(ready, key=lambda i: i.ready_at or now, reverse=True)
            candidates = ordered[: max(0, len(ordered) - keep)]
            eligible = [
                i
                for i in candidates
                if i.ready_at is None or (now - i.ready_at) >= timedelta(minutes=grace)
            ]
            if not candidates:
                match.skipped_reason = "keep_replicas"
            elif not eligible:
                match.skipped_reason = "min_loaded_minutes"
            else:
                for inst in eligible:
                    match.actions.append(_unload_action(dep, inst, params, ctx, reason="idle"))
        matches.append(match)
    return Evaluation(matches=matches, considered=len(targets))


def _unload_action(
    dep: DeploymentSnapshot, inst: InstanceSnapshot, params, ctx, reason: str
) -> PlannedAction:
    return PlannedAction(
        action="unload",
        target_type="instance",
        target_id=inst.id,
        target_name=f"{dep.name}@{inst.node_id}",
        deployment_id=dep.id,
        spec={
            "kind": "unload",
            "instance_id": inst.id,
            "node_id": inst.node_id,
            "drain_timeout_seconds": int(params.get("drain_timeout_seconds", 120)),
            "force_after_timeout": ctx.force_after_timeout,
            "reason": reason,
        },
    )


# ---------------------------------------------------------------------------
# low_traffic_shrink
# ---------------------------------------------------------------------------


async def evaluate_low_traffic_shrink(
    params, target, inputs: RuleInputs, ctx: EvalContext
) -> Evaluation:
    now = inputs.now
    targets = select_targets(target, await inputs.deployments())
    matches: List[TargetMatch] = []
    threshold = float(params["requests_per_minute_below"])
    minutes = int(params["for_minutes"])
    for dep in targets:
        ready = dep.ready_instances
        if not ready:
            continue
        buckets = await inputs.deployment_minutes(dep.id, minutes)
        rpm = [b.requests for b in buckets]
        observed = {
            "requests_per_minute": rpm,
            "threshold": threshold,
            "for_minutes": minutes,
            "complete_buckets": len(buckets),
            "ready_replicas": len(ready),
        }
        if not _complete(buckets, minutes) or any(r >= threshold for r in rpm):
            continue
        match = TargetMatch(dep.id, dep.name, observed)
        action = params.get("action") or "remove_replica"
        floor = max(int(params.get("min_replicas", 1)), int(dep.min_replicas or 0))
        if dep.priority == "always_on":
            floor = max(floor, 1)
        observed["min_replicas_effective"] = floor
        if action == "remove_replica":
            removable = len(ready) - floor
            if removable <= 0:
                match.skipped_reason = "min_replicas"
            else:
                step = min(int(params.get("step_replicas", 1)), removable)
                ordered = sorted(
                    ready,
                    key=lambda i: (
                        i.active_sessions + i.waiting_sessions,
                        -(i.ready_at or now).timestamp(),
                    ),
                )
                for inst in ordered[:step]:
                    act = _unload_action(dep, inst, params, ctx, reason="low_traffic")
                    act.action = "scale_down"
                    act.spec["kind"] = "scale_down"
                    match.actions.append(act)
        else:  # shrink_slice
            shrink = params.get("shrink_slice") or {}
            if dep.slice_vram_bytes and dep.slice_vram_bytes <= int(shrink.get("vram_bytes", 0)):
                match.skipped_reason = "already_at_slice"
            else:
                for inst in ready:
                    match.actions.append(
                        PlannedAction(
                            action="move",
                            target_type="instance",
                            target_id=inst.id,
                            target_name=f"{dep.name}@{inst.node_id}",
                            deployment_id=dep.id,
                            spec={
                                "kind": "move",
                                "instance_id": inst.id,
                                "node_id": inst.node_id,
                                "device_ids": list(inst.device_ids),
                                "slice": {
                                    "vram_bytes": int(shrink["vram_bytes"]),
                                    "compute_pct": int(shrink.get("compute_pct", 100)),
                                },
                                "drain_timeout_seconds": int(
                                    params.get("drain_timeout_seconds", 120)
                                ),
                                "force_after_timeout": ctx.force_after_timeout,
                                "reason": "shrink_slice",
                            },
                        )
                    )
        matches.append(match)
    return Evaluation(matches=matches, considered=len(targets))


# ---------------------------------------------------------------------------
# high_load_grow
# ---------------------------------------------------------------------------


def _grow_condition_met(bucket: MinuteBucket, params) -> bool:
    waiting_above = params.get("waiting_requests_above")
    kv_above = params.get("kv_cache_pct_above")
    checks = []
    if waiting_above is not None:
        checks.append(bucket.waiting_avg > float(waiting_above))
    if kv_above is not None:
        checks.append(bucket.kv_pct_max > float(kv_above))
    if not checks:
        return False
    return all(checks) if params.get("condition", "any") == "all" else any(checks)


async def evaluate_high_load_grow(
    params, target, inputs: RuleInputs, ctx: EvalContext
) -> Evaluation:
    targets = select_targets(target, await inputs.deployments())
    devices = await inputs.devices()
    matches: List[TargetMatch] = []
    minutes = int(params["for_minutes"])
    for dep in targets:
        if not dep.desired_running or not dep.ready_instances:
            continue
        buckets = await inputs.deployment_minutes(dep.id, minutes)
        observed = {
            "waiting_avg": [round(b.waiting_avg, 2) for b in buckets],
            "kv_cache_pct_max": [round(b.kv_pct_max, 1) for b in buckets],
            "waiting_threshold": params.get("waiting_requests_above"),
            "kv_threshold": params.get("kv_cache_pct_above"),
            "condition": params.get("condition", "any"),
            "for_minutes": minutes,
            "complete_buckets": len(buckets),
            "live_replicas": len(dep.live_instances),
        }
        if not _complete(buckets, minutes) or not all(
            _grow_condition_met(b, params) for b in buckets
        ):
            continue
        match = TargetMatch(dep.id, dep.name, observed)
        cap = min(int(params["max_replicas"]), int(dep.max_replicas or params["max_replicas"]))
        observed["max_replicas_effective"] = cap
        room = cap - len(dep.live_instances)
        if room <= 0:
            match.skipped_reason = "max_replicas"
            matches.append(match)
            continue
        step = min(int(params.get("step_replicas", 1)), room)
        placement = plan_placement(
            dep,
            devices,
            count=step,
            strategy=params.get("placement_strategy"),
            min_free_vram_bytes=params.get("min_free_vram_bytes"),
            headroom_pct=ctx.headroom_pct,
        )
        observed["placement"] = placement.to_json()
        if placement.placements:
            for cand in placement.placements:
                match.actions.append(
                    _load_action(dep, cand.node_id, cand.device_ids, cand.slice, "scale_up")
                )
        elif params.get("make_room"):
            evictions = _plan_evictions(
                dep,
                await inputs.deployments(),
                devices,
                inputs.now,
                evict_priorities=["evictable", "normal"],
                victim_idle_minutes=0,
                max_evictions=4,
                drain_timeout=120,
                ctx=ctx,
            )
            if evictions is None:
                match.skipped_reason = "no_capacity"
            else:
                victims, device = evictions
                match.actions.extend(victims)
                load = _load_action(dep, device.node_id, [device.id], None, "scale_up")
                load.spec["depends_on_actions"] = [str(v.target_id) for v in victims]
                match.actions.append(load)
                observed["make_room_device"] = str(device.id)
        else:
            match.skipped_reason = "no_capacity"
        matches.append(match)
    return Evaluation(matches=matches, considered=len(targets))


def _load_action(
    dep: DeploymentSnapshot, node_id, device_ids, slice_spec, kind: str
) -> PlannedAction:
    return PlannedAction(
        action=kind,
        target_type="deployment",
        target_id=dep.id,
        target_name=dep.name,
        deployment_id=dep.id,
        spec={
            "kind": kind,
            "deployment_id": dep.id,
            "node_id": node_id,
            "device_ids": list(device_ids),
            "slice": slice_spec,
            "traffic_weight": 100,
        },
    )


# ---------------------------------------------------------------------------
# make_room_by_priority
# ---------------------------------------------------------------------------


def _plan_evictions(
    requester: DeploymentSnapshot,
    deployments: Sequence[DeploymentSnapshot],
    devices: Sequence[DeviceSnapshot],
    now: datetime,
    evict_priorities: Sequence[str],
    victim_idle_minutes: int,
    max_evictions: int,
    drain_timeout: int,
    ctx: EvalContext,
):
    """
    Pick the device where unloading the fewest lowest-priority idle instances
    frees enough VRAM for ``requester``. Returns (evict actions, device) or None.
    """
    need = max(estimate_vram_need(requester), _reported_need(requester))
    by_instance: Dict[str, DeploymentSnapshot] = {}
    instances: Dict[str, InstanceSnapshot] = {}
    for dep in deployments:
        for inst in dep.ready_instances:
            by_instance[str(inst.id)] = dep
            instances[str(inst.id)] = inst
    requester_rank = requester.priority_rank
    best = None
    for device in devices:
        if not device.usable or not selector_allows(device, requester.node_selector):
            continue
        if (
            requester.cluster_id is not None
            and device.cluster_id is not None
            and str(device.cluster_id) != str(requester.cluster_id)
        ):
            continue
        candidates = []
        for inst_id, inst in instances.items():
            if str(device.id) not in {str(d) for d in inst.device_ids}:
                continue
            dep = by_instance[inst_id]
            if dep.id == requester.id or dep.priority == "always_on":
                continue
            if dep.priority not in evict_priorities or dep.priority_rank >= requester_rank:
                continue
            idle = _minutes_since(now, dep.last_request_at or inst.ready_at)
            if victim_idle_minutes > 0:
                if idle is None or idle < victim_idle_minutes:
                    continue
                if inst.active_sessions + inst.waiting_sessions > 0:
                    continue
            freed = int(dep.slice_vram_bytes or estimate_vram_need(dep) or 0)
            candidates.append((dep.priority_rank, -(idle or 0), freed, dep, inst))
        candidates.sort(key=lambda c: (c[0], c[1]))
        chosen = []
        freed_total = 0
        for rank, _neg_idle, freed, dep, inst in candidates:
            if len(chosen) >= max_evictions:
                break
            chosen.append((dep, inst))
            freed_total += freed
            if (
                device_free_after_evictions(device, freed_total, ctx.headroom_pct) >= need
                and need > 0
            ):
                break
        if not chosen:
            continue
        if need > 0 and device_free_after_evictions(device, freed_total, ctx.headroom_pct) < need:
            continue
        key = (len(chosen), max(c[0].priority_rank for c in chosen))
        if best is None or key < best[0]:
            best = (key, chosen, device)
    if best is None:
        return None
    _, chosen, device = best
    actions = []
    for dep, inst in chosen:
        act = PlannedAction(
            action="evict",
            target_type="instance",
            target_id=inst.id,
            target_name=f"{dep.name}@{inst.node_id}",
            deployment_id=dep.id,
            spec={
                "kind": "unload",
                "instance_id": inst.id,
                "node_id": inst.node_id,
                "drain_timeout_seconds": drain_timeout,
                "force_after_timeout": ctx.force_after_timeout,
                "reason": f"make_room_for:{requester.id}",
                "victim_priority": dep.priority,
            },
        )
        actions.append(act)
    return actions, device


_NEEDS_MIB = re.compile(r"needs (\d+) MiB")


def _reported_need(dep: DeploymentSnapshot) -> int:
    """
    Bytes per device the scheduler said the deployment needs ("... does not fit: needs
    1394 MiB, 655 MiB free"), 0 when unknown. The scheduler's estimate includes KV cache for
    the configured context and is the one placement is judged by; this module's own rough
    estimate is smaller, so without it the rule saw room where the scheduler saw none.
    """
    found = _NEEDS_MIB.search(dep.status_message or "")
    return int(found.group(1)) * 1024 * 1024 if found else 0


def _needs_room(dep: DeploymentSnapshot, trigger: str) -> bool:
    if not dep.desired_running or dep.status not in ("pending", "scheduling", "failed"):
        return False
    if dep.ready_instances:
        return False
    message = (dep.status_message or "").lower()
    # The placement failure reads "replica 1 of 1 does not fit: needs N MiB, M MiB free".
    if any(k in message for k in ("insufficient_vram", "no_capacity", "does not fit")):
        return True
    return trigger != "placement_failure"


async def evaluate_make_room_by_priority(
    params, target, inputs: RuleInputs, ctx: EvalContext
) -> Evaluation:
    deployments = await inputs.deployments()
    devices = await inputs.devices()
    trigger_mode = params.get("trigger", "both")
    if trigger_mode == "placement_failure" and ctx.trigger not in (
        "placement_failure",
        "api",
    ):
        return Evaluation(matches=[], considered=0, decision={"reason": "interval_disabled"})
    requesters = [d for d in select_targets(target, deployments) if _needs_room(d, ctx.trigger)]
    requesters.sort(key=lambda d: (-d.priority_rank, d.created_at or inputs.now))
    matches: List[TargetMatch] = []
    for req in requesters:
        reported = _reported_need(req)
        placement = plan_placement(
            req,
            devices,
            headroom_pct=ctx.headroom_pct,
            min_free_vram_bytes=(max(reported, estimate_vram_need(req)) if reported else None),
        )
        observed = {
            "requester_priority": req.priority,
            "need_bytes": placement.need_bytes,
            "placement_feasible": placement.feasible,
        }
        if placement.feasible:
            # The scheduler can place it without evicting anything; nothing for us to do.
            continue
        match = TargetMatch(req.id, req.name, observed)
        evictions = _plan_evictions(
            req,
            deployments,
            devices,
            inputs.now,
            evict_priorities=params.get("evict_priorities") or ["evictable"],
            victim_idle_minutes=int(params.get("victim_idle_minutes", 5)),
            max_evictions=int(params.get("max_evictions_per_run", 1)),
            drain_timeout=int(params.get("drain_timeout_seconds", 120)),
            ctx=ctx,
        )
        if evictions is None:
            match.skipped_reason = "no_eligible_victim"
        else:
            victims, device = evictions
            observed["victims"] = [str(v.target_id) for v in victims]
            observed["device_id"] = str(device.id)
            match.actions.extend(victims)
            load = _load_action(req, device.node_id, [device.id], None, "load")
            load.spec["depends_on_actions"] = [str(v.target_id) for v in victims]
            match.actions.append(load)
        matches.append(match)
    return Evaluation(matches=matches, considered=len(requesters))


# ---------------------------------------------------------------------------
# auto_heal
# ---------------------------------------------------------------------------


async def evaluate_auto_heal(params, target, inputs: RuleInputs, ctx: EvalContext) -> Evaluation:
    deployments = await inputs.deployments()
    devices = await inputs.devices()
    targets = select_targets(target, deployments)
    matches: List[TargetMatch] = []
    health_failures = params.get("health_failures")
    error_above = params.get("error_rate_pct_above")
    minutes = int(params.get("for_minutes", 5))
    min_requests = int(params.get("min_requests_in_window", 10))
    for dep in targets:
        for inst in dep.live_instances:
            if inst.state not in ("ready", "draining"):
                continue
            reasons = []
            observed: Dict[str, Any] = {
                "instance_id": str(inst.id),
                "health": inst.health,
            }
            if health_failures is not None:
                streak = await inputs.health_failure_streak(inst.id)
                observed["health_failures"] = streak
                observed["health_threshold"] = health_failures
                if inst.health == "unhealthy" and streak >= int(health_failures):
                    reasons.append("health")
            if error_above is not None:
                buckets = await inputs.instance_minutes(inst.id, minutes)
                total = sum(b.requests for b in buckets)
                rates = [b.error_rate_pct for b in buckets]
                observed["error_rate_pct"] = [round(r, 1) if r is not None else None for r in rates]
                observed["requests_in_window"] = total
                if (
                    _complete(buckets, minutes)
                    and total >= min_requests
                    and all(r is not None and r > float(error_above) for r in rates)
                ):
                    reasons.append("error_rate")
            if not reasons:
                continue
            observed["reasons"] = reasons
            match = TargetMatch(dep.id, dep.name, observed)
            action = params.get("action", "restart_then_move")
            restarts = await inputs.restarts_last_hour(inst.id)
            observed["restarts_last_hour"] = restarts
            limit = int(params.get("max_restarts_per_hour", 3))
            if action == "restart_then_move" and restarts >= limit:
                action = "move"
                observed["escalated"] = True
            elif action in ("restart", "move") and restarts >= limit:
                match.skipped_reason = "giving_up"
                matches.append(match)
                continue
            elif action == "restart_then_move":
                action = "restart"
            if action == "restart":
                match.actions.append(
                    PlannedAction(
                        action="restart",
                        target_type="instance",
                        target_id=inst.id,
                        target_name=f"{dep.name}@{inst.node_id}",
                        deployment_id=dep.id,
                        spec={
                            "kind": "restart",
                            "instance_id": inst.id,
                            "node_id": inst.node_id,
                            "drain_timeout_seconds": int(params.get("drain_timeout_seconds", 30)),
                            "force_after_timeout": True,
                            "reason": "auto_heal:" + "+".join(reasons),
                        },
                    )
                )
            else:
                exclude = list(inst.device_ids) if params.get("exclude_failed_device", True) else []
                placement = plan_placement(
                    dep,
                    devices,
                    count=1,
                    exclude_device_ids=exclude,
                    strategy=params.get("move_placement_strategy"),
                    headroom_pct=ctx.headroom_pct,
                )
                observed["placement"] = placement.to_json()
                if not placement.placements:
                    match.skipped_reason = "no_capacity"
                else:
                    cand = placement.placements[0]
                    match.actions.append(
                        PlannedAction(
                            action="move",
                            target_type="instance",
                            target_id=inst.id,
                            target_name=f"{dep.name}@{inst.node_id}",
                            deployment_id=dep.id,
                            spec={
                                "kind": "move",
                                "instance_id": inst.id,
                                "node_id": cand.node_id,
                                "device_ids": list(cand.device_ids),
                                "slice": cand.slice,
                                "order": "load_first",
                                "drain_timeout_seconds": int(
                                    params.get("drain_timeout_seconds", 30)
                                ),
                                "force_after_timeout": True,
                                "reason": "auto_heal:" + "+".join(reasons),
                            },
                        )
                    )
            matches.append(match)
    return Evaluation(matches=matches, considered=len(targets))


# ---------------------------------------------------------------------------
# time_schedule (the engine decides *when*; this builds the action)
# ---------------------------------------------------------------------------


async def evaluate_time_schedule(
    params, target, inputs: RuleInputs, ctx: EvalContext
) -> Evaluation:
    layout_id = uuid.UUID(str(params["layout_id"]))
    match = TargetMatch(
        deployment_id=layout_id,
        deployment_name=f"layout:{layout_id}",
        observed={
            "layout_id": str(layout_id),
            "at": params.get("at"),
            "days": params.get("days"),
            "cron": params.get("cron"),
            "timezone": params.get("timezone") or ctx.default_timezone,
        },
        actions=[
            PlannedAction(
                action="apply_layout",
                target_type="layout",
                target_id=layout_id,
                target_name=None,
                deployment_id=None,
                spec={
                    "kind": "apply_layout",
                    "layout_id": layout_id,
                    "drain_timeout_seconds": int(params.get("drain_timeout_seconds", 300)),
                },
            )
        ],
    )
    return Evaluation(matches=[match], considered=1)


_EVALUATORS = {
    "idle_unload": evaluate_idle_unload,
    "low_traffic_shrink": evaluate_low_traffic_shrink,
    "high_load_grow": evaluate_high_load_grow,
    "make_room_by_priority": evaluate_make_room_by_priority,
    "auto_heal": evaluate_auto_heal,
    "time_schedule": evaluate_time_schedule,
}

PRIORITY_RANK_EXPORT = PRIORITY_RANK
PlacementResultExport = PlacementResult
