"""
Rules service: CRUD + enable/disable + dry-run/run-now + run history for the
admin API, and the evaluation loop the jobs leader runs every tick.

Hooks for other services::

    # The placement scheduler, when a placement fails with insufficient VRAM:
    from app.services.rules.service import make_room_for
    run_ids = await make_room_for(deployment_id)   # runs every enabled make_room_by_priority
                                                   # rule with trigger placement_failure|both
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from app.core.exceptions import (
    DuplicateResourceError,
    ResourceNotFoundError,
    ValidationError,
)
from app.core.logger import get_logger
from app.db.postgres.connection import get_db
from app.db.postgres.repositories.rule_repository import (
    RuleRepository,
    RuleRunRepository,
)
from app.schemas.rules_schema import (
    RuleCreate,
    RuleOut,
    RuleRunAction,
    RuleRunOut,
    RuleRunSummary,
    RuleSchemasOut,
    RuleUpdate,
)
from app.services.rules.validation import (
    schemas_for_api,
    validate_params,
    validate_target,
)
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


def run_out(row: Any, rule_name: Optional[str] = None) -> RuleRunOut:
    return RuleRunOut(
        id=row.id,
        rule_id=row.rule_id,
        rule_name=rule_name,
        rule_type=row.rule_type,
        evaluated_at=row.evaluated_at,
        fired=bool(row.fired),
        dry_run=bool(row.dry_run),
        outcome=row.outcome,
        decision=dict(row.decision or {}),
        actions=[RuleRunAction.model_validate(a) for a in (row.actions or [])],
        error_message=row.error_message,
        duration_ms=row.duration_ms,
        created_at=row.created_at,
    )


class RuleService:
    """Business logic for rules (flush only; ``get_db`` commits)."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = RuleRepository(session)
        self.runs = RuleRunRepository(session)

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db, scope="function")
    ) -> "RuleService":
        return RuleService(db)

    # ------------------------------------------------------------------ helpers

    async def _out(self, rows: List[Any]) -> List[RuleOut]:
        latest = await self.runs.latest_for_rules([r.id for r in rows])
        now = datetime.now(timezone.utc)
        out = []
        for row in rows:
            data = RuleOut.model_validate(row)
            data.next_evaluation_at = next_evaluation_at(row, now)
            last = latest.get(row.id)
            if last is not None:
                data.last_run = RuleRunSummary(
                    id=last.id,
                    evaluated_at=last.evaluated_at,
                    outcome=last.outcome,
                    fired=bool(last.fired),
                    dry_run=bool(last.dry_run),
                )
            out.append(data)
        return out

    async def _get(self, rule_id: uuid.UUID):
        row = await self.repo.get_active(rule_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Rule")
        return row

    async def _check_layout(self, rule_type: str, params: Dict[str, Any]) -> None:
        if rule_type != "time_schedule":
            return
        from app.db.postgres.repositories.layout_repository import LayoutRepository

        layout = await LayoutRepository(self.session).get_active(
            uuid.UUID(str(params["layout_id"]))
        )
        if layout is None:
            raise ValidationError("params.layout_id: layout not found")

    # ------------------------------------------------------------------ reads

    async def list_rules(self, **filters: Any) -> Tuple[List[RuleOut], int]:
        rows, total = await self.repo.list(**filters)
        return await self._out(rows), total

    async def get_rule(self, rule_id: uuid.UUID) -> RuleOut:
        return (await self._out([await self._get(rule_id)]))[0]

    @staticmethod
    def schemas() -> RuleSchemasOut:
        return RuleSchemasOut.model_validate(schemas_for_api())

    async def list_runs(self, **filters: Any) -> Tuple[List[RuleRunOut], int]:
        rows, total = await self.runs.list(**filters)
        names = await self.runs.rule_names(list({r.rule_id for r in rows}))
        return [run_out(r, names.get(r.rule_id)) for r in rows], total

    async def list_rule_runs(
        self, rule_id: uuid.UUID, **filters: Any
    ) -> Tuple[List[RuleRunOut], int]:
        await self._get(rule_id)
        return await self.list_runs(rule_id=rule_id, **filters)

    async def get_run(self, run_id: uuid.UUID) -> RuleRunOut:
        row = await self.runs.get_by_id(run_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Rule run")
        names = await self.runs.rule_names([row.rule_id])
        return run_out(row, names.get(row.rule_id))

    # ------------------------------------------------------------------ writes

    async def create_rule(self, payload: RuleCreate, created_by) -> RuleOut:
        from app.services.audit import audit

        if await self.repo.name_exists(payload.name):
            raise DuplicateResourceError(resource_type="Rule", field="name")
        params = validate_params(payload.type.value, payload.params)
        target = validate_target(payload.target)
        await self._check_layout(payload.type.value, params)
        row = await self.repo.create(
            name=payload.name,
            description=payload.description,
            type=payload.type.value,
            params=params,
            target=target,
            enabled=payload.enabled,
            dry_run=payload.dry_run,
            cooldown_seconds=payload.cooldown_seconds,
            evaluation_interval_seconds=payload.evaluation_interval_seconds,
            priority=payload.priority,
            created_by=created_by,
        )
        out = (await self._out([row]))[0]
        audit.record(
            "rule.created",
            resource_type="rule",
            resource_id=row.id,
            resource_name=row.name,
            after_state=out.model_dump(mode="json"),
        )
        return out

    async def update_rule(self, rule_id: uuid.UUID, payload: RuleUpdate) -> RuleOut:
        from app.services.audit import audit

        row = await self._get(rule_id)
        before = (await self._out([row]))[0].model_dump(mode="json")
        data = payload.model_dump(exclude_unset=True)
        changes: Dict[str, Any] = {}
        if data.get("name") and data["name"] != row.name:
            if await self.repo.name_exists(data["name"], rule_id):
                raise DuplicateResourceError(resource_type="Rule", field="name")
            changes["name"] = data["name"]
        if "description" in data:
            changes["description"] = data["description"]
        if data.get("params") is not None:
            changes["params"] = validate_params(row.type, data["params"])
            await self._check_layout(row.type, changes["params"])
        if data.get("target") is not None:
            changes["target"] = validate_target(data["target"])
        for key in (
            "enabled",
            "dry_run",
            "cooldown_seconds",
            "evaluation_interval_seconds",
            "priority",
        ):
            if data.get(key) is not None:
                changes[key] = data[key]
        if changes:
            changes["updated_at"] = datetime.now(timezone.utc)
            row = await self.repo.update(rule_id, **changes)
        out = (await self._out([row]))[0]
        audit.record(
            "rule.updated",
            resource_type="rule",
            resource_id=rule_id,
            resource_name=out.name,
            before_state=before,
            after_state=out.model_dump(mode="json"),
        )
        return out

    async def set_enabled(self, rule_id: uuid.UUID, enabled: bool) -> RuleOut:
        from app.services.audit import audit

        await self._get(rule_id)
        row = await self.repo.update(
            rule_id, enabled=enabled, updated_at=datetime.now(timezone.utc)
        )
        audit.record(
            "rule.enabled" if enabled else "rule.disabled",
            resource_type="rule",
            resource_id=rule_id,
            resource_name=row.name,
        )
        return (await self._out([row]))[0]

    async def delete_rule(self, rule_id: uuid.UUID) -> None:
        from app.services.audit import audit

        row = await self._get(rule_id)
        await self.repo.soft_delete(rule_id)
        audit.record(
            "rule.deleted",
            resource_type="rule",
            resource_id=rule_id,
            resource_name=row.name,
        )

    async def evaluate_now(self, rule_id: uuid.UUID, dry_run: bool) -> RuleRunOut:
        """``POST /rules/{id}/dry-run`` (even for disabled rules) and ``/run``."""
        from app.services.rules.engine import RuleEngine

        rule = await self._get(rule_id)
        if not dry_run and not rule.enabled:
            raise ValidationError("Rule is disabled; enable it or use dry-run")
        engine = RuleEngine(self.session)
        run = await engine.run(rule, trigger="api", force_dry_run=dry_run)
        await self.repo.release_connection()
        await engine.flush_alerts()
        return run_out(run, rule.name)


# ---------------------------------------------------------------------------
# Jobs-process evaluation loop
# ---------------------------------------------------------------------------


def rule_view(rule: Any, **overrides: Any) -> SimpleNamespace:
    """Detached copy of a rule row (column values) with overrides, safe to mutate."""
    data = {
        c: getattr(rule, c)
        for c in (
            "id",
            "name",
            "description",
            "type",
            "params",
            "target",
            "enabled",
            "dry_run",
            "cooldown_seconds",
            "evaluation_interval_seconds",
            "priority",
            "last_evaluated_at",
            "last_fired_at",
            "fire_count",
            "created_at",
        )
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def next_evaluation_at(rule: Any, now: datetime) -> Optional[datetime]:
    """When the jobs leader will next act on the rule (admin.yaml ``Rule.next_evaluation_at``).

    Disabled rules -> None. ``time_schedule`` -> the next cron/at fire time in the
    rule's zone (the switch happens within ``RULES_TICK_SECONDS`` of it). Every
    other type -> ``last_evaluated_at + evaluation_interval_seconds``, or ``now``
    when never evaluated / overdue (picked up by the next tick).
    """
    if not getattr(rule, "enabled", False):
        return None
    if rule.type == "time_schedule":
        from app.core.settings import settings
        from app.services.rules.windows import build_trigger, next_fire_time

        try:
            trigger = build_trigger(dict(rule.params or {}), settings.RULES_DEFAULT_TIMEZONE)
            return next_fire_time(trigger, now)
        except Exception as exc:  # noqa: BLE001 - a bad stored schedule must not break reads
            logger.debug(f"rule {rule.id}: cannot compute next fire time: {exc}")
            return None
    last = rule.last_evaluated_at
    if last is None:
        return now
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    due = last + timedelta(seconds=int(rule.evaluation_interval_seconds or 30))
    return max(due, now)


def is_due(rule: Any, now: datetime) -> bool:
    if rule.last_evaluated_at is None:
        return True
    return (now - rule.last_evaluated_at).total_seconds() >= int(
        rule.evaluation_interval_seconds or 30
    )


async def evaluate_due_rules(
    now: Optional[datetime] = None,
    fence=None,
    session_factory=None,
    engine_factory=None,
) -> Dict[str, int]:
    """
    One evaluator tick: every enabled rule whose interval elapsed, in priority
    order, each in its own transaction. Stops early when the fence (rules-evaluator
    lease) is lost.
    """
    from app.db.postgres.connection import get_session_maker
    from app.services.rules.engine import RuleEngine

    factory = session_factory or get_session_maker()
    now = now or datetime.now(timezone.utc)
    counts: Dict[str, int] = {}
    async with factory() as session:
        rules = [(r.id, r) for r in await RuleRepository(session).list_enabled()]
    for rule_id, snapshot in rules:
        if not is_due(snapshot, now):
            continue
        if fence is not None and not await fence.check():
            logger.warning("rules tick: rules-evaluator lease lost, stopping")
            break
        async with factory() as session:
            rule = await RuleRepository(session).get_active(rule_id)
            if rule is None or not rule.enabled:
                continue
            engine = (engine_factory or RuleEngine)(session, now=now)
            try:
                run = await engine.run(rule, trigger="interval")
                await session.commit()
            except Exception as exc:  # noqa: BLE001 - one bad rule must not stop the tick
                await session.rollback()
                logger.exception(f"rules tick: rule {rule_id} failed: {exc}")
                counts["error"] = counts.get("error", 0) + 1
                continue
        await engine.flush_alerts()
        outcome = run.outcome if run is not None else "no_match"
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


async def make_room_for(deployment_id: uuid.UUID, session_factory=None) -> List[uuid.UUID]:
    """
    Synchronous make_room_by_priority trigger for a placement failure
    (rules.md section 1). Runs every enabled make_room rule whose trigger is
    ``placement_failure`` or ``both`` with the requester pinned in the target.
    """
    from app.db.postgres.connection import get_session_maker
    from app.services.rules.engine import RuleEngine

    factory = session_factory or get_session_maker()
    run_ids: List[uuid.UUID] = []
    async with factory() as session:
        rules = await RuleRepository(session).list_enabled("make_room_by_priority")
        candidates = [
            r.id
            for r in rules
            if (r.params or {}).get("trigger", "both") in ("placement_failure", "both")
        ]
    for rule_id in candidates:
        async with factory() as session:
            rule = await RuleRepository(session).get_active(rule_id)
            if rule is None:
                continue
            target = dict(rule.target or {})
            wanted = target.get("deployment_ids")
            if wanted and str(deployment_id) not in {str(d) for d in wanted}:
                continue
            target["deployment_ids"] = [str(deployment_id)]
            view = rule_view(rule, target=target)  # evaluation-only override, never persisted
            engine = RuleEngine(session)
            run = await engine.run(view, trigger="placement_failure")
            await session.commit()
        await engine.flush_alerts()
        if run is not None:
            run_ids.append(run.id)
            if run.fired:
                break
    return run_ids
