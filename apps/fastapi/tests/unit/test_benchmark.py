"""
Benchmark: statistics, SLO checks, recommendations and the load generator
against a fake streaming OpenAI server (httpx.MockTransport).
"""

import asyncio
import json

import httpx
import pytest
from app.services.benchmark import stats
from app.services.benchmark.loadgen import LoadGenerator, endpoint_for, synthetic_prompt
from app.services.benchmark.stats import LevelResult, RequestSample


def test_percentile_matches_linear_interpolation():
    data = [10, 20, 30, 40]
    assert stats.percentile(data, 50) == 25.0
    assert stats.percentile(data, 95) == 38.5
    assert stats.percentile([5], 99) == 5.0
    assert stats.percentile([], 50) is None


def test_request_sample_derived_metrics():
    s = RequestSample(True, e2e_ms=1100, ttft_ms=100, output_tokens=101)
    assert s.tpot_ms == 10.0 and s.generation_tps == 100.0
    assert RequestSample(True, e2e_ms=50, ttft_ms=50, output_tokens=1).tpot_ms is None


def _level(c, ttft, e2e, out=101, errors=0, wall=10.0):
    samples = [
        RequestSample(True, e2e, ttft, prompt_tokens=50, output_tokens=out) for _ in range(20)
    ]
    samples += [RequestSample(False, 5.0, error="HTTP 503") for _ in range(errors)]
    return LevelResult(concurrency=c, samples=samples, wall_seconds=wall)


def test_summarize_and_slo():
    slo = {"ttft_ms_p95": 200, "e2e_ms_p95": 3000, "min_generation_tps": 20}
    r = stats.summarize(_level(4, 120, 2000), slo)
    assert r["requests"] == 20 and r["errors"] == 0
    assert r["rps"] == 2.0 and r["generation_tps"] == 202.0 and r["prompt_tps"] == 100.0
    assert r["ttft_ms"]["p95"] == 120 and r["slo_met"] is True
    assert stats.summarize(_level(8, 300, 2000), slo)["slo_met"] is False
    errs = stats.summarize(_level(8, 100, 2000, errors=2), slo)
    assert errs["error_rate_pct"] == pytest.approx(9.091, abs=1e-3) and errs["slo_met"] is False
    assert (
        stats.summarize(_level(8, 100, 2000, errors=2), {"max_error_rate_pct": 10})["slo_met"]
        is True
    )
    assert stats.summarize(LevelResult(1, [], 1.0), {})["slo_met"] is False


def test_best_concurrency_and_recommend():
    slo = {"ttft_ms_p95": 200}
    results = [
        stats.summarize(_level(c, t, 1000), slo) for c, t in [(1, 50), (2, 80), (4, 150), (8, 400)]
    ]
    assert stats.best_concurrency(results) == 4
    rec = stats.recommend(
        results, current_replicas=2, ready_replicas=2, slo=slo, base_flags={"a": 1}
    )
    assert rec["concurrency"] == 4 and rec["per_instance_concurrency"] == 2
    assert rec["launch_flags"] == {"a": 1} and "highest concurrency" in rec["reason"]
    none = stats.recommend(
        [stats.summarize(_level(1, 900, 1000), slo)],
        current_replicas=1,
        ready_replicas=1,
        slo=slo,
    )
    assert none["concurrency"] is None and "not met" in none["reason"]


def test_recommend_picks_best_variant():
    slo = {"ttft_ms_p95": 200}
    a = [stats.summarize(_level(c, t, 1000), slo) for c, t in [(4, 100), (8, 300)]]
    b = [stats.summarize(_level(c, t, 1000), slo) for c, t in [(4, 90), (8, 150)]]
    for r in a:
        r["launch_flags"] = {"parallel": 4}
    for r in b:
        r["launch_flags"] = {"parallel": 8}
    rec = stats.recommend(a + b, current_replicas=1, ready_replicas=1, slo=slo)
    assert rec["launch_flags"] == {"parallel": 8} and rec["concurrency"] == 8


