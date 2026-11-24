"""
Unit tests: usage extraction per format, the metering payload, busy-share
GPU seconds (uptime-based billing), price resolution and token costs,
GPU-minute allocation incl. the unallocated bucket, rollup merging, billing
line pricing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from app.services.billing.pricing import Plan, PlanResolver, gpu_cost, token_costs
from app.services.billing.service import month_bounds, price_lines
from app.services.metering.rollup import (
    allocate,
    device_shares,
    merge_bucket,
    minute_coverage,
    ssh_claims,
)
from app.services.metering.writer import busy_gpu_seconds
from app.services.serving.usage import (
    UsageCollector,
    audio_seconds_from_bytes,
    extract_anthropic_usage,
    extract_ollama_usage,
    extract_openai_usage,
)

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)


# ----------------------------------------------------------------- extraction


def test_openai_usage_variants():
    u = extract_openai_usage(
        {
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "prompt_tokens_details": {"cached_tokens": 6},
                "completion_tokens_details": {"reasoning_tokens": 2},
            }
        }
    )
    assert (u.input_tokens, u.output_tokens, u.cached_tokens, u.reasoning_tokens) == (
        10,
        4,
        6,
        2,
    )
    llama = extract_openai_usage(
        {
            "usage": {"prompt_tokens": 10, "completion_tokens": 1},
            "timings": {"cache_n": 7},
        }
    )
    assert llama.cached_tokens == 7
    resp = extract_openai_usage(
        {
            "usage": {
                "input_tokens": 5,
                "output_tokens": 3,
                "input_tokens_details": {"cached_tokens": 1},
                "output_tokens_details": {"reasoning_tokens": 2},
            }
        },
        "responses",
    )
    assert (resp.input_tokens, resp.cached_tokens, resp.reasoning_tokens) == (5, 1, 2)
    rerank = extract_openai_usage({"meta": {"billed_units": {"search_units": 3}}}, "rerank")
    assert rerank.input_tokens == 3


def test_anthropic_and_ollama_usage():
    u = extract_anthropic_usage(
        {
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 5,
                "cache_read_input_tokens": 20,
                "output_tokens": 7,
            }
        }
    )
    assert u.input_tokens == 35 and u.cached_tokens == 20 and u.output_tokens == 7
    assert u.extra["cache_creation_input_tokens"] == 5
    o = extract_ollama_usage(
        {"prompt_eval_count": 9, "eval_count": 3, "total_duration": 5_000_000_000}
    )
    assert (o.input_tokens, o.output_tokens, o.extra["latency_ms"]) == (9, 3, 5000)


def test_stream_collector_estimates_without_usage_frame():
    c = UsageCollector("openai", "chat/completions")
    for _ in range(4):
        c.feed_openai_chunk({"choices": [{"delta": {"content": "x"}}]})
    u = c.finalize()
    assert u.output_tokens == 4 and u.estimated
    c = UsageCollector("anthropic", "messages")
    c.feed_anthropic_event({"type": "message_start", "message": {"usage": {"input_tokens": 8}}})
    c.feed_anthropic_event({"type": "message_delta", "usage": {"output_tokens": 5}})
    u = c.finalize()
    assert (u.input_tokens, u.output_tokens, u.estimated) == (8, 5, False)


def test_audio_seconds():
    assert audio_seconds_from_bytes(b"\x00" * 16000, "mp3") == pytest.approx(1.0)
    assert audio_seconds_from_bytes(b"", "wav") == 0.0


# ------------------------------------------------------------ busy share / cost


def test_busy_gpu_seconds_divides_by_concurrency():
    assert busy_gpu_seconds(2000, 2, None, 0) == Decimal("4.000")
    assert busy_gpu_seconds(2000, 2, None, 3) == Decimal("1.000")  # 4 concurrent requests
    assert busy_gpu_seconds(1000, 1, 50, 0) == Decimal("0.500")  # half a GPU slice
    assert busy_gpu_seconds(None, 1, None, 0) == Decimal(0)


def _plan(pid="p1", gpu="0.10", inp="0.50", out="1.50", cached="0.05", default=False):
    return {
        "id": pid,
        "name": f"plan-{pid}",
        "currency": "USD",
        "usd_per_gpu_minute": Decimal(gpu),
        "usd_per_1k_input_tokens": Decimal(inp),
        "usd_per_1k_output_tokens": Decimal(out),
        "usd_per_1k_cached_tokens": Decimal(cached),
        "is_default": default,
    }


def test_token_costs_do_not_double_charge_cached():
    plan = Plan.from_row(_plan())
    c = token_costs(plan, input_tokens=2000, output_tokens=1000, cached_tokens=1000)
    assert c["input"] == Decimal("0.5")  # 1000 uncached x 0.50/1k
    assert c["cached"] == Decimal("0.05")
    assert c["output"] == Decimal("1.5")
    assert gpu_cost(plan, Decimal(30)) == Decimal("3.0")
    assert token_costs(None, 1, 1, 1)["input"] == 0


def test_plan_resolution_node_cluster_default():
    resolver = PlanResolver(
        {
            "plans": [_plan("node"), _plan("cluster"), _plan("def", default=True)],
            "nodes": [
                {"id": "n1", "cluster_id": "c1", "price_plan_id": "node"},
                {"id": "n2", "cluster_id": "c1", "price_plan_id": None},
                {"id": "n3", "cluster_id": "c2", "price_plan_id": "deleted-plan"},
            ],
            "clusters": [
                {"id": "c1", "price_plan_id": "cluster"},
                {"id": "c2", "price_plan_id": None},
            ],
        }
    )
    assert resolver.resolve("n1").id == "node"
    assert resolver.resolve("n2").id == "cluster"
    assert resolver.resolve("n3").id == "def"  # deleted plan ignored -> default
    assert resolver.resolve(None, "c1").id == "cluster"
    assert resolver.cluster_of("n2") == "c1"
    assert PlanResolver({"plans": [_plan("x")]}).resolve("n1") is None  # on-prem: no cost


# --------------------------------------------------------------- allocation


def test_minute_coverage_prorates_partial_minutes():
    start = T0 + timedelta(seconds=30)
    stop = T0 + timedelta(minutes=2, seconds=15)
    cov = minute_coverage(start, stop, T0, T0 + timedelta(hours=1))
    assert cov == [
        (T0, 0.5),
        (T0 + timedelta(minutes=1), 1.0),
        (T0 + timedelta(minutes=2), 0.25),
    ]


def test_allocate_pro_rata_by_tokens_and_unallocated():
    shapes = {"d1": {"gpu_count": 2, "slice_compute_pct": None, "model_id": "m1"}}
    up = [
        {"minute": T0, "instance_id": "i1", "node_id": "n1", "deployment_id": "d1"},
        {
            "minute": T0 + timedelta(minutes=1),
            "instance_id": "i1",
            "node_id": "n1",
            "deployment_id": "d1",
        },
        {
            "minute": T0 + timedelta(minutes=2),
            "instance_id": "i1",
            "node_id": "n1",
            "deployment_id": "d1",
            "fraction": 0.5,
        },
    ]
    tokens = [
        {
            "minute": T0,
            "instance_id": "i1",
            "api_key_id": "k1",
            "tokens": 300,
            "requests": 1,
        },
        {
            "minute": T0,
            "instance_id": "i1",
            "api_key_id": "k2",
            "tokens": 100,
            "requests": 3,
        },
        # minute 1: only image requests (no tokens) -> by request count
        {
            "minute": T0 + timedelta(minutes=1),
            "instance_id": "i1",
            "api_key_id": "k2",
            "tokens": 0,
            "requests": 2,
        },
    ]
    out = allocate(up, tokens, shapes)
    assert out[("k1", "m1", "n1", "d1")] == Decimal("1.5")  # 2 GPU-min x 3/4
    assert out[("k2", "m1", "n1", "d1")] == Decimal("0.5") + Decimal("2")
    assert out[(None, "m1", "n1", "d1")] == Decimal("1.0")  # idle half minute x 2 GPUs
    assert sum(out.values()) == Decimal("5.0")  # 2.5 loaded minutes x 2 GPUs


def test_allocate_slice_fraction():
    shapes = {"d1": {"gpu_count": 1, "slice_compute_pct": 25, "model_id": "m1"}}
    up = [{"minute": T0, "instance_id": "i1", "node_id": "n1", "deployment_id": "d1"}]
    assert allocate(up, [], shapes) == {(None, "m1", "n1", "d1"): Decimal("0.25")}


GIB = 1024**3


def _up(instance: str, dep: str, minute=T0, fraction=1.0):
    return {
        "minute": minute,
        "instance_id": instance,
        "node_id": "n1",
        "deployment_id": dep,
        "fraction": fraction,
    }


def test_shared_gpu_never_bills_more_than_one_gpu_minute_per_minute():
    """FULL-TEST #19: vLLM, an embedding server, two llama.cpp children and an observed
    engine on one H200, none sliced: each claimed a whole GPU (5 GPU-min per minute)."""
    totals = {"h200": 141 * GIB}
    info = {
        "vllm": {"device_ids": ["h200"], "resources": {"vram_bytes_per_device": 54 * GIB}},
        "emb": {"device_ids": ["h200"], "resources": {"vram_bytes_used": 3 * GIB}},
        "llama-a": {"device_ids": ["h200"], "resources": {"vram_bytes_per_device": 6 * GIB}},
        "llama-b": {"device_ids": ["h200"], "resources": {"vram_bytes_per_device": 6 * GIB}},
        "observed": {"device_ids": ["h200"], "resources": {}, "origin": "external"},
    }
    shapes = {d: {"gpu_count": 1, "model_id": f"m-{d}"} for d in ("d1", "d2", "d3", "d4", "d5")}
    up = []
    for minute in range(3):
        ts = T0 + timedelta(minutes=minute)
        for inst, dep in zip(info, ("d1", "d2", "d3", "d4", "d5")):
            up.append(_up(inst, dep, ts))
    rows = device_shares(up, info, shapes, totals)
    per_minute = {}
    for r in rows:
        per_minute[r["minute"]] = per_minute.get(r["minute"], 0) + r["gpu_minutes"]
    assert all(abs(v - 1) < Decimal("1e-9") for v in per_minute.values())
    first = {r["instance_id"]: r["gpu_minutes"] for r in rows if r["minute"] == T0}
    assert first["observed"] > first["vllm"] > first["llama-a"] > first["emb"]  # by VRAM
    assert first["llama-a"] == first["llama-b"]
    out = allocate(rows, [], shapes)  # idle, loaded: each keeps its share (unallocated)
    assert abs(sum(out.values()) - 3) < Decimal("1e-9")
    assert out[(None, "m-d1", "n1", "d1")] == first["vllm"] * 3


def test_slices_bill_their_fraction_and_ssh_instances_take_capacity():
    totals = {"g0": 80 * GIB}
    sliced = {
        "s1": {
            "device_ids": ["g0"],
            "resources": {"slice": {"vram_bytes": 4 * GIB, "compute_pct": 25}},
        },
        "s2": {
            "device_ids": ["g0"],
            "resources": {"slice": {"vram_bytes": 4 * GIB, "compute_pct": 25}},
        },
    }
    rows = device_shares([_up("s1", "d1"), _up("s2", "d2")], sliced, {}, totals)
    assert [r["gpu_minutes"] for r in rows] == [Decimal("0.25"), Decimal("0.25")]

    # An unsliced instance next to a 20 % SSH GPU instance gets the remaining 0.8.
    ssh = [
        {
            "device_ids": ["g0"],
            "state": "running",
            "running_at": T0 - timedelta(minutes=5),
            "stopped_at": None,
            "slice_vram_bytes": 8 * GIB,
            "slice_compute_pct": 20,
        }
    ]
    reserved = ssh_claims(ssh, T0, T0 + timedelta(minutes=2))
    assert reserved[(T0, "g0")] == Decimal("0.2")
    whole = {"w": {"device_ids": ["g0"], "resources": {}}}
    rows = device_shares([_up("w", "d3")], whole, {}, totals, reserved)
    assert rows[0]["gpu_minutes"] == Decimal("0.8")

    # Partial minutes that do not overlap still fit; no devices -> legacy gpu_count x slice.
    half = device_shares(
        [_up("s1", "d1", fraction=0.5), _up("w", "d3", fraction=0.5)],
        {**sliced, **whole},
        {},
        totals,
    )
    assert [r["gpu_minutes"] for r in half] == [Decimal("0.125"), Decimal("0.5")]
    legacy = device_shares([_up("cpu", "d4")], {"cpu": {"device_ids": []}}, {}, totals)
    assert "gpu_minutes" not in legacy[0]
    shapes = {"d4": {"gpu_count": 2, "model_id": "m"}}
    assert allocate(legacy, [], shapes) == {(None, "m", "n1", "d4"): Decimal("2.0")}


def test_multi_gpu_instance_bills_each_device():
    info = {"tp": {"device_ids": ["g0", "g1"], "resources": {"vram_bytes_per_device": GIB}}}
    rows = device_shares([_up("tp", "d1")], info, {}, {"g0": 80 * GIB, "g1": 80 * GIB})
    assert rows[0]["gpu_minutes"] == Decimal(2)


def test_merge_bucket_prices_gpu_minutes_by_node_plan():
    resolver = PlanResolver(
        {
            "plans": [_plan("p", gpu="0.20")],
            "nodes": [{"id": "n1", "cluster_id": None, "price_plan_id": "p"}],
        }
    )
    events = [
        {
            "api_key_id": "k1",
            "model_id": "m1",
            "node_id": "n1",
            "deployment_id": "d1",
            "request_count": 3,
            "success_count": 2,
            "error_count": 1,
            "input_tokens": 100,
            "output_tokens": 50,
            "cached_tokens": 10,
            "reasoning_tokens": 0,
            "media_units": 0,
            "latency_ms_sum": 900,
            "ttft_ms_sum": 300,
            "token_cost_usd": Decimal("0.01"),
        }
    ]
    gpu = {
        ("k1", "m1", "n1", "d1"): Decimal("2"),
        (None, "m1", "n1", "d1"): Decimal("1"),
    }
    rows = {r["api_key_id"]: r for r in merge_bucket(events, gpu, resolver)}
    assert rows["k1"]["gpu_seconds"] == Decimal("120.000")
    assert rows["k1"]["cost_usd"] == Decimal("0.410000")  # 0.01 tokens + 2 x 0.20
    assert rows[None]["request_count"] == 0 and rows[None]["cost_usd"] == Decimal("0.200000")


# ------------------------------------------------------------------ billing


def test_price_lines_breakdown_and_unallocated_label():
    resolver = PlanResolver(
        {
            "plans": [_plan("p")],
            "nodes": [
                {"id": "n1", "cluster_id": None, "price_plan_id": "p"},
                {"id": "n2", "cluster_id": None, "price_plan_id": None},
            ],
        }
    )
    lines = [
        {
            "group": ["k1"],
            "labels": ["team-a"],
            "node_id": "n1",
            "raw": {
                "request_count": 2,
                "input_tokens": 2000,
                "output_tokens": 1000,
                "cached_tokens": 1000,
                "gpu_seconds": 600,
                "latency_ms_avg": 100,
            },
        },
        {
            "group": ["k1"],
            "labels": ["team-a"],
            "node_id": "n2",
            "raw": {
                "request_count": 2,
                "input_tokens": 1000,
                "gpu_seconds": 60,
                "latency_ms_avg": 300,
            },
        },
        {
            "group": [None],
            "labels": [None],
            "node_id": "n1",
            "raw": {"gpu_seconds": 120},
        },
    ]
    items, totals = price_lines(lines, ["api_key"], resolver)
    by_key = {i["group"]["api_key"]: i for i in items}
    k1 = by_key["k1"]
    assert k1["cost_breakdown"] == {
        "gpu_time_usd": 1.0,  # 10 GPU-min x 0.10 (n2 has no plan)
        "input_tokens_usd": 0.5,
        "output_tokens_usd": 1.5,
        "cached_tokens_usd": 0.05,
    }
    assert k1["totals"]["cost_usd"] == 3.05 and k1["totals"]["latency_ms_avg"] == 200.0
    assert k1["price_plan_id"] is None  # mixed plans across nodes
    assert by_key[""]["labels"]["api_key"] == "unallocated"
    assert by_key[""]["totals"]["cost_usd"] == pytest.approx(0.2)
    assert totals["cost_usd"] == pytest.approx(3.25) and totals["gpu_minutes"] == pytest.approx(13)


def test_month_bounds():
    start, end = month_bounds(datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc))
    assert start == datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert end == datetime(2027, 1, 1, tzinfo=timezone.utc)
