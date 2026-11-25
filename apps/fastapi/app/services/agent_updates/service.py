"""
Admin-facing agent updates (agent protocol §14): what each node runs and can move to, the
Update button (one node), rolling updates per cluster, the per-cluster policy (manual or
automatic inside a maintenance window) and the history of update runs.

The work itself is done by ``orchestrator`` (shared with the hub and the scheduler); this
service validates, records the run / rollout, commits it (``release_connection``) and kicks it
off at once, so the first step shows without waiting for the scheduler tick.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.exceptions import BaseAPIException, ResourceNotFoundError
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_db
from app.db.postgres.models.generated_models import Nodes
from app.db.postgres.repositories.agent_update_repository import (
    AgentRolloutRepository,
    AgentUpdateRunRepository,
)
from app.db.postgres.repositories.cluster_repository import ClusterRepository
from app.db.postgres.repositories.fleet_event_repository import FleetEventRepository
from app.db.postgres.repositories.node_repository import NodeRepository
from app.schemas.agent_update_schema import (
    AgentRolloutRequest,
    AgentRolloutResponse,
    AgentStageResponse,
    AgentUpdateCounts,
    AgentUpdateOverview,
    AgentUpdatePolicy,
    AgentUpdateRequest,
    AgentUpdateRunResponse,
    ClusterAgentUpdate,
    NodeAgentUpdate,
    policy_from_config,
)
from app.services.agent_updates import orchestrator, releases
from app.services.agent_updates import status as st
from app.services.agent_updates.policy import in_window, next_window_start
from fastapi import Depends, Request, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class AgentUpdateError(BaseAPIException):
    """409 with a specific code: AGENT_UPDATES_DISABLED, AGENT_UPDATE_UNSUPPORTED, ..."""

    def __init__(self, detail: str, error_code: str):
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail, error_code=error_code)


def run_response(row: Any, node_name: Optional[str] = None) -> AgentUpdateRunResponse:
    item = AgentUpdateRunResponse.model_validate(row)
    item.node_name = node_name
    return item


def rollout_response(row: Any) -> AgentRolloutResponse:
    return AgentRolloutResponse.model_validate(row)


class AgentUpdateService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.nodes = NodeRepository(session)
        self.clusters = ClusterRepository(session)
        self.runs = AgentUpdateRunRepository(session)
        self.rollouts = AgentRolloutRepository(session)
        self.events = FleetEventRepository(session)

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db, scope="function")
    ) -> "AgentUpdateService":
        return AgentUpdateService(db)

    # ---------- status ----------

    async def infos(self, rows: Sequence[Nodes]) -> Dict[uuid.UUID, NodeAgentUpdate]:
        """NodeAgentUpdate per node (one query for runs, the target from the release cache)."""
        target, reason = await releases.target_version()
        latest = await self.runs.latest_for_nodes([r.id for r in rows])
        out: Dict[uuid.UUID, NodeAgentUpdate] = {}
        for row in rows:
            run = latest.get(row.id)
            active = (
                run if run is not None and run.state in orchestrator.ACTIVE_RUN_STATES else None
            )
            info = st.node_info(
                row,
                target,
                reason,
                release_version=settings.APP_VERSION,
                active_run=run_response(active, row.name) if active else None,
                last_run=(
                    run_response(run, row.name) if run is not None and active is None else None
                ),
            )
            out[row.id] = NodeAgentUpdate.model_validate(info)
        return out

    async def decorate(self, rows: Sequence[Nodes], items: List[Any]) -> List[Any]:
        """Fill ``NodeResponse.agent_update`` (never raises: the node list renders without it)."""
        try:
            async with self.session.begin_nested():
                infos = await self.infos(rows)
        except Exception as exc:  # noqa: BLE001 - e.g. the table does not exist yet
            logger.debug(f"agent update status unavailable: {exc}")
            return items
        for row, item in zip(rows, items):
            item.agent_update = infos.get(row.id)
        return items

    async def _node(self, node_id: uuid.UUID) -> Nodes:
        node = await self.nodes.get_active(node_id)
        if node is None or node.status == "removed":
            raise ResourceNotFoundError(resource_type="Node")
        return node

    async def node(self, node_id: uuid.UUID) -> NodeAgentUpdate:
        node = await self._node(node_id)
        return (await self.infos([node]))[node.id]

    async def counts(self, rows: Sequence[Nodes]) -> AgentUpdateCounts:
        counts = AgentUpdateCounts(total=len(rows))
        for row, info in zip(rows, (await self.infos(rows)).values()):
            key = st.count_key(info.model_dump(), row.status)
            setattr(counts, key, getattr(counts, key) + 1)
        return counts

    async def overview(self) -> AgentUpdateOverview:
        data = await releases.overview()
        rows = await self.nodes.list_active()
        return AgentUpdateOverview(**data, counts=await self.counts(rows))

    # ---------- one node ----------

    async def _check_updatable(self, node: Nodes, version: str) -> None:
        mode = st.node_mode(node)
        if mode == "managed":
            raise AgentUpdateError(
                "this node is managed by Helm (Kubernetes DaemonSet): update the agent image "
                "instead",
                "AGENT_UPDATE_MANAGED",
            )
        if mode == "manual":
            raise AgentUpdateError(
                f"the agent on this node ({node.agent_version or 'unknown'}) predates "
                f"self-update: {st.MANUAL_INSTRUCTIONS}",
                "AGENT_UPDATE_UNSUPPORTED",
            )
        if mode == "unsupported":
            reason = st.reported(node).get("reason") or "it cannot replace its binary"
            raise AgentUpdateError(
                f"this node cannot update itself ({reason}). {st.UNSUPPORTED_INSTRUCTIONS}",
                "AGENT_UPDATE_UNSUPPORTED",
            )
        if node.status not in st.CONNECTED:
            raise AgentUpdateError(f"node is {node.status}", "NODE_NOT_CONNECTED")
        if releases.compare(node.agent_version, version) == 0:
            raise AgentUpdateError(f"the node already runs {version}", "AGENT_UPDATE_NOT_NEEDED")
        if await self.runs.active_for_node(node.id) is not None:
            raise AgentUpdateError(
                "an update of this node is already in progress",
                "AGENT_UPDATE_IN_PROGRESS",
            )

    async def _version(self, requested: Optional[str]) -> str:
        target, reason = await releases.target_version()
        version = requested or target
        if version is None:
            raise AgentUpdateError(reason or "agent updates are off", "AGENT_UPDATES_DISABLED")
        if await releases.get_manifest(version) is None:
            raise AgentUpdateError(
                f"no signed release manifest for {version}", "AGENT_RELEASE_NOT_FOUND"
            )
        return version

    async def start_node(
        self,
        node_id: uuid.UUID,
        body: AgentUpdateRequest,
        actor_id: Optional[uuid.UUID],
    ) -> AgentUpdateRunResponse:
        node = await self._node(node_id)
        version = await self._version(body.version)
        await self._check_updatable(node, version)
        if await self.rollouts.running(node.cluster_id):
            raise AgentUpdateError(
                "a rolling update of this node's cluster is running",
                "AGENT_ROLLOUT_RUNNING",
            )
        run = await orchestrator.create_run(
            self.session,
            node,
            version,
            trigger="manual",
            actor_id=actor_id,
            health_timeout=body.health_timeout_seconds,
        )
        await self.events.audit(
            "node.agent_update_started",
            actor_type="admin",
            actor_id=actor_id,
            resource_type="node",
            resource_id=node.id,
            resource_name=node.name,
            after_state={
                "run_id": str(run.id),
                "from": node.agent_version,
                "to": version,
            },
        )
        await self.runs.release_connection()
        await orchestrator.advance_run(run.id)
        row = await self.runs.get_by_id(run.id)
        await self.session.refresh(row)
        return run_response(row, node.name)

    async def stage_node(
        self, node_id: uuid.UUID, version: Optional[str] = None
    ) -> AgentStageResponse:
        node = await self._node(node_id)
        version = await self._version(version)
        await self._check_updatable(node, version)
        target = st.node_target(node)
        if not target:
            raise AgentUpdateError(
                "the agent did not report its build target yet",
                "AGENT_UPDATE_UNSUPPORTED",
            )
        await self.runs.release_connection()
        command_id = await orchestrator.send_stage(node.id, version, target)
        return AgentStageResponse(
            node_id=node.id,
            version=version,
            command_id=command_id,
            state="pending" if command_id else "rejected",
        )

    # ---------- runs ----------

    async def list_runs(
        self,
        page: int,
        page_size: int,
        node_id: Optional[uuid.UUID] = None,
        cluster_id: Optional[uuid.UUID] = None,
        rollout_id: Optional[uuid.UUID] = None,
        state: Optional[str] = None,
    ) -> Tuple[List[AgentUpdateRunResponse], int]:
        rows, total = await self.runs.list(page, page_size, node_id, cluster_id, rollout_id, state)
        names = await self._names([r.node_id for r in rows])
        return [run_response(r, names.get(r.node_id)) for r in rows], total

    async def _names(self, node_ids: Sequence[uuid.UUID]) -> Dict[uuid.UUID, str]:
        out: Dict[uuid.UUID, str] = {}
        for nid in set(node_ids):
            node = await self.nodes.get_by_id(nid)
            if node is not None:
                out[nid] = node.name
        return out

    async def get_run(self, run_id: uuid.UUID) -> AgentUpdateRunResponse:
        row = await self.runs.get_by_id(run_id)
        if row is None or row.deleted_at is not None:
            raise ResourceNotFoundError(resource_type="Agent update run")
        return run_response(row, (await self._names([row.node_id])).get(row.node_id))

    # ---------- clusters ----------

    async def _cluster(self, cluster_id: uuid.UUID):
        cluster = await self.clusters.get_active(cluster_id)
        if cluster is None:
            raise ResourceNotFoundError(resource_type="Cluster")
        return cluster

    async def cluster(self, cluster_id: uuid.UUID) -> ClusterAgentUpdate:
        cluster = await self._cluster(cluster_id)
        policy = policy_from_config(cluster.config)
        target, _ = await releases.target_version()
        rows = await self.nodes.list_active(cluster_id)
        running = await self.rollouts.running(cluster_id)
        rollout = running[0] if running else await self.rollouts.latest_for_cluster(cluster_id)
        return ClusterAgentUpdate(
            cluster_id=cluster_id,
            policy=policy,
            in_window=in_window(policy.window) if policy.mode == "auto" else False,
            next_window_at=(next_window_start(policy.window) if policy.mode == "auto" else None),
            target_version=target,
            rollout=rollout_response(rollout) if rollout is not None else None,
            counts=await self.counts(rows),
        )

    async def start_cluster(
        self,
        cluster_id: uuid.UUID,
        body: AgentRolloutRequest,
        actor_id: Optional[uuid.UUID],
    ) -> AgentRolloutResponse:
        cluster = await self._cluster(cluster_id)
        version = await self._version(body.version)
        if await self.rollouts.running(cluster_id):
            raise AgentUpdateError(
                "a rolling update of this cluster is already running",
                "AGENT_ROLLOUT_RUNNING",
            )
        rows = await self.nodes.list_active(cluster_id)
        if not any(orchestrator.skip_reason(n, version) is None for n in rows):
            raise AgentUpdateError(
                f"no node of this cluster can be updated to {version} now (offline, managed, "
                "agents that predate self-update, or already up to date)",
                "AGENT_UPDATE_NOT_NEEDED",
            )
        rollout = await orchestrator.start_rollout(
            self.session, cluster_id, version, trigger="manual", actor_id=actor_id
        )
        await self.events.audit(
            "cluster.agent_rollout_started",
            actor_type="admin",
            actor_id=actor_id,
            resource_type="cluster",
            resource_id=cluster_id,
            resource_name=cluster.name,
            after_state={"rollout_id": str(rollout.id), "version": version},
        )
        await self.rollouts.release_connection()
        await orchestrator.advance_rollout(rollout.id)
        row = await self.rollouts.get_by_id(rollout.id)
        await self.session.refresh(row)
        return rollout_response(row)

    async def set_policy(
        self,
        cluster_id: uuid.UUID,
        policy: AgentUpdatePolicy,
        actor_id: Optional[uuid.UUID],
    ) -> AgentUpdatePolicy:
        cluster = await self._cluster(cluster_id)
        before = policy_from_config(cluster.config)
        config = dict(cluster.config or {})
        config["agent_updates"] = policy.model_dump(mode="json", exclude_none=True)
        await self.session.execute(
            update(type(cluster)).where(type(cluster).id == cluster_id).values(config=config)
        )
        await self.session.flush()
        await self.events.audit(
            "cluster.agent_update_policy",
            actor_type="admin",
            actor_id=actor_id,
            resource_type="cluster",
            resource_id=cluster_id,
            resource_name=cluster.name,
            before_state=before.model_dump(mode="json"),
            after_state=config["agent_updates"],
        )
        return policy

    async def list_rollouts(
        self, page: int, page_size: int, cluster_id: Optional[uuid.UUID] = None
    ) -> Tuple[List[AgentRolloutResponse], int]:
        rows, total = await self.rollouts.list(page, page_size, cluster_id)
        return [rollout_response(r) for r in rows], total

    async def get_rollout(self, rollout_id: uuid.UUID) -> AgentRolloutResponse:
        row = await self.rollouts.get_by_id(rollout_id)
        if row is None or row.deleted_at is not None:
            raise ResourceNotFoundError(resource_type="Agent rollout")
        return rollout_response(row)

    async def cancel_rollout(
        self, rollout_id: uuid.UUID, actor_id: Optional[uuid.UUID]
    ) -> AgentRolloutResponse:
        row = await self.rollouts.get_by_id(rollout_id)
        if row is None or row.deleted_at is not None:
            raise ResourceNotFoundError(resource_type="Agent rollout")
        if not await orchestrator.cancel_rollout(self.session, rollout_id, actor_id):
            raise AgentUpdateError(
                f"the rolling update is {row.state}", "AGENT_ROLLOUT_NOT_RUNNING"
            )
        row = await self.rollouts.get_by_id(rollout_id)
        await self.session.refresh(row)
        return rollout_response(row)


def policy_dict(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return policy_from_config(config).model_dump(mode="json")
