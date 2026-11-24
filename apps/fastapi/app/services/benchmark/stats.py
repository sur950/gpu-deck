"""
Benchmark statistics, SLO checks and recommendations (pure functions).

A level result (one concurrency step) matches ``benchmark_runs.results[]``::

    {"concurrency", "requests", "errors", "rps", "generation_tps", "prompt_tps",
     "ttft_ms": {"p50","p95","p99","avg"}, "e2e_ms": {...}, "tpot_ms": {...},
     "slo_met", "launch_flags"}

SLO keys (``benchmarks.target_slo``): ``ttft_ms_p95``, ``e2e_ms_p95``,
``tpot_ms_p95``, ``min_generation_tps`` (per-request decode speed, p50) and
``max_error_rate_pct`` (default 1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence


@dataclass
class RequestSample:
    ok: bool
    e2e_ms: float
    ttft_ms: Optional[float] = None
    prompt_tokens: int = 0
    output_tokens: int = 0
    error: Optional[str] = None

    @property
    def tpot_ms(self) -> Optional[float]:
        if self.ttft_ms is None or self.output_tokens <= 1:
            return None
        return max(self.e2e_ms - self.ttft_ms, 0.0) / (self.output_tokens - 1)

    @property
    def generation_tps(self) -> Optional[float]:
        tpot = self.tpot_ms
        if tpot is None or tpot <= 0:
            return None
        return 1000.0 / tpot


@dataclass
class LevelResult:
    concurrency: int
    samples: List[RequestSample] = field(default_factory=list)
    wall_seconds: float = 0.0
    launch_flags: Optional[Dict[str, Any]] = None


def percentile(values: Sequence[float], pct: float) -> Optional[float]:
    """Linear-interpolated percentile (numpy 'linear'); None for no values."""
    data = sorted(float(v) for v in values if v is not None)
    if not data:
        return None
    if len(data) == 1:
        return round(data[0], 3)
    rank = (len(data) - 1) * pct / 100.0
    lo = math.floor(rank)
    hi = math.ceil(rank)
    value = data[lo] + (data[hi] - data[lo]) * (rank - lo)
    return round(value, 3)


def dist(values: Iterable[Optional[float]]) -> Dict[str, Optional[float]]:
    vals = [v for v in values if v is not None]
    return {
        "avg": round(sum(vals) / len(vals), 3) if vals else None,
        "p50": percentile(vals, 50),
        "p95": percentile(vals, 95),
        "p99": percentile(vals, 99),
    }


def summarize(level: LevelResult, slo: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ok = [s for s in level.samples if s.ok]
    errors = len(level.samples) - len(ok)
    wall = max(level.wall_seconds, 1e-9)
    out_tokens = sum(s.output_tokens for s in ok)
    in_tokens = sum(s.prompt_tokens for s in ok)
    gen = [s.generation_tps for s in ok if s.generation_tps is not None]
    result: Dict[str, Any] = {
        "concurrency": level.concurrency,
        "requests": len(level.samples),
        "errors": errors,
        "error_rate_pct": (round(100.0 * errors / len(level.samples), 3) if level.samples else 0.0),
        "rps": round(len(ok) / wall, 4),
        "generation_tps": round(out_tokens / wall, 3),
        "prompt_tps": round(in_tokens / wall, 3) if in_tokens else None,
        "per_request_generation_tps": dist(gen),
        "ttft_ms": dist(s.ttft_ms for s in ok),
        "e2e_ms": dist(s.e2e_ms for s in ok),
        "tpot_ms": dist(s.tpot_ms for s in ok),
        "wall_seconds": round(level.wall_seconds, 3),
        "launch_flags": level.launch_flags,
        "error_samples": sorted({s.error for s in level.samples if s.error})[:5],
    }
    result["slo_met"] = slo_met(result, slo or {})
    return result


def slo_met(result: Dict[str, Any], slo: Dict[str, Any]) -> bool:
    """Every configured SLO bound holds (no bounds = met when no errors over the limit)."""
    if not result.get("requests"):
        return False
    max_err = slo.get("max_error_rate_pct")
    max_err = 1.0 if max_err is None else float(max_err)
    if float(result.get("error_rate_pct") or 0.0) > max_err:
        return False
    if result.get("requests") == result.get("errors"):
        return False
    checks = (
        ("ttft_ms_p95", ("ttft_ms", "p95")),
        ("e2e_ms_p95", ("e2e_ms", "p95")),
        ("tpot_ms_p95", ("tpot_ms", "p95")),
    )
    for key, (metric, stat) in checks:
        bound = slo.get(key)
        if bound is None:
            continue
        observed = (result.get(metric) or {}).get(stat)
        if observed is None or observed > float(bound):
            return False
    min_tps = slo.get("min_generation_tps")
    if min_tps is not None:
        observed = (result.get("per_request_generation_tps") or {}).get("p50")
        if observed is None or observed < float(min_tps):
            return False
    return True


def best_concurrency(results: Sequence[Dict[str, Any]]) -> Optional[int]:
    """Highest concurrency whose level met the SLO (levels are independent runs)."""
    met = [r["concurrency"] for r in results if r.get("slo_met")]
    return max(met) if met else None


def recommend(
    results: Sequence[Dict[str, Any]],
    *,
    current_replicas: int,
    ready_replicas: int,
    slo: Dict[str, Any],
    base_flags: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Pick the variant (launch flags) and concurrency with the highest SLO-meeting
    concurrency (ties: higher generation throughput). ``concurrency`` is per
    instance: the API layer spreads the sweep over ``ready_replicas``.
    """
    variants: Dict[str, List[Dict[str, Any]]] = {}
    for r in results:
        key = _flags_key(r.get("launch_flags"))
        variants.setdefault(key, []).append(r)
    best: Optional[Dict[str, Any]] = None
    for rows in variants.values():
        met = [r for r in rows if r.get("slo_met")]
        if not met:
            continue
        top = max(met, key=lambda r: (r["concurrency"], r.get("generation_tps") or 0))
        if best is None or (top["concurrency"], top.get("generation_tps") or 0) > (
            best["concurrency"],
            best.get("generation_tps") or 0,
        ):
            best = top
    replicas = max(ready_replicas, 1)
    if best is None:
        lowest = min(results, key=lambda r: r["concurrency"]) if results else None
        reason = "SLO not met at any concurrency"
        if lowest is not None and (lowest.get("ttft_ms") or {}).get("p95") is not None:
            reason += (
                f" (concurrency {lowest['concurrency']}: TTFT p95 "
                f"{lowest['ttft_ms']['p95']:.0f} ms); try the low_latency preset, a smaller "
                "quantization or a faster device"
            )
        return {
            "concurrency": None,
            "per_instance_concurrency": None,
            "launch_flags": dict(base_flags or {}),
            "replicas": current_replicas,
            "reason": reason,
        }
    per_instance = max(1, math.ceil(best["concurrency"] / replicas))
    flags = best.get("launch_flags")
    reason = (
        f"highest concurrency meeting the SLO is {best['concurrency']} "
        f"({best.get('generation_tps') or 0:.1f} tok/s, TTFT p95 "
        f"{(best.get('ttft_ms') or {}).get('p95') or 0:.0f} ms) across {replicas} replica(s)"
    )
    if flags:
        reason += f" with launch flags {flags}"
    return {
        "concurrency": best["concurrency"],
        "per_instance_concurrency": per_instance,
        "launch_flags": dict(flags if flags is not None else (base_flags or {})),
        "replicas": current_replicas,
        "reason": reason,
    }