def test_default_variants_and_endpoints():
    assert len(stats.default_variants("llama_cpp", [1, 16])) == 3
    assert stats.default_variants("mlx", [1]) == []
    assert endpoint_for("thinking") == "/v1/chat/completions"
    assert endpoint_for("embedding") == "/v1/embeddings"
    with pytest.raises(ValueError):
        endpoint_for("image")
    assert len(synthetic_prompt(100).split()) >= 75


def fake_openai(ttft=0.01, per_token=0.002, tokens=8, fail_every=0):
    state = {"n": 0, "inflight": 0, "max_inflight": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        assert request.headers["authorization"] == "Bearer sk-test"
        body = json.loads(request.content)
        if fail_every and state["n"] % fail_every == 0:
            return httpx.Response(503, json={"error": {"code": "instance_busy", "message": "busy"}})
        if request.url.path == "/v1/embeddings":
            return httpx.Response(
                200,
                json={"data": [{"embedding": [0.1]}], "usage": {"prompt_tokens": 7}},
            )
        assert body["stream"] is True and body["stream_options"] == {"include_usage": True}

        async def stream():
            state["inflight"] += 1
            state["max_inflight"] = max(state["max_inflight"], state["inflight"])
            try:
                await asyncio.sleep(ttft)
                yield b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
                for _ in range(tokens):
                    yield b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
                    await asyncio.sleep(per_token)
                usage = {
                    "prompt_tokens": 40,
                    "completion_tokens": tokens,
                    "total_tokens": 40 + tokens,
                }
                state["inflight"] -= 1
                yield f'data: {{"choices":[],"usage":{json.dumps(usage)}}}\n\n'.encode()
                yield b"data: [DONE]\n\n"
            finally:
                pass

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream())

    return httpx.MockTransport(handler), state


async def test_loadgen_streaming_level():
    transport, state = fake_openai()
    gen = LoadGenerator("http://serving.test", "sk-test", "qwen3", "chat", transport=transport)
    level = await gen.run_level(
        4, {"input_tokens": 64, "output_tokens": 8, "requests_per_level": 12}
    )
    assert len(level.samples) == 12 and all(s.ok for s in level.samples)
    assert state["max_inflight"] == 4
    s = level.samples[0]
    assert s.output_tokens == 8 and s.prompt_tokens == 40
    assert 5 <= s.ttft_ms < s.e2e_ms
    summary = stats.summarize(level, {"ttft_ms_p95": 5000})
    assert summary["slo_met"] and summary["generation_tps"] > 0 and summary["tpot_ms"]["p50"] > 0


async def test_loadgen_errors_and_stop():
    transport, _ = fake_openai(fail_every=3)
    gen = LoadGenerator("http://serving.test", "sk-test", "qwen3", "chat", transport=transport)
    level = await gen.run_level(2, {"requests_per_level": 9, "output_tokens": 4})
    errors = [s for s in level.samples if not s.ok]
    assert len(errors) == 3 and errors[0].error == "503 instance_busy"

    calls = {"n": 0}

    async def stop():
        calls["n"] += 1
        return calls["n"] >= 2

    transport, _ = fake_openai()
    gen = LoadGenerator("http://serving.test", "sk-test", "qwen3", "chat", transport=transport)
    level = await gen.run_level(1, {"requests_per_level": 50, "output_tokens": 2}, should_stop=stop)
    assert len(level.samples) < 50


async def test_loadgen_embeddings():
    transport, _ = fake_openai()
    gen = LoadGenerator("http://serving.test", "sk-test", "nomic", "embedding", transport=transport)
    level = await gen.run_level(2, {"requests_per_level": 4})
    assert all(s.ok and s.prompt_tokens == 7 and s.ttft_ms == s.e2e_ms for s in level.samples)
