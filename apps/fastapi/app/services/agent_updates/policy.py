"""
Per-cluster agent update policy (``clusters.config.agent_updates``) and maintenance windows.

``{"mode": "manual"}`` (default): nodes update when an admin clicks Update (one node) or starts
a rolling update (cluster). ``{"mode": "auto", "window": {"days": ["sat", "sun"], "start":
"02:00", "end": "05:00", "timezone": "UTC"}}``: the scheduler starts a rolling update by itself
inside the window when nodes have the target version staged. A window whose end is earlier than
its start runs past midnight; ``days`` are the days it starts on.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from app.core.logger import get_logger
from app.schemas.agent_update_schema import (
    WEEKDAYS,
    AgentUpdatePolicy,
    AgentUpdateWindow,
)

logger = get_logger(__name__)


def _hm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def _bounds(window: AgentUpdateWindow, day: datetime):
    """Start / end of the window opening on ``day`` (local date) as aware datetimes."""
    tz = ZoneInfo(window.timezone)
    start = datetime.combine(day.date(), _hm(window.start), tzinfo=tz)
    end = datetime.combine(day.date(), _hm(window.end), tzinfo=tz)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def in_window(window: Optional[AgentUpdateWindow], now: Optional[datetime] = None) -> bool:
    if window is None:
        return False
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(window.timezone))
    # The window that opened today or yesterday (overnight windows).
    for back in (0, 1):
        day = local - timedelta(days=back)
        if WEEKDAYS[day.weekday()] not in window.days:
            continue
        start, end = _bounds(window, day)
        if start <= local < end:
            return True
    return False


def next_window_start(
    window: Optional[AgentUpdateWindow], now: Optional[datetime] = None
) -> Optional[datetime]:
    """When the window opens next (now when it is open), in UTC."""
    if window is None:
        return None
    now = now or datetime.now(timezone.utc)
    if in_window(window, now):
        return now.astimezone(timezone.utc)
    local = now.astimezone(ZoneInfo(window.timezone))
    for ahead in range(0, 8):
        day = local + timedelta(days=ahead)
        if WEEKDAYS[day.weekday()] not in window.days:
            continue
        start, _ = _bounds(window, day)
        if start > local:
            return start.astimezone(timezone.utc)
    return None


def auto_allowed(policy: AgentUpdatePolicy, now: Optional[datetime] = None) -> bool:
    return policy.mode == "auto" and in_window(policy.window, now)
