"""
Per-request timing breakdown and the ``Server-Timing`` response header (serving.md §10).

Phases, in milliseconds (``ServingContext.timing``):

========== =================================================================================
route      routing decision in the API layer (model lookup + replica selection)
queue      waiting in the API layer before the request that answered went out: no ready
           replica yet (load on demand) and failed attempts that were retried elsewhere
connect    opening the upstream connection for that request (0 on a warm connection)
rtt        smoothed network round trip from this API layer to the chosen node (probed)
net        this request's network time: upstream wait minus the time the node reported
engine     the engine's time to its response headers, measured on the node
upstream   request headers written -> response headers read, measured in the API layer
ttft       first response byte (streaming: first token) after the request arrived
total      the whole request in the API layer (non-streaming responses only)
========== =================================================================================

The node agent's proxy reports its side in ``x-gpudeck-proxy-timing``
(``queue;dur=..., engine;dur=..., node;dur=...``); the API layer reads and removes it.

``SERVING_SERVER_TIMING``: ``off``, ``generic`` (default: the phase names above with
``dur`` only, nothing that identifies a host) or ``detailed`` (adds ``edge;desc=<API-layer
region>`` and ``node;desc=<node name>``; for trusted callers only).
"""

from __future__ import annotations

from typing import Dict, List, Optional

from app.core.logger import get_logger
from app.core.settings import settings

logger = get_logger(__name__)

PROXY_TIMING_HEADER = "x-gpudeck-proxy-timing"
HEADER_PHASES = ("route", "queue", "connect", "rtt", "net", "engine", "upstream", "total")
RECORD_PHASES = HEADER_PHASES[:-1] + ("ttft", "total")
MODES = ("off", "generic", "detailed")


def mode() -> str:
    value = str(settings.SERVING_SERVER_TIMING or "generic").strip().lower()
    return value if value in MODES else "generic"


def parse_proxy_timing(value: Optional[str]) -> Dict[str, float]:
    """``queue;dur=0.1, engine;dur=12.5, node;dur=12.9`` -> ``{"queue": 0.1, ...}``."""
    out: Dict[str, float] = {}
    if not value:
        return out
    for item in str(value).split(","):
        parts = [p.strip() for p in item.split(";")]
        name = parts[0].lower() if parts else ""
        if not name:
            continue
        for param in parts[1:]:
            key, _, raw = param.partition("=")
            if key.strip().lower() != "dur":
                continue
            try:
                dur = float(raw.strip().strip('"'))
            except ValueError:
                continue
            if dur >= 0:
                out[name] = dur
    return out


def _fmt(value: float) -> str:
    return f"{max(0.0, float(value)):.1f}"


def _quote(text: str) -> str:
    safe = "".join(ch for ch in str(text) if 0x20 <= ord(ch) < 0x7F and ch not in '"\\')
    return f'"{safe[:64]}"'


def server_timing(
    timing: Dict[str, float],
    *,
    include_total: bool,
    region: Optional[str] = None,
    node_name: Optional[str] = None,
) -> Optional[str]:
    """The ``Server-Timing`` value for the phases known so far (None when off or empty)."""
    level = mode()
    if level == "off":
        return None
    items: List[str] = []
    for phase in HEADER_PHASES:
        if phase == "total" and not include_total:
            continue
        value = timing.get(f"{phase}_ms")
        if value is None:
            continue
        items.append(f"{phase};dur={_fmt(value)}")
    if level == "detailed":
        if region:
            items.append(f"edge;desc={_quote(region)}")
        if node_name:
            items.append(f"node;desc={_quote(node_name)}")
    return ", ".join(items) or None


def apply_proxy_timing(timing: Dict[str, float], reported: Dict[str, float]) -> None:
    """Fold the node's report into the request timing (engine, net)."""
    if "engine" in reported:
        timing["engine_ms"] = reported["engine"]
    if "queue" in reported:
        timing["node_queue_ms"] = reported["queue"]
    node_total = reported.get("node")
    upstream = timing.get("upstream_ms")
    if node_total is not None and upstream is not None:
        timing["net_ms"] = max(0.0, upstream - node_total)


def record(timing: Dict[str, float]) -> Dict[str, float]:
    """The breakdown stored with the request (usage event metadata, trace attributes)."""
    out = {}
    for key in (*(f"{p}_ms" for p in RECORD_PHASES), "node_queue_ms"):
        value = timing.get(key)
        if value is not None:
            out[key] = round(float(value), 3)
    return out
