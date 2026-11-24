"""
Usage event writer — runs in the jobs process (Celery queue ``metering``),
never on the request path.

Turns the payload built by ``record_request`` into one ``usage_events`` row:
resolves the price plan (node, else cluster, else default), prices the tokens
and computes the informational GPU busy share (wall time x gpu_count x slice / concurrent
requests; informational, billing uses instance uptime)::

    gpu_seconds = latency_s * gpu_count * slice_pct/100 / concurrent_requests_at_start

Billable GPU minutes come from instance uptime in the hourly rollup
(``services/metering/rollup.py``), so ``cost_usd`` here is the token cost.
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from app.core.logger import get_logger
from app.db.postgres.repositories.usage_event_repository import UsageEventRepository
from app.services.billing.pricing import load_resolver, token_costs
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

_STATUSES = {"success", "error", "timeout", "cancelled"}
_FORMATS = {"openai", "anthropic", "ollama"}


def _uuid(value: Any) -> Optional[uuid.UUID]:
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _ip(value: Any) -> Optional[str]:
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(str(value).split("%")[0]))
    except ValueError:
        return None


def _ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        ts = value
    else:
        try:
            ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            ts = datetime.now(timezone.utc)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def busy_gpu_seconds(
    latency_ms: Optional[int],
    gpu_count: int,
    slice_compute_pct: Optional[int],
    concurrency_at_start: int,
) -> Decimal:
    """Request's share of the instance's GPUs while it ran (informational)."""
    if not latency_ms or latency_ms <= 0:
        return Decimal(0)
    pct = Decimal(slice_compute_pct or 100) / Decimal(100)
    concurrent = max(1, int(concurrency_at_start or 0) + 1)
    value = Decimal(latency_ms) / Decimal(1000) * Decimal(max(1, gpu_count or 1)) * pct
    return (value / Decimal(concurrent)).quantize(Decimal("0.001"))


async def build_event_row(session: AsyncSession, payload: Dict[str, Any]) -> Dict[str, Any]:
    resolver = await load_resolver(session)
    node_id = _uuid(payload.get("node_id"))
    cluster_id = _uuid(payload.get("cluster_id"))
    if cluster_id is None and node_id is not None:
        cluster_id = _uuid(resolver.cluster_of(str(node_id)))
    plan = resolver.resolve(
        str(node_id) if node_id else None, str(cluster_id) if cluster_id else None
    )
    input_tokens = max(0, int(payload.get("input_tokens") or 0))
    output_tokens = max(0, int(payload.get("output_tokens") or 0))
    cached_tokens = max(0, int(payload.get("cached_tokens") or 0))
    costs = token_costs(plan, input_tokens, output_tokens, cached_tokens)
    endpoint = str(payload.get("endpoint") or "")
    if endpoint == "messages/count_tokens":
        costs = {k: Decimal(0) for k in costs}
    cost = sum(costs.values(), Decimal(0))
    gpu_count = max(1, int(payload.get("gpu_count") or 1))
    concurrency = int(payload.get("concurrency_at_start") or 0)
    metadata = dict(payload.get("metadata") or {})
    metadata["cost"] = {k: float(v) for k, v in costs.items()}
    if concurrency:
        metadata["concurrency_at_start"] = concurrency
    status = str(payload.get("status") or "success")
    fmt = str(payload.get("format") or "openai")
    return {
        "ts": _ts(payload.get("ts")),
        "request_id": _uuid(payload.get("request_id")) or uuid.uuid4(),
        "api_key_id": _uuid(payload.get("api_key_id")),
        "model_id": _uuid(payload.get("model_id")),
        "model_name": str(payload.get("model_name") or "unknown")[:255],
        "deployment_id": _uuid(payload.get("deployment_id")),
        "instance_id": _uuid(payload.get("instance_id")),
        "node_id": node_id,
        "cluster_id": cluster_id,
        "format": fmt if fmt in _FORMATS else "openai",
        "endpoint": endpoint[:100] or "unknown",
        "stream": bool(payload.get("stream")),
        "request_count": max(1, int(payload.get("request_count") or 1)),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "reasoning_tokens": max(0, int(payload.get("reasoning_tokens") or 0)),
        "media_units": Decimal(str(payload.get("media_units") or 0)).quantize(Decimal("0.001")),
        "latency_ms": payload.get("latency_ms"),
        "ttft_ms": payload.get("ttft_ms"),
        "gpu_seconds": busy_gpu_seconds(
            payload.get("latency_ms"),
            gpu_count,
            payload.get("slice_compute_pct"),
            concurrency,
        ),
        "gpu_count": gpu_count,
        "status": status if status in _STATUSES else "error",
        "http_status": payload.get("http_status"),
        "error_code": (str(payload["error_code"])[:100] if payload.get("error_code") else None),
        "price_plan_id": _uuid(plan.id) if plan else None,
        "cost_usd": cost.quantize(Decimal("0.00000001")),
        "ip_address": _ip(payload.get("ip_address")),
        "metadata": metadata or None,
    }


async def write_usage_event(session: AsyncSession, payload: Dict[str, Any]) -> int:
    row = await build_event_row(session, payload)
    return await UsageEventRepository(session).insert(row)
