"""
Unit tests: rule param validation against libs/contracts/rules, target
selectors, time windows and time_schedule fire times across timezones / DST.
"""

from datetime import datetime, timedelta, timezone

import pytest
from app.core.exceptions import ValidationError
from app.services.rules.validation import (
    schemas_for_api,
    validate_layout_document,
    validate_params,
    validate_target,
)
from app.services.rules.windows import (
    build_trigger,
    in_time_windows,
    next_fire_time,
    prefetch_due,
    previous_fire_time,
    schedule_decision,
)

UTC = timezone.utc
LAYOUT_ID = "6f0b6a8e-1d0c-4a55-9b0a-0d2a3b1c4d5e"


# ---------------------------------------------------------------- validation


def test_idle_unload_defaults_are_filled():
    params = validate_params("idle_unload", {"idle_minutes": 45})
    assert params["idle_minutes"] == 45
    assert params["min_loaded_minutes"] == 10
    assert params["keep_replicas"] == 0
    assert params["respect_time_windows"] == []
    assert params["timezone"] == "UTC"


@pytest.mark.parametrize(
    "rule_type,params",
    [
        ("idle_unload", {"idle_minutes": "ten"}),
        ("idle_unload", {"idle_minutes": 0}),
        ("idle_unload", {"idle_minutes": 5, "bogus": 1}),
        ("idle_unload", {"idle_minutes": 5, "respect_time_windows": ["25:00-01:00"]}),
        ("idle_unload", {"idle_minutes": 5, "timezone": "Mars/Olympus"}),
        ("time_schedule", {"layout_id": LAYOUT_ID, "timezone": "Not/AZone"}),
        ("time_schedule", {"layout_id": LAYOUT_ID, "cron": "61 * * * *"}),
        ("time_schedule", {"layout_id": LAYOUT_ID, "at": "24:00"}),
        ("time_schedule", {}),
        (
            "low_traffic_shrink",
            {
                "requests_per_minute_below": 1,
                "for_minutes": 5,
                "action": "shrink_slice",
            },
        ),
        (
            "high_load_grow",
            {
                "for_minutes": 2,
                "max_replicas": 2,
                "waiting_requests_above": None,
                "kv_cache_pct_above": None,
            },
        ),
        ("make_room_by_priority", {"evict_priorities": ["always_on"]}),
        ("make_room_by_priority", {"protect_always_on": False}),
        (
            "auto_heal",
            {
                "action": "restart",
                "health_failures": None,
                "error_rate_pct_above": None,
            },
        ),
        ("auto_heal", {"action": "explode"}),
    ],
)
def test_invalid_params_rejected(rule_type, params):
    with pytest.raises(ValidationError):
        validate_params(rule_type, params)


@pytest.mark.parametrize(
    "rule_type,params",
    [
        (
            "time_schedule",
            {"layout_id": LAYOUT_ID, "at": "00:00", "timezone": "Asia/Kolkata"},
        ),
        ("time_schedule", {"layout_id": LAYOUT_ID, "cron": "30 22 * * mon-fri"}),
        (
            "low_traffic_shrink",
            {
                "requests_per_minute_below": 1,
                "for_minutes": 5,
                "action": "shrink_slice",
                "shrink_slice": {"vram_bytes": 1024},
            },
        ),
        ("high_load_grow", {"for_minutes": 2, "max_replicas": 3}),
        ("make_room_by_priority", {}),
        ("auto_heal", {"action": "restart"}),
    ],
)
def test_valid_params_accepted(rule_type, params):
    out = validate_params(rule_type, params)
    for key, value in params.items():
        assert out[key] == value


def test_unknown_rule_type_rejected():
    with pytest.raises(ValidationError):
        validate_params("teleport", {})


def test_target_selector_validation():
    assert validate_target(None) == {}
    assert (
        validate_target({"priorities": ["normal"], "name_glob": "chat-*"})["name_glob"] == "chat-*"
    )
    with pytest.raises(ValidationError):
        validate_target({"priorities": ["sometimes"]})
    with pytest.raises(ValidationError):
        validate_target({"unknown": 1})


def test_layout_document_validation_fills_item_defaults():
    doc = validate_layout_document({"name": "Night", "items": [{"deployment_id": LAYOUT_ID}]})
    assert doc["unlisted_policy"] == "keep"
    assert doc["items"][0]["replicas"] == 1
    assert doc["items"][0]["traffic_weight"] == 100
    with pytest.raises(ValidationError):
        validate_layout_document({"name": "x", "items": []})


def test_schemas_payload_has_every_type():
    payload = schemas_for_api()
    assert set(payload["params"]) == {
        "idle_unload",
        "time_schedule",
        "low_traffic_shrink",
        "high_load_grow",
        "make_room_by_priority",
        "auto_heal",
    }
    assert payload["rule"]["title"].startswith("Rule")


# ---------------------------------------------------------------- windows


def test_time_window_in_rule_timezone_wrapping_midnight():
    windows = ["22:00-06:00"]
    # 17:00 UTC = 22:30 IST -> inside
    assert in_time_windows(datetime(2026, 11, 17, 17, 0, tzinfo=UTC), windows, "Asia/Kolkata")
    # 01:00 UTC = 06:30 IST -> outside
    assert not in_time_windows(datetime(2026, 11, 17, 1, 0, tzinfo=UTC), windows, "Asia/Kolkata")
    # same instant is inside for a UTC rule only if 01:00 in [22:00, 06:00)
    assert in_time_windows(datetime(2026, 11, 17, 1, 0, tzinfo=UTC), windows, "UTC")
    assert in_time_windows(datetime(2026, 11, 17, 1, 0, tzinfo=UTC), [], "UTC")


