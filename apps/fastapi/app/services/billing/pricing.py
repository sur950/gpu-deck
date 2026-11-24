"""
Price plan resolution and cost arithmetic (a node's plan overrides its cluster's;
billable GPU time comes from instance uptime).

Plan for a request / an instance: the node's plan, else its cluster's plan,
else the default plan, else none (on-prem, cost 0). Deleted plans are
ignored.

Token cost: uncached input at ``usd_per_1k_input_tokens``, cached input at
``usd_per_1k_cached_tokens``, output at ``usd_per_1k_output_tokens``.
(``input_tokens`` already includes the cached part, so the cached tokens are
not charged twice.) GPU time is billed from instance uptime minutes by the
metering rollup, never per request.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional

from app.core.logger import get_logger

logger = get_logger(__name__)

_THOUSAND = Decimal(1000)


def _dec(value: Any) -> Decimal:
    if value is None:
        return Decimal(0)
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    currency: str
    usd_per_gpu_minute: Decimal
    usd_per_1k_input_tokens: Decimal
    usd_per_1k_output_tokens: Decimal
    usd_per_1k_cached_tokens: Decimal
    is_default: bool = False

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Plan":
        return cls(
            id=str(row["id"]),
            name=str(row.get("name") or ""),
            currency=str(row.get("currency") or "USD").strip(),
            usd_per_gpu_minute=_dec(row.get("usd_per_gpu_minute")),
            usd_per_1k_input_tokens=_dec(row.get("usd_per_1k_input_tokens")),
            usd_per_1k_output_tokens=_dec(row.get("usd_per_1k_output_tokens")),
            usd_per_1k_cached_tokens=_dec(row.get("usd_per_1k_cached_tokens")),
            is_default=bool(row.get("is_default")),
        )


def token_costs(
    plan: Optional[Plan], input_tokens: int, output_tokens: int, cached_tokens: int
) -> Dict[str, Decimal]:
    """Per-token-type USD cost (``input`` = uncached input only)."""
    if plan is None:
        zero = Decimal(0)
        return {"input": zero, "output": zero, "cached": zero}
    cached = max(0, int(cached_tokens or 0))
    uncached = max(0, int(input_tokens or 0) - cached)
    return {
        "input": Decimal(uncached) * plan.usd_per_1k_input_tokens / _THOUSAND,
        "output": Decimal(max(0, int(output_tokens or 0)))
        * plan.usd_per_1k_output_tokens
        / _THOUSAND,
        "cached": Decimal(cached) * plan.usd_per_1k_cached_tokens / _THOUSAND,
    }


def gpu_cost(plan: Optional[Plan], gpu_minutes: Any) -> Decimal:
    if plan is None:
        return Decimal(0)
    return _dec(gpu_minutes) * plan.usd_per_gpu_minute


class PlanResolver:
    """In-memory node/cluster -> plan map built from ``PricePlanRepository.pricing_snapshot``."""

    def __init__(self, snapshot: Dict[str, Any]) -> None:
        self.plans: Dict[str, Plan] = {
            str(p["id"]): Plan.from_row(p) for p in snapshot.get("plans") or []
        }
        self.default: Optional[Plan] = next((p for p in self.plans.values() if p.is_default), None)
        self.node_plan: Dict[str, Optional[str]] = {}
        self.node_cluster: Dict[str, Optional[str]] = {}
        for n in snapshot.get("nodes") or []:
            nid = str(n["id"])
            self.node_plan[nid] = str(n["price_plan_id"]) if n.get("price_plan_id") else None
            self.node_cluster[nid] = str(n["cluster_id"]) if n.get("cluster_id") else None
        self.cluster_plan: Dict[str, Optional[str]] = {
            str(c["id"]): (str(c["price_plan_id"]) if c.get("price_plan_id") else None)
            for c in snapshot.get("clusters") or []
        }

    def cluster_of(self, node_id: Optional[str]) -> Optional[str]:
        return self.node_cluster.get(str(node_id)) if node_id else None

    def resolve(
        self, node_id: Optional[str] = None, cluster_id: Optional[str] = None
    ) -> Optional[Plan]:
        if node_id:
            pid = self.node_plan.get(str(node_id))
            if pid and pid in self.plans:
                return self.plans[pid]
            cluster_id = cluster_id or self.node_cluster.get(str(node_id))
        if cluster_id:
            pid = self.cluster_plan.get(str(cluster_id))
            if pid and pid in self.plans:
                return self.plans[pid]
        return self.default


_cache: Dict[str, Any] = {"at": 0.0, "resolver": None}
RESOLVER_TTL_SECONDS = 60.0


async def load_resolver(session, *, max_age: float = RESOLVER_TTL_SECONDS) -> PlanResolver:
    """Process-wide resolver, rebuilt at most every ``max_age`` seconds."""
    from app.db.postgres.repositories.price_plan_repository import PricePlanRepository

    now = time.monotonic()
    cached = _cache.get("resolver")
    if cached is not None and now - float(_cache["at"]) < max_age:
        return cached
    resolver = PlanResolver(await PricePlanRepository(session).pricing_snapshot())
    _cache.update(at=now, resolver=resolver)
    return resolver


def invalidate_resolver() -> None:
    _cache.update(at=0.0, resolver=None)
