"""
Agent update runs and rolling updates (``agent_update_runs``, ``agent_update_rollouts``).

Repositories flush, never commit.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import (
    AgentUpdateRollouts,
    AgentUpdateRuns,
    Nodes,
)
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

ACTIVE_RUN_STATES = (
    "pending",
    "staging",
    "staged",
    "swapping",
    "restarting",
    "readopting",
    "health_check",
)
TERMINAL_RUN_STATES = ("succeeded", "rolled_back", "failed", "cancelled")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentUpdateRunRepository(BaseRepository[AgentUpdateRuns]):
    def __init__(self, session: AsyncSession):
        super().__init__(AgentUpdateRuns, session)

    async def active_for_node(self, node_id: uuid.UUID) -> Optional[AgentUpdateRuns]:
        result = await self.session.execute(
            select(AgentUpdateRuns).where(
                AgentUpdateRuns.node_id == node_id,
                AgentUpdateRuns.state.in_(ACTIVE_RUN_STATES),
                AgentUpdateRuns.deleted_at.is_(None),
            )
        )
        return result.scalars().first()

    async def active(self) -> List[AgentUpdateRuns]:
        result = await self.session.execute(
            select(AgentUpdateRuns)
            .where(
                AgentUpdateRuns.state.in_(ACTIVE_RUN_STATES),
                AgentUpdateRuns.deleted_at.is_(None),
            )
            .order_by(AgentUpdateRuns.created_at)
        )
        return list(result.scalars().all())

    async def latest_for_nodes(self, node_ids: Sequence[uuid.UUID]) -> Dict[uuid.UUID, Any]:
        """The newest run per node."""
        if not node_ids:
            return {}
        result = await self.session.execute(
            select(AgentUpdateRuns)
            .where(
                AgentUpdateRuns.node_id.in_(list(node_ids)),
                AgentUpdateRuns.deleted_at.is_(None),
            )
            .order_by(AgentUpdateRuns.node_id, AgentUpdateRuns.created_at.desc())
            .ext(distinct_on(AgentUpdateRuns.node_id))
        )
        return {r.node_id: r for r in result.scalars().all()}

    async def list(
        self,
        page: int,
        page_size: int,
        node_id: Optional[uuid.UUID] = None,
        cluster_id: Optional[uuid.UUID] = None,
        rollout_id: Optional[uuid.UUID] = None,
        state: Optional[str] = None,
    ) -> Tuple[List[AgentUpdateRuns], int]:
        conds = [AgentUpdateRuns.deleted_at.is_(None)]
        if node_id:
            conds.append(AgentUpdateRuns.node_id == node_id)
        if cluster_id:
            conds.append(AgentUpdateRuns.cluster_id == cluster_id)
        if rollout_id:
            conds.append(AgentUpdateRuns.rollout_id == rollout_id)
        if state:
            conds.append(AgentUpdateRuns.state == state)
        total = (
            await self.session.execute(
                select(func.count()).select_from(AgentUpdateRuns).where(*conds)
            )
        ).scalar_one()
        result = await self.session.execute(
            select(AgentUpdateRuns)
            .where(*conds)
            .order_by(AgentUpdateRuns.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), int(total)

    async def set_state(
        self,
        run_id: uuid.UUID,
        state: str,
        *,
        step: Optional[Dict[str, Any]] = None,
        **values: Any,
    ) -> Optional[AgentUpdateRuns]:
        """Move a run to ``state`` (appending ``step``); terminal states set finished_at."""
        row = await self.get_by_id(run_id)
        if row is None:
            return None
        changes: Dict[str, Any] = {"state": state, **values}
        if step is not None:
            changes["steps"] = list(row.steps or []) + [step]
        if state in TERMINAL_RUN_STATES and row.finished_at is None:
            changes["finished_at"] = utcnow()
        await self.session.execute(
            update(AgentUpdateRuns).where(AgentUpdateRuns.id == run_id).values(**changes)
        )
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def add_step(self, run_id: uuid.UUID, step: Dict[str, Any]) -> None:
        row = await self.get_by_id(run_id)
        if row is None:
            return
        await self.session.execute(
            update(AgentUpdateRuns)
            .where(AgentUpdateRuns.id == run_id)
            .values(steps=list(row.steps or []) + [step])
        )
        await self.session.flush()

    async def note_node_updating(self, node_id: uuid.UUID, message: str) -> None:
        """Status line of a node that is offline because its agent restarts for an update."""
        await self.session.execute(
            update(Nodes)
            .where(Nodes.id == node_id, Nodes.status == "offline")
            .values(status_message=message[:1024])
        )
        await self.session.flush()

    async def by_command(self, command_id: uuid.UUID) -> Optional[AgentUpdateRuns]:
        result = await self.session.execute(
            select(AgentUpdateRuns).where(
                (AgentUpdateRuns.apply_command_id == command_id)
                | (AgentUpdateRuns.stage_command_id == command_id)
            )
        )
        return result.scalars().first()


class AgentRolloutRepository(BaseRepository[AgentUpdateRollouts]):
    def __init__(self, session: AsyncSession):
        super().__init__(AgentUpdateRollouts, session)

    async def running(self, cluster_id: Optional[uuid.UUID] = None) -> List[AgentUpdateRollouts]:
        conds = [
            AgentUpdateRollouts.state == "running",
            AgentUpdateRollouts.deleted_at.is_(None),
        ]
        if cluster_id:
            conds.append(AgentUpdateRollouts.cluster_id == cluster_id)
        result = await self.session.execute(
            select(AgentUpdateRollouts).where(*conds).order_by(AgentUpdateRollouts.created_at)
        )
        return list(result.scalars().all())

    async def latest_for_cluster(self, cluster_id: uuid.UUID) -> Optional[AgentUpdateRollouts]:
        result = await self.session.execute(
            select(AgentUpdateRollouts)
            .where(
                AgentUpdateRollouts.cluster_id == cluster_id,
                AgentUpdateRollouts.deleted_at.is_(None),
            )
            .order_by(AgentUpdateRollouts.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def list(
        self, page: int, page_size: int, cluster_id: Optional[uuid.UUID] = None
    ) -> Tuple[List[AgentUpdateRollouts], int]:
        conds = [AgentUpdateRollouts.deleted_at.is_(None)]
        if cluster_id:
            conds.append(AgentUpdateRollouts.cluster_id == cluster_id)
        total = (
            await self.session.execute(
                select(func.count()).select_from(AgentUpdateRollouts).where(*conds)
            )
        ).scalar_one()
        result = await self.session.execute(
            select(AgentUpdateRollouts)
            .where(*conds)
            .order_by(AgentUpdateRollouts.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), int(total)

    async def failed_recently(self, cluster_id: uuid.UUID, version: str, since: datetime) -> bool:
        result = await self.session.execute(
            select(func.count())
            .select_from(AgentUpdateRollouts)
            .where(
                AgentUpdateRollouts.cluster_id == cluster_id,
                AgentUpdateRollouts.target_version == version,
                AgentUpdateRollouts.state == "failed",
                AgentUpdateRollouts.created_at >= since,
            )
        )
        return result.scalar_one() > 0

    async def patch(self, rollout_id: uuid.UUID, **values: Any) -> None:
        if values.get("state") in ("succeeded", "failed", "cancelled"):
            values.setdefault("finished_at", utcnow())
        await self.session.execute(
            update(AgentUpdateRollouts).where(AgentUpdateRollouts.id == rollout_id).values(**values)
        )
        await self.session.flush()
