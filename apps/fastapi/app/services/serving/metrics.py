"""
Prometheus metrics of the serving process (scraped at /metrics).
"""

from __future__ import annotations

from app.core.logger import get_logger
from prometheus_client import Counter, Gauge, Histogram

logger = get_logger(__name__)

requests_total = Counter(
    "gpudeck_serving_requests_total",
    "Serving requests by format, endpoint and status",
    ["format", "endpoint", "status"],
)
ttfb_seconds = Histogram(
    "gpudeck_serving_ttfb_seconds",
    "Time to first upstream byte",
    ["format", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
latency_seconds = Histogram(
    "gpudeck_serving_latency_seconds",
    "Total request latency",
    ["format", "endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300),
)
inflight_gauge = Gauge("gpudeck_serving_inflight", "In-flight upstream requests")
routing_version = Gauge("gpudeck_serving_routing_version", "Loaded routing table version")
upstream_failures_total = Counter(
    "gpudeck_serving_upstream_failures_total", "Upstream connect/TLS failures", ["code"]
)


def observe(ctx, status_code: int) -> None:
    try:
        requests_total.labels(ctx.fmt, ctx.endpoint, str(status_code)).inc()
        latency_seconds.labels(ctx.fmt, ctx.endpoint).observe(ctx.elapsed_ms() / 1000.0)
        if ctx.ttft_ms is not None:
            ttfb_seconds.labels(ctx.fmt, ctx.endpoint).observe(ctx.ttft_ms / 1000.0)
    except Exception:  # noqa: BLE001 - metrics must never break a request
        pass


# metrics.md §6 usage families, counted where requests are served (this process's /metrics).
# gpu_seconds / cost_usd are computed by the metering worker and live in the usage rollups.
usage_requests_total = Counter(
    "gpudeck_usage_requests_total",
    "Metered serving requests",
    ["api_key", "model", "format", "endpoint", "status"],
)
usage_tokens_total = Counter(
    "gpudeck_usage_tokens_total",
    "Metered tokens by kind (input includes cached)",
    ["api_key", "model", "kind"],
)
usage_latency_seconds = Histogram(
    "gpudeck_usage_latency_seconds",
    "Metered request latency",
    ["model", "format"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600),
)
usage_ttft_seconds = Histogram(
    "gpudeck_usage_ttft_seconds",
    "Metered time to first token (streams)",
    ["model", "format"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)


def observe_usage(payload: dict) -> None:
    """Count one metered request (payload = metering's usage event dict)."""
    try:
        key = str(payload.get("api_key_id") or "none")
        model = str(payload.get("model_name") or "unknown")
        fmt = str(payload.get("format") or "")
        usage_requests_total.labels(
            key,
            model,
            fmt,
            str(payload.get("endpoint") or ""),
            str(payload.get("status") or ""),
        ).inc(int(payload.get("request_count") or 1))
        for kind in ("input", "output", "cached", "reasoning"):
            n = payload.get(f"{kind}_tokens") or 0
            if n:
                usage_tokens_total.labels(key, model, kind).inc(int(n))
        if payload.get("latency_ms") is not None:
            usage_latency_seconds.labels(model, fmt).observe(float(payload["latency_ms"]) / 1000.0)
        if payload.get("ttft_ms") is not None:
            usage_ttft_seconds.labels(model, fmt).observe(float(payload["ttft_ms"]) / 1000.0)
    except Exception:  # noqa: BLE001 - metrics must never break a request
        pass
