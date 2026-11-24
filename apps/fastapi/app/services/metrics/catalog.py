"""
Metric catalog — the canonical names from ``libs/contracts/metrics.md``
with unit, type, scope, Prometheus name and how each one is read from the raw
sample tables and the 1-minute rollups.

Query expressions are evaluated per entity (device / instance) and time
bucket over table alias ``t``; ``final`` combines the inner aliases (ratio
metrics such as ``error_rate_pct``). ``combine`` is the default cross-entity
aggregate the overview uses; the ``aggregate`` query parameter overrides it
for single-value metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional


@dataclass(frozen=True)
class MetricSpec:
    name: str
    unit: str
    type: str  # gauge | counter | histogram
    scope: str  # gpu | engine | session | usage | node
    description: str
    prometheus: Optional[str] = None
    raw: Optional[Dict[str, str]] = None
    rollup: Optional[Dict[str, str]] = None
    final: str = "v"
    combine: str = "avg"
    sample_field: Optional[str] = None  # field in the protocol sample (live / exporter)
    ratio_of_sums: bool = False  # cross-entity aggregate = final(SUM(components))

    @property
    def is_ratio(self) -> bool:
        return self.final != "v"

    def as_entry(self) -> Dict[str, Optional[str]]:
        return {
            "name": self.name,
            "unit": self.unit,
            "type": self.type,
            "scope": self.scope,
            "description": self.description,
            "prometheus_name": self.prometheus,
        }


def _g(
    name: str,
    unit: str,
    description: str,
    column: str,
    rollup: Optional[str] = None,
    prom: Optional[str] = None,
    combine: str = "avg",
    raw_agg: str = "AVG",
) -> MetricSpec:
    return MetricSpec(
        name=name,
        unit=unit,
        type="gauge",
        scope="gpu",
        description=description,
        prometheus=prom,
        raw={"v": f"{raw_agg}(t.{column})"},
        rollup={"v": rollup} if rollup else None,
        combine=combine,
        sample_field=column,
    )


def _e(
    name: str,
    unit: str,
    description: str,
    column: Optional[str],
    rollup: Optional[str] = None,
    prom: Optional[str] = None,
    combine: str = "avg",
    raw_agg: str = "AVG",
) -> MetricSpec:
    return MetricSpec(
        name=name,
        unit=unit,
        type="gauge",
        scope="engine",
        description=description,
        prometheus=prom,
        raw={"v": f"{raw_agg}(t.{column})"} if column else None,
        rollup={"v": rollup} if rollup else None,
        combine=combine,
        sample_field=column,
    )


def _counter(
    name: str, unit: str, description: str, column: str, delta: str, prom: str
) -> MetricSpec:
    """Counter: values are increments per bucket (raw max-min, rollup sum of deltas)."""
    return MetricSpec(
        name=name,
        unit=unit,
        type="counter",
        scope="engine",
        description=description + " (increase per step)",
        prometheus=prom,
        raw={"v": f"GREATEST(MAX(t.{column}) - MIN(t.{column}), 0)"},
        rollup={"v": f"SUM(t.{delta})"},
        combine="sum",
        sample_field=column,
    )


GPU_METRICS: List[MetricSpec] = [
    _g(
        "gpu_utilization_pct",
        "percent",
        "GPU core utilization",
        "utilization_pct",
        "AVG(t.utilization_pct_avg)",
        "gpudeck_gpu_utilization_ratio",
    ),
    _g(
        "gpu_utilization_pct_max",
        "percent",
        "Peak GPU utilization in the step",
        "utilization_pct",
        "MAX(t.utilization_pct_max)",
        None,
        "max",
        "MAX",
    ),
    _g(
        "gpu_memory_util_pct",
        "percent",
        "Memory controller busy",
        "memory_util_pct",
        None,
        "gpudeck_gpu_memory_utilization_ratio",
    ),
    _g(
        "gpu_memory_used_bytes",
        "bytes",
        "VRAM in use",
        "memory_used_bytes",
        "AVG(t.memory_used_bytes_avg)",
        "gpudeck_gpu_memory_used_bytes",
        "sum",
    ),
    _g(
        "gpu_memory_total_bytes",
        "bytes",
        "VRAM total",
        "memory_total_bytes",
        "MAX(t.memory_total_bytes)",
        "gpudeck_gpu_memory_total_bytes",
        "sum",
        "MAX",
    ),
    _g(
        "gpu_power_watts",
        "watts",
        "Board power draw",
        "power_watts",
        "AVG(t.power_watts_avg)",
        "gpudeck_gpu_power_watts",
        "sum",
    ),
    _g(
        "gpu_power_limit_watts",
        "watts",
        "Enforced power limit",
        "power_limit_watts",
        None,
        "gpudeck_gpu_power_limit_watts",
        "sum",
        "MAX",
    ),
    _g(
        "gpu_temperature_c",
        "celsius",
        "GPU temperature",
        "temperature_c",
        "AVG(t.temperature_c_avg)",
        "gpudeck_gpu_temperature_celsius",
        "max",
    ),
    _g(
        "gpu_memory_temperature_c",
        "celsius",
        "Memory temperature",
        "memory_temperature_c",
        None,
        "gpudeck_gpu_memory_temperature_celsius",
        "max",
    ),
    _g(
        "gpu_sm_clock_mhz",
        "MHz",
        "SM / graphics clock",
        "sm_clock_mhz",
        "AVG(t.sm_clock_mhz_avg)",
        "gpudeck_gpu_sm_clock_hertz",
    ),
    _g(
        "gpu_memory_clock_mhz",
        "MHz",
        "Memory clock",
        "memory_clock_mhz",
        None,
        "gpudeck_gpu_memory_clock_hertz",
    ),
    _g("gpu_fan_pct", "percent", "Fan speed", "fan_pct", None, "gpudeck_gpu_fan_ratio"),
    _g(
        "gpu_encoder_pct",
        "percent",
        "Video encoder utilization",
        "encoder_pct",
        None,
        "gpudeck_gpu_encoder_ratio",
    ),
    _g(
        "gpu_decoder_pct",
        "percent",
        "Video decoder utilization",
        "decoder_pct",
        None,
        "gpudeck_gpu_decoder_ratio",
    ),
    _g(
        "gpu_pcie_tx_bytes_per_s",
        "bytes/s",
        "PCIe transmit throughput",
        "pcie_tx_bytes_per_s",
        None,
        "gpudeck_gpu_pcie_tx_bytes_per_second",
        "sum",
    ),
    _g(
        "gpu_pcie_rx_bytes_per_s",
        "bytes/s",
        "PCIe receive throughput",
        "pcie_rx_bytes_per_s",
        None,
        "gpudeck_gpu_pcie_rx_bytes_per_second",
        "sum",
    ),
    MetricSpec(
        "gpu_ecc_errors_total",
        "count",
        "counter",
        "gpu",
        "Uncorrected ECC errors since boot",
        "gpudeck_gpu_ecc_errors_total",
        raw={"v": "MAX(t.ecc_errors_total)"},
        combine="sum",
        sample_field="ecc_errors_total",
    ),
    MetricSpec(
        "gpu_error_count",
        "count",
        "gauge",
        "gpu",
        "Vendor error events (Xid, RAS) in the step",
        "gpudeck_gpu_errors_total",
        raw={"v": "SUM(t.error_count)"},
        rollup={"v": "SUM(t.error_count_sum)"},
        combine="sum",
        sample_field="error_count",
    ),
    MetricSpec(
        "gpu_throttle_reasons",
        "set",
        "gauge",
        "gpu",
        "Active clock throttle reasons (count of samples with any reason)",
        "gpudeck_gpu_throttled",
        raw={"v": "COUNT(*) FILTER (WHERE cardinality(t.throttle_reasons) > 0)"},
        combine="sum",
        sample_field="throttle_reasons",
    ),
    _g(
        "gpu_process_count",
        "count",
        "Compute processes on the device",
        "process_count",
        None,
        "gpudeck_gpu_processes",
        "sum",
    ),
    MetricSpec(
        "gpu_energy_wh",
        "Wh",
        "counter",
        "gpu",
        "Energy used per step (rollup only)",
        "gpudeck_gpu_energy_watthours_total",
        rollup={"v": "SUM(t.energy_wh)"},
        combine="sum",
    ),
    MetricSpec(
        "gpu_throttled_seconds",
        "seconds",
        "gauge",
        "gpu",
        "Seconds with any throttle reason (rollup only)",
        None,
        rollup={"v": "SUM(t.throttled_seconds)"},
        combine="sum",
    ),
]

ENGINE_METRICS: List[MetricSpec] = [
    _e(
        "active_sessions",
        "count",
        "Requests being processed",
        "active_sessions",
        "AVG(t.active_sessions_avg)",
        "gpudeck_engine_active_sessions",
        "sum",
    ),
    _e(
        "active_sessions_max",
        "count",
        "Peak running requests in the step",
        "active_sessions",
        "MAX(t.active_sessions_max)",
        None,
        "sum",
        "MAX",
    ),
    _e(
        "waiting_sessions",
        "count",
        "Queued requests",
        "waiting_sessions",
        "AVG(t.waiting_sessions_avg)",
        "gpudeck_engine_waiting_sessions",
        "sum",
    ),
    _e(
        "waiting_sessions_max",
        "count",
        "Peak queued requests in the step",
        "waiting_sessions",
        "MAX(t.waiting_sessions_max)",
        None,
        "sum",
        "MAX",
    ),
    _e(
        "slots_total",
        "count",
        "Engine slots / max sequences",
        "slots_total",
        None,
        "gpudeck_engine_slots",
        "sum",
    ),
    _e(
        "slots_idle",
        "count",
        "Idle engine slots",
        "slots_idle",
        None,
        "gpudeck_engine_slots",
        "sum",
    ),
    _counter(
        "requests_total",
        "count",
        "Requests",
        "requests_total",
        "requests_delta",
        "gpudeck_engine_requests_total",
    ),
    _counter(
        "requests_delta",
        "count",
        "Requests",
        "requests_total",
        "requests_delta",
        "gpudeck_engine_requests_total",
    ),
    _counter(
        "requests_success_total",
        "count",
        "Successful requests",
        "requests_success_total",
        "requests_success_delta",
        "gpudeck_engine_requests_success_total",
    ),
    _counter(
        "errors_total",
        "count",
        "Failed requests",
        "errors_total",
        "errors_delta",
        "gpudeck_engine_errors_total",
    ),
    _counter(
        "errors_delta",
        "count",
        "Failed requests",
        "errors_total",
        "errors_delta",
        "gpudeck_engine_errors_total",
    ),
    _counter(
        "prompt_tokens_total",
        "tokens",
        "Prompt tokens",
        "prompt_tokens_total",
        "prompt_tokens_delta",
        "gpudeck_engine_prompt_tokens_total",
    ),
    _counter(
        "generated_tokens_total",
        "tokens",
        "Generated tokens",
        "generated_tokens_total",
        "generated_tokens_delta",
        "gpudeck_engine_generated_tokens_total",
    ),
    _counter(
        "cached_tokens_total",
        "tokens",
        "Prompt tokens served from cache",
        "cached_tokens_total",
        "cached_tokens_delta",
        "gpudeck_engine_cached_tokens_total",
    ),
    _counter(
        "cached_tokens_delta",
        "tokens",
        "Prompt tokens served from cache",
        "cached_tokens_total",
        "cached_tokens_delta",
        "gpudeck_engine_cached_tokens_total",
    ),
    MetricSpec(
        "computed_tokens",
        "tokens",
        "counter",
        "engine",
        "Prompt tokens actually computed (prompt - cached) per step",
        "gpudeck_engine_computed_prompt_tokens_total",
        raw={
            "v": "GREATEST(MAX(t.prompt_tokens_total) - MIN(t.prompt_tokens_total), 0)"
            " - COALESCE(GREATEST(MAX(t.cached_tokens_total)"
            " - MIN(t.cached_tokens_total), 0), 0)"
        },
        rollup={"v": "SUM(t.prompt_tokens_delta) - COALESCE(SUM(t.cached_tokens_delta), 0)"},
        combine="sum",
    ),
    MetricSpec(
        "requests_per_minute",
        "count/min",
        "gauge",
        "engine",
        "Request rate",
        None,
        raw={
            "n": "GREATEST(MAX(t.requests_total) - MIN(t.requests_total), 0)",
            "s": "GREATEST(EXTRACT(EPOCH FROM MAX(t.ts) - MIN(t.ts)), 1)",
        },
        rollup={"n": "SUM(t.requests_delta)", "s": "COUNT(*) * 60.0"},
        final="n * 60.0 / NULLIF(s, 0)",
        combine="sum",
    ),
    _e(
        "prompt_tps",
        "tokens/s",
        "Prompt processing throughput",
        "prompt_tps",
        "AVG(t.prompt_tps_avg)",
        "gpudeck_engine_prompt_tokens_per_second",
        "sum",
    ),
    _e(
        "generation_tps",
        "tokens/s",
        "Generation throughput",
        "generation_tps",
        "AVG(t.generation_tps_avg)",
        "gpudeck_engine_generation_tokens_per_second",
        "sum",
    ),
    _e(
        "ttft_ms_avg",
        "ms",
        "Time to first token (mean)",
        "ttft_ms_avg",
        "AVG(t.ttft_ms_avg)",
        "gpudeck_engine_ttft_seconds",
    ),
    _e("ttft_ms_p50", "ms", "Time to first token p50", "ttft_ms_p50", None, None),
    _e(
        "ttft_ms_p95",
        "ms",
        "Time to first token p95",
        "ttft_ms_p95",
        "MAX(t.ttft_ms_p95)",
        None,
        "max",
        "MAX",
    ),
    _e(
        "e2e_ms_avg",
        "ms",
        "End-to-end latency (mean)",
        "e2e_ms_avg",
        "AVG(t.e2e_ms_avg)",
        "gpudeck_engine_e2e_latency_seconds",
    ),
    _e("e2e_ms_p50", "ms", "End-to-end latency p50", "e2e_ms_p50", None, None),
    _e(
        "e2e_ms_p95",
        "ms",
        "End-to-end latency p95",
        "e2e_ms_p95",
        "MAX(t.e2e_ms_p95)",
        None,
        "max",
        "MAX",
    ),
    _e(
        "tpot_ms_avg",
        "ms/token",
        "Time per output token",
        "tpot_ms_avg",
        "AVG(t.tpot_ms_avg)",
        "gpudeck_engine_time_per_output_token_seconds",
    ),
    _e(
        "queue_ms_avg",
        "ms",
        "Queue wait",
        "queue_ms_avg",
        None,
        "gpudeck_engine_queue_time_seconds",
    ),
    _e(
        "kv_cache_used_tokens",
        "tokens",
        "KV cache tokens in use",
        "kv_cache_used_tokens",
        None,
        "gpudeck_engine_kv_cache_used_tokens",
        "sum",
    ),
    _e(
        "kv_cache_total_tokens",
        "tokens",
        "KV cache capacity",
        "kv_cache_total_tokens",
        None,
        "gpudeck_engine_kv_cache_total_tokens",
        "sum",
    ),
    _e(
        "kv_cache_usage_pct",
        "percent",
        "KV cache usage",
        "kv_cache_usage_pct",
        "AVG(t.kv_cache_usage_pct_avg)",
        "gpudeck_engine_kv_cache_usage_ratio",
    ),
    _e(
        "kv_cache_usage_pct_max",
        "percent",
        "Peak KV cache usage in the step",
        "kv_cache_usage_pct",
        "MAX(t.kv_cache_usage_pct_max)",
        None,
        "max",
        "MAX",
    ),
    _e(
        "kv_cache_bytes",
        "bytes",
        "KV cache memory",
        "kv_cache_bytes",
        None,
        "gpudeck_engine_kv_cache_bytes",
        "sum",
    ),
    _e(
        "prefix_cache_hit_rate",
        "ratio",
        "Prefix cache hit rate",
        "prefix_cache_hit_rate",
        "AVG(t.prefix_cache_hit_rate)",
        "gpudeck_engine_prefix_cache_hit_ratio",
    ),
    _e(
        "spec_accept_rate",
        "ratio",
        "Speculative decoding acceptance",
        "spec_accept_rate",
        None,
        "gpudeck_engine_speculative_accept_ratio",
    ),
    _e(
        "process_vram_bytes",
        "bytes",
        "Engine process VRAM",
        "process_vram_bytes",
        None,
        "gpudeck_engine_process_vram_bytes",
        "sum",
    ),
    _e(
        "process_rss_bytes",
        "bytes",
        "Engine process RSS",
        "process_rss_bytes",
        None,
        "gpudeck_engine_process_rss_bytes",
        "sum",
    ),
    MetricSpec(
        "health",
        "ratio",
        "gauge",
        "engine",
        "Share of samples healthy (1 = healthy)",
        "gpudeck_engine_healthy",
        raw={"v": "AVG(CASE WHEN t.health = 'healthy' THEN 1.0 ELSE 0.0 END)"},
        rollup={"h": "SUM(t.healthy_seconds)", "s": "COUNT(*) * 60.0"},
        final="LEAST(h / NULLIF(s, 0), 1.0)",
        combine="avg",
        sample_field="health",
    ),
    MetricSpec(
        "error_rate_pct",
        "percent",
        "gauge",
        "engine",
        "Errors / requests",
        "gpudeck_engine_error_ratio",
        raw={
            "e": "GREATEST(MAX(t.errors_total) - MIN(t.errors_total), 0)",
            "r": "GREATEST(MAX(t.requests_total) - MIN(t.requests_total), 0)",
        },
        rollup={"e": "SUM(t.errors_delta)", "r": "SUM(t.requests_delta)"},
        final="e * 100.0 / NULLIF(r, 0)",
        combine="avg",
        ratio_of_sums=True,
    ),
]

SESSION_METRICS: List[MetricSpec] = [
    MetricSpec(n, u, "gauge", "session", d, None, sample_field=n)
    for n, u, d in (
        ("prompt_tokens", "tokens", "Prompt tokens of the session"),
        ("generated_tokens", "tokens", "Tokens generated so far"),
        ("cached_tokens", "tokens", "Prompt tokens reused from cache"),
        ("kv_cache_tokens", "tokens", "KV cache held by the session"),
        ("kv_cache_bytes", "bytes", "KV cache bytes held by the session"),
        ("context_size", "tokens", "Context window of the slot"),
        ("ttft_ms", "ms", "Time to first token"),
        ("elapsed_ms", "ms", "Time since the session started"),
        ("tokens_per_second", "tokens/s", "Generation speed of the session"),
    )
]

USAGE_METRICS: List[MetricSpec] = [
    MetricSpec(
        "requests",
        "count",
        "counter",
        "usage",
        "Serving requests",
        "gpudeck_usage_requests_total",
        rollup={"v": "SUM(request_count)"},
    ),
    MetricSpec(
        "success",
        "count",
        "counter",
        "usage",
        "Successful requests",
        None,
        rollup={"v": "SUM(success_count)"},
    ),
    MetricSpec(
        "errors",
        "count",
        "counter",
        "usage",
        "Failed requests",
        None,
        rollup={"v": "SUM(error_count)"},
    ),
    MetricSpec(
        "input_tokens",
        "tokens",
        "counter",
        "usage",
        "Input tokens",
        "gpudeck_usage_tokens_total",
        rollup={"v": "SUM(input_tokens)"},
    ),
    MetricSpec(
        "output_tokens",
        "tokens",
        "counter",
        "usage",
        "Output tokens",
        "gpudeck_usage_tokens_total",
        rollup={"v": "SUM(output_tokens)"},
    ),
    MetricSpec(
        "cached_tokens",
        "tokens",
        "counter",
        "usage",
        "Cached input tokens",
        "gpudeck_usage_tokens_total",
        rollup={"v": "SUM(cached_tokens)"},
    ),
    MetricSpec(
        "reasoning_tokens",
        "tokens",
        "counter",
        "usage",
        "Reasoning tokens",
        "gpudeck_usage_tokens_total",
        rollup={"v": "SUM(reasoning_tokens)"},
    ),
    MetricSpec(
        "media_units",
        "units",
        "counter",
        "usage",
        "Audio seconds / images / video",
        None,
        rollup={"v": "SUM(media_units)"},
    ),
    MetricSpec(
        "gpu_seconds",
        "seconds",
        "counter",
        "usage",
        "GPU busy share",
        "gpudeck_usage_gpu_seconds_total",
        rollup={"v": "SUM(gpu_seconds)"},
    ),
    MetricSpec(
        "cost_usd",
        "USD",
        "counter",
        "usage",
        "Cost",
        "gpudeck_usage_cost_usd_total",
        rollup={"v": "SUM(cost_usd)"},
    ),
    MetricSpec(
        "latency_ms_avg",
        "ms",
        "gauge",
        "usage",
        "Mean request latency",
        "gpudeck_usage_latency_seconds",
        rollup={"v": "SUM(latency_ms_sum)::float / NULLIF(SUM(request_count), 0)"},
    ),
    MetricSpec(
        "ttft_ms_avg",
        "ms",
        "gauge",
        "usage",
        "Mean time to first token",
        "gpudeck_usage_ttft_seconds",
        rollup={"v": "SUM(ttft_ms_sum)::float / NULLIF(SUM(request_count), 0)"},
    ),
]

NODE_METRICS: List[MetricSpec] = [
    MetricSpec(
        "node_cpu_pct",
        "percent",
        "gauge",
        "node",
        "Host CPU (heartbeat)",
        "gpudeck_node_cpu_ratio",
        sample_field="cpu_pct",
    ),
    MetricSpec(
        "node_memory_used_bytes",
        "bytes",
        "gauge",
        "node",
        "Host memory in use",
        "gpudeck_node_memory_used_bytes",
        sample_field="memory_used_bytes",
    ),
    MetricSpec(
        "node_disk_free_bytes",
        "bytes",
        "gauge",
        "node",
        "Free disk on the cache mount",
        "gpudeck_node_disk_free_bytes",
        sample_field="disk_free_bytes",
    ),
    MetricSpec(
        "node_cache_used_bytes",
        "bytes",
        "gauge",
        "node",
        "Model cache size",
        "gpudeck_node_cache_used_bytes",
        sample_field="cache_used_bytes",
    ),
    MetricSpec(
        "agent_cpu_pct",
        "percent",
        "gauge",
        "node",
        "Agent CPU",
        "gpudeck_agent_cpu_ratio",
        sample_field="cpu_pct",
    ),
    MetricSpec(
        "agent_rss_bytes",
        "bytes",
        "gauge",
        "node",
        "Agent resident memory",
        "gpudeck_agent_rss_bytes",
        sample_field="rss_bytes",
    ),
    MetricSpec(
        "agent_proxy_active_connections",
        "count",
        "gauge",
        "node",
        "Open proxy connections",
        "gpudeck_agent_proxy_active_connections",
        sample_field="proxy_active_connections",
    ),
    MetricSpec(
        "agent_proxy_requests_total",
        "count",
        "counter",
        "node",
        "Proxied requests",
        "gpudeck_agent_proxy_requests_total",
        sample_field="proxy_requests_total",
    ),
    MetricSpec(
        "agent_proxy_errors_total",
        "count",
        "counter",
        "node",
        "Proxy errors",
        "gpudeck_agent_proxy_errors_total",
        sample_field="proxy_errors_total",
    ),
    MetricSpec(
        "agent_scrape_errors_total",
        "count",
        "counter",
        "node",
        "Engine scrape errors",
        "gpudeck_agent_scrape_errors_total",
        sample_field="scrape_errors_total",
    ),
]

ALL_METRICS: List[MetricSpec] = (
    GPU_METRICS + ENGINE_METRICS + SESSION_METRICS + USAGE_METRICS + NODE_METRICS
)
BY_SCOPE: Dict[str, Dict[str, MetricSpec]] = {}
for _spec in ALL_METRICS:
    BY_SCOPE.setdefault(_spec.scope, {})[_spec.name] = _spec

DEFAULT_GPU = [
    "gpu_utilization_pct",
    "gpu_memory_used_bytes",
    "gpu_power_watts",
    "gpu_temperature_c",
]
DEFAULT_INFERENCE = [
    "active_sessions",
    "waiting_sessions",
    "generation_tps",
    "ttft_ms_p95",
    "requests_per_minute",
    "error_rate_pct",
]
DEFAULT_USAGE = ["requests", "input_tokens", "output_tokens", "cost_usd"]


def catalog_entries() -> List[Dict[str, Optional[str]]]:
    seen = set()
    out = []
    for spec in ALL_METRICS:
        key = (spec.scope, spec.name)
        if key in seen:
            continue
        seen.add(key)
        out.append(spec.as_entry())
    return out


@dataclass
class ResolvedMetrics:
    specs: List[MetricSpec] = field(default_factory=list)
    unknown: List[str] = field(default_factory=list)


# Alternative spellings accepted on input; the series keep the requested name.
METRIC_ALIASES: Dict[str, Dict[str, str]] = {
    "usage": {
        "tokens_input": "input_tokens",
        "tokens_output": "output_tokens",
        "tokens_cached": "cached_tokens",
        "tokens_reasoning": "reasoning_tokens",
    },
}


def resolve(scope: str, names: Optional[List[str]], defaults: List[str]) -> ResolvedMetrics:
    """Accept repeated or comma-separated names; unknown names are reported back."""
    wanted: List[str] = []
    for value in names or []:
        wanted.extend(p.strip() for p in str(value).split(",") if p.strip())
    if not wanted:
        wanted = list(defaults)
    table = BY_SCOPE.get(scope, {})
    out = ResolvedMetrics()
    aliases = METRIC_ALIASES.get(scope, {})
    for name in dict.fromkeys(wanted):
        spec = table.get(name)
        if spec is None and name in aliases and aliases[name] in table:
            spec = replace(table[aliases[name]], name=name)
        if spec is None:
            out.unknown.append(name)
        else:
            out.specs.append(spec)
    return out
