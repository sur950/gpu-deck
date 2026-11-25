"""
Network latency (unit, no services): RTT statistics, latency-aware replica selection,
the Server-Timing header, the request timing record, traffic-aware placement and link cost,
the placement context, the history rows and the hub's ping / pong hook.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import random
import uuid
from types import SimpleNamespace
from typing import Dict, Optional

import pytest
from app.core.settings import settings
from app.services.latency.stats import RttStats, percentile
from app.services.serving import latency as serving_latency
from app.services.serving import timing
from app.services.serving.routing import RoutingTable


@pytest.fixture
def tracker(monkeypatch):
    """A fresh process-wide RTT tracker for routing (restored afterwards)."""
    fresh = serving_latency.LatencyTracker()
    monkeypatch.setattr(serving_latency, "_tracker", fresh)
    return fresh


# ---------------------------------------------------------------------------
# RTT statistics
# ---------------------------------------------------------------------------


def test_rtt_stats_ewma_jitter_and_percentiles():
    stats = RttStats(alpha=0.5)
    for value in (10.0, 20.0, 10.0, 20.0):
        stats.add(value, now=1000.0)
    # EWMA: 10 -> 15 -> 12.5 -> 16.25
    assert stats.ewma_ms == pytest.approx(16.25)
    # jitter follows |delta| = 10 with gain 0.5: 0 -> 5 -> 7.5 -> 8.75
    assert stats.jitter_ms == pytest.approx(8.75)
    snap = stats.snapshot()
    assert snap["min_ms"] == 10.0 and snap["max_ms"] == 20.0
    assert snap["p50_ms"] == pytest.approx(15.0)
    assert snap["samples"] == 4 and snap["failures"] == 0
    assert stats.fresh(60, now=1030.0) and not stats.fresh(60, now=1100.0)
    stats.fail()
    assert stats.failures == 1 and stats.ewma_ms == pytest.approx(16.25)


def test_percentile_interpolates():
    assert percentile([], 50) is None
    assert percentile([5.0], 95) == 5.0
    assert percentile(range(1, 101), 95) == pytest.approx(95.05)


def test_tracker_ignores_stale_and_failing_nodes(tracker):
    tracker.record("n1", 12.0)
    assert serving_latency.rtt_ms("n1") == pytest.approx(12.0)
    for _ in range(3):
        tracker.failure("n1")
    assert serving_latency.rtt_ms("n1") is None  # three probes in a row got no answer
    tracker.record("n1", 14.0)
    assert serving_latency.rtt_ms("n1") is not None
    tracker.stats["n1"].updated_at -= 3600
    assert serving_latency.rtt_ms("n1") is None  # stale
    tracker.count_request("dep-1")
    tracker.count_request("dep-1")
    tracker.count_request(None)
    assert tracker.take_traffic() == {"dep-1": 2} and tracker.take_traffic() == {}


def test_probe_targets_prefers_node_list_and_skips_disconnected():
    table = RoutingTable()
    table.load_from_dict(
        {
            "models": {
                "m": {
                    "type": "chat",
                    "instances": [
                        {"instance_id": "i1", "node_id": "a", "proxy_url": "tunnel://a"},
                        {"instance_id": "i2", "node_id": "c", "proxy_url": "https://c:9443"},
                    ],
                }
            },
            "nodes": {
                "a": {"proxy_url": "tunnel://a", "status": "online"},
                "b": {"proxy_url": "https://b:9443", "status": "offline"},
                "d": {"proxy_url": "https://d:9443/", "status": "drained"},
            },
        }
    )
    assert serving_latency.probe_targets(table) == {
        "a": "tunnel://a",
        "d": "https://d:9443",
        "c": "https://c:9443",
    }
    assert serving_latency.path_mode("tunnel://a") == "tunnel"
    assert serving_latency.path_mode("https://d:9443") == "direct"


# ---------------------------------------------------------------------------
# Latency-aware replica selection
# ---------------------------------------------------------------------------


def _table(*instances: Dict) -> RoutingTable:
    table = RoutingTable()
    table.load_from_dict(
        {
            "models": {
                "m": {
                    "type": "chat",
                    "traffic": {"strategy": "least_sessions"},
                    "instances": list(instances),
                }
            }
        }
    )
    return table


def _inst(iid: str, node: str, **kw) -> Dict:
    return {"instance_id": iid, "node_id": node, "proxy_url": f"https://{node}:9443", **kw}


def test_routing_prefers_the_near_node_under_equal_load(tracker):
    table = _table(_inst("far", "n-far"), _inst("near", "n-near"))
    tracker.record("n-near", 2.0)
    tracker.record("n-far", 80.0)
    entry = table.get("m")
    picks = {table.pick(entry, {}).instance_id for _ in range(50)}
    assert picks == {"near"}


def test_routing_spills_to_the_far_node_under_load(tracker, monkeypatch):
    monkeypatch.setattr(settings, "SERVING_LATENCY_LOAD_PENALTY_MS", 200.0)
    monkeypatch.setattr(settings, "SERVING_LATENCY_DEFAULT_CAPACITY", 8)
    table = _table(_inst("far", "n-far"), _inst("near", "n-near"))
    tracker.record("n-near", 2.0)
    tracker.record("n-far", 62.0)  # 60 ms farther = 2.4 in-flight requests of penalty
    entry = table.get("m")
    assert table.pick(entry, {"near": 2}).instance_id == "near"  # 2 + 50 < 62
    assert table.pick(entry, {"near": 3}).instance_id == "far"  # 2 + 75 > 62
    # live sessions from the hub count the same as our own in-flight requests
    busy = _table(_inst("far", "n-far"), _inst("near", "n-near", active_sessions=4))
    assert busy.pick(busy.get("m"), {}).instance_id == "far"


def test_routing_uses_max_concurrent_and_weight(tracker):
    table = _table(
        _inst("far", "n-far"),
        _inst("near", "n-near", max_concurrent=64),
    )
    tracker.record("n-near", 2.0)
    tracker.record("n-far", 62.0)
    entry = table.get("m")
    # 64 slots: 10 in flight cost 200 * 10 / 64 = 31 ms < 60 ms
    assert table.pick(entry, {"near": 10}).instance_id == "near"
    # weight 25 makes every session count four times
    light = _table(_inst("far", "n-far"), _inst("near", "n-near", weight=25))
    assert light.pick(light.get("m"), {"near": 1}).instance_id == "far"


def test_near_equal_replicas_share_load_within_tolerance(tracker, monkeypatch):
    monkeypatch.setattr(settings, "SERVING_LATENCY_TOLERANCE_MS", 5.0)
    random.seed(7)
    table = _table(_inst("a", "n-a"), _inst("b", "n-b"), _inst("c", "n-c"))
    tracker.record("n-a", 10.0)
    tracker.record("n-b", 12.0)  # within 5 ms of a
    tracker.record("n-c", 40.0)
    entry = table.get("m")
    counts = {"a": 0, "b": 0, "c": 0}
    for _ in range(400):
        counts[table.pick(entry, {}).instance_id] += 1
    assert counts["c"] == 0
    assert counts["a"] > 120 and counts["b"] > 120
    # in-flight load decides inside the band
    assert table.pick(entry, {"a": 1}).instance_id == "b"


def test_unmeasured_node_counts_as_the_farthest(tracker):
    table = _table(_inst("new", "n-new"), _inst("near", "n-near"), _inst("far", "n-far"))
    tracker.record("n-near", 3.0)
    tracker.record("n-far", 50.0)
    entry = table.get("m")
    assert table.pick(entry, {}).instance_id == "near"
    # with the near one busy, the unmeasured node ties with the far one (both 50 ms)
    assert table.pick(entry, {"near": 8}).instance_id in ("new", "far")


def test_no_rtt_data_keeps_least_sessions(tracker):
    table = _table(_inst("a", "n-a", active_sessions=3), _inst("b", "n-b"))
    assert table.pick(table.get("m"), {}).instance_id == "b"


def test_latency_routing_off_and_failover_exclusion(tracker, monkeypatch):
    table = _table(_inst("far", "n-far"), _inst("near", "n-near", active_sessions=1))
    tracker.record("n-near", 1.0)
    tracker.record("n-far", 90.0)
    entry = table.get("m")
    assert table.pick(entry, {}).instance_id == "near"
    assert table.pick(entry, {}, exclude=["near"]).instance_id == "far"  # failover
    table.eject("near", 30)
    assert table.pick(entry, {}).instance_id == "far"  # ejected stays out
    table._ejected.clear()
    monkeypatch.setattr(settings, "SERVING_LATENCY_ROUTING", False)
    assert table.pick(entry, {}).instance_id == "far"  # load only: far has no session


def test_round_robin_and_weighted_ignore_latency(tracker):
    table = RoutingTable()
    table.load_from_dict(
        {
            "models": {
                "m": {
                    "type": "chat",
                    "traffic": {"strategy": "round_robin"},
                    "instances": [_inst("far", "n-far"), _inst("near", "n-near")],
                }
            }
        }
    )
    tracker.record("n-near", 1.0)
    tracker.record("n-far", 90.0)
    entry = table.get("m")
    assert [table.pick(entry, {}).instance_id for _ in range(4)] == ["far", "near"] * 2


# ---------------------------------------------------------------------------
# Server-Timing and the timing record
# ---------------------------------------------------------------------------


def test_parse_proxy_timing():
    parsed = timing.parse_proxy_timing('queue;dur=0.05, engine;dur=31.5, node;dur="32.0", x;y=1')
    assert parsed == {"queue": 0.05, "engine": 31.5, "node": 32.0}
    assert timing.parse_proxy_timing(None) == {}
    assert timing.parse_proxy_timing("engine;dur=abc, bad") == {}


def test_apply_proxy_timing_derives_network_time():
    t = {"upstream_ms": 50.0}
    timing.apply_proxy_timing(t, {"queue": 1.0, "engine": 30.0, "node": 31.0})
    assert t == {"upstream_ms": 50.0, "engine_ms": 30.0, "node_queue_ms": 1.0, "net_ms": 19.0}


def test_server_timing_generic_detailed_off(monkeypatch):
    phases = {
        "route_ms": 0.12,
        "queue_ms": 0.0,
        "connect_ms": 0.0,
        "rtt_ms": 18.4,
        "net_ms": 19.0,
        "engine_ms": 30.0,
        "upstream_ms": 50.0,
        "ttft_ms": 51.0,
        "total_ms": 53.2,
    }
    monkeypatch.setattr(settings, "SERVING_SERVER_TIMING", "generic")
    value = timing.server_timing(phases, include_total=True, region="eu-west-9", node_name="gpu-7")
    assert value == (
        "route;dur=0.1, queue;dur=0.0, connect;dur=0.0, rtt;dur=18.4, net;dur=19.0, "
        "engine;dur=30.0, upstream;dur=50.0, total;dur=53.2"
    )
    assert "gpu-7" not in value and "eu-west-9" not in value
    streaming = timing.server_timing(phases, include_total=False)
    assert "total" not in streaming
    monkeypatch.setattr(settings, "SERVING_SERVER_TIMING", "detailed")
    detailed = timing.server_timing(phases, include_total=True, region="eu", node_name='g"7')
    assert detailed.endswith('edge;desc="eu", node;desc="g7"')
    monkeypatch.setattr(settings, "SERVING_SERVER_TIMING", "off")
    assert timing.server_timing(phases, include_total=True) is None
    monkeypatch.setattr(settings, "SERVING_SERVER_TIMING", "bogus")
    assert timing.mode() == "generic"


def _ctx(target=None):
    from app.services.serving.context import ServingContext

    request = SimpleNamespace(state=SimpleNamespace(), headers={}, client=None)
    ctx = ServingContext(request=request, fmt="openai", endpoint="chat/completions", api_key=None)
    ctx.target = target
    return ctx


def test_context_headers_carry_server_timing_without_hostnames(monkeypatch):
    from app.services.serving.routing import Instance, ModelEntry, RouteTarget, get_routing_table

    monkeypatch.setattr(settings, "SERVING_SERVER_TIMING", "generic")
    monkeypatch.setattr(settings, "SERVING_EXPOSE_ROUTING_HEADERS", False)
    monkeypatch.setattr(settings, "API_LAYER_REGION", "eu-west")
    inst = Instance("i-1", "node-1", "https://10.0.0.7:9443", "m")
    target = RouteTarget(ModelEntry("m", None, "chat", None, instances=[inst]), inst, "m")
    table = get_routing_table()
    saved = table.nodes
    table.nodes = {"node-1": {"name": "gpu-box-7"}}
    try:
        ctx = _ctx(target)
        ctx.timing.update({"route_ms": 0.2, "connect_ms": 0.0, "upstream_ms": 42.0})
        headers = ctx.response_headers()
        st = headers["Server-Timing"]
        assert st.startswith("route;dur=0.2, connect;dur=0.0, upstream;dur=42.0, total;dur=")
        flat = " ".join(f"{k}: {v}" for k, v in headers.items())
        for secret in ("10.0.0.7", "gpu-box-7", "node-1", "eu-west"):
            assert secret not in flat
        streamed = ctx.response_headers(streaming=True)["Server-Timing"]
        assert "total" not in streamed
        monkeypatch.setattr(settings, "SERVING_SERVER_TIMING", "detailed")
        st = ctx.response_headers()["Server-Timing"]
        assert 'edge;desc="eu-west"' in st and 'node;desc="gpu-box-7"' in st
        assert "10.0.0.7" not in st
        monkeypatch.setattr(settings, "SERVING_SERVER_TIMING", "off")
        assert "Server-Timing" not in ctx.response_headers()
    finally:
        table.nodes = saved


def test_timing_record_and_usage_metadata(monkeypatch):
    from app.services.metering.service import build_usage_payload
    from app.services.serving.usage import Usage

    monkeypatch.setattr(settings, "API_LAYER_ID", "edge-1")
    monkeypatch.setattr(settings, "API_LAYER_REGION", "us-east")
    ctx = _ctx()
    ctx.model = "m"
    ctx.timing.update({"route_ms": 0.1234, "upstream_ms": 40.0, "net_ms": 9.0})
    ctx.ttft_ms = 45
    meta = build_usage_payload(ctx, Usage(), "success", 200)["metadata"]
    assert meta["api_layer"] == {"id": "edge-1", "region": "us-east"}
    rec = meta["timing"]
    assert rec["route_ms"] == 0.123 and rec["upstream_ms"] == 40.0 and rec["ttft_ms"] == 45.0
    assert rec["total_ms"] >= 0
    assert "rtt_ms" not in rec  # unknown phases are left out
    # no timing at all (rejected before routing): no metadata added
    bare = _ctx()
    bare.timing.clear()
    payload = build_usage_payload(bare, Usage(), "error", 401)
    assert "api_layer" in (payload["metadata"] or {})  # total is always known


# ---------------------------------------------------------------------------
# Traffic-aware placement
# ---------------------------------------------------------------------------


def _node(name: str, **kw):
    from app.services.scheduler.core import DeviceView, NodeView

    nid = uuid.uuid5(uuid.NAMESPACE_DNS, name)
    dev = DeviceView(
        id=uuid.uuid5(nid, "gpu0"),
        node_id=nid,
        index=0,
        vendor="nvidia",
        vram_total_bytes=80 << 30,
    )
    return NodeView(id=nid, name=name, devices=[dev], **kw)


def _ctx_latency(rtt, shares, source="deployment", hub=None):
    from app.services.scheduler.core import LatencyContext

    return LatencyContext(
        rtt_ms=rtt,
        shares=shares,
        source=source,
        hub_rtt_ms=hub or {},
        weight=30.0,
        tolerance_ms=5.0,
        scale_ms=50.0,
        link_weight=10.0,
    )


def test_placement_prefers_the_node_near_the_traffic():
    from app.services.scheduler.core import PlacementRequest, place

    # "a-far" sorts first and would win every tie on name.
    far, near = _node("a-far"), _node("b-near")
    req = PlacementRequest(need_bytes_per_device=10 << 30, replicas=1)
    baseline = place([far, near], req)
    assert baseline.assignments[0].node_id == far.id  # no data: unchanged behaviour
    assert baseline.assignments[0].reasons == []
    req.latency = _ctx_latency({"eu": {far.id: 90.0, near.id: 4.0}}, {"eu": 1.0})
    plan = place([far, near], req)
    assert plan.assignments[0].node_id == near.id
    reason = plan.assignments[0].reasons[0]
    assert reason.startswith("network: 4.0 ms expected round trip") and "eu 100%" in reason


def test_placement_tolerance_keeps_vram_rules():
    from app.services.scheduler.core import PlacementRequest, place

    a, b = _node("a"), _node("b")
    b.devices[0].reserved_bytes = 40 << 30  # b is fuller: spread picks a
    req = PlacementRequest(need_bytes_per_device=10 << 30, replicas=1)
    req.latency = _ctx_latency({"eu": {a.id: 6.0, b.id: 4.0}}, {"eu": 1.0})
    plan = place([a, b], req)
    assert plan.assignments[0].node_id == a.id  # 2 ms apart: within tolerance
    assert "within 5.0 ms" in plan.assignments[0].reasons[0]


def test_second_replica_covers_the_other_region():
    from app.services.scheduler.core import PlacementRequest, place

    eu1, eu2, us = _node("eu-1"), _node("eu-2"), _node("us-1")
    rtt = {
        "eu": {eu1.id: 3.0, eu2.id: 4.0, us.id: 85.0},
        "us": {eu1.id: 88.0, eu2.id: 90.0, us.id: 3.0},
    }
    req = PlacementRequest(need_bytes_per_device=10 << 30, replicas=2)
    baseline = [a.node_id for a in place([eu1, eu2, us], req).assignments]
    assert baseline == [eu1.id, eu2.id]  # spread, ties on name
    req.latency = _ctx_latency(rtt, {"eu": 0.6, "us": 0.4})
    plan = place([eu1, eu2, us], req)
    nodes = [a.node_id for a in plan.assignments]
    assert nodes[0] == eu1.id  # most traffic is in eu
    assert nodes[1] == us.id  # a second eu replica would not help the us traffic
    assert "us 40%" in plan.assignments[1].reasons[0]


def test_existing_replica_counts_as_served():
    from app.services.scheduler.core import PlacementRequest, place

    eu, us = _node("eu"), _node("us")
    rtt = {"eu": {eu.id: 3.0, us.id: 80.0}, "us": {eu.id: 80.0, us.id: 3.0}}
    req = PlacementRequest(need_bytes_per_device=10 << 30, replicas=1, existing_per_node={eu.id: 1})
    req.latency = _ctx_latency(rtt, {"eu": 0.5, "us": 0.5})
    assert place([eu, us], req).assignments[0].node_id == us.id


def test_unmeasured_node_is_placed_as_the_farthest():
    from app.services.scheduler.core import PlacementRequest, place

    measured, unknown = _node("b-measured"), _node("a-unknown")
    req = PlacementRequest(need_bytes_per_device=10 << 30)
    req.latency = _ctx_latency({"eu": {measured.id: 2.0}}, {"eu": 1.0})
    plan = place([unknown, measured], req)
    # The only measurement is also the farthest: no gap, the name decides.
    assert plan.assignments[0].node_id == unknown.id
    assert "no RTT measured" in plan.assignments[0].reasons[0]
    req.latency = _ctx_latency({"eu": {measured.id: 2.0, _node("z").id: 70.0}}, {"eu": 1.0})
    plan = place([unknown, measured], req)
    assert plan.assignments[0].node_id == measured.id  # unknown counts as 70 ms


def test_cross_node_pipeline_prefers_low_link_cost():
    from app.services.scheduler.core import PlacementRequest, link_cost, place

    cluster = uuid.uuid4()
    head = _node("a-head", cluster_id=cluster, labels={"zone": "z1"})
    other_zone = _node("b-other", cluster_id=cluster, labels={"zone": "z2"})
    same_zone = _node("c-same", cluster_id=cluster, labels={"zone": "z1"})
    for n in (head, other_zone, same_zone):
        n.devices[0].vram_total_bytes = 24 << 30
    req = PlacementRequest(
        need_bytes_per_device=20 << 30,
        replicas=1,
        gpu_count=2,
        tp_size=1,
        pp_size=2,
        allow_cross_node_pp=True,
    )
    plan = place([head, other_zone, same_zone], req)
    assert plan.feasible
    shards = plan.assignments[0].shards
    assert [s.node_id for s in shards] == [head.id, same_zone.id]
    assert any("link cost 0" in r and "same zone z1" in r for r in plan.assignments[0].reasons)
    cost, why = link_cost(head, other_zone, None)
    assert cost == 2.0 and "zone z1 vs z2" in why
    lat = _ctx_latency({}, {}, hub={head.id: 2.0, other_zone.id: 102.0})
    cost, why = link_cost(head, other_zone, lat)
    assert cost == pytest.approx(4.0) and "100 ms apart" in why


def test_build_context_shares_fall_back():
    from app.services.latency.placement import build_context

    n1, n2 = uuid.uuid4(), uuid.uuid4()
    live = {
        "api_layers": {"l1": {"region": "eu"}, "l2": {"region": "eu"}, "l3": {"region": "us"}},
        "api": {
            "l1": {str(n1): {"rtt_ms": 9.0}, str(n2): {"rtt_ms": 70.0}},
            "l2": {str(n1): {"rtt_ms": 4.0}},  # best layer of the region wins
            "l3": {str(n2): {"rtt_ms": 2.0}, "not-a-uuid": {"rtt_ms": 1.0}},
        },
        "hub": {str(n1): {"rtt_ms": 30.0}},
    }
    ctx = build_context(live, {"eu": 30, "us": 10, "ap": 5}, {})
    assert ctx.rtt_ms["eu"] == {n1: 4.0, n2: 70.0} and ctx.rtt_ms["us"] == {n2: 2.0}
    assert ctx.source == "deployment" and ctx.shares == {"eu": 0.75, "us": 0.25}
    assert ctx.hub_rtt_ms == {n1: 30.0} and ctx.active
    fleet = build_context(live, {}, {"us": 5})
    assert fleet.source == "fleet" and fleet.shares == {"us": 1.0}
    uniform = build_context(live, {}, {})
    assert uniform.source == "uniform" and uniform.shares == {"eu": 0.5, "us": 0.5}
    empty = build_context({"api": {}, "hub": {}}, {}, {})
    assert not empty.active


def test_traffic_by_region_window():
    from app.services.latency.store import hour_bucket, traffic_by_region

    now = dt.datetime(2026, 1, 2, 10, tzinfo=dt.timezone.utc)
    rows = {
        f"eu|{hour_bucket(now)}": "5",
        f"eu|{hour_bucket(now - dt.timedelta(hours=1))}": "3",
        f"us|{hour_bucket(now)}": "2",
        f"us|{hour_bucket(now - dt.timedelta(hours=30))}": "50",  # outside the window
        "garbage": "1",
    }
    buckets = {hour_bucket(now - dt.timedelta(hours=h)) for h in range(24)}
    assert traffic_by_region(rows, buckets) == {"eu": 8, "us": 2}


def test_snapshot_rows_from_live_view():
    from app.services.latency.service import snapshot_rows

    node = uuid.uuid4()
    live = {
        "api_layers": {"edge-1": {"region": "eu"}},
        "api": {"edge-1": {str(node): {"rtt_ms": 12.5, "jitter_ms": 1.0, "mode": "tunnel"}}},
        "hub": {str(node): {"rtt_ms": 30.0, "p95_ms": 33.0}, "dev": {"rtt_ms": 1.0}},
    }
    now = dt.datetime(2026, 1, 2, 10, 4, 33, tzinfo=dt.timezone.utc)
    rows = snapshot_rows(live, now)
    assert len(rows) == 2  # the non-uuid dev node is skipped
    hub = next(r for r in rows if r["source"] == "hub")
    api = next(r for r in rows if r["source"] == "api_layer")
    assert hub["api_layer_id"] == "" and hub["mode"] is None and hub["p95_ms"] == 33.0
    assert api["region"] == "eu" and api["mode"] == "tunnel" and api["rtt_ms"] == 12.5
    assert api["ts"] == dt.datetime(2026, 1, 2, 10, 4, tzinfo=dt.timezone.utc)


# ---------------------------------------------------------------------------
# Agent hub: ping / pong round trips
# ---------------------------------------------------------------------------


def _node_cert_der(cn: str) -> bytes:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(hours=1))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.DER)


def test_node_id_from_certificate():
    from app.services.agent_hub.latency import node_id_from_der

    node = uuid.uuid4()
    assert node_id_from_der(_node_cert_der(str(node))) == str(node)
    assert node_id_from_der(_node_cert_der("not-a-node")) is None
    assert node_id_from_der(None) is None and node_id_from_der(b"junk") is None


def test_hub_pong_records_rtt(monkeypatch):
    from app.services.agent_hub import latency as hub_latency
    from app.services.agent_hub.ws_protocol import MTLSWebSocketProtocol

    published: Dict[str, Optional[dict]] = {}

    async def fake_publish(node_id, snapshot):
        published[node_id] = snapshot

    async def fake_drop(node_id):
        published[node_id] = None

    monkeypatch.setattr("app.services.latency.store.publish_hub", fake_publish)
    monkeypatch.setattr("app.services.latency.store.drop_hub", fake_drop)

    async def run() -> None:
        loop = asyncio.get_running_loop()
        proto = object.__new__(MTLSWebSocketProtocol)
        proto.loop = loop
        proto.pong_timer = None
        proto.pending_ping_payload = b"\x00\x00\x00\x07"
        proto.ping_sent_at = loop.time() - 0.025
        proto.last_ping_rtt = 0.0
        proto._gpudeck_node = "11111111-1111-4111-8111-111111111111"
        proto.handle_pong(SimpleNamespace(data=b"\x00\x00\x00\x07"))
        proto.handle_pong(SimpleNamespace(data=b"\x00\x00\x00\x07"))  # stale: ignored
        await asyncio.sleep(0)
        stats = hub_latency.stats(proto._gpudeck_node)
        assert stats is not None and stats.samples == 1
        assert 20.0 <= stats.ewma_ms < 200.0
        assert published[proto._gpudeck_node]["rtt_ms"] == pytest.approx(stats.ewma_ms, abs=1e-3)
        hub_latency.forget(proto._gpudeck_node)
        await asyncio.sleep(0)
        assert published[proto._gpudeck_node] is None
        assert hub_latency.stats(proto._gpudeck_node) is None

    asyncio.run(run())