def _flags_key(flags: Optional[Dict[str, Any]]) -> str:
    if not flags:
        return "{}"
    return repr(sorted((str(k), repr(v)) for k, v in flags.items()))


def default_variants(engine_name: str, levels: Sequence[int]) -> List[Dict[str, Any]]:
    """Launch-flag variants auto-tune tries when none are given (max 4, no duplicates)."""
    top = max(levels) if levels else 8
    if engine_name == "llama_cpp":
        out: List[Dict[str, Any]] = []
        for variant in (
            {"parallel": max(2, min(top, 4)), "flash-attn": "on"},
            {"parallel": max(4, min(top, 8)), "flash-attn": "on"},
            {
                "parallel": max(8, top),
                "flash-attn": "on",
                "cache-type-k": "q8_0",
                "cache-type-v": "q8_0",
            },
        ):
            if variant not in out:  # small level sets collapse the first two
                out.append(variant)
        return out
    if engine_name == "vllm":
        return [
            {"max-num-seqs": 16, "enable-chunked-prefill": True},
            {
                "max-num-seqs": 64,
                "enable-chunked-prefill": True,
                "enable-prefix-caching": True,
            },
            {
                "max-num-seqs": 256,
                "max-num-batched-tokens": 8192,
                "enable-chunked-prefill": True,
            },
        ]
    if engine_name == "sglang":
        return [
            {"max-running-requests": 16},
            {"max-running-requests": 64, "schedule-policy": "lpm"},
            {"max-running-requests": 256, "chunked-prefill-size": 8192},
        ]
    if engine_name == "tensorrt_llm":
        return [{"max_batch_size": 16}, {"max_batch_size": 64}, {"max_batch_size": 256}]
    return []
