"""
Deployment service: deployments CRUD, instances, placement previews, scale /
restart / stop / start / traffic, and the instance-level API the swap manager
 uses.

Public API for other services (stable signatures)::

    svc = DeploymentService.for_session(db, hub=None)

    inst = await svc.place_instance(deployment_id, node_id=None, device_ids=None,
                                    slice_spec=None, exclude_node_ids=(), pd_role=None,
                                    traffic_weight=100)            # -> Instances row, state pending
    cmd  = await svc.start_instance(instance_id, swap_operation_id=None)  # -> load_model cmd id
    cmd  = await svc.stop_instance(instance_id, force=False,
                                   drain_timeout_seconds=None,
                                   swap_operation_id=None)  # -> unload_model cmd id | None

``place_instance`` only reserves (scheduler + ``device_slices``); nothing is
sent to the agent until ``start_instance``. ``stop_instance`` on a ``pending``
instance just marks it stopped (returns None). Command outcomes are applied by
the reconciler (``reconcile.py``) from ``agent_commands``; callers that need to
block can wait on the ``AgentHub`` result instead.

The reconciliation loop (desired replicas -> instances) lives in
``reconcile.py`` and runs in the jobs process.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.exceptions import ConflictError, ResourceNotFoundError, ValidationError
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_db
from app.db.postgres.models.generated_models import (
    Deployments,
    Engines,
    EngineVersions,
    Instances,
    Models,
    ModelVersions,
    Nodes,
)
from app.db.postgres.repositories.deployment_repository import DeploymentRepository
from app.db.postgres.repositories.engine_repository import EngineVersionRepository
from app.db.postgres.repositories.instance_repository import (
    DeviceSliceRepository,
    InstanceRepository,
)
from app.db.postgres.repositories.model_repository import (
    ModelFileRepository,
    ModelRepository,
    ModelVersionRepository,
)
from app.db.postgres.repositories.placement_inventory_repository import (
    PlacementInventoryRepository,
)
from app.schemas.deployments_schema import (
    Deployment,
    DeploymentCreate,
    DeploymentSpec,
    DeploymentUpdate,
    ExternalInfo,
    Instance,
    InstanceDrainRequest,
    InstanceLive,
    InstanceUpdate,
    PlacementCandidate,
    PlacementPreview,
    PlacementPreviewRequest,
    PlacementRejection,
    ScaleRequest,
    TrafficWeightsRequest,
)
from app.schemas.engines_schema import CompatCheck
from app.schemas.model_cache_schema import CommandRefs
from app.services.catalog import detect
from app.services.deployments import launch as L
from app.services.deployments.agent_hub_port import AgentHubPort, get_agent_hub
from app.services.deployments.external import (
    ENGINE_LABELS,
    ENGINE_NAMES,
    ensure_managed,
    is_external,
)
from app.services.deployments.states import ensure_transition
from app.services.engines import compat
from app.services.engines.presets import preset_concurrency
from app.services.engines.vram import (
    GIB,
    LLAMA_CPP_DEFAULT_CONTEXT,
    LLAMA_CPP_DEFAULT_UBATCH,
    VramEstimate,
    device_memory_fraction,
    device_need_bytes,
    estimate_vram,
    fraction_base_bytes,
    llama_cpp_kv_cells,
    llama_cpp_ubatch,
)
from app.services.scheduler.core import PlacementPlan, PlacementRequest
from app.services.scheduler.service import SchedulerService
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# Fields whose change needs every instance replaced (rolling update).
SPEC_FIELDS = (
    "model_version_id",
    "engine_version_id",
    "gpu_count",
    "slice_vram_bytes",
    "slice_compute_pct",
    "tp_size",
    "pp_size",
    "preset",
    "pd_split",
    "pd_config",
    "spec_decoding",
    "kv_sharing",
    "kv_sharing_config",
    "launch_flags",
    "env_vars",
    "tool_parser",
    "context_size",
    "control_vectors",
)
DEFAULT_TRAFFIC = {
    "strategy": "least_sessions",
    "failover": True,
    "max_retries": 1,
    "health_eject": True,
}
VRAM_ONLY_REASONS = {"insufficient_vram", "vram_unverified", "vram_tight"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enum(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def spec_hash(dep: Any) -> str:
    """Stable hash of the fields that define what an instance runs."""
    payload = {f: _enum(getattr(dep, f, None)) for f in SPEC_FIELDS}
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def role_targets(dep: Any) -> Dict[Optional[str], int]:
    """Desired instances per PD role (``{None: replicas}`` without PD split)."""
    replicas = int(dep.replicas or 0)
    if not dep.desired_running or getattr(dep, "deleted_at", None) is not None or replicas <= 0:
        return {None: 0} if not dep.pd_split else {"prefill": 0, "decode": 0}
    if dep.pd_split:
        cfg = dep.pd_config or {}
        return {
            "prefill": max(int(cfg.get("prefill_replicas", 1) or 0), 0),
            "decode": max(int(cfg.get("decode_replicas", replicas) or 0), 0),
        }
    return {None: replicas}


PP_RPC_PORT = 50052  # llama.cpp rpc-server default


def node_ranks(shards: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Pipeline stages -> one rank per node (the agent runs one process per instance and
    node): stages on the same node merge their devices; ranks follow the first stage
    of each node, the head (stage 0) node is rank 0.
    """
    out: List[Dict[str, Any]] = []
    by_node: Dict[str, Dict[str, Any]] = {}
    for shard in sorted(shards, key=lambda x: int(x.get("pp_rank") or 0)):
        key = str(shard["node_id"])
        if key not in by_node:
            by_node[key] = {"node_id": key, "device_ids": [], "pp_rank": len(out)}
            out.append(by_node[key])
        by_node[key]["device_ids"].extend(str(d) for d in shard.get("device_ids") or [])
    return out


def pp_master_port(instance_id: uuid.UUID) -> int:
    """Deterministic rendezvous port per group (29500-29899) so restarts reuse it."""
    return 29500 + (uuid.UUID(str(instance_id)).int % 400)


def _audit(event_type: str, **kwargs: Any) -> None:
    try:
        from app.services.audit import audit

        audit.record(event_type, **kwargs)
    except Exception as exc:  # noqa: BLE001 - audit never breaks a request
        logger.debug(f"audit {event_type} skipped: {exc}")


async def _publish(session: Optional[AsyncSession] = None) -> None:
    try:
        from app.services.deployments.routing_publisher import publish_routing_table

        await publish_routing_table(session)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"routing publish skipped: {exc}")


class DeploymentContext:
    """Rows needed to place / launch one deployment."""

    def __init__(
        self,
        dep: Deployments,
        model: Models,
        version: ModelVersions,
        engine_version: EngineVersions,
        engine: Engines,
    ) -> None:
        self.dep = dep
        self.model = model
        self.version = version
        self.engine_version = engine_version
        self.engine = engine

    @property
    def priority(self) -> str:
        return self.dep.priority_override or self.model.priority or "normal"


