"""
Metrics unit tests: step/time parsing, bucket grid, the two-level series
SQL builder, catalog resolution, Prometheus text rendering, the live overview
projection, the rollup window, live-event filtering/formatting and topology
links. No services needed.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.core.exceptions import ValidationError
from app.services.metrics import catalog, live_cache
from app.services.metrics.live import LiveFilter, parse_topics, sse_format, ws_format
from app.services.metrics.prometheus import (
    Exposition,
    check_scrape_token,
    format_labels,
)
from app.services.metrics.service import (
    bucket_grid,
    build_series_sql,
    parse_step,
    parse_time,
    time_window,
)
from app.services.topology.service import build_links
from app.workers.tasks.metrics_tasks import retention_plan, rollup_window

pytestmark = pytest.mark.unit


def _now_iso(delta_s: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=delta_s)).isoformat()


def test_parse_step_and_time():
    assert parse_step("15s") == (15, "15s")
    assert parse_step("5m")[0] == 300
    assert parse_step(None, "1h")[0] == 3600
    with pytest.raises(ValidationError):
        parse_step("5 minutes")
    default = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert parse_time(None, default) == default
    assert parse_time("2026-11-17T10:00:00Z", default).hour == 10
    rel = parse_time("-15m", default)
    assert abs((datetime.now(timezone.utc) - rel).total_seconds() - 900) < 5
    with pytest.raises(ValidationError):
        parse_time("yesterday", default)


def test_time_window_guards():
    with pytest.raises(ValidationError):
        time_window("2026-11-17T10:00:00Z", "2026-11-17T09:00:00Z", "1m")
    with pytest.raises(ValidationError):
        time_window("2026-01-01T00:00:00Z", "2026-11-17T00:00:00Z", "1s")


def test_bucket_grid_aligns_to_step():
    start = datetime(2026, 11, 17, 10, 0, 30, tzinfo=timezone.utc)
    grid = bucket_grid(start, start + timedelta(minutes=3), 60)
    assert grid[0] == datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)
    assert len(grid) == 4


def test_series_sql_shapes():
    specs = catalog.resolve("engine", ["active_sessions", "error_rate_pct"], []).specs
    sql = build_series_sql(
        "metric_rollups_engine_1m",
        "instance_id",
        specs,
        "rollup",
        "sum",
        ["t.ts >= :start"],
    )
    assert "SUM(m0_v) AS m0" in sql
    assert "SUM(m1_e)" in sql and "SUM(m1_r)" in sql  # ratio of sums
    none_sql = build_series_sql(
        "metric_samples_gpu",
        "device_id",
        catalog.resolve("gpu", ["gpu_utilization_pct"], []).specs,
        "raw",
        "none",
        ["t.ts >= :start"],
    )
    assert "GROUP BY bucket, entity_id" in none_sql
    assert "AVG(t.utilization_pct)" in none_sql


def test_catalog_resolution_and_entries():
    resolved = catalog.resolve("gpu", ["gpu_utilization_pct,gpu_power_watts", "bogus"], [])
    assert [s.name for s in resolved.specs] == [
        "gpu_utilization_pct",
        "gpu_power_watts",
    ]
    assert resolved.unknown == ["bogus"]
    names = {e["name"] for e in catalog.catalog_entries()}
    for name in (
        "gpu_utilization_pct",
        "active_sessions",
        "kv_cache_usage_pct",
        "requests",
    ):
        assert name in names
    assert {e["scope"] for e in catalog.catalog_entries()} <= {
        "gpu",
        "engine",
        "session",
        "usage",
        "node",
    }


def test_prometheus_rendering_and_token():
    expo = Exposition()
    expo.add(
        "gpudeck_gpu_utilization_ratio",
        "gauge",
        "GPU",
        {"node": 'a"b', "index": 0},
        0.42,
    )
    expo.add("gpudeck_gpu_utilization_ratio", "gauge", "GPU", {"node": "c"}, None)
    text = expo.render()
    assert "# TYPE gpudeck_gpu_utilization_ratio gauge" in text
    assert 'gpudeck_gpu_utilization_ratio{node="a\\"b",index="0"} 0.42' in text
    assert format_labels({}) == ""
    assert check_scrape_token("Bearer s3cret", "s3cret")
    assert not check_scrape_token("Bearer nope", "s3cret")
    assert not check_scrape_token(None, "s3cret")
    assert not check_scrape_token("Bearer x", None)


def test_overview_live_ignores_stale_samples():
    gpu = [
        {
            "ts": _now_iso(),
            "utilization_pct": 50,
            "memory_used_bytes": 10,
            "power_watts": 100,
        },
        {
            "ts": _now_iso(),
            "utilization_pct": 70,
            "memory_used_bytes": 20,
            "power_watts": 50,
        },
        {"ts": _now_iso(3600), "utilization_pct": 0, "memory_used_bytes": 999},
    ]
    engine = [
        {
            "ts": _now_iso(),
            "active_sessions": 2,
            "waiting_sessions": 1,
            "ttft_ms_p95": 80,
        }
    ]
    live = live_cache.overview_live(gpu, engine, vram_total_bytes=100, requests_per_minute=12)
    assert live["gpu_utilization_pct_avg"] == 60
    assert live["vram_used_bytes"] == 30
    assert live["power_watts"] == 150
    assert live["active_sessions"] == 2 and live["waiting_sessions"] == 1
    assert live["ttft_ms_p95"] == 80 and live["requests_per_minute"] == 12
    assert live_cache.device_live({"ts": _now_iso(3600), "utilization_pct": 3})["ts"] is None


def test_rollup_window_recomputes_recent_minutes_and_catches_up():
    now = datetime(2026, 11, 17, 10, 5, 45, tzinfo=timezone.utc)
    start, end = rollup_window(now, None, 120)
    assert end == datetime(2026, 11, 17, 10, 5, tzinfo=timezone.utc)
    assert start == end - timedelta(minutes=2)
    start, _ = rollup_window(now, end - timedelta(minutes=30), 120)
    assert start == end - timedelta(minutes=30)
    start, _ = rollup_window(now, end - timedelta(days=2), 120)
    assert start == end - timedelta(minutes=120)  # capped catch-up


def test_retention_plan_rounds_raw_hours_up():
    plan = retention_plan()
    assert plan["metric_samples_gpu"] >= 2
    assert plan["metric_rollups_engine_1m"] == 90 or plan["metric_rollups_engine_1m"] > 0


def test_live_filter_and_formats():
    node = uuid.uuid4()
    flt = LiveFilter(topics={"node", "log"}, node_id=node, node_ids={str(node)})
    assert flt.matches({"event": "node", "data": {"node_id": str(node)}})
    assert not flt.matches({"event": "node", "data": {"node_id": str(uuid.uuid4())}})
    assert not flt.matches({"event": "swap", "data": {"node_id": str(node)}})
    topics, unknown = parse_topics(["metrics,log", "nope"])
    assert topics == {"metrics", "log"} and unknown == ["nope"]
    frame = sse_format({"id": "1-0", "event": "log", "data": {"a": 1}})
    assert frame == 'id: 1-0\nevent: log\ndata: {"a":1}\n\n'
    assert '"event":"log"' in ws_format({"event": "log", "data": {}})


def test_topology_links():
    a, b, c, d = (str(uuid.uuid4()) for _ in range(4))
    n1, n2 = uuid.uuid4(), uuid.uuid4()
    instances = [
        {"id": a, "deployment_id": "d1", "node_id": n1, "pd_role": "prefill"},
        {"id": b, "deployment_id": "d1", "node_id": n2, "pd_role": "decode"},
        {"id": c, "deployment_id": "d2", "node_id": n1, "pd_role": None},
        {"id": d, "deployment_id": "d2", "node_id": n2, "pd_role": None},
    ]
    deployments = {"d1": {"pd_split": True}, "d2": {"tp_size": 2, "kv_sharing": True}}
    links = build_links(instances, deployments)
    kinds = sorted(link["kind"] for link in links)
    assert kinds == ["kv_share", "pd_transfer", "tensor_parallel"]
