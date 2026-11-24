#!/usr/bin/env python3
"""Generate the GPU Deck Grafana dashboards from the metric names in libs/contracts/metrics.md.

    python3 infra/observability/grafana/build_dashboards.py          # write dashboards/*.json
    python3 infra/observability/grafana/build_dashboards.py --check  # fail if stale or unknown

Every ``gpudeck_*`` series a panel queries must be a Prometheus name listed in metrics.md (or one
of the serving-process names in SERVING_PROCESS_METRICS); ``--check`` enforces that and that the
committed JSON matches this script. Stdlib only.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
METRICS_MD = ROOT / "libs" / "contracts" / "metrics.md"
OUT = HERE / "dashboards"
# The Helm chart can only read files inside the chart, so it gets copies.
CHART_FILES = ROOT / "infra" / "helm" / "gpu-deck" / "files"
ALERT_RULES = HERE.parent / "prometheus" / "rules" / "gpudeck-alerts.yml"
# Panels use a datasource variable so the dashboards work with any Prometheus datasource uid
# (compose provisions uid gpudeck-prometheus; Kubernetes Grafana installs bring their own).
DS = {"type": "prometheus", "uid": "${datasource}"}

# Exported by the API layer process itself (apps/fastapi/app/services/serving/metrics.py) in
# addition to the catalog names.
SERVING_PROCESS_METRICS = {
    "gpudeck_serving_requests_total",
    "gpudeck_serving_ttfb_seconds",
    "gpudeck_serving_latency_seconds",
    "gpudeck_serving_inflight",
    "gpudeck_serving_routing_version",
    "gpudeck_serving_upstream_failures_total",
}
# Standard process metrics (metrics.md §7, not gpudeck_-prefixed).
GENERIC_PREFIXES = ("http_", "celery_", "cache_", "up")


# ----- panel helpers --------------------------------------------------------------------------


class Board:
    def __init__(self, uid: str, title: str, description: str, tags: list[str]) -> None:
        self.uid, self.title, self.description, self.tags = uid, title, description, tags
        self.panels: list[dict] = []
        self.variables: list[dict] = [
            {
                "name": "datasource",
                "label": "Prometheus",
                "type": "datasource",
                "query": "prometheus",
                "refresh": 1,
                "current": {},
                "hide": 0,
            }
        ]
        self._y = 0
        self._x = 0
        self._row_h = 0
        self._id = 0

    def var(self, name: str, label: str, query: str, multi: bool = True) -> None:
        self.variables.append(
            {
                "name": name,
                "label": label,
                "type": "query",
                "datasource": DS,
                "query": {"query": query, "refId": f"var-{name}"},
                "definition": query,
                "refresh": 2,
                "includeAll": True,
                "multi": multi,
                "allValue": ".*",
                "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
                "sort": 1,
            }
        )

    def row(self, title: str) -> None:
        self._newline()
        self._id += 1
        self.panels.append(
            {
                "id": self._id,
                "type": "row",
                "title": title,
                "collapsed": False,
                "gridPos": {"h": 1, "w": 24, "x": 0, "y": self._y},
                "panels": [],
            }
        )
        self._y += 1

    def _newline(self) -> None:
        if self._x:
            self._y += self._row_h
            self._x, self._row_h = 0, 0

    def _place(self, w: int, h: int) -> dict:
        if self._x + w > 24:
            self._newline()
        pos = {"h": h, "w": w, "x": self._x, "y": self._y}
        self._x += w
        self._row_h = max(self._row_h, h)
        return pos

    def panel(
        self,
        kind: str,
        title: str,
        targets: list[tuple[str, str]],
        unit: str = "short",
        w: int = 8,
        h: int = 8,
        description: str = "",
        stack: bool = False,
        thresholds: list[tuple[float | None, str]] | None = None,
        decimals: int | None = None,
        minv: float | None = None,
        maxv: float | None = None,
    ) -> None:
        self._id += 1
        defaults: dict = {"unit": unit}
        if decimals is not None:
            defaults["decimals"] = decimals
        if minv is not None:
            defaults["min"] = minv
        if maxv is not None:
            defaults["max"] = maxv
        steps = thresholds or [(None, "green")]
        defaults["thresholds"] = {
            "mode": "absolute",
            "steps": [{"color": c, "value": v} for v, c in steps],
        }
        if kind == "timeseries":
            defaults["custom"] = {
                "drawStyle": "line",
                "lineWidth": 1,
                "fillOpacity": 15 if stack else 8,
                "showPoints": "never",
                "spanNulls": True,
                "stacking": {"mode": "normal" if stack else "none", "group": "A"},
            }
        options: dict = {}
        if kind == "timeseries":
            options = {
                "legend": {"displayMode": "table", "placement": "bottom", "calcs": ["mean", "max"]},
                "tooltip": {"mode": "multi", "sort": "desc"},
            }
        elif kind in ("stat", "gauge", "bargauge"):
            options = {
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "colorMode": "value",
                "graphMode": "area",
                "textMode": "auto",
            }
        elif kind == "table":
            options = {"showHeader": True}
        self.panels.append(
            {
                "id": self._id,
                "type": kind,
                "title": title,
                "description": description,
                "datasource": DS,
                "gridPos": self._place(w, h),
                "fieldConfig": {"defaults": defaults, "overrides": []},
                "options": options,
                "targets": [
                    {
                        "datasource": DS,
                        "refId": chr(ord("A") + i),
                        "expr": expr,
                        "legendFormat": legend,
                        "range": kind != "table",
                        "instant": kind == "table",
                        **({"format": "table"} if kind == "table" else {}),
                    }
                    for i, (expr, legend) in enumerate(targets)
                ],
            }
        )

    def render(self) -> dict:
        return {
            "uid": self.uid,
            "title": self.title,
            "description": self.description,
            "tags": ["gpu-deck", *self.tags],
            "timezone": "browser",
            "editable": True,
            "graphTooltip": 1,
            "refresh": "30s",
            "schemaVersion": 39,
            "version": 1,
            "time": {"from": "now-6h", "to": "now"},
            "timepicker": {},
            "annotations": {"list": []},
            "links": [
                {
                    "title": "GPU Deck dashboards",
                    "type": "dashboards",
                    "tags": ["gpu-deck"],
                    "asDropdown": True,
                }
            ],
            "templating": {"list": self.variables},
            "panels": self.panels,
        }


# ----- dashboards -----------------------------------------------------------------------------

GPU_SEL = 'node=~"$node", vendor=~"$vendor", device_name=~"$device"'
ENG_SEL = 'deployment=~"$deployment", model=~"$model", node=~"$node", engine=~"$engine"'
HOT = [(None, "green"), (75, "orange"), (88, "red")]
BUSY = [(None, "green"), (0.8, "orange"), (0.95, "red")]


def gpu_fleet() -> Board:
    b = Board(
        "gpudeck-gpu-fleet",
        "GPU Deck / GPU fleet",
        "Accelerators and GPU machines (metrics.md §1, §5).",
        ["gpu"],
    )
    b.var("node", "Node", "label_values(gpudeck_gpu_utilization_ratio, node)")
    b.var("vendor", "Vendor", "label_values(gpudeck_gpu_utilization_ratio, vendor)")
    b.var("device", "Device", "label_values(gpudeck_gpu_utilization_ratio, device_name)")
    b.row("Overview")
    b.panel("stat", "Nodes up", [("sum(gpudeck_node_up)", "")], w=4, h=4)
    b.panel("stat", "GPUs", [(f"count(gpudeck_gpu_memory_total_bytes{{{GPU_SEL}}})", "")], w=4, h=4)
    b.panel(
        "gauge",
        "Avg GPU utilization",
        [(f"avg(gpudeck_gpu_utilization_ratio{{{GPU_SEL}}})", "")],
        unit="percentunit",
        w=4,
        h=4,
        minv=0,
        maxv=1,
        thresholds=BUSY,
    )
    b.panel(
        "gauge",
        "VRAM in use",
        [
            (
                f"sum(gpudeck_gpu_memory_used_bytes{{{GPU_SEL}}}) / "
                f"sum(gpudeck_gpu_memory_total_bytes{{{GPU_SEL}}})",
                "",
            )
        ],
        unit="percentunit",
        w=4,
        h=4,
        minv=0,
        maxv=1,
        thresholds=BUSY,
    )
    b.panel(
        "stat",
        "Power draw",
        [(f"sum(gpudeck_gpu_power_watts{{{GPU_SEL}}})", "")],
        unit="watt",
        w=4,
        h=4,
    )
    b.panel(
        "stat",
        "Energy (range)",
        [(f"sum(increase(gpudeck_gpu_energy_watthours_total{{{GPU_SEL}}}[$__range]))", "")],
        unit="watth",
        w=4,
        h=4,
    )
    b.row("Utilization and memory")
    leg = "{{node}} #{{index}} {{device_name}}"
    b.panel(
        "timeseries",
        "GPU utilization",
        [(f"gpudeck_gpu_utilization_ratio{{{GPU_SEL}}}", leg)],
        unit="percentunit",
        w=12,
        minv=0,
        maxv=1,
    )
    b.panel(
        "timeseries",
        "Memory controller utilization",
        [(f"gpudeck_gpu_memory_utilization_ratio{{{GPU_SEL}}}", leg)],
        unit="percentunit",
        w=12,
        minv=0,
        maxv=1,
    )
    b.panel(
        "timeseries",
        "VRAM used",
        [(f"gpudeck_gpu_memory_used_bytes{{{GPU_SEL}}}", leg)],
        unit="bytes",
        w=12,
    )
    b.panel(
        "timeseries",
        "VRAM used / total",
        [
            (
                f"gpudeck_gpu_memory_used_bytes{{{GPU_SEL}}} / "
                f"gpudeck_gpu_memory_total_bytes{{{GPU_SEL}}}",
                leg,
            )
        ],
        unit="percentunit",
        w=12,
        minv=0,
        maxv=1,
    )
    b.row("Power, thermals and clocks")
    b.panel(
        "timeseries",
        "Power",
        [
            (f"gpudeck_gpu_power_watts{{{GPU_SEL}}}", leg),
            (f"gpudeck_gpu_power_limit_watts{{{GPU_SEL}}}", "limit " + leg),
        ],
        unit="watt",
    )
    b.panel(
        "timeseries",
        "Temperature",
        [
            (f"gpudeck_gpu_temperature_celsius{{{GPU_SEL}}}", leg),
            (f"gpudeck_gpu_memory_temperature_celsius{{{GPU_SEL}}}", "mem " + leg),
        ],
        unit="celsius",
        thresholds=HOT,
    )
    b.panel(
        "timeseries",
        "Fan",
        [(f"gpudeck_gpu_fan_ratio{{{GPU_SEL}}}", leg)],
        unit="percentunit",
        minv=0,
        maxv=1,
    )
    b.panel(
        "timeseries",
        "Clocks",
        [
            (f"gpudeck_gpu_sm_clock_hertz{{{GPU_SEL}}}", "sm " + leg),
            (f"gpudeck_gpu_memory_clock_hertz{{{GPU_SEL}}}", "mem " + leg),
        ],
        unit="hertz",
    )
    b.panel(
        "timeseries",
        "Throttle reasons",
        [(f"gpudeck_gpu_throttled{{{GPU_SEL}}} > 0", "{{node}} #{{index}} {{reason}}")],
        unit="bool_yes_no",
        description="1 while the reason is active.",
    )
    b.panel(
        "timeseries",
        "Encoder / decoder",
        [
            (f"gpudeck_gpu_encoder_ratio{{{GPU_SEL}}}", "enc " + leg),
            (f"gpudeck_gpu_decoder_ratio{{{GPU_SEL}}}", "dec " + leg),
        ],
        unit="percentunit",
    )
    b.row("PCIe, errors, processes")
    b.panel(
        "timeseries",
        "PCIe throughput",
        [
            (f"gpudeck_gpu_pcie_tx_bytes_per_second{{{GPU_SEL}}}", "tx " + leg),
            (f"gpudeck_gpu_pcie_rx_bytes_per_second{{{GPU_SEL}}}", "rx " + leg),
        ],
        unit="Bps",
    )
    b.panel(
        "timeseries",
        "ECC / Xid errors (rate)",
        [
            (f"increase(gpudeck_gpu_ecc_errors_total{{{GPU_SEL}}}[5m])", "ecc " + leg),
            (f"increase(gpudeck_gpu_errors_total{{{GPU_SEL}}}[5m])", "xid " + leg),
        ],
        thresholds=[(None, "green"), (1, "red")],
    )
    b.panel(
        "timeseries",
        "Processes on GPU",
        [(f"gpudeck_gpu_processes{{{GPU_SEL}}}", leg)],
    )
    b.row("Machines")
    nsel = 'node=~"$node"'
    b.panel(
        "timeseries",
        "Host CPU",
        [(f"gpudeck_node_cpu_ratio{{{nsel}}}", "{{node}}")],
        unit="percentunit",
        minv=0,
        maxv=1,
    )
    b.panel(
        "timeseries",
        "Host memory used",
        [(f"gpudeck_node_memory_used_bytes{{{nsel}}}", "{{node}}")],
        unit="bytes",
    )
    b.panel(
        "timeseries",
        "Model cache / free disk",
        [
            (f"gpudeck_node_cache_used_bytes{{{nsel}}}", "cache {{node}}"),
            (f"gpudeck_node_disk_free_bytes{{{nsel}}}", "free {{node}} {{mount}}"),
        ],
        unit="bytes",
    )
    return b


def inference() -> Board:
    b = Board(
        "gpudeck-inference",
        "GPU Deck / Inference",
        "Engines, sessions, KV cache and latency per deployment (metrics.md §2, §3).",
        ["inference"],
    )
    b.var("deployment", "Deployment", "label_values(gpudeck_engine_active_sessions, deployment)")
    b.var("model", "Model", "label_values(gpudeck_engine_active_sessions, model)")
    b.var("node", "Node", "label_values(gpudeck_engine_active_sessions, node)")
    b.var("engine", "Engine", "label_values(gpudeck_engine_active_sessions, engine)")
    s = ENG_SEL
    leg = "{{deployment}} @ {{node}}"
    b.row("Traffic")
    b.panel(
        "stat", "Active sessions", [(f"sum(gpudeck_engine_active_sessions{{{s}}})", "")], w=4, h=4
    )
    b.panel(
        "stat",
        "Waiting sessions",
        [(f"sum(gpudeck_engine_waiting_sessions{{{s}}})", "")],
        w=4,
        h=4,
        thresholds=[(None, "green"), (1, "orange"), (10, "red")],
    )
    b.panel(
        "stat",
        "Requests / min",
        [(f"sum(rate(gpudeck_engine_requests_total{{{s}}}[5m])) * 60", "")],
        unit="reqpm",
        w=4,
        h=4,
    )
    b.panel(
        "stat",
        "Generation tok/s",
        [(f"sum(gpudeck_engine_generation_tokens_per_second{{{s}}})", "")],
        w=4,
        h=4,
    )
    b.panel(
        "stat",
        "Error ratio (5m)",
        [
            (
                f"sum(rate(gpudeck_engine_errors_total{{{s}}}[5m])) / "
                f"clamp_min(sum(rate(gpudeck_engine_requests_total{{{s}}}[5m])), 1e-9)",
                "",
            )
        ],
        unit="percentunit",
        w=4,
        h=4,
        thresholds=[(None, "green"), (0.01, "orange"), (0.05, "red")],
    )
    b.panel(
        "stat",
        "Healthy instances",
        [(f"sum(gpudeck_engine_healthy{{{s}}})", "")],
        w=4,
        h=4,
    )
    b.panel(
        "timeseries",
        "Sessions",
        [
            (f"sum by (deployment, node) (gpudeck_engine_active_sessions{{{s}}})", "active " + leg),
            (
                f"sum by (deployment, node) (gpudeck_engine_waiting_sessions{{{s}}})",
                "waiting " + leg,
            ),
        ],
        w=12,
    )
    b.panel(
        "timeseries",
        "Slots",
        [(f"sum by (deployment, state) (gpudeck_engine_slots{{{s}}})", "{{deployment}} {{state}}")],
        w=12,
    )
    b.panel(
        "timeseries",
        "Requests / s",
        [
            (
                f"sum by (deployment) (rate(gpudeck_engine_requests_total{{{s}}}[5m]))",
                "{{deployment}}",
            ),
            (
                f"sum by (deployment) (rate(gpudeck_engine_requests_success_total{{{s}}}[5m]))",
                "ok {{deployment}}",
            ),
            (
                f"sum by (deployment) (rate(gpudeck_engine_errors_total{{{s}}}[5m]))",
                "errors {{deployment}}",
            ),
        ],
        unit="reqps",
        w=12,
    )
    b.panel(
        "timeseries",
        "Error ratio",
        [(f"max by (deployment) (gpudeck_engine_error_ratio{{{s}}})", "{{deployment}}")],
        unit="percentunit",
        w=12,
    )
    b.row("Throughput and tokens")
    b.panel(
        "timeseries",
        "Prompt tokens / s",
        [
            (
                f"sum by (deployment) (gpudeck_engine_prompt_tokens_per_second{{{s}}})",
                "{{deployment}}",
            )
        ],
    )
    b.panel(
        "timeseries",
        "Generation tokens / s",
        [
            (
                f"sum by (deployment) (gpudeck_engine_generation_tokens_per_second{{{s}}})",
                "{{deployment}}",
            )
        ],
    )
    b.panel(
        "timeseries",
        "Cached vs computed prompt tokens",
        [
            (f"sum(rate(gpudeck_engine_cached_tokens_total{{{s}}}[5m]))", "cached"),
            (f"sum(rate(gpudeck_engine_computed_prompt_tokens_total{{{s}}}[5m]))", "computed"),
            (f"sum(rate(gpudeck_engine_generated_tokens_total{{{s}}}[5m]))", "generated"),
            (f"sum(rate(gpudeck_engine_prompt_tokens_total{{{s}}}[5m]))", "prompt total"),
        ],
        unit="short",
        description="Tokens per second; cached = served from the prefix/KV cache.",
    )
    b.row("Latency")
    b.panel(
        "timeseries",
        "Time to first token (mean)",
        [(f"avg by (deployment) (gpudeck_engine_ttft_seconds{{{s}}})", "{{deployment}}")],
        unit="s",
    )
    b.panel(
        "timeseries",
        "End-to-end latency (mean)",
        [(f"avg by (deployment) (gpudeck_engine_e2e_latency_seconds{{{s}}})", "{{deployment}}")],
        unit="s",
    )
    b.panel(
        "timeseries",
        "Time per output token / queue time",
        [
            (
                f"avg by (deployment) (gpudeck_engine_time_per_output_token_seconds{{{s}}})",
                "tpot {{deployment}}",
            ),
            (
                f"avg by (deployment) (gpudeck_engine_queue_time_seconds{{{s}}})",
                "queue {{deployment}}",
            ),
        ],
        unit="s",
    )
    b.row("KV cache and memory")
    b.panel(
        "timeseries",
        "KV cache usage",
        [
            (
                f"max by (deployment, instance_id) (gpudeck_engine_kv_cache_usage_ratio{{{s}}})",
                "{{deployment}} {{instance_id}}",
            )
        ],
        unit="percentunit",
        minv=0,
        maxv=1,
        thresholds=BUSY,
    )
    b.panel(
        "timeseries",
        "KV cache tokens",
        [
            (
                f"sum by (deployment) (gpudeck_engine_kv_cache_used_tokens{{{s}}})",
                "used {{deployment}}",
            ),
            (
                f"sum by (deployment) (gpudeck_engine_kv_cache_total_tokens{{{s}}})",
                "capacity {{deployment}}",
            ),
        ],
    )
    b.panel(
        "timeseries",
        "KV cache bytes",
        [(f"sum by (deployment) (gpudeck_engine_kv_cache_bytes{{{s}}})", "{{deployment}}")],
        unit="bytes",
    )
    b.panel(
        "timeseries",
        "Prefix cache hit / speculative accept",
        [
            (
                f"avg by (deployment) (gpudeck_engine_prefix_cache_hit_ratio{{{s}}})",
                "prefix hit {{deployment}}",
            ),
            (
                f"avg by (deployment) (gpudeck_engine_speculative_accept_ratio{{{s}}})",
                "spec accept {{deployment}}",
            ),
        ],
        unit="percentunit",
    )
    b.panel(
        "timeseries",
        "Engine VRAM",
        [(f"sum by (deployment, node) (gpudeck_engine_process_vram_bytes{{{s}}})", leg)],
        unit="bytes",
    )
    b.panel(
        "timeseries",
        "Engine RSS",
        [(f"sum by (deployment, node) (gpudeck_engine_process_rss_bytes{{{s}}})", leg)],
        unit="bytes",
    )
    return b


def usage_billing() -> Board:
    b = Board(
        "gpudeck-usage",
        "GPU Deck / Usage and cost",
        "Serving API usage per API key and model (metrics.md §6).",
        ["usage", "billing"],
    )
    b.var("api_key", "API key", "label_values(gpudeck_usage_requests_total, api_key)")
    b.var("model", "Model", "label_values(gpudeck_usage_requests_total, model)")
    s = 'api_key=~"$api_key", model=~"$model"'
    b.row("Totals for the time range")
    b.panel(
        "stat",
        "Requests",
        [(f"sum(increase(gpudeck_usage_requests_total{{{s}}}[$__range]))", "")],
        w=6,
        h=4,
    )
    b.panel(
        "stat",
        "Tokens",
        [(f"sum(increase(gpudeck_usage_tokens_total{{{s}}}[$__range]))", "")],
        w=6,
        h=4,
    )
    b.panel(
        "stat",
        "GPU hours (busy share)",
        [(f"sum(increase(gpudeck_usage_gpu_seconds_total{{{s}}}[$__range])) / 3600", "")],
        w=6,
        h=4,
        decimals=2,
    )
    b.panel(
        "stat",
        "Cost",
        [(f"sum(increase(gpudeck_usage_cost_usd_total{{{s}}}[$__range]))", "")],
        unit="currencyUSD",
        w=6,
        h=4,
        decimals=2,
    )
    b.row("Over time")
    b.panel(
        "timeseries",
        "Requests / s by model",
        [(f"sum by (model) (rate(gpudeck_usage_requests_total{{{s}}}[5m]))", "{{model}}")],
        unit="reqps",
        w=12,
        stack=True,
    )
    b.panel(
        "timeseries",
        "Requests / s by status",
        [(f"sum by (status) (rate(gpudeck_usage_requests_total{{{s}}}[5m]))", "{{status}}")],
        unit="reqps",
        w=12,
        stack=True,
    )
    b.panel(
        "timeseries",
        "Tokens / s by kind",
        [(f"sum by (kind) (rate(gpudeck_usage_tokens_total{{{s}}}[5m]))", "{{kind}}")],
        w=12,
        stack=True,
    )
    b.panel(
        "timeseries",
        "Cost / hour by API key",
        [
            (
                f"sum by (api_key) (rate(gpudeck_usage_cost_usd_total{{{s}}}[1h])) * 3600",
                "{{api_key}}",
            )
        ],
        unit="currencyUSD",
        w=12,
        stack=True,
    )
    b.panel(
        "timeseries",
        "Latency p50 / p95",
        [
            (
                f"histogram_quantile(0.5, sum by (le)"
                f" (rate(gpudeck_usage_latency_seconds_bucket{{{s}}}[5m])))",
                "p50",
            ),
            (
                f"histogram_quantile(0.95, sum by (le)"
                f" (rate(gpudeck_usage_latency_seconds_bucket{{{s}}}[5m])))",
                "p95",
            ),
        ],
        unit="s",
        w=12,
    )
    b.panel(
        "timeseries",
        "Time to first token p50 / p95",
        [
            (
                f"histogram_quantile(0.5, sum by (le)"
                f" (rate(gpudeck_usage_ttft_seconds_bucket{{{s}}}[5m])))",
                "p50",
            ),
            (
                f"histogram_quantile(0.95, sum by (le)"
                f" (rate(gpudeck_usage_ttft_seconds_bucket{{{s}}}[5m])))",
                "p95",
            ),
        ],
        unit="s",
        w=12,
    )
    b.row("Top consumers")
    b.panel(
        "table",
        "Cost by API key and model (range)",
        [
            (
                "sort_desc(sum by (api_key, model) "
                f"(increase(gpudeck_usage_cost_usd_total{{{s}}}[$__range])))",
                "",
            )
        ],
        unit="currencyUSD",
        w=24,
        h=10,
    )
    return b


def control_plane() -> Board:
    b = Board(
        "gpudeck-control-plane",
        "GPU Deck / Control plane",
        "Admin API, API layer, agent hub, jobs and node agents (metrics.md §5, §7).",
        ["control-plane"],
    )
    b.var("job", "Process", 'label_values(up{job=~"gpudeck-.*"}, job)')
    b.row("Processes")
    b.panel(
        "stat",
        "Targets up",
        [('sum(up{job=~"gpudeck-.*"})', "")],
        w=4,
        h=4,
    )
    b.panel("stat", "Connected nodes", [("sum(gpudeck_hub_connected_nodes)", "")], w=4, h=4)
    b.panel(
        "stat",
        "Jobs leader",
        [("max(gpudeck_jobs_leader)", "")],
        w=4,
        h=4,
        thresholds=[(None, "red"), (1, "green")],
    )
    b.panel(
        "stat",
        "Ingest lag",
        [("max(gpudeck_hub_ingest_lag_seconds)", "")],
        unit="s",
        w=4,
        h=4,
        thresholds=[(None, "green"), (10, "orange"), (30, "red")],
    )
    b.panel(
        "stat",
        "Serving in flight",
        [("sum(gpudeck_serving_inflight)", "")],
        w=4,
        h=4,
    )
    b.panel(
        "stat",
        "Routing table version",
        [("max(gpudeck_serving_routing_version)", "")],
        w=4,
        h=4,
    )
    b.row("HTTP")
    b.panel(
        "timeseries",
        "Requests / s",
        [('sum by (job) (rate(http_requests_total{job=~"$job"}[5m]))', "{{job}}")],
        unit="reqps",
    )
    b.panel(
        "timeseries",
        "Latency p95",
        [
            (
                "histogram_quantile(0.95, sum by (le, job) "
                '(rate(http_request_duration_seconds_bucket{job=~"$job"}[5m])))',
                "{{job}}",
            )
        ],
        unit="s",
    )
    b.panel(
        "timeseries",
        "In progress",
        [('sum by (job) (http_requests_in_progress{job=~"$job"})', "{{job}}")],
    )
    b.row("Serving upstream")
    b.panel(
        "timeseries",
        "Upstream latency p95",
        [
            (
                "avg by (deployment) (gpudeck_engine_e2e_latency_seconds)",
                "engine {{deployment}}",
            ),
            (
                "histogram_quantile(0.95, sum by (le, format)"
                " (rate(gpudeck_serving_latency_seconds_bucket[5m])))",
                "{{format}}",
            ),
        ],
        unit="s",
    )
    b.panel(
        "timeseries",
        "Time to first byte p95",
        [
            (
                "histogram_quantile(0.95, sum by (le, format)"
                " (rate(gpudeck_serving_ttfb_seconds_bucket[5m])))",
                "{{format}}",
            )
        ],
        unit="s",
    )
    b.panel(
        "timeseries",
        "Requests by status / upstream failures",
        [
            ("sum by (status) (rate(gpudeck_serving_requests_total[5m]))", "{{status}}"),
            ("sum by (code) (rate(gpudeck_serving_upstream_failures_total[5m]))", "fail {{code}}"),
        ],
        unit="reqps",
    )
    b.row("Agent hub and ingest")
    b.panel(
        "timeseries",
        "Hub messages / s",
        [
            (
                "sum by (type, direction) (rate(gpudeck_hub_messages_total[5m]))",
                "{{direction}} {{type}}",
            )
        ],
    )
    b.panel(
        "timeseries",
        "Ingested rows / s",
        [("sum by (table) (rate(gpudeck_hub_ingest_rows_total[5m]))", "{{table}}")],
    )
    b.panel(
        "timeseries",
        "Connected nodes",
        [("sum(gpudeck_hub_connected_nodes)", "connected")],
    )
    b.row("Jobs: rules, swaps, Celery")
    b.panel(
        "timeseries",
        "Rule evaluations / min",
        [
            (
                "sum by (rule_type, outcome) (rate(gpudeck_rules_evaluations_total[5m])) * 60",
                "{{rule_type}} {{outcome}}",
            )
        ],
    )
    b.panel(
        "timeseries",
        "Swaps",
        [
            ("sum by (kind, state) (increase(gpudeck_swaps_total[15m]))", "{{kind}} {{state}}"),
            (
                "histogram_quantile(0.95, sum by (le)"
                " (rate(gpudeck_swap_duration_seconds_bucket[30m])))",
                "p95 duration (s)",
            ),
        ],
    )
    b.panel(
        "timeseries",
        "Celery tasks / min",
        [("sum by (task, state) (rate(celery_tasks_total[5m])) * 60", "{{task}} {{state}}")],
    )
    b.row("Node agents")
    b.panel(
        "timeseries",
        "Agent CPU",
        [
            ("gpudeck_agent_cpu_ratio", "cpu {{node}}"),
        ],
        unit="percentunit",
    )
    b.panel(
        "timeseries",
        "Agent memory",
        [("gpudeck_agent_rss_bytes", "{{node}}")],
        unit="bytes",
    )
    b.panel(
        "timeseries",
        "Agent proxy",
        [
            ("gpudeck_agent_proxy_active_connections", "connections {{node}}"),
            ("rate(gpudeck_agent_proxy_requests_total[5m])", "req/s {{node}}"),
            ("rate(gpudeck_agent_proxy_errors_total[5m])", "errors/s {{node}}"),
            ("rate(gpudeck_agent_scrape_errors_total[5m])", "scrape errors/s {{node}}"),
            ("gpudeck_agent_commands_running", "commands {{node}}"),
        ],
    )
    return b


BOARDS = {
    "gpu-fleet.json": gpu_fleet,
    "inference.json": inference,
    "usage-billing.json": usage_billing,
    "control-plane.json": control_plane,
}


# ----- validation -----------------------------------------------------------------------------


def catalog_names() -> set[str]:
    text = METRICS_MD.read_text()
    names = set(re.findall(r"`(gpudeck_[a-z0-9_]+)[`{ ]", text))
    # Shorthand pairs in §1: "`gpudeck_gpu_encoder_ratio`, `_decoder_ratio`" and "tx/rx".
    for full, short in re.findall(r"`(gpudeck_[a-z0-9_]+)`, `(_[a-z0-9_]+)`", text):
        stem = full.rsplit("_", 2)[0]
        names.add(stem + short)
    names.update(n.replace("_tx_", "_rx_") for n in list(names) if "_tx_" in n)
    # §5 lists node/agent metrics by canonical name with the generic "gpudeck_node_*" /
    # "gpudeck_agent_*" mapping; apply the exporter's naming rules (_pct -> _ratio).
    section5 = text.split("## 5.", 1)[1].split("## 6.", 1)[0]
    canon = set(re.findall(r"`(node_[a-z0-9_]+)", section5))
    agent = re.search(r"`agent_\*` \(([^)]*)\)", section5)
    if agent:
        canon.update("agent_" + n for n in re.findall(r"`([a-z0-9_]+)`", agent.group(1)))
    names.update("gpudeck_" + re.sub(r"_pct$", "_ratio", n) for n in canon)
    names.add("gpudeck_node_up")  # fleet exporter: 1 while the node heartbeats
    return names


def used_names(board: dict) -> set[str]:
    exprs = [t["expr"] for p in board["panels"] for t in p.get("targets", [])]
    exprs += [v["definition"] for v in board["templating"]["list"] if "definition" in v]
    found = set()
    for e in exprs:
        found.update(re.findall(r"\b(gpudeck_[a-z0-9_]+)", e))
    return {re.sub(r"_(bucket|sum|count)$", "", n) for n in found}


def sync(paths: list[Path], text: str, check: bool) -> int:
    """Write text to every path, or with check=True report stale copies."""
    status = 0
    for path in paths:
        if check:
            if not path.exists() or path.read_text() != text:
                rel = path.relative_to(ROOT)
                print(f"{rel} is stale; run {Path(__file__).name}", file=sys.stderr)
                status = 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            print(f"wrote {path.relative_to(ROOT)}")
    return status


def main() -> int:
    check = "--check" in sys.argv[1:]
    known = catalog_names() | SERVING_PROCESS_METRICS
    status = 0
    for fname, build in BOARDS.items():
        board = build().render()
        unknown = sorted(used_names(board) - known)
        if unknown:
            print(f"{fname}: metrics not in metrics.md: {unknown}", file=sys.stderr)
            status = 1
        text = json.dumps(board, indent=2, ensure_ascii=False) + "\n"
        targets = [OUT / fname, CHART_FILES / "dashboards" / fname]
        status |= sync(targets, text, check)
    status |= sync([CHART_FILES / "prometheus" / ALERT_RULES.name], ALERT_RULES.read_text(), check)
    return status


if __name__ == "__main__":
    sys.exit(main())
