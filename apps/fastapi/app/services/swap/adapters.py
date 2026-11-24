"""
Production implementations of the swap ports (app/services/swap/ports.py).

- ``DbSwapStore``       — ``swap_operations`` / ``instances`` / ``device_slices`` via short
                          sessions that commit every write (the state machine must survive a
                          crash between steps). Swap writes are fenced by the jobs-leader lease.
- ``HubCommandPort``    — ``AgentHub.send_command(wait=False)`` + polling ``agent_commands``
                          until a terminal state or ``expires_at`` (resumable after restart).
- ``RedisRoutingPort``  — patches the API-layer routing table (serving.md section 7) in place,
                          keeps per-instance overrides and pushes the node proxy routing section.
- ``DbLoadArgs``        — builds ``LoadModelArgs`` (agent-protocol command.json) from the
                          deployment, model version, engine version, node and devices.
- ``ProxyHealthProbe``  — one ``GET /instances/{id}/_health`` through the node proxy (mTLS).
- ``RedisLockPort``     — per-device locks (``SET NX PX`` + owner-checked release).
- ``ModelCachePrefetch``— ``ModelCacheService.prefetch``.
- ``AlertsAdapter``     — ``app.services.alerts.service.emit``.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from app.core.logger import get_logger
from app.core.settings import settings
from app.services.swap.ports import CommandResult, FenceLost, InstanceRecord, Swap

logger = get_logger(__name__)

TERMINAL_COMMAND_STATES = ("succeeded", "failed", "rejected", "timed_out", "cancelled")


def _session_factory_default():
    from app.db.postgres.connection import get_session_maker

    return get_session_maker()()


def _uuid(value: Any) -> Optional[uuid.UUID]:
    if value in (None, ""):
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _json_default(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default)


def swap_from_row(row: Any) -> Swap:
    return Swap(
        id=row.id,
        kind=row.kind,
        state=row.state,
        trigger=row.trigger,
        deployment_id=row.deployment_id,
        from_instance_id=row.from_instance_id,
        to_instance_id=row.to_instance_id,
        node_id=row.node_id,
        plan=dict(row.plan or {}),
        steps=[dict(s) for s in (row.steps or [])],
        current_step=int(row.current_step or 0),
        rollback=dict(row.rollback or {}),
        drain_timeout_seconds=int(row.drain_timeout_seconds or 0),
        dry_run=bool(row.dry_run),
        started_at=row.started_at,
        finished_at=row.finished_at,
        error_message=row.error_message,
        rule_run_id=row.rule_run_id,
        layout_id=row.layout_id,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def instance_from_row(row: Any) -> InstanceRecord:
    return InstanceRecord(
        id=row.id,
        deployment_id=row.deployment_id,
        node_id=row.node_id,
        device_ids=list(row.device_ids or []),
        state=row.state,
        health=row.health,
        port=row.port,
        served_model_name=row.served_model_name,
        traffic_weight=int(row.traffic_weight if row.traffic_weight is not None else 100),
        engine_process_id=row.engine_process_id,
        resources=dict(row.resources or {}),
        ready_at=row.ready_at,
        restart_count=int(row.restart_count or 0),
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class DbSwapStore:
    """SwapStore backed by Postgres. Every call is its own committed transaction."""

    def __init__(self, session_factory: Optional[Callable[[], Any]] = None, fence: Any = None):
        self.session_factory = session_factory or _session_factory_default
        self.fence = fence  # app.services.ha.service.LeaseFence or None (admin API writes)
        self._actors: Dict[str, Dict[str, Any]] = {}
        self._labels: Dict[str, Dict[str, Any]] = {}

    async def get_swap(self, swap_id: uuid.UUID) -> Optional[Swap]:
        from app.db.postgres.repositories.swap_operation_repository import (
            SwapOperationRepository,
        )

        async with self.session_factory() as session:
            row = await SwapOperationRepository(session).get_by_id(swap_id)
            if row is None:
                return None
            swap = swap_from_row(row)
            actor = {"actor_type": "system", "actor_id": None}
            labels: Dict[str, Any] = {}
            from app.db.postgres.models.generated_models import (
                Deployments,
                RuleRuns,
                Rules,
            )
            from sqlalchemy import select

            if swap.rule_run_id:
                found = (
                    await session.execute(
                        select(RuleRuns.rule_id, Rules.name)
                        .join(Rules, Rules.id == RuleRuns.rule_id)
                        .where(RuleRuns.id == swap.rule_run_id)
                    )
                ).first()
                if found is not None:
                    labels["rule_name"] = found[1]
                if swap.trigger in ("rule", "auto_heal"):
                    actor = {
                        "actor_type": "rule",
                        "actor_id": found[0] if found else None,
                    }
            if actor["actor_type"] == "system" and swap.created_by:
                actor = {"actor_type": "admin", "actor_id": swap.created_by}
            if swap.deployment_id:
                labels["deployment_name"] = (
                    await session.execute(
                        select(Deployments.name).where(Deployments.id == swap.deployment_id)
                    )
                ).scalar_one_or_none()
            self._actors[str(swap.id)] = actor
            self._labels[str(swap.id)] = labels
            return swap

    def swap_labels(self, swap_id: uuid.UUID) -> Dict[str, Any]:
        """Display names (deployment_name, rule_name) for live events, from ``get_swap``."""
        return self._labels.get(str(swap_id), {})

    async def save_swap(self, swap: Swap) -> None:
        from sqlalchemy import text

        fence_sql = ""
        params: Dict[str, Any] = {
            "id": swap.id,
            "state": swap.state,
            "plan": dumps(swap.plan),
            "steps": dumps(swap.steps),
            "current_step": int(swap.current_step),
            "rollback": dumps(swap.rollback),
            "to_instance_id": swap.to_instance_id,
            "started_at": swap.started_at,
            "finished_at": swap.finished_at,
            "error_message": (swap.error_message or None) and str(swap.error_message)[:2048],
        }
        if self.fence is not None:
            fence_sql = " AND " + self.fence.sql_condition()
            params.update(self.fence.params())
        sql = text(f"""
            UPDATE swap_operations
               SET state = CAST(:state AS swap_state),
                   plan = CAST(:plan AS jsonb),
                   steps = CAST(:steps AS jsonb),
                   current_step = :current_step,
                   rollback = CAST(:rollback AS jsonb)
                              || CASE WHEN (rollback ->> 'cancel_requested') = 'true'
                                      THEN '{{"cancel_requested": true}}'::jsonb
                                      ELSE '{{}}'::jsonb END,
                   to_instance_id = :to_instance_id,
                   started_at = :started_at,
                   finished_at = :finished_at,
                   error_message = :error_message,
                   updated_at = now()
             WHERE id = :id{fence_sql}
            """)
        async with self.session_factory() as session:
            result = await session.execute(sql, params)
            await session.commit()
        if (result.rowcount or 0) == 0 and self.fence is not None:
            raise FenceLost(f"swap {swap.id}: lease {self.fence.name} token {self.fence.version}")

    async def cancel_requested(self, swap_id: uuid.UUID) -> bool:
        from sqlalchemy import text

        async with self.session_factory() as session:
            value = (
                await session.execute(
                    text(
                        "SELECT rollback ->> 'cancel_requested' FROM swap_operations WHERE id = :id"
                    ),
                    {"id": swap_id},
                )
            ).scalar_one_or_none()
        return value == "true"

    async def swap_states(self, swap_ids: Sequence[uuid.UUID]) -> Dict[str, str]:
        from app.db.postgres.models.generated_models import SwapOperations
        from sqlalchemy import select

        if not swap_ids:
            return {}
        async with self.session_factory() as session:
            rows = await session.execute(
                select(SwapOperations.id, SwapOperations.state).where(
                    SwapOperations.id.in_(list(swap_ids))
                )
            )
            return {str(r[0]): r[1] for r in rows.all()}

    async def get_instance(self, instance_id: uuid.UUID) -> Optional[InstanceRecord]:
        from app.db.postgres.repositories.cluster_state_repository import (
            ClusterStateRepository,
        )

        if instance_id is None:
            return None
        async with self.session_factory() as session:
            row = await ClusterStateRepository(session).get_instance(instance_id)
            return instance_from_row(row) if row is not None else None

    async def update_instance(self, instance_id: uuid.UUID, **fields: Any) -> None:
        from app.db.postgres.repositories.cluster_state_repository import (
            ClusterStateRepository,
        )

        fields = {k: v for k, v in fields.items()}
        if "state_message" in fields and fields["state_message"]:
            fields["state_message"] = str(fields["state_message"])[:2048]
        if "health_message" in fields and fields["health_message"]:
            fields["health_message"] = str(fields["health_message"])[:1024]
        fields["updated_at"] = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            await ClusterStateRepository(session).update_instance(instance_id, **fields)
            await session.commit()

    async def create_instance(
        self,
        deployment_id: uuid.UUID,
        node_id: uuid.UUID,
        device_ids: Sequence[uuid.UUID],
        traffic_weight: int,
        served_model_name: Optional[str],
        instance_id: Optional[uuid.UUID] = None,
    ) -> InstanceRecord:
        from app.db.postgres.repositories.cluster_state_repository import (
            ClusterStateRepository,
        )

        async with self.session_factory() as session:
            row = await ClusterStateRepository(session).create_instance(
                deployment_id,
                node_id,
                list(device_ids),
                traffic_weight=traffic_weight,
                served_model_name=served_model_name,
                instance_id=instance_id,
            )
            record = instance_from_row(row)
            await session.commit()
        return record

    async def reserve_slices(
        self,
        instance_id: uuid.UUID,
        deployment_id: uuid.UUID,
        device_ids: Sequence[uuid.UUID],
        vram_bytes: int,
        compute_pct: int,
    ) -> None:
        from app.db.postgres.repositories.cluster_state_repository import (
            ClusterStateRepository,
        )

        async with self.session_factory() as session:
            await ClusterStateRepository(session).reserve_slices(
                instance_id, deployment_id, device_ids, vram_bytes, compute_pct
            )
            await session.commit()

    async def set_slices_status(self, instance_id: uuid.UUID, status: str) -> None:
        from app.db.postgres.repositories.cluster_state_repository import (
            ClusterStateRepository,
        )

        async with self.session_factory() as session:
            await ClusterStateRepository(session).set_slices_status(instance_id, status)
            await session.commit()

    async def set_deployment_status(
        self, deployment_id: uuid.UUID, status: str, message: Optional[str]
    ) -> None:
        from app.db.postgres.repositories.cluster_state_repository import (
            ClusterStateRepository,
        )

        async with self.session_factory() as session:
            await ClusterStateRepository(session).set_deployment_status(
                deployment_id, status, (message or None) and str(message)[:2048]
            )
            await session.commit()

    async def after_finished(self, swap: Swap) -> None:
        """
        Keep ``deployments.replicas`` equal to the live instance count (clamped to the
        deployment's bounds) so the deployments reconciler does not undo a rule's scale/unload.
        A deployment left with no live instance after a completed swap is ``stopped``.
        """
        if swap.state != "completed" or swap.deployment_id is None:
            return
        await sync_deployment_replicas(self.session_factory, swap.deployment_id, swap.kind)

    async def write_audit(
        self,
        event_type: str,
        resource_type: str,
        resource_id: Optional[uuid.UUID],
        resource_name: Optional[str],
        before: Optional[Dict[str, Any]],
        after: Optional[Dict[str, Any]],
        message: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
        outcome: str = "success",
    ) -> None:
        from app.services.audit import audit

        swap_id = (metadata or {}).get("swap_operation_id") or (
            str(resource_id) if resource_type == "swap_operation" else None
        )
        actor = self._actors.get(str(swap_id), {"actor_type": "system", "actor_id": None})
        audit.record(
            event_type,
            actor_type=actor["actor_type"],
            actor_id=actor["actor_id"],
            resource_type=resource_type,
            resource_id=resource_id,
            resource_name=resource_name,
            before_state=before,
            after_state=after,
            message=message,
            metadata=metadata,
            outcome=outcome,
        )


async def sync_deployment_replicas(session_factory, deployment_id: uuid.UUID, kind: str) -> None:
    from sqlalchemy import text

    async with session_factory() as session:
        await session.execute(
            text("""
                WITH live AS (
                    SELECT COUNT(*) AS n FROM instances
                     WHERE deployment_id = :dep
                       AND state IN ('pending','loading','ready','draining')
                )
                UPDATE deployments d
                   SET replicas = GREATEST(d.min_replicas,
                                           LEAST((SELECT n FROM live), d.max_replicas)),
                       status = CASE
                           WHEN (SELECT n FROM live) = 0 THEN 'stopped'::deployment_status
                           WHEN d.status IN ('stopped','pending','scheduling','scaling','degraded',
                                             'failed') THEN 'running'::deployment_status
                           ELSE d.status END,
                       status_message = CASE WHEN (SELECT n FROM live) = 0
                           THEN :msg ELSE d.status_message END,
                       updated_at = now()
                 WHERE d.id = :dep AND d.origin = 'managed'
                """),
            {"dep": deployment_id, "msg": f"no live instance after swap {kind}"},
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Commands (agent hub)
# ---------------------------------------------------------------------------


class HubCommandPort:
    """Send through the AgentHub without blocking; poll ``agent_commands`` for the result."""

    def __init__(
        self,
        session_factory: Optional[Callable[[], Any]] = None,
        poll_seconds: Optional[float] = None,
        hub: Any = None,
    ):
        self.session_factory = session_factory or _session_factory_default
        self.poll = float(poll_seconds or settings.SWAP_COMMAND_POLL_SECONDS)
        self._hub = hub

    def hub(self):
        if self._hub is None:
            from app.services.agent_hub.service import AgentHub

            self._hub = AgentHub
        return self._hub

    async def send(
        self,
        node_id: uuid.UUID,
        command: str,
        args: Dict[str, Any],
        timeout_seconds: int,
        swap_operation_id: Optional[uuid.UUID] = None,
        instance_id: Optional[uuid.UUID] = None,
    ) -> uuid.UUID:
        outcome = await self.hub().send_command(
            node_id,
            command,
            json.loads(dumps(args)),
            timeout=int(max(1, timeout_seconds)),
            swap_operation_id=swap_operation_id,
            instance_id=instance_id,
            wait=False,
        )
        return outcome.command_id

    async def wait(self, command_id: uuid.UUID, timeout_seconds: float) -> CommandResult:
        from app.db.postgres.models.generated_models import AgentCommands
        from sqlalchemy import select

        loop = asyncio.get_running_loop()
        local_deadline = loop.time() + float(timeout_seconds)
        while True:
            async with self.session_factory() as session:
                row = (
                    await session.execute(
                        select(AgentCommands).where(AgentCommands.id == command_id)
                    )
                ).scalar_one_or_none()
            if row is None:
                return CommandResult(
                    "failed",
                    error_code="unknown_command",
                    error_message="agent command row not found",
                )
            if row.state in TERMINAL_COMMAND_STATES:
                return CommandResult(row.state, row.result, row.error_code, row.error_message)
            now = datetime.now(timezone.utc)
            expired = row.expires_at is not None and now > row.expires_at
            if expired or loop.time() > local_deadline:
                await self._mark_timed_out(command_id)
                return CommandResult(
                    "timed_out",
                    None,
                    "timeout",
                    "no terminal command_result before the deadline",
                )
            await asyncio.sleep(self.poll)

    async def _mark_timed_out(self, command_id: uuid.UUID) -> None:
        from app.db.postgres.repositories.swap_operation_repository import (
            AgentCommandRepository as SwapCommandRepository,
        )

        try:
            async with self.session_factory() as session:
                await SwapCommandRepository(session).mark_timed_out(command_id)
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"mark_timed_out({command_id}) failed: {exc}")

    async def cancel(self, command_id: uuid.UUID) -> None:
        from app.db.postgres.repositories.swap_operation_repository import (
            AgentCommandRepository as SwapCommandRepository,
        )

        async with self.session_factory() as session:
            repo = SwapCommandRepository(session)
            row = await repo.get_by_id(command_id)
            if row is None or row.state in TERMINAL_COMMAND_STATES:
                return
            node_id = row.node_id
            await repo.mark_cancelled(command_id)
            await session.commit()
        try:
            await self.hub().send_command(
                node_id,
                "cancel",
                {"target_command_id": str(command_id)},
                timeout=60,
                wait=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"cancel command for {command_id} not sent: {exc}")


# ---------------------------------------------------------------------------
# Routing (API layer table + node proxy)
# ---------------------------------------------------------------------------


class RedisRoutingPort:
    """
    Stops / resumes API-layer routing for one instance.

    Preferred path: ``routing_publisher.set_instance_accepting`` (its
    not-accepting set + a full table rebuild from the DB). Fallback when that
    module is unavailable:

    1. ``gpu-deck:routing:overrides`` (hash ``instance_id -> {"accepting", "weight"}``)
       is the durable record of what the swap manager wants; a table builder
       applies it on every rebuild.
    2. The live table ``gpu-deck:routing:v1`` is patched in place (optimistic
       WATCH/MULTI) and the new version is published on ``gpu-deck:routing:updated``
       so every API-layer replica reloads within milliseconds.
    3. The node proxy gets a ``config_push`` of its ``routing`` section (built
       from ``instances.state``/``traffic_weight``, which the executor wrote first).
    """

    def __init__(self, redis_factory=None, session_factory=None, hub: Any = None):
        self.redis_factory = redis_factory
        self.session_factory = session_factory or _session_factory_default
        self._hub = hub

    async def _redis(self):
        if self.redis_factory is not None:
            return await self.redis_factory()
        from app.db.redis.client import get_redis

        return await get_redis()

    @staticmethod
    def keys():
        from app.db.redis.client import redis_key

        return (
            redis_key("routing", "v1"),
            redis_key("routing", "updated"),
            redis_key("routing", "overrides"),
        )

    @staticmethod
    def _publisher():
        """The routing publisher (rebuilds the whole table from the DB), when present."""
        try:
            from app.services.deployments import routing_publisher

            return routing_publisher
        except Exception:  # noqa: BLE001
            return None

    async def set_accepting(self, instance_id: uuid.UUID, accepting: bool) -> None:
        publisher = self._publisher()
        if publisher is not None:
            try:
                await publisher.set_instance_accepting(instance_id, bool(accepting))
                await self._push_node(instance_id)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"routing publisher failed ({exc}); patching the table directly")
        await self._apply(instance_id, {"accepting": bool(accepting)})

    async def set_weight(self, instance_id: uuid.UUID, weight: int) -> None:
        publisher = self._publisher()
        if publisher is not None:
            try:
                # the weight itself is read from instances.traffic_weight
                await publisher.publish_routing_table()
                await self._push_node(instance_id)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"routing publisher failed ({exc}); patching the table directly")
        await self._apply(instance_id, {"weight": int(weight)})

    async def refresh(self) -> None:
        publisher = self._publisher()
        if publisher is not None:
            try:
                await publisher.publish_routing_table()
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"routing publish failed: {exc}")
        table_key, channel, _ = self.keys()
        try:
            client = await self._redis()
            raw = await client.get(table_key)
            version = json.loads(raw).get("version", 0) if raw else 0
            await client.publish(channel, json.dumps({"version": version}))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"routing refresh publish failed: {exc}")

    async def _apply(self, instance_id: uuid.UUID, patch: Dict[str, Any]) -> None:
        table_key, channel, overrides_key = self.keys()
        iid = str(instance_id)
        try:
            client = await self._redis()
            current = await client.hget(overrides_key, iid)
            merged = {**(json.loads(current) if current else {}), **patch}
            if merged.get("accepting", True) and merged.get("weight", 100) == 100:
                await client.hdel(overrides_key, iid)
            else:
                await client.hset(overrides_key, iid, json.dumps(merged))
            version = await self._patch_table(client, table_key, iid, patch)
            if version is not None:
                await client.publish(channel, json.dumps({"version": version}))
        except Exception as exc:  # noqa: BLE001 - the proxy push below still stops traffic
            logger.warning(f"routing table patch for {iid} failed: {exc}")
        await self._push_node(instance_id)

    @staticmethod
    async def _patch_table(
        client, table_key: str, iid: str, patch: Dict[str, Any]
    ) -> Optional[int]:
        for _ in range(5):
            async with client.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(table_key)
                    raw = await pipe.get(table_key)
                    if not raw:
                        await pipe.unwatch()
                        return None
                    table = json.loads(raw)
                    hit = False
                    for model in (table.get("models") or {}).values():
                        for inst in model.get("instances") or []:
                            if str(inst.get("instance_id")) == iid:
                                inst.update(patch)
                                hit = True
                    if not hit:
                        await pipe.unwatch()
                        return None
                    table["version"] = int(table.get("version") or 0) + 1
                    table["generated_at"] = datetime.now(timezone.utc).isoformat()
                    pipe.multi()
                    pipe.set(table_key, json.dumps(table))
                    await pipe.execute()
                    return table["version"]
                except Exception as exc:  # noqa: BLE001 - WatchError: retry
                    if type(exc).__name__ != "WatchError":
                        raise
        return None

    async def _push_node(self, instance_id: uuid.UUID) -> None:
        try:
            from app.db.postgres.repositories.cluster_state_repository import (
                ClusterStateRepository,
            )

            async with self.session_factory() as session:
                inst = await ClusterStateRepository(session).get_instance(instance_id)
            if inst is None:
                return
            hub = self._hub
            if hub is None:
                from app.services.agent_hub.service import AgentHub

                hub = AgentHub
            await hub.push_config(inst.node_id, sections=["routing"])
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"routing config_push for instance {instance_id} failed: {exc}")


# ---------------------------------------------------------------------------
# Instance lifecycle through the deployments service
# ---------------------------------------------------------------------------


class B3DeploymentPort:
    """``DeploymentPort`` over ``DeploymentService`` (one committed session per call)."""

    def __init__(self, session_factory=None):
        self.session_factory = session_factory or _session_factory_default

    @staticmethod
    def available() -> bool:
        try:
            from app.services.deployments.service import DeploymentService

            return all(
                hasattr(DeploymentService, name)
                for name in (
                    "place_instance",
                    "start_instance",
                    "stop_instance",
                    "for_session",
                )
            )
        except Exception:  # noqa: BLE001
            return False

    async def place_instance(self, deployment_id, node_id, device_ids, slice_spec, traffic_weight):
        from app.services.deployments.service import DeploymentService

        async with self.session_factory() as session:
            svc = DeploymentService.for_session(session)
            try:
                row = await svc.place_instance(
                    deployment_id,
                    node_id=node_id,
                    device_ids=list(device_ids or []) or None,
                    slice_spec=slice_spec or None,
                    traffic_weight=int(traffic_weight),
                )
            except Exception as exc:  # noqa: BLE001 - NO_CAPACITY etc. fail the step cleanly
                from app.services.swap.ports import StepFailure

                raise StepFailure(f"placement failed: {exc}", "no_capacity")
            record = instance_from_row(row)
            await session.commit()
        return record

    async def start_instance(self, instance_id, swap_operation_id):
        from app.services.deployments.service import DeploymentService

        async with self.session_factory() as session:
            command_id = await DeploymentService.for_session(session).start_instance(
                instance_id, swap_operation_id=swap_operation_id
            )
            await session.commit()
        return command_id

    async def stop_instance(self, instance_id, force, drain_timeout_seconds, swap_operation_id):
        from app.services.deployments.service import DeploymentService

        async with self.session_factory() as session:
            command_id = await DeploymentService.for_session(session).stop_instance(
                instance_id,
                force=bool(force),
                drain_timeout_seconds=int(drain_timeout_seconds),
                swap_operation_id=swap_operation_id,
            )
            await session.commit()
        return command_id


# ---------------------------------------------------------------------------
# Load args
# ---------------------------------------------------------------------------


class DbLoadArgs:
    """Builds ``LoadModelArgs`` (libs/contracts/agent-protocol/command.json)."""

    def __init__(self, session_factory=None):
        self.session_factory = session_factory or _session_factory_default

    async def build_load_args(
        self, instance: InstanceRecord, slice_spec: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        from app.db.postgres.repositories.cluster_state_repository import (
            ClusterStateRepository,
        )
        from app.db.postgres.repositories.inventory_repository import (
            InventoryRepository,
        )

        async with self.session_factory() as session:
            repo = ClusterStateRepository(session)
            bundle = await repo.model_bundle(instance.deployment_id)
            if bundle is None or bundle["model"] is None or bundle["version"] is None:
                from app.services.swap.ports import StepFailure

                raise StepFailure("deployment / model version not found", "deployment_not_found")
            node = await repo.get_node(instance.node_id)
            devices = await repo.get_devices(instance.device_ids)
            catalog = await InventoryRepository(session).engine_catalog_for(
                getattr(node, "os", None),
                getattr(node, "arch", None),
                getattr(node, "id", None),
            )
            source = await self._model_source(session, bundle)
        dep, model, version = bundle["deployment"], bundle["model"], bundle["version"]
        engine_version, engine = bundle["engine_version"], bundle["engine"]
        engine_spec = next(
            (e for e in catalog if e["engine_version_id"] == str(dep.engine_version_id)),
            None,
        )
        if engine_spec is None:
            engine_spec = {
                "engine_version_id": str(dep.engine_version_id),
                "name": getattr(engine, "name", "custom"),
                "version": getattr(engine_version, "version", "unknown"),
                "runtime": getattr(engine, "runtime", "native"),
                "image": getattr(engine_version, "image", None),
                "artifact": None,
                "launch_template": dict(getattr(engine_version, "launch_template", None) or {})
                or {"command": ["{{engine}}"]},
                "health": dict(getattr(engine_version, "health_config", None) or {}),
                "metrics": dict(getattr(engine_version, "metrics_config", None) or {}),
                "port_range": [10240, 10340],
                "capabilities": dict(getattr(engine, "capabilities", None) or {}),
            }
        engine_name = engine_spec["name"]
        flags = self._flags(engine_name, engine_version, model, dep)
        cache_root = (getattr(node, "cache_dir", None) or "").rstrip("/") or (
            "/usr/local/var/gpudeck/cache"
            if getattr(node, "os", None) == "macos"
            else "/var/lib/gpudeck/cache"
        )
        served = instance.served_model_name or dep.name
        slice_obj = None
        if slice_spec and slice_spec.get("vram_bytes"):
            slice_obj = {
                "vram_bytes": int(slice_spec["vram_bytes"]),
                "compute_pct": int(slice_spec.get("compute_pct", 100)),
            }
        by_id = {str(d.id): d for d in devices}
        device_list = []
        for dev_id in instance.device_ids:
            d = by_id.get(str(dev_id))
            if d is None:
                continue
            device_list.append(
                {
                    "device_id": str(d.id),
                    "index": int(d.index),
                    "vendor": d.vendor,
                    "slice": slice_obj,
                }
            )
        health_cfg = engine_spec.get("health") or {}
        return {
            "instance_id": str(instance.id),
            "deployment_id": str(dep.id),
            "engine": engine_spec,
            "model": {
                "model_id": str(model.id),
                "model_version_id": str(version.id),
                "name": model.name,
                "served_model_name": served,
                "type": model.type,
                "format": version.format,
                "files": [
                    {
                        "model_file_id": str(f.id),
                        "path": f.path,
                        "format": f.format,
                        "size_bytes": int(f.size_bytes or 0),
                        "sha256": f.sha256,
                        "is_required": bool(f.is_required),
                    }
                    for f in bundle["files"]
                ],
                "cache_dir": f"{cache_root}/{version.id}/",
                "source": source,
                "config": dict(version.config or {}),
                "trust_remote_code": bool(model.trust_remote_code),
                "allow_pickle": bool(model.allow_pickle),
            },
            "devices": device_list,
            "port": int(instance.port or 0),
            "engine_process_id": (
                str(instance.engine_process_id) if instance.engine_process_id else None
            ),
            "launch": {
                "flags": flags,
                "env": {str(k): str(v) for k, v in (dep.env_vars or {}).items()},
                "context_size": dep.context_size or model.default_context_size,
                "tool_parser": dep.tool_parser or model.tool_parser_default,
                "control_vectors": list(dep.control_vectors or []),
                "tp_size": int(dep.tp_size or 1),
                "pp_size": int(dep.pp_size or 1),
                "preset": dep.preset,
                "pd_split": bool(dep.pd_split),
                "pd_role": None,
                "pd_config": dict(dep.pd_config or {}),
                "spec_decoding": dict(dep.spec_decoding or {}),
                "kv_sharing": bool(dep.kv_sharing),
                "kv_sharing_config": dict(dep.kv_sharing_config or {}),
            },
            "draft_model": None,
            "health_timeout_seconds": int(health_cfg.get("startup_timeout_seconds") or 600),
        }

    @staticmethod
    def _flags(engine_name, engine_version, model, dep) -> Dict[str, Any]:
        try:
            from app.services.engines.presets import merge_launch_flags, preset_flags

            return merge_launch_flags(
                getattr(engine_version, "default_launch_flags", None),
                getattr(model, "default_launch_flags", None),
                preset_flags(engine_name, dep.preset),
                dep.launch_flags,
            )
        except Exception:  # noqa: BLE001 - deployments presets missing: plain layering
            merged: Dict[str, Any] = {}
            for layer in (
                getattr(engine_version, "default_launch_flags", None) or {},
                getattr(model, "default_launch_flags", None) or {},
                dep.launch_flags or {},
            ):
                merged.update(layer)
            return merged

    @staticmethod
    async def _model_source(session, bundle) -> Optional[Dict[str, Any]]:
        try:
            from app.services.model_cache.service import ModelCacheService

            return await ModelCacheService.for_session(session).model_source(
                bundle["model"], bundle["version"]
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"model source via ModelCacheService unavailable: {exc}")
            model = bundle["model"]
            return {
                "kind": model.source,
                "repo_id": model.repo_id,
                "revision": bundle["version"].revision,
                "endpoint": None,
                "token": None,
            }


# ---------------------------------------------------------------------------
# Health probe
# ---------------------------------------------------------------------------


class ProxyHealthProbe:
    """
    ``GET https://<proxy>/instances/{id}/_health`` with the control-plane client
    certificate. Returns ``skipped`` (not a failure) when no client cert is
    configured: the agent's own ready+healthy result from ``load_model`` is
    then the health signal.
    """

    def __init__(self, session_factory=None, attempts: int = 3, delay_seconds: float = 2.0):
        self.session_factory = session_factory or _session_factory_default
        self.attempts = attempts
        self.delay = delay_seconds

    async def probe(self, instance: InstanceRecord) -> CommandResult:
        import httpx

        cert = getattr(settings, "CONTROL_PLANE_CLIENT_CERT_PATH", None)
        key = getattr(settings, "CONTROL_PLANE_CLIENT_KEY_PATH", None)
        ca = getattr(settings, "API_LAYER_CA_BUNDLE_PATH", None)
        if not cert or not key:
            return CommandResult("skipped", error_message="no control-plane client certificate")
        from app.db.postgres.repositories.cluster_state_repository import (
            ClusterStateRepository,
        )

        async with self.session_factory() as session:
            node = await ClusterStateRepository(session).get_node(instance.node_id)
        host = getattr(node, "proxy_address", None) or (
            str(node.ip_address) if node is not None and node.ip_address else None
        )
        if not host:
            return CommandResult("skipped", error_message="node has no proxy address")
        url = f"https://{host}:{node.proxy_port}/instances/{instance.id}/_health"
        # Same TLS policy as the API layer: chain verified against the internal CA and the
        # peer identified by CN == node id (proxies are dialled by IP, not a hostname).
        from app.services.pki.proxy_tls import node_proxy_ssl_context

        ctx = node_proxy_ssl_context()
        if ca and os.path.exists(ca):
            ctx.load_verify_locations(cafile=ca)
        else:
            from app.services.pki.service import PkiService

            async with self.session_factory() as session:
                ctx.load_verify_locations(
                    cadata=(await PkiService.from_session(session).ensure_ca()).bundle_pem
                )
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        last_error = None
        timeout = float(settings.SWAP_HEALTH_PROBE_TIMEOUT_SECONDS)
        for attempt in range(self.attempts):
            try:
                async with httpx.AsyncClient(verify=ctx, timeout=timeout) as c:
                    resp = await c.get(url)
                body = (
                    resp.json()
                    if resp.headers.get("content-type", "").startswith("application/json")
                    else {}
                )
                if resp.status_code == 200 and body.get("health", "healthy") == "healthy":
                    return CommandResult("succeeded", {"status_code": 200, **body})
                last_error = f"HTTP {resp.status_code} {body}"
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < self.attempts:
                await asyncio.sleep(self.delay)
        return CommandResult("failed", {"url": url}, "health_timeout", last_error)


# ---------------------------------------------------------------------------
# Device locks
# ---------------------------------------------------------------------------

_RELEASE_SCRIPT = """
local released = 0
for i, key in ipairs(KEYS) do
  if redis.call('GET', key) == ARGV[1] then
    redis.call('DEL', key)
    released = released + 1
  end
end
return released
"""


class RedisLockPort:
    """All-or-nothing ``SET NX PX`` on every device key; release only what we own."""

    def __init__(self, redis_factory=None):
        self.redis_factory = redis_factory

    async def _redis(self):
        if self.redis_factory is not None:
            return await self.redis_factory()
        from app.db.redis.client import get_redis

        return await get_redis()

    @staticmethod
    def _full(keys: Sequence[str]) -> List[str]:
        from app.db.redis.client import redis_key

        return [redis_key(*k.split(":")) for k in keys]

    async def acquire(self, keys: Sequence[str], owner: str, ttl_seconds: int) -> bool:
        client = await self._redis()
        full = self._full(keys)
        taken: List[str] = []
        for key in full:
            ok = await client.set(key, owner, nx=True, px=int(ttl_seconds * 1000))
            if not ok and await client.get(key) == owner:
                await client.pexpire(key, int(ttl_seconds * 1000))
                ok = True
            if not ok:
                if taken:
                    await client.eval(_RELEASE_SCRIPT, len(taken), *taken, owner)
                return False
            taken.append(key)
        return True

    async def release(self, keys: Sequence[str], owner: str) -> None:
        full = self._full(keys)
        if not full:
            return
        try:
            client = await self._redis()
            await client.eval(_RELEASE_SCRIPT, len(full), *full, owner)
        except Exception as exc:  # noqa: BLE001 - TTL frees it anyway
            logger.warning(f"device lock release failed: {exc}")


# ---------------------------------------------------------------------------
# Prefetch (model cache) and alerts
# ---------------------------------------------------------------------------


class ModelCachePrefetch:
    def __init__(self, session_factory=None):
        self.session_factory = session_factory or _session_factory_default

    async def prefetch(
        self,
        model_version_id: uuid.UUID,
        node_id: uuid.UUID,
        pin: bool = False,
        pin_reason: Optional[str] = None,
    ) -> Optional[uuid.UUID]:
        from app.services.model_cache.service import ModelCacheService

        async with self.session_factory() as session:
            service = ModelCacheService.for_session(session)
            command_id = await service.prefetch(
                node_id, model_version_id, pin=pin, pin_reason=pin_reason
            )
            await session.commit()
        return command_id


class AlertsAdapter:
    async def emit(
        self,
        event_type: str,
        severity: str,
        title: str,
        message: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[uuid.UUID]:
        from app.services.alerts.service import emit

        return await emit(event_type, severity, title, message, **kwargs)
