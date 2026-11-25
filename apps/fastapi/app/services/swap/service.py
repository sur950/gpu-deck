"""
Swap service: the admin API side of the swap manager (list / get / create /
cancel / retry) plus ``SwapManager`` — the leader-only driver that walks every
non-terminal ``swap_operations`` row through ``SwapExecutor``.

Driver model (jobs process): the ``swap.drive`` job runs every
``SWAP_DRIVE_INTERVAL_SECONDS`` on every jobs replica but acts only on the one
holding ``jobs-leader``. Each active swap runs in its own thread/event loop
(a drain can take minutes) up to ``RULES_MAX_CONCURRENT_SWAPS`` at once, oldest
first; every write is fenced by the lease token, so a leader change hands the
swap over at the next step boundary (state is in the row, see executor.py).
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.exceptions import ConflictError, ResourceNotFoundError, ValidationError
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_db
from app.schemas.swaps_schema import SwapCreate, SwapOperationOut
from app.services.swap.executor import SwapExecutor
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

TERMINAL = ("completed", "failed", "rolled_back", "cancelled")
RETRYABLE = ("failed", "rolled_back", "cancelled")


def swap_out(row: Any, rule_name: Optional[str] = None, deployment_name: Optional[str] = None):
    data = {
        "id": row.id,
        "kind": row.kind,
        "state": row.state,
        "trigger": row.trigger,
        "rule_run_id": row.rule_run_id,
        "rule_name": rule_name,
        "layout_id": row.layout_id,
        "deployment_id": row.deployment_id,
        "deployment_name": deployment_name,
        "from_instance_id": row.from_instance_id,
        "to_instance_id": row.to_instance_id,
        "node_id": row.node_id,
        "plan": dict(row.plan or {}),
        "steps": [
            {
                k: v
                for k, v in dict(s).items()
                if k
                in (
                    "name",
                    "state",
                    "started_at",
                    "finished_at",
                    "command_id",
                    "error",
                    "details",
                )
            }
            for s in (row.steps or [])
        ],
        "current_step": int(row.current_step or 0),
        "rollback": dict(row.rollback or {}),
        "drain_timeout_seconds": int(row.drain_timeout_seconds or 0),
        "dry_run": bool(row.dry_run),
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "error_message": row.error_message,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    return SwapOperationOut.model_validate(data)


class SwapService:
    """Admin API operations on swap_operations."""

    def __init__(self, session: AsyncSession):
        from app.db.postgres.repositories.cluster_state_repository import (
            ClusterStateRepository,
        )
        from app.db.postgres.repositories.swap_operation_repository import (
            SwapOperationRepository,
        )

        self.session = session
        self.repo = SwapOperationRepository(session)
        self.state = ClusterStateRepository(session)

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db, scope="function")
    ) -> "SwapService":
        return SwapService(db)

    # ------------------------------------------------------------------ reads

    async def _enrich(self, rows: List[Any]) -> List[SwapOperationOut]:
        from app.db.postgres.models.generated_models import Deployments, RuleRuns, Rules
        from sqlalchemy import select

        run_ids = {r.rule_run_id for r in rows if r.rule_run_id}
        dep_ids = {r.deployment_id for r in rows if r.deployment_id}
        rule_names: Dict[Any, str] = {}
        dep_names: Dict[Any, str] = {}
        if run_ids:
            result = await self.session.execute(
                select(RuleRuns.id, Rules.name)
                .join(Rules, Rules.id == RuleRuns.rule_id)
                .where(RuleRuns.id.in_(list(run_ids)))
            )
            rule_names = {r[0]: r[1] for r in result.all()}
        if dep_ids:
            result = await self.session.execute(
                select(Deployments.id, Deployments.name).where(Deployments.id.in_(list(dep_ids)))
            )
            dep_names = {r[0]: r[1] for r in result.all()}
        return [
            swap_out(r, rule_names.get(r.rule_run_id), dep_names.get(r.deployment_id)) for r in rows
        ]

    async def list_swaps(self, **filters: Any) -> Tuple[List[SwapOperationOut], int]:
        rows, total = await self.repo.list(**filters)
        return await self._enrich(rows), total

    async def get_swap(self, swap_id: uuid.UUID) -> SwapOperationOut:
        row = await self.repo.get_by_id(swap_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Swap operation")
        return (await self._enrich([row]))[0]

    # ------------------------------------------------------------------ create

    async def _pick_instance(self, deployment_id: uuid.UUID, instance_id: Optional[uuid.UUID]):
        if instance_id is not None:
            inst = await self.state.get_instance(instance_id)
            if inst is None or inst.deployment_id != deployment_id:
                raise ResourceNotFoundError(resource_type="Instance")
            if inst.state not in ("ready", "draining", "loading", "pending", "error"):
                raise ConflictError(f"Instance is {inst.state}")
            return inst
        live = [i for i in await self.state.live_instances([deployment_id]) if i.state == "ready"]
        if not live:
            raise ConflictError("Deployment has no ready instance")
        return sorted(live, key=lambda i: i.ready_at or i.created_at, reverse=True)[0]

    async def _placement(self, deployment_id, node_id, device_ids, exclude=()):
        from app.services.rules.inputs import DbRuleInputs
        from app.services.swap.placement import plan_placement

        inputs = DbRuleInputs(self.session)
        deps = {str(d.id): d for d in await inputs.deployments()}
        dep = deps.get(str(deployment_id))
        if dep is None:
            raise ResourceNotFoundError(resource_type="Deployment")
        result = plan_placement(
            dep,
            await inputs.devices(),
            count=1,
            exclude_device_ids=exclude,
            node_id=node_id,
            device_ids=device_ids or None,
        )
        if not result.placements:
            raise ConflictError(f"No capacity for the deployment ({result.reason})")
        return result.placements[0]

    async def create_swap(
        self, payload: SwapCreate, created_by: Optional[uuid.UUID]
    ) -> SwapOperationOut:
        from app.services.audit import audit
        from app.services.deployments.external import (
            ensure_deployment_managed,
            ensure_instance_managed,
        )
        from app.services.swap.planner import SwapPlanner

        # Observed (external) deployments are never swapped: 409 EXTERNAL_DEPLOYMENT.
        await ensure_deployment_managed(self.session, payload.deployment_id)
        await ensure_instance_managed(self.session, payload.instance_id)
        dep = await self.state.get_deployment(payload.deployment_id)
        if dep is None:
            raise ResourceNotFoundError(resource_type="Deployment")
        spec: Dict[str, Any] = {
            "kind": payload.kind.value,
            "deployment_id": dep.id,
            "drain_timeout_seconds": payload.drain_timeout_seconds,
            "force_after_timeout": payload.force,
            "force": payload.force,
            "reason": "manual",
        }
        if payload.device_ids and payload.node_id is None:
            raise ValidationError("device_ids requires node_id")
        if payload.kind.value == "load":
            if dep.max_replicas is not None:
                live = await self.state.live_instances([dep.id])
                if len(live) >= int(dep.max_replicas):
                    raise ConflictError("Deployment is already at max_replicas")
            cand = await self._placement(dep.id, payload.node_id, payload.device_ids)
            spec.update(node_id=cand.node_id, device_ids=cand.device_ids, slice=cand.slice)
        else:
            inst = await self._pick_instance(dep.id, payload.instance_id)
            spec["instance_id"] = inst.id
            if await self.repo.active_for_instance(inst.id):
                raise ConflictError("A swap is already running for this instance")
            if payload.kind.value == "move":
                cand = await self._placement(
                    dep.id,
                    payload.node_id,
                    payload.device_ids,
                    exclude=() if payload.device_ids else list(inst.device_ids or []),
                )
                spec.update(node_id=cand.node_id, device_ids=cand.device_ids, slice=cand.slice)
        row = await SwapPlanner(self.session).create(
            spec,
            trigger="manual",
            deployment_id=dep.id,
            created_by=created_by,
            dry_run=payload.dry_run,
        )
        if payload.dry_run:
            now = datetime.now(timezone.utc)
            steps = [dict(s, state="skipped") for s in row.steps]
            await self.repo.persist_state(
                row, state="completed", steps=steps, started_at=now, finished_at=now
            )
        audit.record(
            "swap.created",
            resource_type="swap_operation",
            resource_id=row.id,
            resource_name=row.kind,
            after_state={
                "kind": row.kind,
                "plan": row.plan,
                "dry_run": payload.dry_run,
            },
        )
        return (await self._enrich([row]))[0]

    # ------------------------------------------------------------------ cancel / retry

    async def cancel(self, swap_id: uuid.UUID) -> SwapOperationOut:
        from app.services.audit import audit

        row = await self.repo.get_by_id(swap_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Swap operation")
        if row.state in TERMINAL:
            raise ConflictError(f"Swap is already {row.state}")
        cancelled_now = row.state == "pending" and row.started_at is None
        if cancelled_now:
            now = datetime.now(timezone.utc)
            steps = [dict(s, state="skipped") for s in row.steps or []]
            await self.repo.persist_state(
                row,
                state="cancelled",
                steps=steps,
                finished_at=now,
                error_message="cancelled by operator",
            )
        else:
            rollback = dict(row.rollback or {})
            rollback["cancel_requested"] = True
            await self.repo.persist_state(row, rollback=rollback)
        audit.record(
            "swap.cancel_requested",
            resource_type="swap_operation",
            resource_id=row.id,
            resource_name=row.kind,
        )
        out = (await self._enrich([row]))[0]
        if cancelled_now and not row.dry_run:
            # Never started, so no executor will announce it; commit first so a client
            # refetching on the frame sees the cancelled row.
            from app.services.alerts.live import publish_live

            await self.session.commit()
            await publish_live(
                "swap",
                {
                    "swap_id": str(out.id),
                    "state": "cancelled",
                    "kind": out.kind,
                    "deployment_id": out.deployment_id,
                    "deployment_name": out.deployment_name,
                    "node_id": out.node_id,
                    "rule_name": out.rule_name,
                    "trigger": out.trigger,
                    "error_message": "cancelled by operator",
                },
            )
        return out

    async def retry(self, swap_id: uuid.UUID, created_by: Optional[uuid.UUID]) -> SwapOperationOut:
        from app.services.audit import audit
        from app.services.swap.ports import make_step

        row = await self.repo.get_by_id(swap_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Swap operation")
        if row.state not in RETRYABLE:
            raise ConflictError(
                f"Only failed, rolled back or cancelled swaps can be retried ({row.state})"
            )
        plan = dict(row.plan or {})
        if row.from_instance_id is not None:
            inst = await self.state.get_instance(row.from_instance_id)
            if inst is None or inst.state not in ("ready", "draining", "error"):
                raise ConflictError("The source instance is no longer live; create a new swap")
        if plan.get("load"):
            plan["load"] = {**plan["load"], "instance_id": None}
        plan["depends_on"] = []
        plan["retry_of"] = str(row.id)
        steps = [make_step(step["name"]) for step in (row.steps or [])]
        new = await self.repo.create(
            kind=row.kind,
            state="pending",
            trigger="manual",
            rule_run_id=None,
            layout_id=row.layout_id,
            deployment_id=row.deployment_id,
            from_instance_id=row.from_instance_id,
            to_instance_id=None,
            node_id=row.node_id,
            plan=plan,
            steps=steps,
            current_step=0,
            rollback={},
            drain_timeout_seconds=row.drain_timeout_seconds,
            dry_run=False,
            created_by=created_by,
        )
        audit.record(
            "swap.retried",
            resource_type="swap_operation",
            resource_id=new.id,
            resource_name=new.kind,
            metadata={"retry_of": str(row.id)},
        )
        return (await self._enrich([new]))[0]


# ---------------------------------------------------------------------------
# Leader-only driver
# ---------------------------------------------------------------------------


def build_executor(fence=None, **overrides: Any) -> SwapExecutor:
    """SwapExecutor wired to the production adapters (overrides replace any port)."""
    from app.services.alerts.live import publish_live
    from app.services.swap.adapters import (
        AlertsAdapter,
        B3DeploymentPort,
        DbLoadArgs,
        DbSwapStore,
        HubCommandPort,
        ProxyHealthProbe,
        RedisLockPort,
        RedisRoutingPort,
    )

    ports = {
        "store": DbSwapStore(fence=fence),
        "commands": HubCommandPort(),
        "routing": RedisRoutingPort(),
        "load_args": DbLoadArgs(),
        "health": ProxyHealthProbe(),
        "locks": RedisLockPort(),
        "alerts": AlertsAdapter(),
        "live": publish_live,
        "deployments": B3DeploymentPort() if B3DeploymentPort.available() else None,
    }
    ports.update(overrides)
    return SwapExecutor(
        fence=fence.check if fence is not None else None,
        max_duration_seconds=settings.SWAP_MAX_DURATION_SECONDS,
        lock_ttl_seconds=settings.SWAP_DEVICE_LOCK_TTL_SECONDS,
        owner=f"{settings.HA_INSTANCE_ID}:{fence.version if fence else 0}",
        **ports,
    )


class SwapManager:
    """Starts one driver thread per active swap (bounded), never twice for the same swap."""

    def __init__(self, max_concurrent: Optional[int] = None, executor_factory=build_executor):
        self.max_concurrent = int(max_concurrent or settings.RULES_MAX_CONCURRENT_SWAPS)
        self.executor_factory = executor_factory
        self._inflight: set = set()
        self._lock = threading.Lock()

    def inflight(self) -> List[str]:
        with self._lock:
            return sorted(self._inflight)

    async def active_swap_ids(self) -> List[uuid.UUID]:
        from app.db.postgres.connection import get_session_maker
        from app.db.postgres.repositories.swap_operation_repository import (
            SwapOperationRepository,
        )

        async with get_session_maker()() as session:
            rows = await SwapOperationRepository(session).list_active()
            return [r.id for r in rows]

    def start(self, swap_ids: List[uuid.UUID], fence, threaded: bool = True) -> List[str]:
        started = []
        for swap_id in swap_ids:
            key = str(swap_id)
            with self._lock:
                if key in self._inflight:
                    continue
                if len(self._inflight) >= self.max_concurrent:
                    break
                self._inflight.add(key)
            started.append(key)
            if threaded:
                threading.Thread(
                    target=self._run_thread,
                    args=(swap_id, fence),
                    name=f"swap-{key[:8]}",
                    daemon=True,
                ).start()
        return started

    def _run_thread(self, swap_id: uuid.UUID, fence) -> None:
        try:
            asyncio.run(self.drive_one(swap_id, fence))
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"swap driver thread for {swap_id} crashed: {exc}")

    async def drive_one(self, swap_id: uuid.UUID, fence) -> Any:
        try:
            executor = self.executor_factory(fence)
            return await executor.drive(swap_id)
        finally:
            with self._lock:
                self._inflight.discard(str(swap_id))


swap_manager = SwapManager()
