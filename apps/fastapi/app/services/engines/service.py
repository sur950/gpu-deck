"""
Engine registry service: engines, engine versions (incl. the custom engine
slot), compatibility checks and auto engine pick.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.exceptions import ConflictError, ResourceNotFoundError, ValidationError
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_db
from app.db.postgres.models.generated_models import (
    Devices,
    EngineCompatChecks,
    Engines,
    EngineVersions,
    Models,
    ModelVersions,
    Nodes,
)
from app.db.postgres.repositories.engine_repository import (
    EngineCompatCheckRepository,
    EngineRepository,
    EngineVersionRepository,
)
from app.db.postgres.repositories.model_repository import (
    ModelRepository,
    ModelVersionRepository,
)
from app.db.postgres.repositories.placement_inventory_repository import (
    PlacementInventoryRepository,
)
from app.schemas.engines_schema import (
    AutoPickRequest,
    AutoPickResult,
    CompatCheck,
    CompatCheckRequest,
    Engine,
    EngineUpdate,
    EngineVersion,
    EngineVersionCreate,
    EngineVersionUpdate,
)
from app.services.catalog import detect
from app.services.engines import compat
from app.services.engines.presets import (
    merge_launch_flags,
    preset_flags,
    render_engine_flags,
)
from app.services.engines.seed import SEED_ENGINE_VERSIONS
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class EngineService:
    """Engines + versions + compat + auto-pick."""

    def __init__(
        self,
        session: AsyncSession,
        engines: EngineRepository,
        versions: EngineVersionRepository,
        checks: EngineCompatCheckRepository,
        models: ModelRepository,
        model_versions: ModelVersionRepository,
        inventory: PlacementInventoryRepository,
    ) -> None:
        self.session = session
        self.engines = engines
        self.versions = versions
        self.checks = checks
        self.models = models
        self.model_versions = model_versions
        self.inventory = inventory

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db, scope="function")
    ) -> "EngineService":
        return EngineService.for_session(db)

    @classmethod
    def for_session(cls, db: AsyncSession) -> "EngineService":
        return cls(
            db,
            EngineRepository(db),
            EngineVersionRepository(db),
            EngineCompatCheckRepository(db),
            ModelRepository(db),
            ModelVersionRepository(db),
            PlacementInventoryRepository(db),
        )

    # ------------------------------------------------------------------ seed

    async def seed_builtin_versions(self) -> int:
        """
        Insert the pinned built-in engine versions that are missing. Idempotent. An engine
        without versions gets the seed version as its default; an engine that already has
        versions (an older release's pin, operator versions) gets it as a non-default
        version, so upgrading never changes what existing deployments run.
        """
        from sqlalchemy import text

        # Several admin API workers start together: one seeds, the others then see its rows.
        await self.versions.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('gpudeck:engines:seed'))")
        )
        created = 0
        for spec in SEED_ENGINE_VERSIONS:
            engine = await self.engines.get_by_name(spec["engine"])
            if engine is None:
                continue
            existing = await self.versions.list_for_engine(engine.id)
            if await self._version_ever_existed(engine.id, spec["version"]):
                # present, or deleted by an operator: never re-created; a present row of the
                # same pinned build gets the current host-compatibility metadata.
                row = await self.versions.get_by_label(engine.id, spec["version"])
                merged = refresh_compat(row.artifacts if row else None, spec.get("artifacts"))
                if row is not None and merged is not None:
                    await self.versions.update(row.id, artifacts=merged)
                    logger.info(
                        f"engine {spec['engine']} {spec['version']}: compatibility metadata "
                        "refreshed"
                    )
                continue
            await self.versions.create(
                engine_id=engine.id,
                version=spec["version"],
                status=spec.get("status", "available"),
                image=spec.get("image"),
                artifacts=spec.get("artifacts", {}),
                launch_template=spec.get("launch_template", {}),
                default_launch_flags=spec.get("default_launch_flags", {}),
                health_config=spec.get("health_config", {}),
                metrics_config=spec.get("metrics_config", {}),
                port_range=self.versions.port_range_value(*settings.get_engine_port_range()),
                min_driver_version=spec.get("min_driver_version"),
                requirements=spec.get("requirements", {}),
                is_default=bool(spec.get("is_default", True)) and not existing,
            )
            created += 1
        if created:
            logger.info(f"seeded {created} built-in engine version(s)")
        return created

    async def _version_ever_existed(self, engine_id: uuid.UUID, version: str) -> bool:
        from sqlalchemy import select

        row = await self.versions.session.execute(
            select(EngineVersions.id)
            .where(EngineVersions.engine_id == engine_id, EngineVersions.version == version)
            .limit(1)
        )
        return row.first() is not None

    # --------------------------------------------------------------- engines

    async def list_engines(self, **filters) -> List[Engine]:
        rows = await self.engines.list_engines(**filters)
        versions = await self.versions.list_for_engines([r.id for r in rows])
        return [_engine_out(r, versions.get(r.id, [])) for r in rows]

    async def get_engine(self, engine_id: uuid.UUID) -> Engine:
        row = await self._engine_or_404(engine_id)
        return _engine_out(row, await self.versions.list_for_engine(engine_id))

    async def update_engine(self, engine_id: uuid.UUID, data: EngineUpdate) -> Engine:
        row = await self._engine_or_404(engine_id)
        changes = data.model_dump(exclude_unset=True)
        if row.is_builtin and row.name != "custom":
            for key in (
                "supported_model_types",
                "supported_vendors",
                "supported_formats",
            ):
                if key in changes:
                    raise ValidationError(f"{key} of a built-in engine cannot be edited")
        for key in ("supported_model_types", "supported_vendors", "supported_formats"):
            if key in changes and changes[key] is not None:
                changes[key] = [v.value if hasattr(v, "value") else v for v in changes[key]]
        await self.engines.update(engine_id, **changes)
        return await self.get_engine(engine_id)

    # -------------------------------------------------------- engine versions

    async def list_versions(self, engine_id: uuid.UUID) -> List[EngineVersion]:
        engine = await self._engine_or_404(engine_id)
        return [_version_out(v, engine) for v in await self.versions.list_for_engine(engine_id)]

    async def get_version(self, engine_id: uuid.UUID, version_id: uuid.UUID) -> EngineVersion:
        engine = await self._engine_or_404(engine_id)
        return _version_out(await self._version_or_404(engine_id, version_id), engine)

    async def create_version(
        self, engine_id: uuid.UUID, data: EngineVersionCreate
    ) -> EngineVersion:
        engine = await self._engine_or_404(engine_id)
        if await self.versions.get_by_label(engine_id, data.version):
            raise ConflictError(f"Version {data.version!r} already exists for {engine.name}")
        _validate_version_payload(engine, data.model_dump())
        is_default = data.is_default or not await self.versions.list_for_engine(engine_id)
        if is_default:
            await self.versions.clear_default(engine_id)
        lo, hi = data.port_range or settings.get_engine_port_range()
        row = await self.versions.create(
            engine_id=engine_id,
            version=data.version,
            status=data.status.value,
            image=data.image,
            artifacts=data.artifacts,
            launch_template=data.launch_template or _default_template(engine),
            default_launch_flags=data.default_launch_flags,
            health_config=data.health_config or _default_health(),
            metrics_config=data.metrics_config or _default_metrics(engine),
            port_range=self.versions.port_range_value(lo, hi),
            min_driver_version=data.min_driver_version,
            requirements=data.requirements,
            is_default=is_default,
        )
        return _version_out(row, engine)

    async def update_version(
        self, engine_id: uuid.UUID, version_id: uuid.UUID, data: EngineVersionUpdate
    ) -> EngineVersion:
        engine = await self._engine_or_404(engine_id)
        await self._version_or_404(engine_id, version_id)
        changes = data.model_dump(exclude_unset=True)
        if "version" in changes:
            other = await self.versions.get_by_label(engine_id, changes["version"])
            if other and other.id != version_id:
                raise ConflictError(
                    f"Version {changes['version']!r} already exists for {engine.name}"
                )
        if "status" in changes and changes["status"] is not None:
            changes["status"] = changes["status"].value
        if "port_range" in changes:
            pr = changes.pop("port_range")
            if pr:
                if len(pr) != 2 or pr[0] >= pr[1]:
                    raise ValidationError("port_range must be [start, end) with start < end")
                changes["port_range"] = self.versions.port_range_value(int(pr[0]), int(pr[1]))
        if changes.get("is_default"):
            await self.versions.clear_default(engine_id)
        elif "is_default" in changes and changes["is_default"] is False:
            raise ValidationError("Set another version as default instead of clearing this one")
        _validate_version_payload(engine, changes)
        await self.versions.update(version_id, **changes)
        return await self.get_version(engine_id, version_id)

    async def delete_version(self, engine_id: uuid.UUID, version_id: uuid.UUID) -> None:
        await self._engine_or_404(engine_id)
        row = await self._version_or_404(engine_id, version_id)
        if await self.versions.deployment_count(version_id):
            raise ConflictError("ENGINE_VERSION_IN_USE: deployments reference this version")
        await self.versions.soft_delete(version_id)
        if row.is_default:
            remaining = await self.versions.list_for_engine(engine_id)
            if remaining:
                await self.versions.update(remaining[0].id, is_default=True)

    # ----------------------------------------------------------------- compat

    async def run_compat_check(self, req: CompatCheckRequest) -> List[CompatCheck]:
        mv = await self._model_version_or_404(req.model_version_id)
        model = await self.models.get_active(mv.model_id)
        if model is None:
            raise ResourceNotFoundError(resource_type="Model")
        target = await self._target_for(
            req.node_id, req.device_vendor.value if req.device_vendor else None
        )
        shape = compat.Shape(gpu_count=req.gpu_count, context_size=req.context_size)
        candidates = await self._candidates(req.engine_version_id)
        outcomes = []
        for ev, engine in candidates:
            outcome = compat.check_compat(
                _model_info(model, mv),
                _engine_info(engine, ev),
                target,
                shape,
                headroom_pct=settings.SCHEDULER_VRAM_HEADROOM_PCT,
                kv_fallback=settings.SCHEDULER_KV_BYTES_PER_TOKEN_FALLBACK,
            )
            outcomes.append(outcome)
        out = []
        for outcome in compat.rank(outcomes):
            row = await self.checks.record(
                model_version_id=mv.id,
                engine_version_id=uuid.UUID(outcome.engine.engine_version_id),
                node_id=req.node_id,
                device_vendor=target.vendor,
                result=outcome.result,
                score=outcome.score,
                reasons=outcome.reasons,
                estimated_vram_bytes=outcome.estimated_vram_bytes,
            )
            out.append(_check_out(row, outcome.engine))
        return out

    async def list_checks(self, **filters) -> Tuple[List[CompatCheck], int]:
        rows, total = await self.checks.list_checks(**filters)
        return [_check_out(chk, _engine_info(engine, ev)) for chk, ev, engine in rows], total

    async def auto_pick(self, req: AutoPickRequest) -> AutoPickResult:
        mv = await self._model_version_or_404(req.model_version_id)
        model = await self.models.get_active(mv.model_id)
        if model is None:
            raise ResourceNotFoundError(resource_type="Model")
        outcomes = await self.evaluate_engines(
            model,
            mv,
            node_id=req.node_id,
            cluster_id=req.cluster_id,
            shape=compat.Shape(
                gpu_count=req.gpu_count,
                context_size=req.context_size,
                preset=req.preset.value if req.preset else None,
            ),
        )
        best = compat.auto_pick(outcomes)
        checks = []
        for outcome in compat.rank(outcomes):
            row = await self.checks.record(
                model_version_id=mv.id,
                engine_version_id=uuid.UUID(outcome.engine.engine_version_id),
                node_id=req.node_id,
                device_vendor=None,
                result=outcome.result,
                score=outcome.score,
                reasons=outcome.reasons,
                estimated_vram_bytes=outcome.estimated_vram_bytes,
            )
            checks.append((outcome, _check_out(row, outcome.engine)))
        if best is None:
            return AutoPickResult(alternatives=[c for _, c in checks])
        best_check = next(c for o, c in checks if o is best)
        ev_row = await self.versions.get_with_engine(uuid.UUID(best.engine.engine_version_id))
        engine_version = _version_out(ev_row[0], ev_row[1]) if ev_row else None
        parser = model.tool_parser_default if best.engine.name == "llama_cpp" else None
        parser = parser or detect.default_tool_parser(model.family, best.engine.name)
        launch = {
            "flags": merge_launch_flags(
                best.engine.default_launch_flags,
                model.default_launch_flags,
                preset_flags(best.engine.name, req.preset.value if req.preset else None),
                {},
            ),
            "context_size": req.context_size or model.default_context_size,
            "tool_parser": parser,
            "tp_size": 1,
            "pp_size": 1,
        }
        return AutoPickResult(
            engine_version=engine_version,
            check=best_check,
            alternatives=[c for o, c in checks if o is not best],
            suggested_launch_flags=render_engine_flags(best.engine.name, launch),
            suggested_tool_parser=parser,
        )

    async def evaluate_engines(
        self,
        model: Models,
        mv: ModelVersions,
        *,
        node_id: Optional[uuid.UUID] = None,
        cluster_id: Optional[uuid.UUID] = None,
        shape: Optional[compat.Shape] = None,
        engine_version_id: Optional[uuid.UUID] = None,
    ) -> List[compat.CompatOutcome]:
        """Compat outcomes for every candidate engine version (used by deployments too)."""
        shape = shape or compat.Shape()
        if node_id is not None:
            targets = await self._targets_for_node(node_id)
        else:
            targets = await self._targets_for_cluster(cluster_id)
            if not targets:
                targets = [compat.Target()]
        candidates = await self._candidates(engine_version_id)
        outcomes: Dict[str, compat.CompatOutcome] = {}
        for ev, engine in candidates:
            einfo = _engine_info(engine, ev)
            best_for_engine: Optional[compat.CompatOutcome] = None
            for target in targets:
                outcome = compat.check_compat(
                    _model_info(model, mv),
                    einfo,
                    target,
                    shape,
                    headroom_pct=settings.SCHEDULER_VRAM_HEADROOM_PCT,
                    kv_fallback=settings.SCHEDULER_KV_BYTES_PER_TOKEN_FALLBACK,
                )
                if best_for_engine is None or compat.rank([outcome, best_for_engine])[0] is outcome:
                    best_for_engine = outcome
            if best_for_engine is not None:
                outcomes[einfo.engine_version_id] = best_for_engine
        return list(outcomes.values())

    # -------------------------------------------------------------- internals

    async def _candidates(
        self, engine_version_id: Optional[uuid.UUID]
    ) -> List[Tuple[EngineVersions, Engines]]:
        if engine_version_id is not None:
            pair = await self.versions.get_with_engine(engine_version_id)
            if pair is None:
                raise ResourceNotFoundError(resource_type="Engine version")
            return [pair]
        return [(ev, e) for ev, e in await self.versions.list_all_with_engine() if e.is_enabled]

    async def _target_for(
        self, node_id: Optional[uuid.UUID], vendor: Optional[str]
    ) -> compat.Target:
        if node_id is None:
            return compat.Target(vendor=vendor)
        node, devices = await self.inventory.node_with_devices(node_id)
        if node is None:
            raise ResourceNotFoundError(resource_type="Node")
        return _target_from_node(node, devices, vendor)

    async def _targets_for_node(self, node_id: uuid.UUID) -> List[compat.Target]:
        node, devices = await self.inventory.node_with_devices(node_id)
        if node is None:
            raise ResourceNotFoundError(resource_type="Node")
        return _targets_from_node(node, devices)

    async def _targets_for_cluster(self, cluster_id: Optional[uuid.UUID]) -> List[compat.Target]:
        rows = await self.inventory.online_nodes_with_devices(cluster_id)
        return [t for node, devices in rows for t in _targets_from_node(node, devices)]

    async def _engine_or_404(self, engine_id: uuid.UUID) -> Engines:
        row = await self.engines.get_active(engine_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Engine")
        return row

    async def _version_or_404(self, engine_id: uuid.UUID, version_id: uuid.UUID) -> EngineVersions:
        row = await self.versions.get_active(version_id)
        if row is None or row.engine_id != engine_id:
            raise ResourceNotFoundError(resource_type="Engine version")
        return row

    async def _model_version_or_404(self, version_id: uuid.UUID) -> ModelVersions:
        row = await self.model_versions.get_active(version_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Model version")
        return row


# ---------------------------------------------------------------- mapping


def _port_range_list(value: Any) -> List[int]:
    lower = getattr(value, "lower", None)
    upper = getattr(value, "upper", None)
    if lower is None or upper is None:
        lo, hi = settings.get_engine_port_range()
        return [lo, hi]
    if getattr(value, "bounds", "[)")[1] == "]":
        upper = upper + 1
    return [int(lower), int(upper)]


def _version_out(v: EngineVersions, engine: Engines) -> EngineVersion:
    return EngineVersion(
        id=v.id,
        engine_id=v.engine_id,
        engine_name=engine.name,
        version=v.version,
        status=v.status,
        image=v.image,
        artifacts=v.artifacts or {},
        launch_template=v.launch_template or {},
        default_launch_flags=v.default_launch_flags or {},
        health_config=v.health_config or {},
        metrics_config=v.metrics_config or {},
        port_range=_port_range_list(v.port_range),
        min_driver_version=v.min_driver_version,
        requirements=v.requirements or {},
        is_default=bool(v.is_default),
        created_at=v.created_at,
        updated_at=v.updated_at,
    )


def _engine_out(e: Engines, versions: Sequence[EngineVersions]) -> Engine:
    return Engine(
        id=e.id,
        name=e.name,
        display_name=e.display_name,
        description=e.description,
        runtime=e.runtime,
        supported_model_types=list(e.supported_model_types or []),
        supported_vendors=list(e.supported_vendors or []),
        supported_os=list(e.supported_os or []),
        supported_formats=list(e.supported_formats or []),
        capabilities=e.capabilities or {},
        is_builtin=bool(e.is_builtin),
        is_enabled=bool(e.is_enabled),
        versions=[_version_out(v, e) for v in versions],
        created_at=e.created_at,
        updated_at=e.updated_at,
    )


def _check_out(row: EngineCompatChecks, einfo: compat.EngineInfo) -> CompatCheck:
    return CompatCheck(
        id=row.id,
        model_version_id=row.model_version_id,
        engine_version_id=row.engine_version_id,
        engine_name=einfo.name,
        engine_version=einfo.version,
        node_id=row.node_id,
        device_vendor=row.device_vendor,
        result=row.result,
        score=row.score,
        reasons=row.reasons or [],
        estimated_vram_bytes=row.estimated_vram_bytes,
        checked_at=row.checked_at,
    )


def _engine_info(engine: Engines, ev: EngineVersions) -> compat.EngineInfo:
    return compat.EngineInfo(
        engine_id=str(engine.id),
        engine_version_id=str(ev.id),
        name=engine.name,
        version=ev.version,
        runtime=engine.runtime,
        is_enabled=bool(engine.is_enabled),
        version_status=ev.status,
        supported_model_types=list(engine.supported_model_types or []),
        supported_vendors=list(engine.supported_vendors or []),
        supported_os=list(engine.supported_os or []),
        supported_formats=list(engine.supported_formats or []),
        capabilities=engine.capabilities or {},
        image=ev.image,
        artifacts=ev.artifacts or {},
        default_launch_flags=ev.default_launch_flags or {},
        launch_template=ev.launch_template or {},
        min_driver_version=ev.min_driver_version,
        requirements=ev.requirements or {},
    )


def _model_info(model: Models, mv: ModelVersions) -> compat.ModelVersionInfo:
    return compat.ModelVersionInfo(
        model_version_id=str(mv.id),
        model_type=model.type,
        format=mv.format,
        status=mv.status,
        total_size_bytes=int(mv.total_size_bytes or 0),
        parameter_count=mv.parameter_count,
        quantization=mv.quantization,
        config=mv.config or {},
        allow_pickle=bool(model.allow_pickle),
        trust_remote_code=bool(model.trust_remote_code),
        family=model.family,
        default_launch_flags=model.default_launch_flags or {},
    )


def _lowest_version(values: Sequence[Optional[str]]) -> Optional[str]:
    """The oldest parseable version (the weakest device decides), else None."""
    parsed = [(compat.version_tuple(v), v) for v in values if v]
    known = [(t, v) for t, v in parsed if t is not None]
    return min(known)[1] if known else None


def _target_from_node(
    node: Nodes, devices: Sequence[Devices], vendor: Optional[str]
) -> compat.Target:
    vendors = {d.vendor for d in devices}
    picked = vendor or (next(iter(vendors)) if len(vendors) == 1 else None)
    candidates = [d for d in devices if not picked or d.vendor == picked]
    vram = [int(d.vram_total_bytes or 0) for d in candidates]
    caps = node.capabilities or {}
    engines = [e.get("name") for e in caps.get("engines", []) if isinstance(e, dict)]
    # Driver / runtime versions only from the picked vendor's devices: an NVIDIA minimum must
    # never be compared with an AMD driver (mixed node, no vendor -> nothing to compare).
    return compat.Target(
        os=node.os,
        arch=node.arch,
        vendor=picked,
        device_vram_bytes=vram or None,
        driver_version=(
            _lowest_version([d.driver_version for d in candidates]) if picked else None
        ),
        runtime_version=(
            _lowest_version([d.runtime_version for d in candidates]) if picked else None
        ),
        node_engines=engines or None,
    )


def _targets_from_node(node: Nodes, devices: Sequence[Devices]) -> List[compat.Target]:
    """One target per device vendor (a mixed NVIDIA/AMD node is checked per vendor)."""
    vendors = sorted({d.vendor for d in devices})
    if len(vendors) <= 1:
        return [_target_from_node(node, devices, None)]
    return [_target_from_node(node, devices, v) for v in vendors]


def _validate_version_payload(engine: Engines, data: Dict[str, Any]) -> None:
    template = data.get("launch_template")
    if template:
        cmd = template.get("command")
        if not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) for c in cmd):
            raise ValidationError("launch_template.command must be a non-empty list of strings")
    if (
        engine.runtime == "container"
        and "image" in data
        and not data.get("image")
        and engine.name == "custom"
    ):
        raise ValidationError("A custom engine version needs a container image")
    pr = data.get("port_range")
    if pr is not None and (len(pr) != 2 or pr[0] >= pr[1]):
        raise ValidationError("port_range must be [start, end) with start < end")


def refresh_compat(
    current: Optional[Dict[str, Any]], seeded: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """
    ``current`` artifacts with the seed's host-compatibility keys (``COMPAT_KEYS``: min_glibc,
    fallbacks, container) copied onto every entry that still pins the same build (same url and
    sha256). None when nothing changes. Never touches what an entry downloads.
    """
    from app.services.engines.seed import COMPAT_KEYS

    if not isinstance(current, dict) or not isinstance(seeded, dict):
        return None
    merged = copy.deepcopy(current)
    changed = False
    for key, want in seeded.items():
        have = merged.get(key)
        if not isinstance(have, dict) or not isinstance(want, dict):
            continue
        if (have.get("url"), have.get("sha256")) != (
            want.get("url"),
            want.get("sha256"),
        ):
            continue  # an operator re-pinned this platform: leave it alone
        for k in COMPAT_KEYS:
            if k in want and have.get(k) != want[k]:
                have[k] = copy.deepcopy(want[k])
                changed = True
    return merged if changed else None


def _default_template(engine: Engines) -> Dict[str, Any]:
    for spec in SEED_ENGINE_VERSIONS:
        if spec["engine"] == engine.name:
            return dict(spec["launch_template"])
    return {
        "command": [
            "serve",
            "--port",
            "{{port}}",
            "--model",
            "{{model_dir}}",
            "{{flags}}",
        ],
        "env": {},
        "working_dir": None,
        "router_mode": False,
    }


def _default_health() -> Dict[str, Any]:
    return {
        "path": "/health",
        "method": "GET",
        "interval_seconds": 5,
        "timeout_seconds": 3,
        "healthy_statuses": [200],
        "startup_timeout_seconds": 600,
        "failure_threshold": 3,
    }


def _default_metrics(engine: Engines) -> Dict[str, Any]:
    for spec in SEED_ENGINE_VERSIONS:
        if spec["engine"] == engine.name:
            return dict(spec["metrics_config"])
    return {
        "path": None,
        "format": "none",
        "slots_path": None,
        "scrape_interval_seconds": 2,
        "derive_from_proxy": True,
        "metric_prefix": None,
    }
