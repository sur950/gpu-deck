"""
Repositories for the swap manager: ``swap_operations`` and ``agent_commands``.

The swap executor keeps its state machine in ``swap_operations`` (state,
steps[], current_step, rollback) so a restart of the jobs process resumes
exactly where it stopped. ``agent_commands`` rows are the durable command
queue the agent hub replays to a node on reconnect (protocol.md section 3).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import AgentCommands, SwapOperations
from app.db.postgres.repositories.base_repository import BaseRepository
from app.db.postgres.repositories.rule_repository import parse_sort
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

logger = get_logger(__name__)

TERMINAL_STATES = ("completed", "failed", "rolled_back", "cancelled")
_SORTABLE = {"created_at", "updated_at", "started_at", "finished_at", "state", "kind"}


class SwapOperationRepository(BaseRepository[SwapOperations]):
    """CRUD, listing and state persistence for swap operations."""

    def __init__(self, session: AsyncSession):
        super().__init__(SwapOperations, session)

    async def list(
        self,
        page: int = 1,
        page_size: int = 50,
        sort: Optional[str] = None,
        state: Optional[str] = None,
        kind: Optional[str] = None,
        trigger: Optional[str] = None,
        deployment_id: Optional[uuid.UUID] = None,
        node_id: Optional[uuid.UUID] = None,
        active_only: bool = False,
        from_ts: Optional[datetime] = None,
        to_ts: Optional[datetime] = None,
    ) -> Tuple[List[SwapOperations], int]:
        conditions: List[Any] = []
        if state:
            conditions.append(SwapOperations.state == state)
        if kind:
            conditions.append(SwapOperations.kind == kind)
        if trigger:
            conditions.append(SwapOperations.trigger == trigger)
        if deployment_id is not None:
            conditions.append(SwapOperations.deployment_id == deployment_id)
        if node_id is not None:
            conditions.append(SwapOperations.node_id == node_id)
        if active_only:
            conditions.append(SwapOperations.state.notin_(TERMINAL_STATES))
        if from_ts is not None:
            conditions.append(SwapOperations.created_at >= from_ts)
        if to_ts is not None:
            conditions.append(SwapOperations.created_at < to_ts)
        where = and_(*conditions) if conditions else True
        total = (
            await self.session.execute(
                select(func.count()).select_from(SwapOperations).where(where)
            )
        ).scalar_one()
        field, desc = parse_sort(sort, _SORTABLE)
        column = getattr(SwapOperations, field)
        query = (
            select(SwapOperations)
            .where(where)
            .order_by(column.desc() if desc else column.asc(), SwapOperations.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list((await self.session.execute(query)).scalars().all()), total

    async def count_active(self) -> int:
        query = (
            select(func.count())
            .select_from(SwapOperations)
            .where(SwapOperations.state.notin_(TERMINAL_STATES))
        )
        return (await self.session.execute(query)).scalar_one()

    async def list_active(self) -> List[SwapOperations]:
        query = (
            select(SwapOperations)
            .where(SwapOperations.state.notin_(TERMINAL_STATES))
            .order_by(SwapOperations.created_at.asc())
        )
        return list((await self.session.execute(query)).scalars().all())

    async def active_for_deployment(self, deployment_id: uuid.UUID) -> List[SwapOperations]:
        query = select(SwapOperations).where(
            SwapOperations.deployment_id == deployment_id,
            SwapOperations.state.notin_(TERMINAL_STATES),
        )
        return list((await self.session.execute(query)).scalars().all())

    async def active_for_instance(self, instance_id: uuid.UUID) -> List[SwapOperations]:
        query = select(SwapOperations).where(
            SwapOperations.state.notin_(TERMINAL_STATES),
            (SwapOperations.from_instance_id == instance_id)
            | (SwapOperations.to_instance_id == instance_id),
        )
        return list((await self.session.execute(query)).scalars().all())

    async def restarts_last_hour(self, instance_id: uuid.UUID, now: datetime) -> int:
        """auto_heal escalation input: restarts of this instance in the last hour."""
        from datetime import timedelta

        query = (
            select(func.count())
            .select_from(SwapOperations)
            .where(
                SwapOperations.kind == "restart",
                SwapOperations.from_instance_id == instance_id,
                SwapOperations.created_at >= now - timedelta(hours=1),
                SwapOperations.state.notin_(["cancelled"]),
            )
        )
        return (await self.session.execute(query)).scalar_one()

    async def persist_state(self, swap: SwapOperations, **fields: Any) -> None:
        """Apply fields and make sure JSONB mutations are flushed."""
        for key, value in fields.items():
            setattr(swap, key, value)
        for json_col in ("plan", "steps", "rollback"):
            flag_modified(swap, json_col)
        await self.session.flush()

    async def set_fields(self, swap_id: uuid.UUID, **fields: Any) -> None:
        await self.session.execute(
            update(SwapOperations).where(SwapOperations.id == swap_id).values(**fields)
        )
        await self.session.flush()


class AgentCommandRepository(BaseRepository[AgentCommands]):
    """Durable command queue rows for the node agents."""

    def __init__(self, session: AsyncSession):
        super().__init__(AgentCommands, session)

    async def enqueue(
        self,
        node_id: uuid.UUID,
        command_type: str,
        payload: Dict[str, Any],
        timeout_seconds: int,
        swap_operation_id: Optional[uuid.UUID] = None,
        instance_id: Optional[uuid.UUID] = None,
        command_id: Optional[uuid.UUID] = None,
    ) -> AgentCommands:
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        return await self.create(
            id=command_id or uuid.uuid4(),
            node_id=node_id,
            command_type=command_type,
            payload=payload,
            state="pending",
            attempts=0,
            swap_operation_id=swap_operation_id,
            instance_id=instance_id,
            expires_at=now + timedelta(seconds=timeout_seconds),
        )

    async def mark_timed_out(self, command_id: uuid.UUID) -> None:
        await self.session.execute(
            update(AgentCommands)
            .where(
                AgentCommands.id == command_id,
                AgentCommands.state.in_(["pending", "sent", "accepted", "running"]),
            )
            .values(
                state="timed_out",
                error_code="timeout",
                error_message="no terminal command_result before expires_at",
                completed_at=datetime.now(timezone.utc),
            )
        )
        await self.session.flush()

    async def mark_cancelled(self, command_id: uuid.UUID) -> None:
        await self.session.execute(
            update(AgentCommands)
            .where(
                AgentCommands.id == command_id,
                AgentCommands.state.in_(["pending", "sent", "accepted", "running"]),
            )
            .values(state="cancelled", completed_at=datetime.now(timezone.utc))
        )
        await self.session.flush()
