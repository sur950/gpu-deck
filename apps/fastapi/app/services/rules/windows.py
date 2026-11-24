"""
Clock helpers for rules: timezone-aware time windows (``HH:MM-HH:MM`` in a
rule timezone, wrapping past midnight) and time_schedule fire-time
computation (``at`` + ``days`` or a 5-field cron, in an IANA zone, DST-safe
through APScheduler's CronTrigger).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

from app.core.logger import get_logger
from apscheduler.triggers.cron import CronTrigger

logger = get_logger(__name__)

_DAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def zone(name: Optional[str], default: str = "UTC") -> ZoneInfo:
    return ZoneInfo(name or default)


def _parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def in_time_windows(now: datetime, windows: Sequence[str], tz_name: Optional[str]) -> bool:
    """
    True if ``now`` falls inside any ``HH:MM-HH:MM`` window expressed in the
    rule timezone. A window whose end is before its start wraps past midnight
    (``22:00-06:00``). Empty list = always.
    """
    if not windows:
        return True
    local = now.astimezone(zone(tz_name))
    current = local.time().replace(second=0, microsecond=0)
    for window in windows:
        start_s, end_s = window.split("-")
        start, end = _parse_hhmm(start_s), _parse_hhmm(end_s)
        if start <= end:
            if start <= current < end:
                return True
        else:  # wraps midnight
            if current >= start or current < end:
                return True
    return False


def build_trigger(params: Dict[str, Any], default_tz: str = "UTC") -> CronTrigger:
    """CronTrigger for a time_schedule rule (cron wins over at/days)."""
    tz = zone(params.get("timezone"), default_tz)
    if params.get("cron"):
        return CronTrigger.from_crontab(params["cron"], timezone=tz)
    at = _parse_hhmm(params.get("at") or "00:00")
    days = params.get("days") or _DAY_ORDER
    day_of_week = ",".join(d for d in _DAY_ORDER if d in days)
    return CronTrigger(day_of_week=day_of_week, hour=at.hour, minute=at.minute, timezone=tz)


def _wall(dt: datetime, tz) -> tuple:
    local = dt.astimezone(tz)
    return (local.year, local.month, local.day, local.hour, local.minute)


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    return value.astimezone(timezone.utc) if value is not None else None


def _fires(trigger: CronTrigger, start: datetime, end: datetime, limit: int = 100_000):
    """
    Fire times in ``[start, end]`` with DST fall-back duplicates removed: when the
    clock repeats an hour (01:30 EDT then 01:30 EST) only the first one fires.
    Spring-forward gaps fire once at the shifted time (APScheduler behaviour).
    """
    tz = trigger.timezone
    # Work in UTC: aware arithmetic/comparison on a ZoneInfo datetime is wall-clock
    # based and ignores ``fold``, which breaks on the repeated hour.
    cursor = _utc(trigger.get_next_fire_time(None, start))
    last_wall = None
    count = 0
    while cursor is not None and cursor <= end and count < limit:
        wall = _wall(cursor, tz)
        if wall != last_wall:
            yield cursor
        last_wall = wall
        following = _utc(trigger.get_next_fire_time(None, cursor + timedelta(microseconds=1)))
        if following is not None and following <= cursor:  # ambiguous local time: step past it
            following = _utc(trigger.get_next_fire_time(None, cursor + timedelta(minutes=1)))
        cursor = following
        count += 1


_DST_SLACK = timedelta(hours=3)


def next_fire_time(trigger: CronTrigger, after: datetime) -> Optional[datetime]:
    """First fire time strictly after ``after`` (UTC-aware result, DST-deduplicated)."""
    horizon = after + timedelta(days=400)
    for fire in _fires(trigger, after - _DST_SLACK, horizon):
        if fire > after:
            return fire.astimezone(timezone.utc)
    return None


def previous_fire_time(
    trigger: CronTrigger, now: datetime, lookback: timedelta = timedelta(hours=48)
) -> Optional[datetime]:
    """Latest fire time <= ``now`` within ``lookback``; None if there is none."""
    last = None
    for fire in _fires(trigger, now - lookback - _DST_SLACK, now):
        if fire >= now - lookback:
            last = fire
    return last.astimezone(timezone.utc) if last else None


@dataclass
class ScheduleDecision:
    due_at: Optional[datetime]  # fire time that should be applied now (None = nothing due)
    next_at: Optional[datetime]
    reason: str


def schedule_decision(
    params: Dict[str, Any],
    now: datetime,
    last_fired_at: Optional[datetime],
    tick_seconds: int,
    default_tz: str = "UTC",
    lookback: timedelta = timedelta(hours=48),
) -> ScheduleDecision:
    """
    Decide whether a time_schedule rule is due.

    - A fire time inside the last tick window (``now - tick <= t <= now``) is
      always due unless it has already been applied (``last_fired_at >= t``).
    - An older missed fire time (process was down) is due only when
      ``catch_up`` is on and the next fire is more than 1 hour away
      (rules.md: "apply the layout on start-up when the next run is more than
      1 h away").
    """
    trigger = build_trigger(params, default_tz)
    prev = previous_fire_time(trigger, now, lookback)
    nxt = next_fire_time(trigger, now)
    if prev is None:
        return ScheduleDecision(None, nxt, "no_fire_time_in_lookback")
    if last_fired_at is not None and last_fired_at >= prev:
        return ScheduleDecision(None, nxt, "already_applied")
    age = (now - prev).total_seconds()
    if age <= max(tick_seconds, 1) * 2:
        return ScheduleDecision(prev, nxt, "on_time")
    if not params.get("catch_up", True):
        return ScheduleDecision(None, nxt, "missed_no_catch_up")
    if nxt is not None and (nxt - now) <= timedelta(hours=1):
        return ScheduleDecision(None, nxt, "missed_next_run_within_1h")
    return ScheduleDecision(prev, nxt, "catch_up")


def prefetch_due(
    params: Dict[str, Any], now: datetime, default_tz: str = "UTC"
) -> Optional[datetime]:
    """The upcoming fire time whose prefetch window has started, else None."""
    minutes = int(params.get("prefetch_minutes_before") or 0)
    if minutes <= 0:
        return None
    nxt = next_fire_time(build_trigger(params, default_tz), now)
    if nxt is None:
        return None
    if nxt - timedelta(minutes=minutes) <= now < nxt:
        return nxt
    return None


def describe_days(days: Optional[List[str]]) -> str:
    return ",".join(days or _DAY_ORDER)
