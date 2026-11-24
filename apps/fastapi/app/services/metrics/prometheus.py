"""
Prometheus exposition of the fleet — ``GET /api/v1/metrics/prometheus``.

Renders the latest sample per device / instance / node from the live cache
with the catalog's Prometheus names and labels (``libs/contracts/metrics.md``):

- GPU: ``node``, ``node_id``, ``device_id``, ``vendor``, ``index``, ``device_name``
- engine: ``node``, ``node_id``, ``instance_id``, ``deployment``,
  ``deployment_id``, ``model``, ``model_id``, ``engine``
- node / agent: ``node``, ``node_id``

``_pct`` gauges are exported as ``_ratio`` (divided by 100); ``_ms`` latencies
as seconds; clocks in hertz. Stale samples (older than
``METRICS_LIVE_STALE_SECONDS``) are skipped so dead targets disappear.
Process-level metrics stay on each process's own ``/metrics``.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.core.logger import get_logger
from app.services.metrics import live_cache
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# (sample field, prometheus name, type, help, scale)
GPU_FIELDS: List[Tuple[str, str, str, str, float]] = [
    (
        "utilization_pct",
        "gpudeck_gpu_utilization_ratio",
        "gauge",
        "GPU utilization",
        0.01,
    ),
    (
        "memory_util_pct",
        "gpudeck_gpu_memory_utilization_ratio",
        "gauge",
        "Memory controller busy",
        0.01,
    ),
    ("memory_used_bytes", "gpudeck_gpu_memory_used_bytes", "gauge", "VRAM used", 1.0),
    (
        "memory_total_bytes",
        "gpudeck_gpu_memory_total_bytes",
        "gauge",
        "VRAM total",
        1.0,
    ),
    ("power_watts", "gpudeck_gpu_power_watts", "gauge", "Power draw", 1.0),
    ("power_limit_watts", "gpudeck_gpu_power_limit_watts", "gauge", "Power limit", 1.0),
    (
        "temperature_c",
        "gpudeck_gpu_temperature_celsius",
        "gauge",
        "GPU temperature",
        1.0,
    ),
    (
        "memory_temperature_c",
        "gpudeck_gpu_memory_temperature_celsius",
        "gauge",
        "Memory temperature",
        1.0,
    ),
    ("sm_clock_mhz", "gpudeck_gpu_sm_clock_hertz", "gauge", "SM clock", 1e6),
    (
        "memory_clock_mhz",
        "gpudeck_gpu_memory_clock_hertz",
        "gauge",
        "Memory clock",
        1e6,
    ),
    ("fan_pct", "gpudeck_gpu_fan_ratio", "gauge", "Fan speed", 0.01),
    ("encoder_pct", "gpudeck_gpu_encoder_ratio", "gauge", "Encoder utilization", 0.01),
    ("decoder_pct", "gpudeck_gpu_decoder_ratio", "gauge", "Decoder utilization", 0.01),
    (
        "pcie_tx_bytes_per_s",
        "gpudeck_gpu_pcie_tx_bytes_per_second",
        "gauge",
        "PCIe TX",
        1.0,
    ),
    (
        "pcie_rx_bytes_per_s",
        "gpudeck_gpu_pcie_rx_bytes_per_second",
        "gauge",
        "PCIe RX",
        1.0,
    ),
    (
        "ecc_errors_total",
        "gpudeck_gpu_ecc_errors_total",
        "counter",
        "Uncorrected ECC errors",
        1.0,
    ),
    ("process_count", "gpudeck_gpu_processes", "gauge", "Compute processes", 1.0),
]

ENGINE_FIELDS: List[Tuple[str, str, str, str, float]] = [
    (
        "active_sessions",
        "gpudeck_engine_active_sessions",
        "gauge",
        "Running requests",
        1.0,
    ),
    (
        "waiting_sessions",
        "gpudeck_engine_waiting_sessions",
        "gauge",
        "Queued requests",
        1.0,
    ),
    ("requests_total", "gpudeck_engine_requests_total", "counter", "Requests", 1.0),
    (
        "requests_success_total",
        "gpudeck_engine_requests_success_total",
        "counter",
        "Successful requests",
        1.0,
    ),
    ("errors_total", "gpudeck_engine_errors_total", "counter", "Failed requests", 1.0),
    (
        "prompt_tokens_total",
        "gpudeck_engine_prompt_tokens_total",
        "counter",
        "Prompt tokens",
        1.0,
    ),
    (
        "generated_tokens_total",
        "gpudeck_engine_generated_tokens_total",
        "counter",
        "Generated tokens",
        1.0,
    ),
    (
        "cached_tokens_total",
        "gpudeck_engine_cached_tokens_total",
        "counter",
        "Cached prompt tokens",
        1.0,
    ),
    (
        "prompt_tps",
        "gpudeck_engine_prompt_tokens_per_second",
        "gauge",
        "Prompt throughput",
        1.0,
    ),
    (
        "generation_tps",
        "gpudeck_engine_generation_tokens_per_second",
        "gauge",
        "Generation throughput",
        1.0,
    ),
    (
        "ttft_ms_avg",
        "gpudeck_engine_ttft_seconds",
        "gauge",
        "Mean time to first token",
        0.001,
    ),
    (
        "e2e_ms_avg",
        "gpudeck_engine_e2e_latency_seconds",
        "gauge",
        "Mean end-to-end latency",
        0.001,
    ),
    (
        "tpot_ms_avg",
        "gpudeck_engine_time_per_output_token_seconds",
        "gauge",
        "Time per output token",
        0.001,
    ),
    ("queue_ms_avg", "gpudeck_engine_queue_time_seconds", "gauge", "Queue time", 0.001),
    (
        "kv_cache_used_tokens",
        "gpudeck_engine_kv_cache_used_tokens",
        "gauge",
        "KV tokens in use",
        1.0,
    ),
    (
        "kv_cache_total_tokens",
        "gpudeck_engine_kv_cache_total_tokens",
        "gauge",
        "KV capacity",
        1.0,
    ),
    (
        "kv_cache_usage_pct",
        "gpudeck_engine_kv_cache_usage_ratio",
        "gauge",
        "KV cache usage",
        0.01,
    ),
    ("kv_cache_bytes", "gpudeck_engine_kv_cache_bytes", "gauge", "KV cache bytes", 1.0),
    (
        "prefix_cache_hit_rate",
        "gpudeck_engine_prefix_cache_hit_ratio",
        "gauge",
        "Prefix cache hit ratio",
        1.0,
    ),
    (
        "spec_accept_rate",
        "gpudeck_engine_speculative_accept_ratio",
        "gauge",
        "Speculative acceptance",
        1.0,
    ),
    (
        "process_vram_bytes",
        "gpudeck_engine_process_vram_bytes",
        "gauge",
        "Engine VRAM",
        1.0,
    ),
    (
        "process_rss_bytes",
        "gpudeck_engine_process_rss_bytes",
        "gauge",
        "Engine RSS",
        1.0,
    ),
]

NODE_LOAD_FIELDS = [
    ("cpu_pct", "gpudeck_node_cpu_ratio", "gauge", "Host CPU", 0.01),
    (
        "memory_used_bytes",
        "gpudeck_node_memory_used_bytes",
        "gauge",
        "Host memory used",
        1.0,
    ),
    (
        "disk_free_bytes",
        "gpudeck_node_disk_free_bytes",
        "gauge",
        "Free disk (cache mount)",
        1.0,
    ),
    (
        "cache_used_bytes",
        "gpudeck_node_cache_used_bytes",
        "gauge",
        "Model cache size",
        1.0,
    ),
]

AGENT_FIELDS = [
    ("cpu_pct", "gpudeck_agent_cpu_ratio", "gauge", "Agent CPU", 0.01),
    ("rss_bytes", "gpudeck_agent_rss_bytes", "gauge", "Agent RSS", 1.0),
    (
        "proxy_active_connections",
        "gpudeck_agent_proxy_active_connections",
        "gauge",
        "Proxy connections",
        1.0,
    ),
    (
        "proxy_requests_total",
        "gpudeck_agent_proxy_requests_total",
        "counter",
        "Proxied requests",
        1.0,
    ),
    (
        "proxy_errors_total",
        "gpudeck_agent_proxy_errors_total",
        "counter",
        "Proxy errors",
        1.0,
    ),
    (
        "scrape_errors_total",
        "gpudeck_agent_scrape_errors_total",
        "counter",
        "Scrape errors",
        1.0,
    ),
]


def escape_label(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def format_labels(labels: Dict[str, Any]) -> str:
    parts = [f'{k}="{escape_label(v)}"' for k, v in labels.items() if v is not None]
    return "{" + ",".join(parts) + "}" if parts else ""


def format_value(value: float) -> str:
    if value != value:  # NaN
        return "NaN"
    if float(value).is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(float(value))


class Exposition:
    """Collects samples per metric family and renders text format 0.0.4."""

    def __init__(self) -> None:
        self.families: Dict[str, Tuple[str, str, List[str]]] = {}

    def add(
        self, name: str, mtype: str, help_text: str, labels: Dict[str, Any], value: Any
    ) -> None:
        if value is None:
            return
        try:
            number = float(value)
        except (TypeError, ValueError):
            return
        family = self.families.setdefault(name, (mtype, help_text, []))
        family[2].append(f"{name}{format_labels(labels)} {format_value(number)}")

    def render(self) -> str:
        out: List[str] = []
        for name, (mtype, help_text, lines) in self.families.items():
            out.append(f"# HELP {name} {help_text}")
            out.append(f"# TYPE {name} {mtype}")
            out.extend(lines)
        return "\n".join(out) + "\n"


def _add_fields(
    expo: Exposition,
    fields: Iterable[Tuple[str, str, str, str, float]],
    sample: Dict[str, Any],
    labels: Dict[str, Any],
) -> None:
    for field_name, prom, mtype, help_text, scale in fields:
        value = sample.get(field_name)
        if value is not None:
            expo.add(prom, mtype, help_text, labels, float(value) * scale)


async def _lookups(
    session: AsyncSession,
) -> Tuple[Dict[str, Dict], Dict[str, Dict], Dict[str, str]]:
    devices = (
        (
            await session.execute(
                text(
                    "SELECT d.id::text AS id, d.vendor::text AS vendor, d.index, d.name, "
                    "n.name AS node FROM devices d JOIN nodes n ON n.id = d.node_id "
                    "WHERE d.deleted_at IS NULL"
                )
            )
        )
        .mappings()
        .all()
    )
    instances = (
        (
            await session.execute(
                text(
                    "SELECT i.id::text AS id, d.id::text AS deployment_id, d.name AS deployment, "
                    "m.id::text AS model_id, m.name AS model, e.name::text AS engine, "
                    "n.name AS node "
                    "FROM instances i JOIN deployments d ON d.id = i.deployment_id "
                    "JOIN models m ON m.id = d.model_id "
                    "JOIN engine_versions ev ON ev.id = d.engine_version_id "
                    "JOIN engines e ON e.id = ev.engine_id JOIN nodes n ON n.id = i.node_id "
                    "WHERE i.state <> 'stopped'"
                )
            )
        )
        .mappings()
        .all()
    )
    nodes = (
        (
            await session.execute(
                text("SELECT id::text AS id, name FROM nodes WHERE deleted_at IS NULL")
            )
        )
        .mappings()
        .all()
    )
    return (
        {r["id"]: dict(r) for r in devices},
        {r["id"]: dict(r) for r in instances},
        {r["id"]: r["name"] for r in nodes},
    )


async def render_fleet_exposition(session: AsyncSession) -> str:
    devices, instances, node_names = await _lookups(session)
    expo = Exposition()
    for device_id, sample in (await live_cache.get_all("gpu")).items():
        if not live_cache.is_fresh(sample):
            continue
        info = devices.get(device_id, {})
        node_id = sample.get("node_id")
        labels = {
            "node": info.get("node") or node_names.get(str(node_id)),
            "node_id": node_id,
            "device_id": device_id,
            "vendor": info.get("vendor"),
            "index": info.get("index"),
            "device_name": info.get("name"),
        }
        _add_fields(expo, GPU_FIELDS, sample, labels)
        for reason in sample.get("throttle_reasons") or []:
            expo.add(
                "gpudeck_gpu_throttled",
                "gauge",
                "Throttle reason active",
                {**labels, "reason": reason},
                1,
            )
    for instance_id, sample in (await live_cache.get_all("engine")).items():
        if not live_cache.is_fresh(sample):
            continue
        info = instances.get(instance_id, {})
        node_id = sample.get("node_id")
        labels = {
            "node": info.get("node") or node_names.get(str(node_id)),
            "node_id": node_id,
            "instance_id": instance_id,
            "deployment": info.get("deployment"),
            "deployment_id": sample.get("deployment_id") or info.get("deployment_id"),
            "model": info.get("model"),
            "model_id": info.get("model_id"),
            "engine": info.get("engine"),
        }
        _add_fields(expo, ENGINE_FIELDS, sample, labels)
        if sample.get("health") is not None:
            expo.add(
                "gpudeck_engine_healthy",
                "gauge",
                "Engine health (1 = healthy)",
                labels,
                1 if sample["health"] == "healthy" else 0,
            )
        total, idle = sample.get("slots_total"), sample.get("slots_idle")
        if total is not None:
            expo.add(
                "gpudeck_engine_slots",
                "gauge",
                "Engine slots",
                {**labels, "state": "total"},
                total,
            )
        if idle is not None:
            expo.add(
                "gpudeck_engine_slots",
                "gauge",
                "Engine slots",
                {**labels, "state": "idle"},
                idle,
            )
    for node_id, beat in (await live_cache.get_all("node")).items():
        if not live_cache.is_fresh(beat, 60):
            continue
        labels = {"node": node_names.get(node_id), "node_id": node_id}
        _add_fields(expo, NODE_LOAD_FIELDS, beat.get("load") or {}, labels)
        expo.add("gpudeck_node_up", "gauge", "Node heartbeat seen recently", labels, 1)
    for node_id, agent in (await live_cache.get_all("agent")).items():
        if not live_cache.is_fresh(agent, 60):
            continue
        labels = {"node": node_names.get(node_id), "node_id": node_id}
        _add_fields(expo, AGENT_FIELDS, agent, labels)
    return expo.render()


def check_scrape_token(authorization: Optional[str], token: Optional[str]) -> bool:
    """True when the bearer token matches PROMETHEUS_SCRAPE_TOKEN (constant-time)."""
    import hmac

    if not token or not authorization:
        return False
    scheme, _, value = authorization.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(value.strip(), token)