class DeploymentService:
    """Deployments + instances + placement."""

    def __init__(
        self,
        session: AsyncSession,
        deployments: DeploymentRepository,
        instances: InstanceRepository,
        slices: DeviceSliceRepository,
        models: ModelRepository,
        versions: ModelVersionRepository,
        files: ModelFileRepository,
        engine_versions: EngineVersionRepository,
        inventory: PlacementInventoryRepository,
        scheduler: SchedulerService,
        hub: Optional[AgentHubPort] = None,
        publish: bool = True,
    ) -> None:
        self.session = session
        self.deployments = deployments
        self.instances = instances
        self.slices = slices
        self.models = models
        self.versions = versions
        self.files = files
        self.engine_versions = engine_versions
        self.inventory = inventory
        self.scheduler = scheduler
        self._hub = hub
        self.publish_enabled = publish

    @property
    def hub(self) -> AgentHubPort:
        if self._hub is None:
            self._hub = get_agent_hub(self.session)
        return self._hub

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db, scope="function")
    ) -> "DeploymentService":
        return DeploymentService.for_session(db)

    @classmethod
    def for_session(
        cls, db: AsyncSession, hub: Optional[AgentHubPort] = None, publish: bool = True
    ) -> "DeploymentService":
        inventory = PlacementInventoryRepository(db)
        return cls(
            db,
            DeploymentRepository(db),
            InstanceRepository(db),
            DeviceSliceRepository(db),
            ModelRepository(db),
            ModelVersionRepository(db),
            ModelFileRepository(db),
            EngineVersionRepository(db),
            inventory,
            SchedulerService(db, inventory),
            hub,
            publish,
        )

    async def publish(self) -> None:
        if self.publish_enabled:
            await _publish(self.session)

    # ================================================================ views

    async def list_deployments(self, **filters: Any) -> Tuple[List[Deployment], int]:
        rows, total = await self.deployments.list_deployments(**filters)
        return await self._deployments_out(rows), total

    async def get_deployment(self, deployment_id: uuid.UUID) -> Deployment:
        dep = await self._deployment_or_404(deployment_id)
        return (await self._deployments_out([dep]))[0]

    async def list_instances(self, **filters: Any) -> Tuple[List[Instance], int]:
        rows, total = await self.instances.list_instances(**filters)
        return await self._instances_out(rows), total

    async def get_instance(self, instance_id: uuid.UUID) -> Instance:
        row = await self.instances.get_joined(instance_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Instance")
        return (await self._instances_out([row]))[0]

    async def instance_sessions(self, instance_id: uuid.UUID) -> Dict[str, Any]:
        inst = await self._instance_or_404(instance_id)
        from app.services.deployments import live

        sessions: List[Dict[str, Any]] = []
        engine = (await live.samples("engine", [inst.id]))[0] or {}
        raw = (await live.samples("sessions", [inst.id]))[0]
        if isinstance(raw, dict):
            sessions = list(raw.get("sessions") or [])
        active = engine.get("active_sessions")
        waiting = engine.get("waiting_sessions")
        if active is None:
            active = sum(1 for s in sessions if s.get("state") in ("processing", "active"))
        if waiting is None:
            waiting = sum(1 for s in sessions if s.get("state") in ("waiting", "queued"))
        return {
            "ts": engine.get("ts") or utcnow().isoformat(),
            "instance_id": str(inst.id),
            "active": int(active or 0),
            "waiting": int(waiting or 0),
            "slots_total": engine.get("slots_total"),
            "sessions": sessions,
        }

    async def list_swaps(
        self, deployment_id: uuid.UUID, page: int, page_size: int
    ) -> Tuple[List[Dict[str, Any]], int]:
        await self._deployment_or_404(deployment_id)
        rows, total = await self.deployments.list_swaps(deployment_id, page, page_size)
        out = []
        for r in rows:
            out.append(
                {
                    "id": r.id,
                    "kind": r.kind,
                    "state": r.state,
                    "trigger": r.trigger,
                    "rule_run_id": r.rule_run_id,
                    "layout_id": r.layout_id,
                    "deployment_id": r.deployment_id,
                    "from_instance_id": r.from_instance_id,
                    "to_instance_id": r.to_instance_id,
                    "node_id": r.node_id,
                    "plan": r.plan or {},
                    "steps": r.steps or [],
                    "current_step": r.current_step,
                    "rollback": r.rollback or {},
                    "drain_timeout_seconds": r.drain_timeout_seconds,
                    "dry_run": r.dry_run,
                    "started_at": r.started_at,
                    "finished_at": r.finished_at,
                    "error_message": r.error_message,
                    "created_by": r.created_by,
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                }
            )
        return out, total

    # ============================================================ create

    async def create_deployment(
        self, data: DeploymentCreate, created_by: Optional[uuid.UUID] = None
    ) -> Deployment | PlacementPreview:
        if await self.deployments.get_by_name(data.name):
            raise ConflictError(f"A deployment named {data.name!r} already exists")
        model, version = await self._model_and_version(data.model_id, data.model_version_id)
        ev, engine, outcome = await self._resolve_engine(model, version, data)
        values = self._values_from_spec(data, model, version, ev, engine)
        if data.dry_run:
            transient = Deployments(id=uuid.uuid4(), **values)
            transient.desired_running = True
            transient.deleted_at = None
            ctx = DeploymentContext(transient, model, version, ev, engine)
            return await self._preview_for(ctx, int(data.replicas), outcome)
        start = bool(data.start) and int(data.replicas) > 0
        dep = await self.deployments.create(
            **values,
            status="scheduling" if start else "stopped",
            desired_running=start,
            created_by=created_by,
        )
        _audit(
            "deployment.created",
            resource_type="deployment",
            resource_id=dep.id,
            resource_name=dep.name,
            after_state={
                "model": model.name,
                "engine": engine.name,
                "replicas": dep.replicas,
            },
        )
        if start:
            from app.services.deployments.reconcile import Reconciler

            await Reconciler(self).reconcile_deployment(dep.id)
        await self.publish()
        return await self.get_deployment(dep.id)

    async def _model_and_version(
        self, model_id: uuid.UUID, version_id: Optional[uuid.UUID]
    ) -> Tuple[Models, ModelVersions]:
        model = await self.models.get_active(model_id)
        if model is None:
            raise ResourceNotFoundError(resource_type="Model")
        if version_id is not None:
            version = await self.versions.get_active(version_id)
            if version is None or version.model_id != model.id:
                raise ResourceNotFoundError(resource_type="Model version")
        else:
            version = await self.versions.get_default(model.id)
            if version is None:
                raise ValidationError(f"Model {model.name!r} has no versions")
        if version.status in ("blocked", "missing"):
            raise ConflictError(
                f"Model version {version.version!r} is {version.status}: "
                f"{version.status_message or 'fix or re-verify it first'}"
            )
        return model, version

    async def _resolve_engine(
        self, model: Models, version: ModelVersions, spec: Any
    ) -> Tuple[EngineVersions, Engines, Optional[compat.CompatOutcome]]:
        from app.services.engines.service import EngineService

        engines = EngineService.for_session(self.session)
        shape = compat.Shape(
            gpu_count=int(spec.gpu_count or 1),
            tp_size=int(spec.tp_size or 1),
            pp_size=int(spec.pp_size or 1),
            context_size=spec.context_size,
            preset=_enum(spec.preset),
            slice_vram_bytes=spec.slice_vram_bytes,
            pd_split=bool(spec.pd_split),
            spec_decoding=bool((spec.spec_decoding or {}).get("enabled")),
            kv_sharing=bool(spec.kv_sharing),
            control_vectors=bool(spec.control_vectors),
            launch_flags=dict(spec.launch_flags or {}),
        )
        outcomes = await engines.evaluate_engines(
            model,
            version,
            cluster_id=spec.cluster_id,
            shape=shape,
            engine_version_id=spec.engine_version_id,
        )
        if spec.engine_version_id is not None:
            outcome = outcomes[0] if outcomes else None
            if outcome is None:
                raise ResourceNotFoundError(resource_type="Engine version")
        else:
            outcome = compat.auto_pick(outcomes)
            if outcome is None:
                outcome = _first_vram_only(outcomes)
            if outcome is None:
                reasons = sorted(
                    {r["message"] for o in outcomes for r in o.reasons if r["severity"] == "error"}
                )
                raise ValidationError(
                    "No compatible engine for this model: " + "; ".join(reasons[:6])
                )
        hard = [
            r
            for r in outcome.reasons
            if r["severity"] == "error" and r["code"] not in VRAM_ONLY_REASONS
        ]
        if hard:
            raise ValidationError(
                f"{outcome.engine.name} {outcome.engine.version} is incompatible: "
                + "; ".join(r["message"] for r in hard)
            )
        pair = await self.engine_versions.get_with_engine(
            uuid.UUID(outcome.engine.engine_version_id)
        )
        if pair is None:
            raise ResourceNotFoundError(resource_type="Engine version")
        return pair[0], pair[1], outcome

    def _values_from_spec(
        self,
        data: DeploymentSpec,
        model: Models,
        version: ModelVersions,
        ev: EngineVersions,
        engine: Engines,
    ) -> Dict[str, Any]:
        _validate_shape(data, engine)
        parser = data.tool_parser
        if parser is None:
            parser = model.tool_parser_default if engine.name == "llama_cpp" else None
            parser = parser or detect.default_tool_parser(model.family, engine.name)
        traffic = {**DEFAULT_TRAFFIC, **(data.traffic_config or {})}
        return {
            "name": data.name,
            "description": data.description,
            "model_id": model.id,
            "model_version_id": version.id,
            "engine_version_id": ev.id,
            "cluster_id": data.cluster_id,
            "priority_override": _enum(data.priority_override),
            "replicas": int(data.replicas),
            "min_replicas": int(data.min_replicas),
            "max_replicas": int(data.max_replicas),
            "gpu_count": int(data.gpu_count),
            "slice_vram_bytes": data.slice_vram_bytes,
            "slice_compute_pct": (
                data.slice_compute_pct
                if data.slice_compute_pct is not None
                else (100 if data.slice_vram_bytes else None)
            ),
            "tp_size": int(data.tp_size),
            "pp_size": int(data.pp_size),
            "preset": _enum(data.preset),
            "pd_split": bool(data.pd_split),
            "pd_config": dict(data.pd_config or {}),
            "spec_decoding": dict(data.spec_decoding or {}),
            "kv_sharing": bool(data.kv_sharing),
            "kv_sharing_config": dict(data.kv_sharing_config or {}),
            "launch_flags": dict(data.launch_flags or {}),
            "env_vars": dict(data.env_vars or {}),
            "tool_parser": parser,
            "context_size": data.context_size or model.default_context_size,
            "control_vectors": list(data.control_vectors or []),
            "placement_strategy": _enum(data.placement_strategy),
            "node_selector": dict(data.node_selector or {}),
            "device_vendor_filter": [_enum(v) for v in data.device_vendor_filter or []],
            "traffic_config": traffic,
        }

    # ============================================================ update

    async def update_deployment(
        self, deployment_id: uuid.UUID, data: DeploymentUpdate
    ) -> Deployment:
        dep = await self._managed_deployment_or_404(deployment_id)
        changes = data.model_dump(exclude_unset=True)
        if "name" in changes and changes["name"].lower() != dep.name.lower():
            if await self.deployments.get_by_name(changes["name"]):
                raise ConflictError(f"A deployment named {changes['name']!r} already exists")
        before = {f: _enum(getattr(dep, f)) for f in changes if hasattr(dep, f)}
        merged = DeploymentSpec.model_validate(
            {
                **_spec_dict(dep),
                **{k: v for k, v in changes.items()},
                "max_replicas": changes.get(
                    "max_replicas",
                    max(dep.max_replicas, changes.get("replicas", dep.replicas)),
                ),
            }
        )
        spec_changed = any(f in changes and before.get(f) != _enum(changes[f]) for f in SPEC_FIELDS)
        model, version = await self._model_and_version(dep.model_id, merged.model_version_id)
        if spec_changed or "engine_version_id" in changes:
            ev, engine, _ = await self._resolve_engine(model, version, merged)
        else:
            pair = await self.engine_versions.get_with_engine(dep.engine_version_id)
            if pair is None:
                raise ResourceNotFoundError(resource_type="Engine version")
            ev, engine = pair
        values = self._values_from_spec(merged, model, version, ev, engine)
        values.pop("model_id", None)
        if "traffic_config" not in changes:
            values["traffic_config"] = dep.traffic_config
        await self.deployments.update(dep.id, **values)
        new_hash = spec_hash(await self._deployment_or_404(dep.id))
        _audit(
            "deployment.updated",
            resource_type="deployment",
            resource_id=dep.id,
            resource_name=values.get("name", dep.name),
            before_state=_jsonable(before),
            after_state=_jsonable({k: values.get(k) for k in changes if k in values}),
            metadata={"rolling_update": spec_changed, "spec_hash": new_hash},
        )
        if spec_changed:
            await self.deployments.set_status(dep.id, "scaling", "rolling update in progress")
            await self._reset_crashloop(dep.id)
        if "traffic_config" in changes or spec_changed or "replicas" in changes:
            from app.services.deployments.reconcile import Reconciler

            await Reconciler(self).reconcile_deployment(dep.id)
        await self.publish()
        return await self.get_deployment(dep.id)

    # =========================================================== actions

    async def delete_deployment(self, deployment_id: uuid.UUID, force: bool = False) -> CommandRefs:
        dep = await self._managed_deployment_or_404(deployment_id)
        live = await self.instances.for_deployment(dep.id)
        await self.deployments.soft_delete(dep.id)
        cmds = []
        for inst in live:
            if inst.state in ("unloading",):
                continue
            cmd = await self.stop_instance(
                inst.id,
                force=force,
                drain_timeout_seconds=(
                    0 if force else settings.DEPLOYMENT_UNLOAD_DRAIN_TIMEOUT_SECONDS
                ),
            )
            if cmd:
                cmds.append(cmd)
        if not await self.instances.for_deployment(dep.id):
            await self.deployments.set_status(dep.id, "stopped", "deleted")
        _audit(
            "deployment.deleted",
            resource_type="deployment",
            resource_id=dep.id,
            resource_name=dep.name,
            metadata={"force": force, "instances": len(live)},
        )
        await self.publish()
        return CommandRefs(command_ids=cmds)

    async def scale(self, deployment_id: uuid.UUID, req: ScaleRequest) -> CommandRefs:
        dep = await self._managed_deployment_or_404(deployment_id)
        replicas = int(req.replicas)
        if replicas < dep.min_replicas:
            raise ValidationError(f"replicas {replicas} is below min_replicas {dep.min_replicas}")
        values: Dict[str, Any] = {
            "replicas": replicas,
            "max_replicas": max(dep.max_replicas, replicas, 1),
        }
        if req.placement_strategy is not None:
            values["placement_strategy"] = _enum(req.placement_strategy)
        if replicas > 0 and not dep.desired_running:
            values["desired_running"] = True
        await self.deployments.update(dep.id, **values)
        _audit(
            "deployment.scaled",
            resource_type="deployment",
            resource_id=dep.id,
            resource_name=dep.name,
            before_state={"replicas": dep.replicas},
            after_state={"replicas": replicas},
        )
        from app.services.deployments.reconcile import Reconciler

        cmds = await Reconciler(self).reconcile_deployment(
            dep.id, drain_timeout_seconds=req.drain_timeout_seconds
        )
        await self.publish()
        return CommandRefs(command_ids=cmds)

    async def stop(self, deployment_id: uuid.UUID) -> CommandRefs:
        dep = await self._managed_deployment_or_404(deployment_id)
        await self.deployments.update(dep.id, desired_running=False)
        cmds = []
        for inst in await self.instances.for_deployment(dep.id):
            if inst.state == "unloading":
                continue
            cmd = await self.stop_instance(
                inst.id,
                drain_timeout_seconds=settings.DEPLOYMENT_UNLOAD_DRAIN_TIMEOUT_SECONDS,
            )
            if cmd:
                cmds.append(cmd)
        remaining = await self.instances.for_deployment(dep.id)
        await self.deployments.set_status(
            dep.id,
            "scaling" if remaining else "stopped",
            "stopping" if remaining else None,
        )
        _audit(
            "deployment.stopped",
            resource_type="deployment",
            resource_id=dep.id,
            resource_name=dep.name,
        )
        await self.publish()
        return CommandRefs(command_ids=cmds)

    async def start(self, deployment_id: uuid.UUID) -> CommandRefs:
        dep = await self._managed_deployment_or_404(deployment_id)
        values: Dict[str, Any] = {"desired_running": True}
        if dep.replicas == 0:
            values["replicas"] = max(1, dep.min_replicas)
            values["max_replicas"] = max(dep.max_replicas, values["replicas"])
        await self.deployments.update(dep.id, **values)
        await self.deployments.set_status(dep.id, "scheduling", None)
        await self._reset_crashloop(dep.id)
        _audit(
            "deployment.started",
            resource_type="deployment",
            resource_id=dep.id,
            resource_name=dep.name,
        )
        from app.services.deployments.reconcile import Reconciler

        cmds = await Reconciler(self).reconcile_deployment(dep.id)
        await self.publish()
        return CommandRefs(command_ids=cmds)

    async def restart(self, deployment_id: uuid.UUID, req: InstanceDrainRequest) -> CommandRefs:
        """Rolling restart: restart one instance now, the reconciler does the rest one by one."""
        dep = await self._managed_deployment_or_404(deployment_id)
        live = [
            i for i in await self.instances.for_deployment(dep.id) if i.state in ("ready", "error")
        ]
        if not live:
            raise ConflictError("Deployment has no running instances to restart")
        stamp = utcnow().isoformat()
        for inst in live[1:]:
            await self.instances.merge_resources(
                inst.id,
                {
                    "restart_requested_at": stamp,
                    "restart_drain_timeout": req.timeout_seconds,
                },
            )
        cmd = await self.restart_instance(live[0].id, req)
        _audit(
            "deployment.restarted",
            resource_type="deployment",
            resource_id=dep.id,
            resource_name=dep.name,
            metadata={"instances": len(live)},
        )
        return cmd

    async def set_traffic(self, deployment_id: uuid.UUID, req: TrafficWeightsRequest) -> Deployment:
        dep = await self._managed_deployment_or_404(deployment_id)
        live = {i.id: i for i in await self.instances.for_deployment(dep.id)}
        unknown = [str(i) for i in req.weights if i not in live]
        if unknown:
            raise ValidationError(f"Instances not in this deployment: {', '.join(unknown)}")
        for iid, weight in req.weights.items():
            await self.instances.set_fields(iid, traffic_weight=int(weight))
        if req.strategy is not None:
            traffic = {
                **DEFAULT_TRAFFIC,
                **(dep.traffic_config or {}),
                "strategy": _enum(req.strategy),
            }
            await self.deployments.update(dep.id, traffic_config=traffic)
        _audit(
            "deployment.traffic_updated",
            resource_type="deployment",
            resource_id=dep.id,
            resource_name=dep.name,
            after_state={
                "weights": {str(k): v for k, v in req.weights.items()},
                "strategy": _enum(req.strategy),
            },
        )
        await self.publish()
        return await self.get_deployment(dep.id)

    # ---------------------------------------------------- instance actions

    async def update_instance(self, instance_id: uuid.UUID, data: InstanceUpdate) -> Instance:
        inst = await self._managed_instance_or_404(instance_id)
        if data.traffic_weight is not None:
            await self.instances.set_fields(inst.id, traffic_weight=int(data.traffic_weight))
            _audit(
                "instance.weight_changed",
                resource_type="instance",
                resource_id=inst.id,
                before_state={"traffic_weight": inst.traffic_weight},
                after_state={"traffic_weight": data.traffic_weight},
            )
        await self.publish()
        return await self.get_instance(inst.id)

    async def remove_instance(self, instance_id: uuid.UUID, force: bool = False) -> CommandRefs:
        """DELETE /instances/{id}: unload it and reduce the deployment's replicas by one."""
        inst = await self._managed_instance_or_404(instance_id)
        dep = await self.deployments.get_any(inst.deployment_id)
        if (
            dep is not None
            and dep.deleted_at is None
            and inst.state in ("pending", "loading", "ready", "error")
        ):
            replicas = max(int(dep.replicas) - 1, 0)
            if replicas < dep.min_replicas:
                raise ConflictError(
                    f"Removing this instance would go below min_replicas ({dep.min_replicas}); "
                    "lower min_replicas or scale instead"
                )
            await self.deployments.update(dep.id, replicas=replicas)
        cmd = await self.stop_instance(
            inst.id,
            force=force,
            drain_timeout_seconds=(
                0 if force else settings.DEPLOYMENT_UNLOAD_DRAIN_TIMEOUT_SECONDS
            ),
        )
        await self.publish()
        return CommandRefs(command_ids=[cmd] if cmd else [])

    async def drain_instance(
        self, instance_id: uuid.UUID, req: InstanceDrainRequest
    ) -> CommandRefs:
        inst = await self._managed_instance_or_404(instance_id)
        then = _enum(req.then)
        if then == "unload":
            return await self.remove_instance(inst.id)
        if then == "restart":
            return await self.restart_instance(inst.id, req)
        ensure_transition(inst.state, "draining")
        await self.instances.set_state(inst.id, "draining", "drain requested")
        await self.instances.release_connection()
        await self._set_accepting(inst.id, False)
        cmd = await self.hub.send_command(
            inst.node_id,
            "drain_instance",
            {
                "instance_id": str(inst.id),
                "timeout_seconds": req.timeout_seconds,
                "wait_for_zero": True,
                "resume": False,
            },
            instance_id=inst.id,
            timeout_seconds=req.timeout_seconds + 60,
        )
        await self.instances.merge_resources(inst.id, {"drain_command_id": str(cmd)})
        _audit("instance.drained", resource_type="instance", resource_id=inst.id)
        await self.publish()
        return CommandRefs(command_ids=[cmd])

    async def restart_instance(
        self, instance_id: uuid.UUID, req: InstanceDrainRequest
    ) -> CommandRefs:
        inst = await self._managed_instance_or_404(instance_id)
        if inst.state in ("pending",):
            raise ConflictError("INVALID_STATE: instance has not been started yet")
        ensure_transition(inst.state, "loading")
        await self.instances.set_state(
            inst.id,
            "loading",
            "restarting",
            restart_count=int(inst.restart_count or 0) + 1,
            started_at=utcnow(),
            ready_at=None,
        )
        await self.instances.release_connection()
        cmd = await self.hub.send_command(
            inst.node_id,
            "restart_instance",
            {
                "instance_id": str(inst.id),
                "drain_timeout_seconds": req.timeout_seconds,
                "force": False,
            },
            instance_id=inst.id,
            timeout_seconds=req.timeout_seconds + int(settings.DEPLOYMENT_LOAD_TIMEOUT_SECONDS),
        )
        resources = dict((await self._instance_or_404(inst.id)).resources or {})
        resources.pop("restart_requested_at", None)
        resources.update(last_command_id=str(cmd), last_command="restart_instance")
        await self.instances.set_fields(inst.id, resources=resources)
        _audit("instance.restarted", resource_type="instance", resource_id=inst.id)
        await self.publish()
        return CommandRefs(command_ids=[cmd])

    # ======================================================= preview

    async def preview(self, req: PlacementPreviewRequest) -> PlacementPreview:
        if req.deployment_id is not None:
            dep = await self._deployment_or_404(req.deployment_id)
            ctx = await self.context(dep)
            live = await self.instances.for_deployment(dep.id, ("pending", "loading", "ready"))
            replicas = req.replicas if req.replicas is not None else dep.replicas
            extra = max(int(replicas) - len(live), 0)
            return await self._preview_for(ctx, extra, None, existing=live)
        spec = req.deployment
        assert spec is not None
        model, version = await self._model_and_version(spec.model_id, spec.model_version_id)
        ev, engine, outcome = await self._resolve_engine(model, version, spec)
        values = self._values_from_spec(spec, model, version, ev, engine)
        transient = Deployments(id=uuid.uuid4(), **values)
        transient.desired_running = True
        transient.deleted_at = None
        ctx = DeploymentContext(transient, model, version, ev, engine)
        replicas = req.replicas if req.replicas is not None else spec.replicas
        return await self._preview_for(ctx, int(replicas), outcome)

    async def _preview_for(
        self,
        ctx: DeploymentContext,
        replicas: int,
        outcome: Optional[compat.CompatOutcome],
        existing: Sequence[Instances] = (),
    ) -> PlacementPreview:
        estimate = self.estimate(ctx)
        req = self.placement_request(
            ctx,
            estimate,
            replicas,
            existing=existing,
            blocked_nodes=await self.blocked_nodes(ctx),
        )
        nodes = await self.scheduler.snapshot(ctx.dep.cluster_id)
        plan = await self.scheduler.plan(req, nodes=nodes)
        would_evict: List[Instance] = []
        if not plan.feasible:
            victims = await self.scheduler.evictions(req, ctx.priority, nodes=nodes)
            if victims:
                joined = [await self.instances.get_joined(v.instance_id) for v in victims]
                would_evict = await self._instances_out([j for j in joined if j])
        return PlacementPreview(
            feasible=plan.feasible,
            placements=await self._candidates_out(plan, ctx, estimate),
            rejected=[
                PlacementRejection(node_id=r.node_id, device_ids=r.device_ids, reason=r.reason)
                for r in plan.rejected
            ],
            would_evict=would_evict,
            compat=_compat_out(outcome, ctx) if outcome else None,
        )

    async def _candidates_out(
        self, plan: PlacementPlan, ctx: DeploymentContext, estimate: VramEstimate
    ) -> List[PlacementCandidate]:
        device_ids = [d for a in plan.assignments for d in a.all_device_ids()]
        devices = {d.id: d for d in await self.inventory.devices_by_ids(device_ids)}
        nodes = await self.inventory.nodes_by_ids([a.node_id for a in plan.assignments])
        flags = L.effective_flags(ctx.dep, ctx.model, ctx.engine, ctx.engine_version)
        fraction = device_memory_fraction(ctx.engine.name, flags)
        out = []
        for a in plan.assignments:
            node = nodes.get(a.node_id)
            need = estimate.per_gpu_bytes
            reasons = list(a.reasons)
            if a.slice:
                need = int(a.slice["vram_bytes"])
            elif fraction:
                totals = [
                    int(devices[d].vram_total_bytes or 0)
                    for d in a.all_device_ids()
                    if d in devices
                ]
                if totals:
                    need = device_need_bytes(ctx.engine.name, fraction, estimate, max(totals))
                    if need > estimate.per_gpu_bytes:
                        share = (
                            "the memory left after weights"
                            if fraction_base_bytes(ctx.engine.name, estimate)
                            else "the device"
                        )
                        reasons.append(
                            f"{ctx.engine.name} pre-allocates {fraction:g} of {share}: "
                            f"{need / GIB:.1f} GiB per device (model estimate "
                            f"{estimate.per_gpu_bytes / GIB:.1f} GiB)"
                        )
            out.append(
                PlacementCandidate(
                    node_id=a.node_id,
                    node_name=node.name if node else None,
                    device_ids=a.all_device_ids(),
                    device_names=[devices[d].name for d in a.all_device_ids() if d in devices],
                    slice=a.slice,
                    engine_version_id=ctx.engine_version.id,
                    score=a.score,
                    estimated_vram_bytes=need,
                    free_vram_bytes_after=a.free_after_bytes,
                    reasons=reasons,
                )
            )
        return out

    # ======================================================= placement API

    def estimate(self, ctx: DeploymentContext) -> VramEstimate:
        dep, model, mv, engine, ev = (
            ctx.dep,
            ctx.model,
            ctx.version,
            ctx.engine,
            ctx.engine_version,
        )
        flags = L.effective_flags(dep, model, engine, ev)
        conc = preset_concurrency(dep.preset, flags, engine.name)
        traffic = dep.traffic_config or {}
        if isinstance(traffic.get("max_concurrent_per_instance"), int):
            conc = max(conc, int(traffic["max_concurrent_per_instance"]))
        kv_dtype = str(flags.get("cache-type-k") or flags.get("kv-cache-dtype") or "f16")
        cfg = dict(mv.config or {})
        cfg["model_type_hint"] = model.type
        context = dep.context_size or model.default_context_size
        kv_cells: Optional[int] = None
        activation_scale = 1.0
        if engine.name == "llama_cpp":
            # Mirror the agent's launch: slots (max_concurrency sent by launch_spec, else 4)
            # share one KV buffer of context x slots cells; ubatch sizes compute buffers.
            traffic_max = traffic.get("max_concurrent_per_instance")
            max_conc = (
                int(traffic_max)
                if isinstance(traffic_max, int) and traffic_max >= 1
                else (conc if dep.preset or conc > 1 else None)
            )
            kv_cells, conc = llama_cpp_kv_cells(context, flags, max_conc)
            activation_scale = llama_cpp_ubatch(flags) / LLAMA_CPP_DEFAULT_UBATCH
            context = context or LLAMA_CPP_DEFAULT_CONTEXT
        return estimate_vram(
            engine_name=engine.name,
            weights_bytes=int(mv.total_size_bytes or 0),
            parameter_count=mv.parameter_count,
            quantization=mv.quantization,
            config=cfg,
            context_size=context,
            concurrency=conc,
            kv_dtype=kv_dtype,
            tp_size=int(dep.tp_size or 1),
            pp_size=int(dep.pp_size or 1),
            kv_fallback_bytes_per_token=settings.SCHEDULER_KV_BYTES_PER_TOKEN_FALLBACK,
            kv_cells=kv_cells,
            activation_scale=activation_scale,
        )

    def placement_request(
        self,
        ctx: DeploymentContext,
        estimate: VramEstimate,
        replicas: int,
        *,
        existing: Sequence[Instances] = (),
        node_id: Optional[uuid.UUID] = None,
        device_ids: Sequence[uuid.UUID] = (),
        exclude_node_ids: Sequence[uuid.UUID] = (),
        blocked_nodes: Optional[Dict[uuid.UUID, str]] = None,
    ) -> PlacementRequest:
        dep, engine = ctx.dep, ctx.engine
        caps = engine.capabilities or {}
        per_node: Dict[uuid.UUID, int] = {}
        for inst in existing:
            per_node[inst.node_id] = per_node.get(inst.node_id, 0) + 1
        selector = dict(dep.node_selector or {})
        model_node = (getattr(ctx.model, "metadata_", None) or {}).get("node_id")
        if model_node and ctx.model.source == "local_path" and not selector.get("node_ids"):
            # Node-local model files (registered from a node scan) exist only there.
            selector["node_ids"] = [str(model_node)]
        return PlacementRequest(
            need_bytes_per_device=estimate.per_gpu_bytes,
            replicas=max(int(replicas), 0),
            gpu_count=int(dep.gpu_count or 1),
            tp_size=int(dep.tp_size or 1),
            pp_size=int(dep.pp_size or 1),
            strategy=_enum(dep.placement_strategy) or "spread",
            slice_vram_bytes=dep.slice_vram_bytes,
            slice_compute_pct=dep.slice_compute_pct,
            cluster_id=dep.cluster_id,
            node_selector=selector,
            vendors=list(engine.supported_vendors or []) or None,
            vendor_filter=list(dep.device_vendor_filter or []),
            supported_os=list(engine.supported_os or []) or None,
            exclude_node_ids=list(exclude_node_ids),
            existing_per_node=per_node,
            allow_cross_node_pp=bool(caps.get("pipeline_parallel")) and int(dep.pp_size or 1) > 1,
            headroom_pct=settings.SCHEDULER_VRAM_HEADROOM_PCT,
            max_device_combos=settings.SCHEDULER_MAX_DEVICE_COMBOS,
            pinned_node_id=node_id,
            pinned_device_ids=list(device_ids),
            min_device_fraction=device_memory_fraction(
                engine.name,
                L.effective_flags(dep, ctx.model, engine, ctx.engine_version),
            ),
            fraction_base_bytes=fraction_base_bytes(engine.name, estimate),
            blocked_nodes=dict(blocked_nodes or {}),
            deployment_id=dep.id,
        )

    async def blocked_nodes(self, ctx: DeploymentContext) -> Dict[uuid.UUID, str]:
        """Nodes where the deployment's engine cannot run (not installed, no build, no runtime)."""
        out: Dict[uuid.UUID, str] = {}
        for node in await self.inventory.nodes_in(ctx.dep.cluster_id):
            reason = engine_unavailable_reason(node, ctx.engine, ctx.engine_version)
            if reason:
                out[node.id] = reason
        return out

    async def context(self, dep: Deployments) -> DeploymentContext:
        ensure_managed(dep)  # placement / launch context: never for observed deployments
        model = await self.models.get_by_id(dep.model_id)
        version = await self.versions.get_by_id(dep.model_version_id)
        pair = await self.engine_versions.get_with_engine(dep.engine_version_id)
        if model is None or version is None or pair is None:
            raise ResourceNotFoundError(resource_type="Deployment dependencies")
        return DeploymentContext(dep, model, version, pair[0], pair[1])

    async def place_instance(
        self,
        deployment_id: uuid.UUID,
        *,
        node_id: Optional[uuid.UUID] = None,
        device_ids: Optional[Sequence[uuid.UUID]] = None,
        slice_spec: Optional[Dict[str, int]] = None,
        exclude_node_ids: Sequence[uuid.UUID] = (),
        pd_role: Optional[str] = None,
        traffic_weight: int = 100,
    ) -> Instances:
        """Reserve devices for one new replica and create its ``pending`` row (no agent call)."""
        dep = await self.deployments.get_active(deployment_id)
        if dep is None:
            raise ResourceNotFoundError(resource_type="Deployment")
        ctx = await self.context(dep)
        estimate = self.estimate(ctx)
        if slice_spec:
            ctx.dep.slice_vram_bytes = int(slice_spec["vram_bytes"])
            ctx.dep.slice_compute_pct = int(slice_spec.get("compute_pct") or 100)
        existing = await self.instances.for_deployment(dep.id, ("pending", "loading", "ready"))
        req = self.placement_request(
            ctx,
            estimate,
            1,
            existing=existing,
            node_id=node_id,
            device_ids=list(device_ids or []),
            exclude_node_ids=exclude_node_ids,
            blocked_nodes=await self.blocked_nodes(ctx),
        )
        plan = await self.scheduler.plan(req)
        if not plan.feasible:
            raise ConflictError(f"NO_CAPACITY: {plan.reason or 'no placement'}")
        return await self.create_instance_for(
            ctx, plan.assignments[0], estimate, pd_role, traffic_weight
        )

    async def _unique_served_name(
        self, base: str, deployment_id: uuid.UUID, node_id: Optional[uuid.UUID]
    ) -> str:
        """
        ``<model>--<dep8>``; a second live replica of the same deployment on the same node
        (extra replica, benchmark/auto-tune variant) gets ``-2``, ``-3``... so llama.cpp
        router presets and the per-node (port, served name) uniqueness never clash.
        """
        if node_id is None:
            return base
        from app.db.postgres.models.generated_models import Instances
        from sqlalchemy import select

        rows = await self.session.execute(
            select(Instances.served_model_name).where(
                Instances.deployment_id == deployment_id,
                Instances.node_id == node_id,
                Instances.state != "stopped",
            )
        )
        taken = {r for (r,) in rows.all() if r}
        if base not in taken:
            return base
        n = 2
        while f"{base}-{n}" in taken:
            n += 1
        return f"{base}-{n}"

    async def create_instance_for(
        self,
        ctx: DeploymentContext,
        assignment: Any,
        estimate: VramEstimate,
        pd_role: Optional[str] = None,
        traffic_weight: int = 100,
    ) -> Instances:
        dep = ctx.dep
        per_device = (
            int(assignment.slice["vram_bytes"]) if assignment.slice else estimate.per_gpu_bytes
        )
        fraction = device_memory_fraction(
            ctx.engine.name,
            L.effective_flags(dep, ctx.model, ctx.engine, ctx.engine_version),
        )
        if fraction and not assignment.slice:
            devices = await self.inventory.devices_by_ids(list(assignment.all_device_ids()))
            totals = [int(d.vram_total_bytes or 0) for d in devices]
            if totals:
                per_device = max(
                    per_device, device_need_bytes(ctx.engine.name, fraction, estimate, max(totals))
                )
        resources: Dict[str, Any] = {
            "vram_bytes_per_device": per_device,
            "estimate": estimate.as_dict(),
            "spec_hash": spec_hash(dep),
            "slice": assignment.slice,
            "placement_score": assignment.score,
            "engine": ctx.engine.name,
        }
        if getattr(assignment, "shards", None):
            resources["pp_shards"] = [
                {
                    "node_id": str(s.node_id),
                    "device_ids": [str(d) for d in s.device_ids],
                    "pp_rank": s.pp_rank,
                }
                for s in assignment.shards
            ]
        inst = await self.instances.create(
            deployment_id=dep.id,
            node_id=assignment.node_id,
            device_ids=list(assignment.all_device_ids()),
            state="pending",
            health="unknown",
            traffic_weight=int(traffic_weight),
            pd_role=pd_role,
            served_model_name=await self._unique_served_name(
                L.served_model_name(ctx.model.name, dep.id), dep.id, assignment.node_id
            ),
            resources=resources,
        )
        if assignment.slice:
            await self.slices.reserve(
                inst.id,
                dep.id,
                assignment.device_ids,
                int(assignment.slice["vram_bytes"]),
                int(assignment.slice["compute_pct"]),
                name=f"{dep.name}/{str(inst.id)[:8]}",
            )
        logger.info(
            f"placed instance {inst.id} deployment={dep.name} node={assignment.node_id} "
            f"devices={len(assignment.all_device_ids())} need={per_device >> 20}MiB"
        )
        return inst

    async def start_instance(
        self, instance_id: uuid.UUID, *, swap_operation_id: Optional[uuid.UUID] = None
    ) -> uuid.UUID:
        """Send ``load_model`` (plus ``start_engine`` for a new router process); returns its id."""
        inst = await self._managed_instance_or_404(instance_id)
        ensure_transition(inst.state, "loading")
        dep = await self.deployments.get_any(inst.deployment_id)
        if dep is None:
            raise ResourceNotFoundError(resource_type="Deployment")
        ctx = await self.context(dep)
        node = (await self.inventory.nodes_by_ids([inst.node_id])).get(inst.node_id)
        if node is None:
            raise ResourceNotFoundError(resource_type="Node")
        shards = node_ranks((inst.resources or {}).get("pp_shards") or [])
        head_ids = (
            [uuid.UUID(d) for d in shards[0]["device_ids"]]
            if shards
            else list(inst.device_ids or [])
        )
        devices = await self.inventory.devices_by_ids(head_ids)
        if not devices:
            raise ConflictError("Instance has no devices assigned")
        slice_spec = (inst.resources or {}).get("slice")
        installed = ctx.engine.name in _native_engines(node)
        engine = L.engine_spec(
            ctx.engine,
            ctx.engine_version,
            node.os,
            node.arch,
            installed,
            devices,
            getattr(node, "capabilities", None),
        )
        cache_root = L.default_cache_root(node)
        source = await self._model_source(ctx.model, ctx.version)
        files = await self.files.list_for_version(ctx.version.id)
        if not files:
            raise ConflictError("Model version has no files")
        model = L.model_spec(
            ctx.model,
            ctx.version,
            files,
            inst.served_model_name or ctx.model.name,
            cache_root,
            source,
        )
        draft_spec, draft_path = await self._draft_model(ctx, cache_root, inst.served_model_name)
        launch = L.launch_spec(
            dep, ctx.model, ctx.engine, ctx.engine_version, inst.pd_role, draft_path
        )
        assignments = L.device_assignments(devices, slice_spec)
        worker_cmds: List[str] = []
        if len(shards) > 1:
            worker_cmds = await self._launch_pp_workers(
                inst, dep, ctx, engine, model, launch, shards, swap_operation_id
            )
            launch = {
                **launch,
                "distributed": await self._distributed_spec(inst, shards, 0, ctx),
            }
        process_id: Optional[uuid.UUID] = None
        start_needed = False
        # A cross-node pipeline-parallel head loads its model in its own process (llama.cpp
        # `--rpc` applies to the process that loads the model), never in a shared router.
        if L.is_router_mode(ctx.engine_version) and len(shards) <= 1:
            process_id = inst.engine_process_id or await self.instances.router_process_for(
                inst.node_id, ctx.engine_version.id, head_ids
            )
            if process_id is None or (
                inst.engine_process_id is None
                and await self.instances.live_on_process(process_id, exclude_id=inst.id) == 0
            ):
                process_id = uuid.uuid4()
                start_needed = True
        await self.instances.set_state(
            inst.id,
            "loading",
            "load requested",
            engine_process_id=process_id,
            health="unknown",
        )
        await self.instances.release_connection()
        extra: Dict[str, Any] = {}
        if start_needed and process_id is not None:
            start_cmd = await self.hub.send_command(
                inst.node_id,
                "start_engine",
                L.start_engine_args(process_id, engine, assignments, launch),
                instance_id=inst.id,
                swap_operation_id=swap_operation_id,
            )
            extra["start_command_id"] = str(start_cmd)
        args = L.load_model_args(
            instance_id=inst.id,
            deployment=dep,
            engine=engine,
            model=model,
            devices=assignments,
            launch=launch,
            engine_process_id=process_id,
            draft_model=draft_spec,
        )
        cmd = await self.hub.send_command(
            inst.node_id,
            "load_model",
            args,
            instance_id=inst.id,
            swap_operation_id=swap_operation_id,
            timeout_seconds=int(settings.DEPLOYMENT_LOAD_TIMEOUT_SECONDS),
        )
        if worker_cmds:
            extra["pp_worker_command_ids"] = worker_cmds
        extra.update(
            last_command_id=str(cmd),
            last_command="load_model",
            last_command_at=utcnow().isoformat(),
        )
        await self.instances.merge_resources(inst.id, extra)
        logger.info(f"load_model sent instance={inst.id} node={node.name} command={cmd}")
        return cmd

    async def _distributed_spec(
        self,
        inst: Instances,
        shards: List[Dict[str, Any]],
        rank: int,
        ctx: DeploymentContext,
    ) -> Dict[str, Any]:
        """``launch.distributed`` for one rank (agent-protocol DistributedSpec, protocol.md §9)."""
        node_ids = [uuid.UUID(str(s["node_id"])) for s in shards]
        nodes = await self.inventory.nodes_by_ids(node_ids)
        llama = ctx.engine.name == "llama_cpp"
        peers = []
        for s in shards:
            node = nodes.get(uuid.UUID(str(s["node_id"])))
            address = (node.proxy_address or node.ip_address) if node else None
            if not address:
                raise ConflictError(f"pipeline stage node {s['node_id']} has no private address")
            peers.append(
                {
                    "rank": int(s["pp_rank"]),
                    "node_id": str(s["node_id"]),
                    "address": str(address),
                    "rpc_port": (PP_RPC_PORT if llama and int(s["pp_rank"]) > 0 else None),
                    "device_ids": [str(d) for d in s.get("device_ids") or []],
                }
            )
        peers.sort(key=lambda p: p["rank"])
        me = next(p for p in peers if p["rank"] == rank)
        return {
            "group_id": str(inst.id),
            "rank": rank,
            "world_size": len(peers),
            "master_addr": peers[0]["address"],
            "master_port": pp_master_port(inst.id),
            "bind_address": me["address"],
            "rpc_port": me["rpc_port"],
            "peers": peers,
        }

    async def _launch_pp_workers(
        self,
        inst: Instances,
        dep: Deployments,
        ctx: DeploymentContext,
        engine: Dict[str, Any],
        model: Dict[str, Any],
        launch: Dict[str, Any],
        shards: List[Dict[str, Any]],
        swap_operation_id: Optional[uuid.UUID],
    ) -> List[str]:
        """Workers first (protocol.md §9): one load_model per rank > 0, same instance_id."""
        sent = []
        for shard in sorted(shards, key=lambda s: int(s["pp_rank"])):
            rank = int(shard["pp_rank"])
            if rank == 0:
                continue
            node_id = uuid.UUID(str(shard["node_id"]))
            devices = await self.inventory.devices_by_ids(
                [uuid.UUID(str(d)) for d in shard.get("device_ids") or []]
            )
            args = L.load_model_args(
                instance_id=inst.id,
                deployment=dep,
                engine=engine,
                model=model,
                devices=L.device_assignments(devices, None),
                launch={
                    **launch,
                    "distributed": await self._distributed_spec(inst, shards, rank, ctx),
                },
            )
            cmd = await self.hub.send_command(
                node_id,
                "load_model",
                args,
                instance_id=inst.id,
                swap_operation_id=swap_operation_id,
                timeout_seconds=int(settings.DEPLOYMENT_LOAD_TIMEOUT_SECONDS),
            )
            sent.append(str(cmd))
        return sent

    async def stop_instance(
        self,
        instance_id: uuid.UUID,
        *,
        force: bool = False,
        drain_timeout_seconds: Optional[int] = None,
        swap_operation_id: Optional[uuid.UUID] = None,
    ) -> Optional[uuid.UUID]:
        """Send ``unload_model`` (agent drains first unless ``force``); None if nothing to do."""
        inst = await self._managed_instance_or_404(instance_id)
        if inst.state == "stopped":
            return None
        if inst.state == "pending" and not (inst.resources or {}).get("last_command_id"):
            await self.instances.set_state(inst.id, "stopped", "cancelled before load")
            await self.slices.set_status_for_instance(inst.id, "released")
            return None
        ensure_transition(inst.state, "unloading")
        stop_engine = False
        if inst.engine_process_id is not None:
            stop_engine = (
                await self.instances.live_on_process(inst.engine_process_id, exclude_id=inst.id)
                == 0
            )
        drain = (
            settings.DEPLOYMENT_UNLOAD_DRAIN_TIMEOUT_SECONDS
            if drain_timeout_seconds is None
            else drain_timeout_seconds
        )
        await self.instances.set_state(inst.id, "unloading", "unload requested")
        await self.instances.release_connection()
        await self._set_accepting(inst.id, False)
        cmd = await self.hub.send_command(
            inst.node_id,
            "unload_model",
            L.unload_model_args(
                inst.id,
                force=force,
                drain_timeout_seconds=int(drain),
                stop_engine_if_empty=stop_engine,
            ),
            instance_id=inst.id,
            swap_operation_id=swap_operation_id,
            timeout_seconds=int(drain) + 120,
        )
        await self.instances.merge_resources(
            inst.id,
            {
                "last_command_id": str(cmd),
                "last_command": "unload_model",
                "last_command_at": utcnow().isoformat(),
            },
        )
        # Cross-node pipeline parallel: head first (above), then every worker rank.
        for shard in node_ranks((inst.resources or {}).get("pp_shards") or []):
            if int(shard.get("pp_rank") or 0) == 0:
                continue
            await self.hub.send_command(
                uuid.UUID(str(shard["node_id"])),
                "unload_model",
                L.unload_model_args(inst.id, force=True, drain_timeout_seconds=0),
                instance_id=inst.id,
                swap_operation_id=swap_operation_id,
                timeout_seconds=120,
            )
        logger.info(f"unload_model sent instance={inst.id} force={force} command={cmd}")
        return cmd

    # ============================================================ helpers

    async def _set_accepting(self, instance_id: uuid.UUID, accepting: bool) -> None:
        if not self.publish_enabled:
            return
        try:
            from app.services.deployments.routing_publisher import (
                set_instance_accepting,
            )

            await set_instance_accepting(instance_id, accepting)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"accepting flag not updated for {instance_id}: {exc}")

    async def _model_source(
        self, model: Models, version: ModelVersions
    ) -> Optional[Dict[str, Any]]:
        from app.services.model_cache.service import ModelCacheService

        return await ModelCacheService.for_session(self.session, self._hub).model_source(
            model, version
        )

    async def _draft_model(
        self, ctx: DeploymentContext, cache_root: str, served: Optional[str]
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        spec = ctx.dep.spec_decoding or {}
        draft_id = spec.get("draft_model_version_id")
        if (
            not spec.get("enabled")
            or spec.get("method") not in ("draft_model", None)
            or not draft_id
        ):
            return None, None
        try:
            draft_version = await self.versions.get_active(uuid.UUID(str(draft_id)))
        except ValueError:
            draft_version = None
        if draft_version is None:
            return None, None
        draft_model = await self.models.get_by_id(draft_version.model_id)
        files = await self.files.list_for_version(draft_version.id)
        if draft_model is None or not files:
            return None, None
        source = await self._model_source(draft_model, draft_version)
        spec_out = L.model_spec(
            draft_model,
            draft_version,
            files,
            f"{served or draft_model.name}-draft",
            cache_root,
            source,
        )
        weights = [f for f in files if f.format in ("gguf", "safetensors", "mlx")] or files
        path = f"{cache_root}/{draft_version.id}/{weights[0].path}"
        return spec_out, path

    async def _reset_crashloop(self, deployment_id: uuid.UUID) -> None:
        try:
            from app.db.redis.client import get_redis, redis_key

            client = await get_redis()
            await client.set(
                redis_key("deployments", str(deployment_id), "crashloop_reset"),
                utcnow().isoformat(),
                ex=24 * 3600,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"crashloop reset marker not stored: {exc}")

    async def _deployment_or_404(self, deployment_id: uuid.UUID) -> Deployments:
        dep = await self.deployments.get_active(deployment_id)
        if dep is None:
            raise ResourceNotFoundError(resource_type="Deployment")
        return dep

    async def _instance_or_404(self, instance_id: uuid.UUID) -> Instances:
        inst = await self.instances.get_by_id(instance_id)
        if inst is None:
            raise ResourceNotFoundError(resource_type="Instance")
        return inst

    async def _managed_deployment_or_404(self, deployment_id: uuid.UUID) -> Deployments:
        """For every mutation: 409 ``EXTERNAL_DEPLOYMENT`` on an observed deployment."""
        dep = await self._deployment_or_404(deployment_id)
        ensure_managed(dep)
        return dep

    async def _managed_instance_or_404(self, instance_id: uuid.UUID) -> Instances:
        """For every mutation / command: 409 ``EXTERNAL_DEPLOYMENT`` on an observed instance."""
        inst = await self._instance_or_404(instance_id)
        ensure_managed(inst, "instance")
        return inst

    # ------------------------------------------------------------ output

    async def _instances_out(
        self, rows: Sequence[Tuple[Instances, str, str, str, str]]
    ) -> List[Instance]:
        lives = await _instance_lives(self.session, [r[0].id for r in rows])
        out = []
        for inst, dep_name, model_name, node_name, engine_name in rows:
            out.append(
                Instance(
                    id=inst.id,
                    deployment_id=inst.deployment_id,
                    deployment_name=dep_name,
                    model_name=model_name,
                    node_id=inst.node_id,
                    node_name=node_name,
                    device_ids=list(inst.device_ids or []),
                    port=inst.port,
                    served_model_name=inst.served_model_name,
                    engine_process_id=inst.engine_process_id,
                    engine_name=engine_name,
                    state=inst.state,
                    state_message=inst.state_message,
                    health=inst.health,
                    health_message=inst.health_message,
                    traffic_weight=int(inst.traffic_weight),
                    pd_role=inst.pd_role,
                    pid=inst.pid,
                    container_id=inst.container_id,
                    resources=inst.resources or {},
                    restart_count=int(inst.restart_count or 0),
                    state_changed_at=inst.state_changed_at,
                    started_at=inst.started_at,
                    ready_at=inst.ready_at,
                    stopped_at=inst.stopped_at,
                    last_health_at=inst.last_health_at,
                    last_request_at=inst.last_request_at,
                    live=lives.get(inst.id),
                    origin=getattr(inst, "origin", None) or "managed",
                    created_at=inst.created_at,
                    updated_at=inst.updated_at,
                )
            )
        return out

    async def _deployments_out(self, rows: Sequence[Deployments]) -> List[Deployment]:
        ids = [d.id for d in rows]
        ctx = await self.deployments.context(ids)
        inst_rows, _ = await self.instances.list_instances(
            deployment_ids=ids, page=1, page_size=max(len(ids) * 64, 1)
        )
        instances = await self._instances_out(inst_rows)
        by_dep: Dict[uuid.UUID, List[Instance]] = {}
        for inst in instances:
            by_dep.setdefault(inst.deployment_id, []).append(inst)
        swaps = await self.deployments.active_swap_ids(ids)
        externals = await _external_infos([d for d in rows if is_external(d)], by_dep)
        out = []
        for d in rows:
            model, ev, engine = ctx.get(d.id, (None, None, None))
            insts = sorted(by_dep.get(d.id, []), key=lambda i: i.created_at)
            ext = externals.get(d.id)
            out.append(
                Deployment(
                    **_spec_dict(d),
                    id=d.id,
                    model_name=(
                        model.name if model else (ext.models[0] if ext and ext.models else None)
                    ),
                    model_type=model.type if model else None,
                    effective_priority=d.priority_override or (model.priority if model else None),
                    engine_name=(
                        engine.name if engine else (ENGINE_NAMES.get(ext.engine) if ext else None)
                    ),
                    engine_version=ev.version if ev else (ext.version if ext else None),
                    origin=getattr(d, "origin", None) or "managed",
                    external=ext,
                    status=d.status,
                    status_message=d.status_message,
                    desired_running=bool(d.desired_running),
                    last_request_at=d.last_request_at,
                    instances=insts,
                    ready_replicas=sum(1 for i in insts if i.state.value == "ready"),
                    active_swap_ids=swaps.get(d.id, []),
                    live=_aggregate_live([i.live for i in insts if i.live]),
                    created_at=d.created_at,
                    updated_at=d.updated_at,
                )
            )
        return out


# -------------------------------------------------------------- module helpers


async def _external_infos(
    rows: Sequence[Deployments], by_dep: Dict[uuid.UUID, List[Instance]]
) -> Dict[uuid.UUID, ExternalInfo]:
    """``external`` view of observed deployments: the agent's report plus the latest engine
    sample's request mix (status classes, top endpoints) when the log is followed."""
    from app.services.deployments import live

    if not rows:
        return {}
    inst_ids = [i.id for d in rows for i in by_dep.get(d.id, [])]
    latest = dict(zip(inst_ids, await live.samples("engine", inst_ids)))
    out: Dict[uuid.UUID, ExternalInfo] = {}
    for d in rows:
        ext = dict(d.external or {})
        sample: Dict[str, Any] = {}
        for i in by_dep.get(d.id, []):
            sample = latest.get(i.id) or sample
        extra = sample.get("extra") or {}
        try:
            out[d.id] = ExternalInfo(
                node_id=ext.get("node_id"),
                node_name=ext.get("node_name"),
                name=str(ext.get("name") or d.name),
                engine=str(ext.get("engine") or "generic"),
                engine_label=ENGINE_LABELS.get(str(ext.get("engine"))),
                endpoint=ext.get("endpoint"),
                models=list(ext.get("models") or []),
                version=ext.get("version"),
                sources=list(ext.get("sources") or []),
                pid=ext.get("pid"),
                device_ids=list(ext.get("device_ids") or []),
                health=ext.get("health") if ext.get("health") in _HEALTH else None,
                message=ext.get("message"),
                last_ok_at=ext.get("last_ok_at"),
                last_seen_at=ext.get("last_seen_at"),
                first_seen_at=ext.get("first_seen_at"),
                stale_since=ext.get("stale_since"),
                requests_per_minute=extra.get("requests_per_minute"),
                status_counts={
                    str(k): int(v) for k, v in (extra.get("status_counts") or {}).items()
                },
                top_endpoints=[
                    e for e in (extra.get("top_endpoints") or []) if isinstance(e, dict)
                ][:10],
                process_vram_bytes=sample.get("process_vram_bytes"),
                log_derived=bool(extra.get("log_derived")),
            )
        except Exception as exc:  # noqa: BLE001 - a malformed report must not break the list
            logger.debug(f"external info for {d.id} not shown: {exc}")
    return out


_HEALTH = ("healthy", "unhealthy", "unknown")


def _spec_dict(d: Deployments) -> Dict[str, Any]:
    return {
        "name": d.name,
        "description": d.description,
        "model_id": d.model_id,
        "model_version_id": d.model_version_id,
        "engine_version_id": d.engine_version_id,
        "cluster_id": d.cluster_id,
        "priority_override": d.priority_override,
        "replicas": d.replicas,
        "min_replicas": d.min_replicas,
        "max_replicas": d.max_replicas,
        "gpu_count": d.gpu_count,
        "slice_vram_bytes": d.slice_vram_bytes,
        "slice_compute_pct": d.slice_compute_pct,
        "tp_size": d.tp_size,
        "pp_size": d.pp_size,
        "preset": d.preset,
        "pd_split": bool(d.pd_split),
        "pd_config": d.pd_config or {},
        "spec_decoding": d.spec_decoding or {},
        "kv_sharing": bool(d.kv_sharing),
        "kv_sharing_config": d.kv_sharing_config or {},
        "launch_flags": d.launch_flags or {},
        "env_vars": d.env_vars or {},
        "tool_parser": d.tool_parser,
        "context_size": d.context_size,
        "control_vectors": d.control_vectors or [],
        "placement_strategy": d.placement_strategy,
        "node_selector": d.node_selector or {},
        "device_vendor_filter": list(d.device_vendor_filter or []),
        "traffic_config": d.traffic_config or {},
    }


def _validate_shape(data: Any, engine: Engines) -> None:
    caps = engine.capabilities or {}
    gpus, tp, pp = (
        int(data.gpu_count or 1),
        int(data.tp_size or 1),
        int(data.pp_size or 1),
    )
    if gpus != tp * pp:
        raise ValidationError(f"gpu_count ({gpus}) must equal tp_size × pp_size ({tp}×{pp})")
    if data.slice_vram_bytes and gpus > 1:
        raise ValidationError("GPU slices apply to single-GPU replicas (gpu_count=1)")
    if data.control_vectors and not caps.get("control_vectors"):
        raise ValidationError(f"{engine.name} does not support control vectors")
    for cv in data.control_vectors or []:
        if not isinstance(cv, dict) or not cv.get("path"):
            raise ValidationError("each control vector needs a path")
    spec = data.spec_decoding or {}
    if spec.get("enabled"):
        method = spec.get("method") or "ngram"
        if method not in ("draft_model", "ngram", "eagle", "mtp"):
            raise ValidationError("spec_decoding.method must be draft_model, ngram, eagle or mtp")
        if method == "draft_model" and not spec.get("draft_model_version_id"):
            raise ValidationError("spec_decoding.method=draft_model needs draft_model_version_id")
    if data.pd_split:
        cfg = data.pd_config or {}
        for key in ("prefill_replicas", "decode_replicas"):
            if key in cfg and (not isinstance(cfg[key], int) or cfg[key] < 0):
                raise ValidationError(f"pd_config.{key} must be a non-negative integer")
        if cfg.get("transfer") not in (None, "nixl", "mooncake"):
            raise ValidationError("pd_config.transfer must be nixl or mooncake")
    if data.kv_sharing:
        backend = (data.kv_sharing_config or {}).get("backend")
        if backend not in (None, "lmcache", "mooncake", "native"):
            raise ValidationError("kv_sharing_config.backend must be lmcache, mooncake or native")
    traffic = data.traffic_config or {}
    if traffic.get("strategy") not in (
        None,
        "round_robin",
        "least_sessions",
        "weighted",
    ):
        raise ValidationError(
            "traffic_config.strategy must be round_robin, least_sessions or weighted"
        )


def _first_vram_only(
    outcomes: Sequence[compat.CompatOutcome],
) -> Optional[compat.CompatOutcome]:
    """Best outcome whose only errors are VRAM fit (deployable once capacity frees up)."""
    for o in compat.rank(outcomes):
        errors = [r for r in o.reasons if r["severity"] == "error"]
        if errors and all(r["code"] in VRAM_ONLY_REASONS for r in errors):
            return o
    return None


def _compat_out(outcome: compat.CompatOutcome, ctx: DeploymentContext) -> CompatCheck:
    return CompatCheck(
        id=uuid.uuid4(),
        model_version_id=ctx.version.id,
        engine_version_id=uuid.UUID(outcome.engine.engine_version_id),
        engine_name=outcome.engine.name,
        engine_version=outcome.engine.version,
        result=outcome.result,
        score=outcome.score,
        reasons=outcome.reasons,
        estimated_vram_bytes=outcome.estimated_vram_bytes,
        checked_at=utcnow(),
    )


def engine_unavailable_reason(node: Any, engine: Any, engine_version: Any) -> Optional[str]:
    """Why ``engine`` cannot run on ``node`` (None = it can)."""
    if engine.name in _installed_engines(node):
        return None
    caps = getattr(node, "capabilities", None) or {}
    if engine.runtime == "container":
        runtimes = caps.get("runtimes")
        if runtimes is not None and "container" not in runtimes:
            return "node has no container runtime"
        if not getattr(engine_version, "image", None):
            return f"{engine.name} {engine_version.version} has no container image"
        return None
    node_os, node_arch = getattr(node, "os", None), getattr(node, "arch", None)
    if L.select_artifact(engine_version, node_os, node_arch, capabilities=caps):
        return None
    from app.services.engines import artifacts as arts

    glibc, container = arts.node_host(caps)
    why = arts.incompatibility(
        getattr(engine_version, "artifacts", None) or {},
        node_os,
        node_arch,
        glibc,
        container,
    )
    if why:
        return f"{engine.name} {engine_version.version}: {why}"
    return (
        f"{engine.name} is not installed on the node and has no pinned build for "
        f"{node_os}/{node_arch}"
    )


def _installed_engines(node: Nodes) -> List[str]:
    caps = node.capabilities or {}
    return [e.get("name") for e in caps.get("engines", []) if isinstance(e, dict) and e.get("name")]


def _native_engines(node: Nodes) -> List[str]:
    """Engines installed on the node itself (not the container images it can pull)."""
    caps = node.capabilities or {}
    return [
        e.get("name")
        for e in caps.get("engines", [])
        if isinstance(e, dict) and e.get("name") and (e.get("runtime") or "native") == "native"
    ]


def _jsonable(value: Any) -> Any:
    return json.loads(
        json.dumps(value, default=lambda v: _enum(v) if hasattr(v, "value") else str(v))
    )


async def _instance_lives(
    session: AsyncSession, instance_ids: Sequence[uuid.UUID]
) -> Dict[uuid.UUID, InstanceLive]:
    """Latest engine sample per instance plus its last-minute rollup (requests per minute)."""
    from app.db.postgres.repositories.metric_repository import MetricRepository
    from app.services.deployments import live

    try:
        rollups = await MetricRepository(session).last_minute_engine_rollups(list(instance_ids))
    except Exception as exc:  # noqa: BLE001 - live data is best effort
        logger.debug(f"engine rollup lookup failed: {exc}")
        rollups = {}
    out = {}
    for iid, sample in zip(instance_ids, await live.samples("engine", instance_ids)):
        if sample:
            data = live.instance_live(sample, rollups.get(iid))
            if data.get("ts"):
                out[iid] = InstanceLive.model_validate(data)
    return out


def _aggregate_live(lives: Sequence[InstanceLive]) -> Optional[InstanceLive]:
    if not lives:
        return None

    def total(attr: str) -> Optional[float]:
        vals = [getattr(x, attr) for x in lives if getattr(x, attr) is not None]
        return sum(vals) if vals else None

    def mean(attr: str) -> Optional[float]:
        vals = [getattr(x, attr) for x in lives if getattr(x, attr) is not None]
        return sum(vals) / len(vals) if vals else None

    def peak(attr: str) -> Optional[float]:
        vals = [getattr(x, attr) for x in lives if getattr(x, attr) is not None]
        return max(vals) if vals else None

    ts = [x.ts for x in lives if x.ts]
    active = total("active_sessions")
    waiting = total("waiting_sessions")
    return InstanceLive(
        ts=max(ts) if ts else None,
        active_sessions=int(active) if active is not None else None,
        waiting_sessions=int(waiting) if waiting is not None else None,
        kv_cache_usage_pct=mean("kv_cache_usage_pct"),
        generation_tps=total("generation_tps"),
        ttft_ms_p95=peak("ttft_ms_p95"),
        requests_per_minute=total("requests_per_minute"),
        error_rate_pct=mean("error_rate_pct"),
    )
