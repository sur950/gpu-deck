"""
``RuleInputs`` — the adapter between the evaluators and the metric / state
tables (rules.md section 1 "Inputs").

The evaluators only see the plain dataclasses below, so unit tests feed
synthetic data through ``StaticRuleInputs`` and production uses
``DbRuleInputs`` which reads ``deployments``, ``instances``, ``models``,
``devices``, ``device_slices``, ``metric_rollups_engine_1m`` and
``metric_samples_engine`` through ``ClusterStateRepository``. When the metrics
metrics query service lands it can replace the SQL inside ``DbRuleInputs``
without touching the evaluators.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Protocol, Sequence

from app.core.logger import get_logger

logger = get_logger(__name__)

PRIORITY_RANK = {"always_on": 3, "normal": 2, "evictable": 1}


@dataclass
class InstanceSnapshot:
    id: uuid.UUID
    deployment_id: uuid.UUID
    node_id: uuid.UUID
    device_ids: List[uuid.UUID]
    state: str
    health: str
    ready_at: Optional[datetime] = None
    traffic_weight: int = 100
    restart_count: int = 0
    engine_process_id: Optional[uuid.UUID] = None
    served_model_name: Optional[str] = None
    port: Optional[int] = None
    active_sessions: int = 0
    waiting_sessions: int = 0

    @property
    def is_live(self) -> bool:
        return self.state in ("pending", "loading", "ready", "draining")

    @property
    def is_ready(self) -> bool:
        return self.state == "ready"


@dataclass
class DeploymentSnapshot:
    id: uuid.UUID
    name: str
    model_id: uuid.UUID
    model_name: str
    model_type: str
    priority: str  # effective: priority_override else models.priority
    status: str
    desired_running: bool
    replicas: int
    min_replicas: int
    max_replicas: int
    last_request_at: Optional[datetime]
    cluster_id: Optional[uuid.UUID]
    engine_name: str
    placement_strategy: str
    model_version_id: uuid.UUID
    model_size_bytes: int = 0
    gpu_count: int = 1
    slice_vram_bytes: Optional[int] = None
    slice_compute_pct: Optional[int] = None
    status_message: Optional[str] = None
    created_at: Optional[datetime] = None
    instances: List[InstanceSnapshot] = field(default_factory=list)
    node_labels: Dict[str, str] = field(default_factory=dict)
    # deployments.node_selector (node_ids / device_ids / labels), honoured by plan_placement
    node_selector: Dict[str, Any] = field(default_factory=dict)

    @property
    def priority_rank(self) -> int:
        return PRIORITY_RANK.get(self.priority, 2)

    @property
    def live_instances(self) -> List[InstanceSnapshot]:
        return [i for i in self.instances if i.is_live]

    @property
    def ready_instances(self) -> List[InstanceSnapshot]:
        return [i for i in self.instances if i.is_ready]

    @property
    def node_ids(self) -> List[uuid.UUID]:
        return sorted({i.node_id for i in self.live_instances}, key=str)

    @property
    def live_sessions(self) -> tuple:
        return (
            sum(i.active_sessions for i in self.live_instances),
            sum(i.waiting_sessions for i in self.live_instances),
        )


@dataclass
class DeviceSnapshot:
    id: uuid.UUID
    node_id: uuid.UUID
    node_name: str
    node_status: str
    vendor: str
    index: int
    name: str
    vram_total_bytes: int
    reserved_bytes: int = 0
    observed_used_bytes: Optional[int] = None
    health: str = "unknown"
    cluster_id: Optional[uuid.UUID] = None
    node_labels: Dict[str, str] = field(default_factory=dict)
    # Memory held by processes GPU Deck does not run (None = unknown), from the live sample.
    external_used_bytes: Optional[int] = None

    @property
    def used_bytes(self) -> int:
        """As the scheduler: max(reserved, GPU Deck's observed use) + memory used outside GPU
        Deck (``app.services.scheduler.usage.occupied_bytes``)."""
        from app.services.scheduler.usage import occupied_bytes

        return occupied_bytes(
            int(self.reserved_bytes or 0),
            self.observed_used_bytes,
            self.external_used_bytes,
        )

    @property
    def free_bytes(self) -> int:
        return max(0, int(self.vram_total_bytes) - self.used_bytes)

    @property
    def usable(self) -> bool:
        return self.node_status == "online" and self.health in ("healthy", "unknown")


@dataclass
class MinuteBucket:
    """One complete 1-minute bucket aggregated over a deployment (or one instance)."""

    ts: datetime
    requests: int = 0
    errors: int = 0
    waiting_avg: float = 0.0
    waiting_max: int = 0
    kv_pct_max: float = 0.0
    error_rate_pct: Optional[float] = None
    sample_count: int = 0


class RuleInputs(Protocol):
    """What evaluators need. Implementations must be cheap to call repeatedly in one tick."""

    now: datetime

    async def deployments(self) -> List[DeploymentSnapshot]: ...

    async def devices(self) -> List[DeviceSnapshot]: ...

    async def deployment_minutes(
        self, deployment_id: uuid.UUID, minutes: int
    ) -> List[MinuteBucket]:
        """Complete buckets for [now - minutes, now) aggregated over the deployment's instances."""
        ...

    async def instance_minutes(
        self, instance_id: uuid.UUID, minutes: int
    ) -> List[MinuteBucket]: ...

    async def current_minute_requests(self, deployment_id: uuid.UUID) -> int: ...

    async def health_failure_streak(self, instance_id: uuid.UUID) -> int: ...

    async def restarts_last_hour(self, instance_id: uuid.UUID) -> int: ...

    async def active_swap_count(self) -> int: ...

    async def cached_bytes_on_node(
        self, node_id: uuid.UUID, model_version_id: uuid.UUID
    ) -> int: ...


# ---------------------------------------------------------------------------
# Bucket helpers shared by both implementations
# ---------------------------------------------------------------------------


def minute_floor(ts: datetime) -> datetime:
    return ts.replace(second=0, microsecond=0)


def window_bounds(now: datetime, minutes: int) -> tuple:
    """Complete buckets in [now - M min, now): bucket ts in [floor(now) - M, floor(now))."""
    end = minute_floor(now)
    return end - timedelta(minutes=minutes), end


def aggregate_rollups(
    rows: Sequence[Dict[str, Any]],
    since: datetime,
    until: datetime,
    by_instance: bool = False,
) -> List[MinuteBucket]:
    """Sum/max engine rollup rows into one MinuteBucket per minute (missing minutes are absent)."""
    buckets: Dict[datetime, MinuteBucket] = {}
    for row in rows:
        ts = row["ts"]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ts = minute_floor(ts)
        if ts < since or ts >= until:
            continue
        bucket = buckets.get(ts)
        if bucket is None:
            bucket = buckets[ts] = MinuteBucket(ts=ts)
        bucket.requests += int(row.get("requests_delta") or 0)
        bucket.errors += int(row.get("errors_delta") or 0)
        bucket.waiting_avg += float(row.get("waiting_sessions_avg") or 0.0)
        bucket.waiting_max = max(bucket.waiting_max, int(row.get("waiting_sessions_max") or 0))
        bucket.kv_pct_max = max(
            bucket.kv_pct_max,
            float(row.get("kv_cache_usage_pct_max") or row.get("kv_cache_usage_pct_avg") or 0.0),
        )
        bucket.sample_count += int(row.get("sample_count") or 0)
    for bucket in buckets.values():
        bucket.error_rate_pct = (
            (bucket.errors / bucket.requests * 100.0) if bucket.requests > 0 else None
        )
    return [buckets[k] for k in sorted(buckets)]


# ---------------------------------------------------------------------------
# Static implementation (tests, dry runs with synthetic data)
# ---------------------------------------------------------------------------


class StaticRuleInputs:
    """In-memory inputs; every value is given explicitly."""

    def __init__(
        self,
        now: Optional[datetime] = None,
        deployments: Optional[List[DeploymentSnapshot]] = None,
        devices: Optional[List[DeviceSnapshot]] = None,
        deployment_minutes: Optional[Dict[uuid.UUID, List[MinuteBucket]]] = None,
        instance_minutes: Optional[Dict[uuid.UUID, List[MinuteBucket]]] = None,
        current_minute: Optional[Dict[uuid.UUID, int]] = None,
        health_streaks: Optional[Dict[uuid.UUID, int]] = None,
        restarts: Optional[Dict[uuid.UUID, int]] = None,
        active_swaps: int = 0,
        cached_bytes: Optional[Dict[tuple, int]] = None,
    ):
        self.now = now or datetime.now(timezone.utc)
        self._deployments = deployments or []
        self._devices = devices or []
        self._dep_minutes = deployment_minutes or {}
        self._inst_minutes = instance_minutes or {}
        self._current = current_minute or {}
        self._streaks = health_streaks or {}
        self._restarts = restarts or {}
        self._active_swaps = active_swaps
        self._cached = cached_bytes or {}

    async def deployments(self) -> List[DeploymentSnapshot]:
        return list(self._deployments)

    async def devices(self) -> List[DeviceSnapshot]:
        return list(self._devices)

    async def deployment_minutes(
        self, deployment_id: uuid.UUID, minutes: int
    ) -> List[MinuteBucket]:
        since, until = window_bounds(self.now, minutes)
        return [b for b in self._dep_minutes.get(deployment_id, []) if since <= b.ts < until]

    async def instance_minutes(self, instance_id: uuid.UUID, minutes: int) -> List[MinuteBucket]:
        since, until = window_bounds(self.now, minutes)
        return [b for b in self._inst_minutes.get(instance_id, []) if since <= b.ts < until]

    async def current_minute_requests(self, deployment_id: uuid.UUID) -> int:
        return int(self._current.get(deployment_id, 0))

    async def health_failure_streak(self, instance_id: uuid.UUID) -> int:
        return int(self._streaks.get(instance_id, 0))

    async def restarts_last_hour(self, instance_id: uuid.UUID) -> int:
        return int(self._restarts.get(instance_id, 0))

    async def active_swap_count(self) -> int:
        return self._active_swaps

    async def cached_bytes_on_node(self, node_id: uuid.UUID, model_version_id: uuid.UUID) -> int:
        return int(self._cached.get((node_id, model_version_id), 0))


# ---------------------------------------------------------------------------
# Database implementation
# ---------------------------------------------------------------------------


class DbRuleInputs:
    """Reads the schema directly through ClusterStateRepository; caches per tick."""

    def __init__(self, session, now: Optional[datetime] = None, swap_repo=None):
        from app.db.postgres.repositories.cluster_state_repository import (
            ClusterStateRepository,
        )

        self.session = session
        self.repo = ClusterStateRepository(session)
        self.now = now or datetime.now(timezone.utc)
        self._swap_repo = swap_repo
        self._deployments: Optional[List[DeploymentSnapshot]] = None
        self._devices: Optional[List[DeviceSnapshot]] = None
        self._rollup_rows: Optional[List[Dict[str, Any]]] = None
        self._current: Optional[Dict[str, int]] = None

    async def deployments(self) -> List[DeploymentSnapshot]:
        if self._deployments is not None:
            return self._deployments
        rows = await self.repo.deployments_with_models()
        dep_ids = [r["deployment"].id for r in rows]
        instances = await self.repo.live_instances(dep_ids)
        samples = {s["instance_id"]: s for s in await self.repo.latest_engine_samples(dep_ids)}
        node_labels = {d.node_id: d.node_labels for d in await self.devices()}
        by_dep: Dict[uuid.UUID, List[InstanceSnapshot]] = {}
        for inst in instances:
            sample = samples.get(inst.id) or samples.get(str(inst.id)) or {}
            by_dep.setdefault(inst.deployment_id, []).append(
                InstanceSnapshot(
                    id=inst.id,
                    deployment_id=inst.deployment_id,
                    node_id=inst.node_id,
                    device_ids=list(inst.device_ids or []),
                    state=inst.state,
                    health=inst.health,
                    ready_at=inst.ready_at,
                    traffic_weight=inst.traffic_weight,
                    restart_count=inst.restart_count or 0,
                    engine_process_id=inst.engine_process_id,
                    served_model_name=inst.served_model_name,
                    port=inst.port,
                    active_sessions=int(sample.get("active_sessions") or 0),
                    waiting_sessions=int(sample.get("waiting_sessions") or 0),
                )
            )
        out = []
        for row in rows:
            dep = row["deployment"]
            insts = by_dep.get(dep.id, [])
            labels: Dict[str, str] = {}
            for inst in insts:
                labels.update(node_labels.get(inst.node_id, {}) or {})
            out.append(
                DeploymentSnapshot(
                    id=dep.id,
                    name=dep.name,
                    model_id=dep.model_id,
                    model_name=row["model_name"],
                    model_type=row["model_type"],
                    priority=dep.priority_override or row["model_priority"],
                    status=dep.status,
                    desired_running=bool(dep.desired_running),
                    replicas=dep.replicas,
                    min_replicas=dep.min_replicas,
                    max_replicas=dep.max_replicas,
                    last_request_at=dep.last_request_at,
                    cluster_id=dep.cluster_id,
                    engine_name=row["engine_name"],
                    placement_strategy=dep.placement_strategy,
                    model_version_id=dep.model_version_id,
                    model_size_bytes=row["model_size_bytes"],
                    gpu_count=dep.gpu_count,
                    slice_vram_bytes=dep.slice_vram_bytes,
                    slice_compute_pct=dep.slice_compute_pct,
                    status_message=dep.status_message,
                    created_at=dep.created_at,
                    instances=insts,
                    node_labels=labels,
                    node_selector=dict(dep.node_selector or {}),
                )
            )
        self._deployments = out
        return out

    async def devices(self) -> List[DeviceSnapshot]:
        if self._devices is not None:
            return self._devices
        rows = await self.repo.devices_with_usage()
        self._devices = [
            DeviceSnapshot(
                id=r["id"],
                node_id=r["node_id"],
                node_name=r["node_name"],
                node_status=r["node_status"],
                vendor=r["vendor"],
                index=r["index"],
                name=r["name"],
                vram_total_bytes=int(r["vram_total_bytes"] or 0),
                reserved_bytes=int(r["reserved_bytes"] or 0),
                observed_used_bytes=r["observed_used_bytes"],
                health=r["health"],
                cluster_id=r["cluster_id"],
                node_labels=dict(r["node_labels"] or {}),
            )
            for r in rows
        ]
        await self._overlay_live_usage(self._devices)
        return self._devices

    async def _overlay_live_usage(self, devices: List[DeviceSnapshot]) -> None:
        """Fresh live samples win over the last stored row, and add the memory used outside GPU
        Deck (per-process list) so rule placements never plan into it."""
        try:
            from app.services.scheduler.usage import device_usage

            usage = await device_usage(self.session, [d.id for d in devices])
        except Exception as exc:  # noqa: BLE001 - stored samples still apply
            logger.debug(f"live device usage unavailable: {exc}")
            return
        for d in devices:
            u = usage.get(str(d.id))
            if u is None or not u.fresh:
                continue
            if u.used_bytes is not None:
                d.observed_used_bytes = u.used_bytes
            d.external_used_bytes = u.external_bytes

    async def _rollups(self) -> List[Dict[str, Any]]:
        if self._rollup_rows is None:
            deployments = await self.deployments()
            since, until = window_bounds(self.now, 24 * 60)
            self._rollup_rows = await self.repo.engine_rollups(
                [d.id for d in deployments], since, until
            )
        return self._rollup_rows

    async def deployment_minutes(
        self, deployment_id: uuid.UUID, minutes: int
    ) -> List[MinuteBucket]:
        since, until = window_bounds(self.now, minutes)
        rows = [r for r in await self._rollups() if str(r["deployment_id"]) == str(deployment_id)]
        return aggregate_rollups(rows, since, until)

    async def instance_minutes(self, instance_id: uuid.UUID, minutes: int) -> List[MinuteBucket]:
        since, until = window_bounds(self.now, minutes)
        rows = [r for r in await self._rollups() if str(r["instance_id"]) == str(instance_id)]
        return aggregate_rollups(rows, since, until)

    async def current_minute_requests(self, deployment_id: uuid.UUID) -> int:
        if self._current is None:
            deployments = await self.deployments()
            self._current = await self.repo.current_minute_requests([d.id for d in deployments])
        return int(self._current.get(str(deployment_id), 0))

    async def health_failure_streak(self, instance_id: uuid.UUID) -> int:
        streak = await self.repo.unhealthy_streak(instance_id)
        if streak == 0:
            # No samples yet (node offline / engine without metrics): fall back to the
            # instances.health flag kept by the hub.
            inst = await self.repo.get_instance(instance_id)
            if inst is not None and inst.health == "unhealthy":
                return 1
        return streak

    async def restarts_last_hour(self, instance_id: uuid.UUID) -> int:
        from app.db.postgres.repositories.swap_operation_repository import (
            SwapOperationRepository,
        )

        repo = self._swap_repo or SwapOperationRepository(self.session)
        return await repo.restarts_last_hour(instance_id, self.now)

    async def active_swap_count(self) -> int:
        from app.db.postgres.repositories.swap_operation_repository import (
            SwapOperationRepository,
        )

        repo = self._swap_repo or SwapOperationRepository(self.session)
        return await repo.count_active()

    async def cached_bytes_on_node(self, node_id: uuid.UUID, model_version_id: uuid.UUID) -> int:
        return await self.repo.cached_bytes_on_node(node_id, model_version_id)
