"""
Rule engine: one evaluation of one rule (rules.md sections 1-2 and 4).

``RuleEngine.run(rule)``:

1. validate params (schema defaults filled), evaluate the rule type over
   ``RuleInputs`` (evaluators.py, pure);
2. per target: protections (``skipped``), per-(rule, deployment) cooldown
   (``cooldown``), a swap already running for the deployment
   (``skipped: swap_in_progress``), the cluster-wide swap cap
   (``skipped: swap_capacity``);
3. dry run (rule flag or the dry-run API) records the planned actions and
   starts the cooldown but creates nothing; otherwise each action becomes a
   ``swap_operations`` row (SwapPlanner) the leader's swap manager executes;
4. writes one ``rule_runs`` row (``no_match`` at most once per
   ``RULE_RUN_NO_MATCH_LOG_INTERVAL_SECONDS`` for interval runs), updates
   ``rules.last_evaluated_at / last_fired_at / fire_count``, audits
   ``rule.fired`` and emits alerts.
5. ``flush_alerts()`` (after the caller committed) emits the queued alerts and
   publishes a ``rule_run`` live event for fired / dry-run / error runs
   (best effort, never raises).

``time_schedule`` rules are evaluated on every tick with a timezone-aware
cron computation (windows.schedule_decision) instead of APScheduler cron jobs
so that leader election, catch-up and dry run behave the same as for the
other types; the fire happens within one ``RULES_TICK_SECONDS`` of the minute.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Protocol

from app.core.logger import get_logger
from app.core.settings import settings
from app.services.ha.automation_metrics import observe_rule
from app.services.rules.evaluators import (
    EvalContext,
    Evaluation,
    PlannedAction,
    TargetMatch,
    evaluate,
)
from app.services.rules.validation import validate_params, validate_target
from app.services.rules.windows import prefetch_due, schedule_decision

logger = get_logger(__name__)

OUTCOME_ORDER = ["fired", "dry_run", "cooldown", "skipped", "no_match"]
HEAL_EVENT = {"restart": "heal.restart", "move": "heal.move"}


class CooldownStore(Protocol):
    async def remaining(self, rule_id: uuid.UUID, target: str) -> float: ...

    async def start(self, rule_id: uuid.UUID, target: str, seconds: int) -> None: ...


class MemoryCooldowns:
    """In-process cooldowns (tests)."""

    def __init__(self, clock=None) -> None:
        self._until: Dict[str, datetime] = {}
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def remaining(self, rule_id: uuid.UUID, target: str) -> float:
        until = self._until.get(f"{rule_id}:{target}")
        if until is None:
            return 0.0
        return max(0.0, (until - self.clock()).total_seconds())

    async def start(self, rule_id: uuid.UUID, target: str, seconds: int) -> None:
        if seconds > 0:
            self._until[f"{rule_id}:{target}"] = self.clock() + timedelta(seconds=seconds)


class RedisCooldowns:
    """``rule:cooldown:{rule_id}:{target}`` with TTL; falls back to rule_runs when Redis is down."""

    def __init__(self, session=None) -> None:
        self.session = session

    @staticmethod
    def _key(rule_id: uuid.UUID, target: str) -> str:
        from app.db.redis.client import redis_key

        return redis_key("rule", "cooldown", str(rule_id), str(target))

    async def remaining(self, rule_id: uuid.UUID, target: str) -> float:
        try:
            from app.db.redis.client import get_redis

            ttl = await (await get_redis()).pttl(self._key(rule_id, target))
            return max(0.0, ttl / 1000.0) if ttl and ttl > 0 else 0.0
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"cooldown lookup via Redis failed ({exc}); using rule_runs")
        if self.session is None:
            return 0.0
        from app.db.postgres.repositories.rule_repository import (
            RuleRepository,
            RuleRunRepository,
        )

        rule = await RuleRepository(self.session).get_by_id(rule_id)
        try:
            last = await RuleRunRepository(self.session).last_fire_for_deployment(
                rule_id, uuid.UUID(str(target))
            )
        except ValueError:
            return 0.0
        if rule is None or last is None:
            return 0.0
        left = rule.cooldown_seconds - (datetime.now(timezone.utc) - last).total_seconds()
        return max(0.0, left)

    async def start(self, rule_id: uuid.UUID, target: str, seconds: int) -> None:
        if seconds <= 0:
            return
        try:
            from app.db.redis.client import get_redis

            await (await get_redis()).set(self._key(rule_id, target), "1", ex=int(seconds))
        except Exception as exc:  # noqa: BLE001 - rule_runs remain the fallback record
            logger.warning(f"cooldown set via Redis failed: {exc}")


def _outcome_of(states: List[str]) -> str:
    for outcome in OUTCOME_ORDER:
        if outcome in states:
            return outcome
    return "no_match"


class RuleEngine:
    """Evaluates rules and turns decisions into swap operations (flush only; caller commits)."""

    def __init__(
        self,
        session,
        inputs=None,
        cooldowns: Optional[CooldownStore] = None,
        alerts=None,
        layouts=None,
        planner=None,
        now: Optional[datetime] = None,
        live=None,
    ) -> None:
        from app.db.postgres.repositories.rule_repository import (
            RuleRepository,
            RuleRunRepository,
        )
        from app.db.postgres.repositories.swap_operation_repository import (
            SwapOperationRepository,
        )
        from app.services.rules.inputs import DbRuleInputs

        self.session = session
        self.inputs = inputs or DbRuleInputs(session, now=now)
        self.now = now or self.inputs.now
        self.cooldowns = cooldowns or RedisCooldowns(session)
        self._alerts = alerts
        self._layouts = layouts
        self._planner = planner
        self.rules = RuleRepository(session)
        self.runs = RuleRunRepository(session)
        self.swaps = SwapOperationRepository(session)
        self.pending_alerts: List[Dict[str, Any]] = []
        self.pending_live: List[Dict[str, Any]] = []
        self._live = live

    # ------------------------------------------------------------------ collaborators

    def layouts(self):
        if self._layouts is None:
            from app.services.layouts.service import LayoutService

            self._layouts = LayoutService(self.session, inputs=self.inputs)
        return self._layouts

    def planner(self):
        if self._planner is None:
            from app.services.swap.planner import SwapPlanner

            self._planner = SwapPlanner(self.session)
        return self._planner

    def _ctx(self, trigger: str) -> EvalContext:
        return EvalContext(
            default_timezone=settings.RULES_DEFAULT_TIMEZONE,
            force_after_timeout=bool(settings.SWAP_FORCE_AFTER_DRAIN_TIMEOUT),
            trigger=trigger,
        )

    # ------------------------------------------------------------------ public

    async def run(self, rule, *, trigger: str = "interval", force_dry_run: bool = False):
        """Evaluate ``rule`` once. Returns the rule_runs row, or None for an unlogged no_match."""
        started = time.monotonic()
        dry = bool(rule.dry_run or force_dry_run)
        try:
            if rule.type == "time_schedule":
                result = await self._time_schedule(rule, dry, trigger)
            else:
                params = validate_params(rule.type, dict(rule.params or {}))
                target = validate_target(dict(rule.target or {}))
                evaluation = await evaluate(
                    rule.type, params, target, self.inputs, self._ctx(trigger)
                )
                result = await self._apply(rule, evaluation, dry, trigger)
        except Exception as exc:  # noqa: BLE001 - every evaluator failure is a recorded run
            logger.exception(f"rule {rule.id} ({rule.type}) evaluation failed: {exc}")
            result = {
                "outcome": "error",
                "decision": {"trigger": trigger, "dry_run": dry},
                "actions": [],
                "error": f"{type(exc).__name__}: {exc}"[:2048],
            }
        duration_ms = int((time.monotonic() - started) * 1000)
        return await self._record(rule, result, dry, trigger, duration_ms)

    # ------------------------------------------------------------------ evaluation -> actions

    async def _apply(self, rule, evaluation: Evaluation, dry: bool, trigger: str) -> Dict[str, Any]:
        targets: List[Dict[str, Any]] = []
        actionable: List[TargetMatch] = []
        for match in evaluation.matches:
            entry = {
                "deployment_id": str(match.deployment_id),
                "deployment_name": match.deployment_name,
                "observed": match.observed,
            }
            if match.skipped_reason:
                entry.update(outcome="skipped", reason=match.skipped_reason)
            elif not match.actions:
                entry.update(outcome="skipped", reason="no_action")
            else:
                remaining = await self.cooldowns.remaining(rule.id, str(match.deployment_id))
                if remaining > 0:
                    entry.update(
                        outcome="cooldown",
                        cooldown_remaining_seconds=round(remaining, 1),
                    )
                elif not dry and await self._swap_in_progress(match):
                    entry.update(outcome="skipped", reason="swap_in_progress")
                else:
                    entry["outcome"] = "dry_run" if dry else "fired"
                    actionable.append(match)
            targets.append(entry)

        if actionable and not dry:
            active = await self.inputs.active_swap_count()
            cap = int(settings.RULES_MAX_CONCURRENT_SWAPS)
            allowed: List[TargetMatch] = []
            for match in actionable:
                needed = len(match.actions)
                if active + needed > cap:
                    self._target(targets, match).update(
                        outcome="skipped",
                        reason="swap_capacity",
                        active_swaps=active,
                        cap=cap,
                    )
                    continue
                active += needed
                allowed.append(match)
            actionable = allowed

        actions: List[PlannedAction] = []
        for match in actionable:
            actions.extend(match.actions)
        decision = {
            **(evaluation.decision or {}),
            "trigger": trigger,
            "dry_run": dry,
            "considered": evaluation.considered,
            "targets": targets,
            "deployment_ids": [str(m.deployment_id) for m in actionable],
        }
        outcome = _outcome_of([t["outcome"] for t in targets])
        return {
            "outcome": outcome,
            "decision": decision,
            "matches": actionable,
            "actions": actions,
            "error": None,
        }

    @staticmethod
    def _target(targets: List[Dict[str, Any]], match: TargetMatch) -> Dict[str, Any]:
        for t in targets:
            if t["deployment_id"] == str(match.deployment_id):
                return t
        return {}

    async def _swap_in_progress(self, match: TargetMatch) -> bool:
        try:
            return bool(await self.swaps.active_for_deployment(match.deployment_id))
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ time_schedule

    async def _time_schedule(self, rule, dry: bool, trigger: str) -> Dict[str, Any]:
        params = validate_params("time_schedule", dict(rule.params or {}))
        layout_id = uuid.UUID(str(params["layout_id"]))
        now = self.now
        decision: Dict[str, Any] = {
            "trigger": trigger,
            "dry_run": dry,
            "layout_id": str(layout_id),
            "timezone": params.get("timezone"),
        }
        if trigger == "interval":
            await self._maybe_prefetch(rule, params, layout_id)
            sched = schedule_decision(
                params,
                now,
                rule.last_fired_at,
                int(settings.RULES_TICK_SECONDS),
                settings.RULES_DEFAULT_TIMEZONE,
                timedelta(hours=int(settings.RULES_SCHEDULE_LOOKBACK_HOURS)),
            )
            decision.update(
                reason=sched.reason,
                due_at=sched.due_at and sched.due_at.isoformat(),
                next_at=sched.next_at and sched.next_at.isoformat(),
            )
            if sched.due_at is None:
                return {
                    "outcome": "no_match",
                    "decision": decision,
                    "actions": [],
                    "error": None,
                }
            fire_key = f"fire:{sched.due_at.isoformat()}"
            if dry and await self.cooldowns.remaining(rule.id, fire_key) > 0:
                decision["reason"] = "already_dry_run"
                return {
                    "outcome": "no_match",
                    "decision": decision,
                    "actions": [],
                    "error": None,
                }
            decision["fire_key"] = fire_key
        layouts = self.layouts()
        diff = await layouts.diff(layout_id)
        decision["diff"] = diff.model_dump(mode="json", by_alias=True)
        target = {
            "deployment_id": str(layout_id),
            "deployment_name": f"layout:{layout_id}",
            "observed": {"entries": len(diff.entries), "feasible": diff.feasible},
        }
        action = PlannedAction(
            action="apply_layout",
            target_type="layout",
            target_id=layout_id,
            target_name=None,
            deployment_id=None,
            spec={
                "kind": "apply_layout",
                "layout_id": layout_id,
                "drain_timeout_seconds": int(params.get("drain_timeout_seconds", 300)),
            },
        )
        target["outcome"] = "dry_run" if dry else "fired"
        decision["targets"] = [target]
        decision["deployment_ids"] = []
        return {
            "outcome": target["outcome"],
            "decision": decision,
            "actions": [action],
            "layout": {
                "id": layout_id,
                "drain": int(params.get("drain_timeout_seconds", 300)),
            },
            "error": None,
        }

    async def _maybe_prefetch(self, rule, params: Dict[str, Any], layout_id: uuid.UUID) -> None:
        upcoming = prefetch_due(params, self.now, settings.RULES_DEFAULT_TIMEZONE)
        if upcoming is None:
            return
        key = f"prefetch:{upcoming.isoformat()}"
        if await self.cooldowns.remaining(rule.id, key) > 0:
            return
        await self.cooldowns.start(
            rule.id, key, int(params.get("prefetch_minutes_before", 15)) * 60 + 120
        )
        try:
            ids = await self.layouts().prefetch(
                layout_id,
                pin=True,
                pin_reason=f"layout {layout_id} at {upcoming.isoformat()}",
            )
            logger.info(
                f"rule {rule.id}: prefetch for layout {layout_id} at {upcoming}: "
                f"{len(ids)} command(s)"
            )
            from app.services.audit import audit

            audit.record(
                "rule.prefetch",
                actor_type="rule",
                actor_id=rule.id,
                resource_type="layout",
                resource_id=layout_id,
                metadata={
                    "fire_at": upcoming.isoformat(),
                    "command_ids": [str(i) for i in ids],
                },
            )
        except Exception as exc:  # noqa: BLE001 - prefetch is best effort
            logger.warning(f"rule {rule.id}: prefetch for layout {layout_id} failed: {exc}")

    # ------------------------------------------------------------------ record

    async def _record(
        self, rule, result: Dict[str, Any], dry: bool, trigger: str, duration_ms: int
    ):
        outcome = result["outcome"]
        now = self.now
        observe_rule(rule.type, outcome)
        if outcome == "no_match" and trigger == "interval":
            last = await self.runs.last_no_match_at(rule.id)
            interval = int(settings.RULE_RUN_NO_MATCH_LOG_INTERVAL_SECONDS)
            if last is not None and (now - last).total_seconds() < interval:
                await self.rules.mark_evaluated(rule.id, now)
                return None
        run = await self.runs.create(
            rule_id=rule.id,
            rule_type=rule.type,
            evaluated_at=now,
            fired=outcome == "fired",
            dry_run=dry,
            outcome=outcome,
            decision=_jsonable(result.get("decision") or {}),
            actions=[],
            error_message=result.get("error"),
            duration_ms=duration_ms,
        )
        actions = list(result.get("actions") or [])
        if outcome == "fired":
            try:
                await self._execute(rule, run, result, actions)
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"rule {rule.id}: creating swaps failed: {exc}")
                run.outcome = "error"
                run.fired = False
                run.error_message = f"swap creation failed: {exc}"[:2048]
                outcome = "error"
        elif dry:
            for action in actions:
                action.status = "dry_run"
        run.actions = [_action_json(a) for a in actions]
        await self.session.flush()

        if outcome == "fired":
            await self.rules.mark_fired(rule.id, now)
        else:
            await self.rules.mark_evaluated(rule.id, now)
        if outcome in ("fired", "dry_run"):
            await self._start_cooldowns(rule, result)
        self._audit_and_alert(rule, run, outcome, result, actions)
        self._queue_live(rule, run, outcome, dry, trigger, actions)
        return run

    def _queue_live(
        self,
        rule,
        run,
        outcome: str,
        dry: bool,
        trigger: str,
        actions: List[PlannedAction],
    ) -> None:
        if outcome not in ("fired", "dry_run", "error"):
            return
        summary = ", ".join(f"{a.action} {a.target_name or a.target_id}" for a in actions[:5])
        self.pending_live.append(
            {
                "id": str(run.id),
                "rule_run_id": str(run.id),
                "rule_id": str(rule.id),
                "rule_name": rule.name,
                "rule_type": rule.type,
                "outcome": outcome,
                "dry_run": bool(dry),
                "trigger": trigger,
                "message": (run.error_message if outcome == "error" else (summary or None)),
                "error_message": run.error_message,
                "actions": len(actions),
            }
        )

    async def _execute(
        self, rule, run, result: Dict[str, Any], actions: List[PlannedAction]
    ) -> None:
        layout = result.get("layout")
        if layout is not None:
            applied = await self.layouts().apply(
                layout["id"],
                drain_timeout_seconds=layout["drain"],
                trigger="rule",
                rule_run_id=run.id,
            )
            for action in actions:
                action.status = "queued"
                action.message = f"{len(applied.swap_operation_ids)} swap(s)"
                action.spec["swap_operation_ids"] = [str(s) for s in applied.swap_operation_ids]
                if applied.swap_operation_ids:
                    action.swap_operation_id = applied.swap_operation_ids[0]
            return
        trigger = "auto_heal" if rule.type == "auto_heal" else "rule"
        for match in result.get("matches") or []:
            by_target: Dict[str, uuid.UUID] = {}
            for action in match.actions:
                depends = [
                    by_target[d]
                    for d in action.spec.get("depends_on_actions", [])
                    if d in by_target
                ]
                row = await self.planner().create(
                    action.spec,
                    trigger=trigger,
                    deployment_id=action.deployment_id,
                    rule_run_id=run.id,
                    depends_on=depends,
                )
                action.swap_operation_id = row.id
                action.status = "queued"
                if action.target_id is not None:
                    by_target[str(action.target_id)] = row.id

    async def _start_cooldowns(self, rule, result: Dict[str, Any]) -> None:
        seconds = int(rule.cooldown_seconds or 0)
        decision = result.get("decision") or {}
        if rule.type == "time_schedule":
            if decision.get("fire_key"):
                await self.cooldowns.start(
                    rule.id,
                    decision["fire_key"],
                    int(settings.RULES_SCHEDULE_LOOKBACK_HOURS) * 3600,
                )
            return
        for dep_id in decision.get("deployment_ids") or []:
            await self.cooldowns.start(rule.id, dep_id, seconds)

    def _audit_and_alert(
        self, rule, run, outcome: str, result, actions: List[PlannedAction]
    ) -> None:
        from app.services.audit import audit

        summary = ", ".join(f"{a.action} {a.target_name or a.target_id}" for a in actions[:5])
        if outcome == "fired":
            audit.record(
                "rule.fired",
                actor_type="rule",
                actor_id=rule.id,
                actor_name=rule.name,
                resource_type="rule",
                resource_id=rule.id,
                resource_name=rule.name,
                after_state={"actions": run.actions},
                metadata={"rule_run_id": str(run.id)},
                message=summary,
            )
            event = "rule.fired"
            if rule.type == "auto_heal" and actions:
                event = HEAL_EVENT.get(actions[0].action, "rule.fired")
            self._queue_alert(
                event,
                "warning" if event.startswith("heal.") else "info",
                f"Rule '{rule.name}' fired",
                summary,
                rule,
                run,
            )
        elif outcome == "dry_run":
            self._queue_alert(
                "rule.dry_run",
                "info",
                f"Rule '{rule.name}' (dry run) would fire",
                summary,
                rule,
                run,
            )
        elif outcome == "error":
            audit.record(
                "rule.error",
                actor_type="rule",
                actor_id=rule.id,
                actor_name=rule.name,
                resource_type="rule",
                resource_id=rule.id,
                resource_name=rule.name,
                outcome="failure",
                message=run.error_message,
            )
            self._queue_alert(
                "rule.error",
                "warning",
                f"Rule '{rule.name}' failed",
                run.error_message,
                rule,
                run,
            )
        elif outcome == "skipped":
            giving_up = [
                t
                for t in (result.get("decision") or {}).get("targets", [])
                if t.get("reason") == "giving_up"
            ]
            if giving_up:
                self._queue_alert(
                    "heal.giving_up",
                    "critical",
                    f"Auto heal gave up on '{giving_up[0]['deployment_name']}'",
                    "max_restarts_per_hour reached; manual action needed",
                    rule,
                    run,
                )

    def _queue_alert(self, event_type, severity, title, message, rule, run) -> None:
        self.pending_alerts.append(
            {
                "event_type": event_type,
                "severity": severity,
                "title": title,
                "message": message,
                "resource_type": "rule",
                "resource_id": rule.id,
                "resource_name": rule.name,
                "payload": {
                    "rule_run_id": str(run.id),
                    "rule_type": rule.type,
                    "actions": run.actions,
                },
                "dedupe_key": (
                    f"{event_type}:{rule.id}:{run.id}"
                    if event_type != "rule.error"
                    else f"rule.error:{rule.id}"
                ),
            }
        )

    async def flush_alerts(self) -> None:
        """Emit queued alerts (call after the caller committed the run)."""
        alerts = self._alerts
        if alerts is None:
            from app.services.alerts.service import emit

            alerts_emit = emit
        else:
            alerts_emit = alerts.emit
        queued, self.pending_alerts = self.pending_alerts, []
        for item in queued:
            event_type = item.pop("event_type")
            severity = item.pop("severity")
            title = item.pop("title")
            message = item.pop("message")
            try:
                await alerts_emit(event_type, severity, title, message, **item)
            except Exception as exc:  # noqa: BLE001 - emit never raises; a fake might
                logger.error(f"rule alert {event_type} failed: {exc}")
        await self.flush_live()

    async def flush_live(self) -> None:
        """Publish queued ``rule_run`` live events. Never raises."""
        queued, self.pending_live = self.pending_live, []
        if not queued:
            return
        publish = self._live
        if publish is None:
            from app.services.alerts.live import publish_live

            publish = publish_live
        for item in queued:
            try:
                await publish("rule_run", item)
            except Exception as exc:  # noqa: BLE001 - live events are best effort
                logger.debug(f"rule_run live publish failed: {exc}")


def _action_json(action: PlannedAction) -> Dict[str, Any]:
    data = action.to_json()
    data.pop("spec", None)
    return data


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value
