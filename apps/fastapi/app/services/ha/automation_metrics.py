"""
Prometheus metrics of the automation domains (metrics.md §7): rules, swaps and
the jobs-leader lease. Registered on import by the modules that update them
(rules engine, swap executor, HA lease state); dashboards in
infra/observability query these exact names.
"""

from __future__ import annotations

from typing import Any, Callable

from app.core.logger import get_logger
from prometheus_client import Counter, Gauge, Histogram

logger = get_logger(__name__)

rules_evaluations_total = Counter(
    "gpudeck_rules_evaluations_total",
    "Rule evaluations by rule type and outcome (fired, dry_run, no_match, cooldown, ...)",
    ["rule_type", "outcome"],
)
swaps_total = Counter(
    "gpudeck_swaps_total",
    "Swap operations that reached a terminal state, by kind and state",
    ["kind", "state"],
)
swap_duration_seconds = Histogram(
    "gpudeck_swap_duration_seconds",
    "Wall time of a swap operation from start to its terminal state",
    ["kind"],
    buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1200, 2400, 3600),
)
jobs_leader = Gauge(
    "gpudeck_jobs_leader", "1 while this process holds the jobs-leader lease, else 0"
)

TERMINAL_SWAP_STATES = ("completed", "failed", "rolled_back", "cancelled")


def observe_rule(rule_type: Any, outcome: Any) -> None:
    try:
        rules_evaluations_total.labels(rule_type=str(rule_type), outcome=str(outcome)).inc()
    except Exception as exc:  # noqa: BLE001 - metrics never break evaluation
        logger.debug(f"rules metric failed: {exc}")


def observe_swap(swap: Any) -> None:
    """Count a swap once, when it is saved in a terminal state."""
    try:
        state = str(getattr(swap, "state", ""))
        if state not in TERMINAL_SWAP_STATES or getattr(swap, "_metrics_observed", False):
            return
        kind = str(getattr(swap, "kind", "") or "unknown")
        swaps_total.labels(kind=kind, state=state).inc()
        started, finished = getattr(swap, "started_at", None), getattr(swap, "finished_at", None)
        if started is not None and finished is not None:
            swap_duration_seconds.labels(kind=kind).observe(
                max(0.0, (finished - started).total_seconds())
            )
        try:
            setattr(swap, "_metrics_observed", True)
        except Exception:  # noqa: BLE001 - frozen objects
            pass
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"swap metric failed: {exc}")


def bind_leader_gauge(is_leader: Callable[[], bool]) -> None:
    jobs_leader.set_function(lambda: 1.0 if is_leader() else 0.0)
