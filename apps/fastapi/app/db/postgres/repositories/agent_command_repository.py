"""
Agent command repository: the persisted queue of commands sent to node
agents (``agent_commands``). The row id is the protocol envelope id; results
update ``state`` and the agent is idempotent on that id (protocol.md §3-4).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import AgentCommands
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

TERMINAL_STATES = ("succeeded", "failed", "rejected", "timed_out", "cancelled")
REPLAYABLE_STATES = ("pending", "sent")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentCommandRepository(BaseRepository[AgentCommands]):
    def __init__(self, session: AsyncSession):
        super().__init__(AgentCommands, session)

    async def list_for_node(
        self, node_id: uuid.UUID, page: int, page_size: int, state: Optional[str] = None
    ) -> Tuple[List[AgentCommands], int]:
        query = select(AgentCommands).where(AgentCommands.node_id == node_id)
        if state:
            query = query.where(AgentCommands.state == state)
        total = (
            await self.session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        rows = (
            await self.session.execute(
                query.order_by(AgentCommands.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars()
        return list(rows.all()), total

    async def replayable_for_node(self, node_id: uuid.UUID) -> List[AgentCommands]:
        """Rows in (pending, sent) with expires_at in the future — re-sent after reconnect."""
        result = await self.session.execute(
            select(AgentCommands)
            .where(
                AgentCommands.node_id == node_id,
                AgentCommands.state.in_(REPLAYABLE_STATES),
                (AgentCommands.expires_at.is_(None)) | (AgentCommands.expires_at > utcnow()),
            )
            .order_by(AgentCommands.created_at)
        )
        return list(result.scalars().all())

    async def in_flight_for_node(self, node_id: uuid.UUID) -> List[AgentCommands]:
        result = await self.session.execute(
            select(AgentCommands).where(
                AgentCommands.node_id == node_id,
                AgentCommands.state.in_(("pending", "sent", "accepted", "running")),
            )
        )
        return list(result.scalars().all())

    async def mark_sent(self, command_id: uuid.UUID) -> None:
        await self.session.execute(
            update(AgentCommands)
            .where(
                AgentCommands.id == command_id,
                AgentCommands.state.in_(REPLAYABLE_STATES),
            )
            .values(state="sent", sent_at=utcnow(), attempts=AgentCommands.attempts + 1)
        )

    async def apply_result(
        self,
        command_id: uuid.UUID,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        progress_pct: Optional[float] = None,
        finished_at: Optional[datetime] = None,
    ) -> Optional[AgentCommands]:
        """Apply a command_result frame; terminal states are never overwritten."""
        row = await self.get_by_id(command_id)
        if row is None:
            return None
        if row.state in TERMINAL_STATES:
            return row
        values: Dict[str, Any] = {}
        if status == "accepted":
            values.update(state="accepted", accepted_at=row.accepted_at or utcnow())
        elif status == "running":
            values.update(state="running", accepted_at=row.accepted_at or utcnow())
            if progress_pct is not None:
                values["progress_pct"] = progress_pct
        elif status in ("succeeded", "failed", "rejected", "cancelled"):
            values.update(
                state=status,
                completed_at=finished_at or utcnow(),
                accepted_at=row.accepted_at or utcnow(),
                result=result,
                error_code=error_code,
                error_message=error_message,
            )
            if progress_pct is not None:
                values["progress_pct"] = progress_pct
            elif status == "succeeded":
                values["progress_pct"] = 100.0
        if values:
            await self.session.execute(
                update(AgentCommands).where(AgentCommands.id == command_id).values(**values)
            )
            await self.session.flush()
            await self.session.refresh(row)
        return row

    async def mark_timed_out(self, command_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            update(AgentCommands)
            .where(
                AgentCommands.id == command_id,
                AgentCommands.state.notin_(TERMINAL_STATES),
            )
            .values(
                state="timed_out",
                completed_at=utcnow(),
                error_code="timeout",
                error_message="no terminal result before expires_at",
            )
        )
        return result.rowcount == 1

    async def expire_overdue(self) -> List[AgentCommands]:
        """Mark every non-terminal command past expires_at as timed_out; returns them."""
        result = await self.session.execute(
            select(AgentCommands).where(
                AgentCommands.state.notin_(TERMINAL_STATES),
                AgentCommands.expires_at.is_not(None),
                AgentCommands.expires_at < utcnow(),
            )
        )
        rows = list(result.scalars().all())
        for row in rows:
            await self.mark_timed_out(row.id)
        return rows

    async def pending_for_node(self, node_id: uuid.UUID, command_types: Sequence[str]) -> int:
        """Non-terminal commands of the given types for one node."""
        result = await self.session.execute(
            select(func.count()).where(
                AgentCommands.node_id == node_id,
                AgentCommands.command_type.in_(list(command_types)),
                AgentCommands.state.notin_(TERMINAL_STATES),
            )
        )
        return result.scalar_one()

    async def undispatched_for_nodes(
        self, node_ids: Sequence[uuid.UUID], older_than_seconds: float
    ) -> List[AgentCommands]:
        """``pending`` rows (never sent) older than the threshold, for the hub sweeper."""
        if not node_ids:
            return []
        cutoff = utcnow() - timedelta(seconds=older_than_seconds)
        result = await self.session.execute(
            select(AgentCommands)
            .where(
                AgentCommands.node_id.in_(list(node_ids)),
                AgentCommands.state == "pending",
                AgentCommands.created_at <= cutoff,
                (AgentCommands.expires_at.is_(None)) | (AgentCommands.expires_at > utcnow()),
            )
            .order_by(AgentCommands.created_at)
            .limit(500)
        )
        return list(result.scalars().all())

    async def mark_cancelled(self, command_id: uuid.UUID, message: str) -> bool:
        """Cancel a command that the agent never accepted (pending/sent)."""
        result = await self.session.execute(
            update(AgentCommands)
            .where(
                AgentCommands.id == command_id,
                AgentCommands.state.in_(REPLAYABLE_STATES),
            )
            .values(
                state="cancelled",
                completed_at=utcnow(),
                error_code="cancelled",
                error_message=message[:2048],
            )
        )
        return result.rowcount == 1
