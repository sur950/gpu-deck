"""
Instance state machine and deployment status derivation (pure).

Instance lifecycle (``instance_state`` enum)::

    pending ──load sent──> loading ──healthy──> ready ──drain──> draining
       │                      │                  │                  │
       │                      └──fail──> error   └──unload──> unloading ──> stopped
       └──────────────cancel────────────────────────────────────────────────> stopped

``error`` can go to ``unloading`` (cleanup), ``stopped`` or back to ``pending``
/ ``loading`` (restart). ``stopped`` is terminal (rows are kept for history;
a restart creates a new row). Agent-reported states (inventory / events)
are applied as facts and never rejected; ``can_transition`` guards the
transitions the deployments service itself initiates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, Optional

from app.core.exceptions import ConflictError

TRANSITIONS: Dict[str, FrozenSet[str]] = {
    "pending": frozenset({"loading", "ready", "stopped", "error", "unloading"}),
    "loading": frozenset({"ready", "error", "unloading", "stopped", "draining"}),
    "ready": frozenset({"draining", "unloading", "loading", "error", "stopped"}),
    "draining": frozenset({"ready", "unloading", "stopped", "error", "loading"}),
    "unloading": frozenset({"stopped", "error"}),
    "error": frozenset({"pending", "loading", "unloading", "stopped", "ready"}),
    "stopped": frozenset(),
}

LIVE = frozenset({"pending", "loading", "ready", "draining", "unloading"})
ACTIVE = frozenset({"pending", "loading", "ready"})
TERMINAL = frozenset({"stopped"})


def can_transition(current: str, target: str) -> bool:
    return current == target or target in TRANSITIONS.get(current, frozenset())


def ensure_transition(current: str, target: str, what: str = "instance") -> None:
    if not can_transition(current, target):
        raise ConflictError(f"INVALID_STATE: {what} is {current}; cannot go to {target}")


@dataclass
class StatusInput:
    deleted: bool
    desired_running: bool
    target: int
    states: Iterable[str]
    active_swaps: int = 0
    unplaceable: Optional[str] = None
    crash_looping: bool = False


def derive_status(inp: StatusInput) -> str:
    """Deployment status from desired replicas and the instance states."""
    states = list(inp.states)
    ready = sum(1 for s in states if s == "ready")
    loading = sum(1 for s in states if s in ("pending", "loading"))
    going = sum(1 for s in states if s in ("draining", "unloading"))
    live = ready + loading + going
    if inp.deleted:
        return "deleting"
    if inp.crash_looping:
        return "failed"
    if not inp.desired_running or inp.target == 0:
        return "scaling" if live else "stopped"
    if inp.active_swaps:
        return "scaling"
    if ready >= inp.target and loading == 0 and going == 0:
        return "running"
    if ready == 0:
        if loading:
            return "deploying"
        return "pending" if inp.unplaceable else "scheduling"
    if loading or going:
        return "scaling"
    return "degraded"