# ---------------------------------------------------------------- schedules / DST


def test_daily_midnight_in_kolkata():
    trigger = build_trigger({"at": "00:00", "timezone": "Asia/Kolkata"})
    nxt = next_fire_time(trigger, datetime(2026, 11, 17, 12, 0, tzinfo=UTC))
    assert nxt == datetime(2026, 11, 17, 18, 30, tzinfo=UTC)


def test_days_are_local_days():
    trigger = build_trigger({"at": "00:00", "days": ["mon"], "timezone": "Asia/Kolkata"})
    # Monday 00:00 IST is Sunday 18:30 UTC
    nxt = next_fire_time(trigger, datetime(2026, 11, 17, 12, 0, tzinfo=UTC))
    assert nxt == datetime(2026, 11, 22, 18, 30, tzinfo=UTC)
    assert nxt.weekday() == 6


def test_midnight_new_york_across_dst_end():
    trigger = build_trigger({"at": "00:00", "timezone": "America/New_York"})
    first = next_fire_time(trigger, datetime(2026, 10, 31, 12, 0, tzinfo=UTC))
    second = next_fire_time(trigger, first)
    assert first == datetime(2026, 11, 1, 4, 0, tzinfo=UTC)  # EDT (UTC-4)
    assert second == datetime(2026, 11, 2, 5, 0, tzinfo=UTC)  # EST (UTC-5)


def test_repeated_hour_on_fall_back_fires_once():
    trigger = build_trigger({"at": "01:30", "timezone": "America/New_York"})
    first = next_fire_time(trigger, datetime(2026, 10, 31, 12, 0, tzinfo=UTC))
    second = next_fire_time(trigger, first)
    assert first == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert second == datetime(2026, 11, 2, 6, 30, tzinfo=UTC)  # not 06:30Z on Nov 1
    # during the repeated hour the previous fire is still the first 01:30
    assert previous_fire_time(trigger, datetime(2026, 11, 1, 6, 40, tzinfo=UTC)) == first


def test_skipped_hour_on_spring_forward_fires_once():
    trigger = build_trigger({"at": "02:30", "timezone": "America/New_York"})
    first = next_fire_time(trigger, datetime(2026, 3, 7, 12, 0, tzinfo=UTC))
    second = next_fire_time(trigger, first)
    assert first.date() == datetime(2026, 3, 8).date()
    assert second == datetime(2026, 3, 9, 6, 30, tzinfo=UTC)


def test_cron_overrides_at_and_days():
    trigger = build_trigger({"cron": "*/15 * * * *", "at": "05:00", "timezone": "Asia/Kolkata"})
    assert next_fire_time(trigger, datetime(2026, 11, 17, 12, 1, tzinfo=UTC)) == datetime(
        2026, 11, 17, 12, 15, tzinfo=UTC
    )


def _params(**extra):
    return {
        "at": "00:00",
        "timezone": "UTC",
        "layout_id": LAYOUT_ID,
        "catch_up": True,
        "prefetch_minutes_before": 15,
        **extra,
    }


def test_schedule_decision_on_time_and_already_applied():
    now = datetime(2026, 11, 17, 0, 0, 5, tzinfo=UTC)
    d = schedule_decision(_params(), now, None, tick_seconds=10)
    assert d.reason == "on_time" and d.due_at == datetime(2026, 11, 17, tzinfo=UTC)
    d = schedule_decision(_params(), now, last_fired_at=now, tick_seconds=10)
    assert d.due_at is None and d.reason == "already_applied"


def test_schedule_decision_catch_up_rules():
    # jobs process was down at 00:00; at 03:00 the next run (tomorrow) is > 1 h away
    now = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)
    last = datetime(2026, 11, 16, 0, 0, 1, tzinfo=UTC)
    assert schedule_decision(_params(), now, last, 10).reason == "catch_up"
    assert schedule_decision(_params(catch_up=False), now, last, 10).reason == "missed_no_catch_up"
    # missed, but the next run is within the hour -> wait for it
    late = datetime(2026, 11, 17, 23, 30, tzinfo=UTC)
    assert schedule_decision(_params(), late, last, 10).reason == "missed_next_run_within_1h"


def test_prefetch_window():
    params = _params(prefetch_minutes_before=15)
    assert prefetch_due(params, datetime(2026, 11, 17, 23, 50, tzinfo=UTC)) == datetime(
        2026, 11, 18, tzinfo=UTC
    )
    assert prefetch_due(params, datetime(2026, 11, 17, 23, 40, tzinfo=UTC)) is None
    assert (
        prefetch_due(
            _params(prefetch_minutes_before=0),
            datetime(2026, 11, 17, 23, 50, tzinfo=UTC),
        )
        is None
    )


def test_lookback_limits_catch_up():
    now = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)
    trigger = build_trigger(_params(days=["mon"]))  # 2026-11-16 is a Monday
    assert previous_fire_time(trigger, now, timedelta(hours=48)) == datetime(
        2026, 11, 16, tzinfo=UTC
    )
    assert previous_fire_time(trigger, now, timedelta(hours=12)) is None
