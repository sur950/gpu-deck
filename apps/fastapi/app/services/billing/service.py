"""
Billing: price plans, cost reports and the live month-to-date estimate.

- ``PricePlanService``: CRUD for ``price_plans`` (one default plan; soft
  delete detaches the plan from clusters and nodes). Attaching a plan is done
  on the cluster / node (the cluster / node endpoints set ``price_plan_id``).
- ``BillingService``: reports over ``usage_rollups_hourly`` priced with the
  *current* plans (``regenerate`` re-prices), grouped by api_key / model /
  node / cluster / deployment, with a cost breakdown (GPU time, input,
  output, cached tokens); CSV export; month-to-date estimate with a linear
  projection.

GPU Deck identifies and shows costs only (product decision 2026-11-18): there
are no budgets, thresholds or budget alerts.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from app.core.exceptions import (
    DuplicateResourceError,
    ResourceNotFoundError,
    ValidationError,
)
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_db
from app.db.postgres.models.generated_models import BillingReports, PricePlans
from app.db.postgres.repositories.billing_report_repository import (
    BillingReportRepository,
)
from app.db.postgres.repositories.price_plan_repository import PricePlanRepository
from app.db.postgres.repositories.usage_rollup_repository import UsageRollupRepository
from app.services.audit.service import audit
from app.services.billing.pricing import (
    PlanResolver,
    gpu_cost,
    invalidate_resolver,
    token_costs,
)
from fastapi import Depends, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

REPORT_GROUPS = ("api_key", "model", "node", "cluster", "deployment")
FILTER_KEYS = {
    "api_key_ids": "api_key_id",
    "model_ids": "model_id",
    "node_ids": "node_id",
    "cluster_ids": "cluster_id",
    "deployment_ids": "deployment_id",
}


def month_bounds(at: datetime) -> Tuple[datetime, datetime]:
    at = at.astimezone(timezone.utc)
    start = at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def _f(value: Any, digits: int = 8) -> float:
    return round(float(value or 0), digits)


# ==========================================
# Price plans
# ==========================================


class PricePlanService:
    def __init__(self, repo: PricePlanRepository) -> None:
        self.repo = repo

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db)
    ) -> "PricePlanService":
        return PricePlanService(PricePlanRepository(db))

    @staticmethod
    def to_dict(plan: PricePlans) -> Dict[str, Any]:
        return {
            "id": plan.id,
            "name": plan.name,
            "description": plan.description,
            "currency": (plan.currency or "USD").strip(),
            "usd_per_gpu_minute": float(plan.usd_per_gpu_minute or 0),
            "usd_per_1k_input_tokens": float(plan.usd_per_1k_input_tokens or 0),
            "usd_per_1k_output_tokens": float(plan.usd_per_1k_output_tokens or 0),
            "usd_per_1k_cached_tokens": float(plan.usd_per_1k_cached_tokens or 0),
            "is_default": bool(plan.is_default),
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
        }

    async def list_plans(
        self, page: int, page_size: int, search: Optional[str], sort: Optional[str]
    ):
        rows, total = await self.repo.list_plans(page, page_size, search, sort)
        return [self.to_dict(r) for r in rows], total

    async def _get(self, plan_id: uuid.UUID) -> PricePlans:
        plan = await self.repo.get_active(plan_id)
        if plan is None:
            raise ResourceNotFoundError(resource_type="Price plan")
        return plan

    async def get_plan(self, plan_id: uuid.UUID) -> Dict[str, Any]:
        return self.to_dict(await self._get(plan_id))

    async def create_plan(self, data: Dict[str, Any], created_by: Any = None) -> Dict[str, Any]:
        if await self.repo.name_taken(data["name"]):
            raise DuplicateResourceError(resource_type="Price plan", field="name")
        if data.get("is_default"):
            await self.repo.clear_default()
        try:
            plan = await self.repo.create(
                **data, currency=settings.BILLING_CURRENCY[:3], created_by=created_by
            )
        except IntegrityError:
            raise DuplicateResourceError(resource_type="Price plan", field="name")
        invalidate_resolver()
        result = self.to_dict(plan)
        audit.record(
            "price_plan.create",
            resource_type="price_plan",
            resource_id=plan.id,
            resource_name=plan.name,
            after_state=result,
        )
        return result

    async def update_plan(self, plan_id: uuid.UUID, data: Dict[str, Any]) -> Dict[str, Any]:
        plan = await self._get(plan_id)
        before = self.to_dict(plan)
        changes = {k: v for k, v in data.items() if v is not None or k == "description"}
        if "name" in changes and await self.repo.name_taken(changes["name"], exclude_id=plan_id):
            raise DuplicateResourceError(resource_type="Price plan", field="name")
        if changes.get("is_default"):
            await self.repo.clear_default(except_id=plan_id)
        try:
            plan = await self.repo.update(plan_id, **changes)
        except IntegrityError:
            raise DuplicateResourceError(resource_type="Price plan", field="name")
        invalidate_resolver()
        after = self.to_dict(plan)
        audit.record(
            "price_plan.update",
            resource_type="price_plan",
            resource_id=plan_id,
            resource_name=plan.name,
            before_state=before,
            after_state=after,
        )
        return after

    async def delete_plan(self, plan_id: uuid.UUID) -> None:
        plan = await self._get(plan_id)
        detached = await self.repo.soft_delete(plan_id)
        invalidate_resolver()
        audit.record(
            "price_plan.delete",
            resource_type="price_plan",
            resource_id=plan_id,
            resource_name=plan.name,
            before_state=self.to_dict(plan),
            metadata={"detached": detached},
        )


# ==========================================
# Reports and estimate
# ==========================================


def report_filters(
    period_start: datetime, period_end: datetime, filters: Dict[str, Any]
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"from": period_start, "to": period_end}
    for public, column in FILTER_KEYS.items():
        values = filters.get(public) or []
        if isinstance(values, (str, uuid.UUID)):
            values = [values]
        parsed = []
        for v in values:
            try:
                parsed.append(uuid.UUID(str(v)))
            except ValueError:
                raise ValidationError(f"filters.{public}: '{v}' is not a uuid")
        if parsed:
            out[column] = parsed
    return out


def _empty_totals() -> Dict[str, Any]:
    return {
        "request_count": 0,
        "success_count": 0,
        "error_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "media_units": 0.0,
        "gpu_seconds": 0.0,
        "gpu_minutes": 0.0,
        "latency_ms_avg": None,
        "ttft_ms_avg": None,
        "cost_usd": 0.0,
    }


def price_lines(
    lines: List[Dict[str, Any]], group_by: List[str], resolver: PlanResolver
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Price rollup sums with the current plans; returns (line_items, report totals)."""
    acc: Dict[tuple, Dict[str, Any]] = {}
    for line in lines:
        raw = line["raw"]
        plan = resolver.resolve(line["node_id"])
        costs = token_costs(
            plan,
            int(raw.get("input_tokens") or 0),
            int(raw.get("output_tokens") or 0),
            int(raw.get("cached_tokens") or 0),
        )
        gpu_minutes = Decimal(str(raw.get("gpu_seconds") or 0)) / Decimal(60)
        g_cost = gpu_cost(plan, gpu_minutes)
        key = tuple("" if v is None else str(v) for v in line["group"])
        entry = acc.get(key)
        if entry is None:
            labels = {}
            for idx, k in enumerate(group_by):
                label = line["labels"][idx]
                labels[k] = (
                    "unallocated"
                    if k == "api_key" and line["group"][idx] is None
                    else (label or "")
                )
            entry = acc[key] = {
                "group": dict(zip(group_by, key)),
                "labels": labels,
                "plans": set(),
                "sums": defaultdict(Decimal),
                "lat": Decimal(0),
                "ttft": Decimal(0),
            }
        if plan is not None:
            entry["plans"].add((plan.id, plan.name))
        else:
            entry["plans"].add((None, None))
        sums = entry["sums"]
        n = Decimal(int(raw.get("request_count") or 0))
        for field in (
            "request_count",
            "success_count",
            "error_count",
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "reasoning_tokens",
        ):
            sums[field] += Decimal(int(raw.get(field) or 0))
        sums["media_units"] += Decimal(str(raw.get("media_units") or 0))
        sums["gpu_seconds"] += Decimal(str(raw.get("gpu_seconds") or 0))
        sums["gpu_usd"] += g_cost
        sums["input_usd"] += costs["input"]
        sums["output_usd"] += costs["output"]
        sums["cached_usd"] += costs["cached"]
        if raw.get("latency_ms_avg") is not None:
            entry["lat"] += Decimal(str(raw["latency_ms_avg"])) * n
        if raw.get("ttft_ms_avg") is not None:
            entry["ttft"] += Decimal(str(raw["ttft_ms_avg"])) * n
    items = []
    report = _empty_totals()
    lat_total = ttft_total = Decimal(0)
    for entry in acc.values():
        s = entry["sums"]
        requests = int(s["request_count"])
        cost = s["gpu_usd"] + s["input_usd"] + s["output_usd"] + s["cached_usd"]
        totals = {
            "request_count": requests,
            "success_count": int(s["success_count"]),
            "error_count": int(s["error_count"]),
            "input_tokens": int(s["input_tokens"]),
            "output_tokens": int(s["output_tokens"]),
            "cached_tokens": int(s["cached_tokens"]),
            "reasoning_tokens": int(s["reasoning_tokens"]),
            "media_units": _f(s["media_units"], 3),
            "gpu_seconds": _f(s["gpu_seconds"], 3),
            "gpu_minutes": _f(s["gpu_seconds"] / 60, 4),
            "latency_ms_avg": _f(entry["lat"] / requests, 2) if requests else None,
            "ttft_ms_avg": _f(entry["ttft"] / requests, 2) if requests else None,
            "cost_usd": _f(cost),
        }
        plans = entry["plans"]
        plan_id, plan_name = next(iter(plans)) if len(plans) == 1 else (None, None)
        items.append(
            {
                "group": entry["group"],
                "labels": entry["labels"],
                "price_plan_id": plan_id,
                "price_plan_name": plan_name,
                "totals": totals,
                "cost_breakdown": {
                    "gpu_time_usd": _f(s["gpu_usd"]),
                    "input_tokens_usd": _f(s["input_usd"]),
                    "output_tokens_usd": _f(s["output_usd"]),
                    "cached_tokens_usd": _f(s["cached_usd"]),
                },
            }
        )
        for field in (
            "request_count",
            "success_count",
            "error_count",
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "reasoning_tokens",
        ):
            report[field] += totals[field]
        for field in ("media_units", "gpu_seconds", "gpu_minutes", "cost_usd"):
            report[field] = round(report[field] + totals[field], 8)
        lat_total += entry["lat"]
        ttft_total += entry["ttft"]
    if report["request_count"]:
        report["latency_ms_avg"] = _f(lat_total / report["request_count"], 2)
        report["ttft_ms_avg"] = _f(ttft_total / report["request_count"], 2)
    items.sort(key=lambda i: i["totals"]["cost_usd"], reverse=True)
    return items, report


class BillingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.reports = BillingReportRepository(session)
        self.rollups = UsageRollupRepository(session)
        self.plans = PricePlanRepository(session)

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db)
    ) -> "BillingService":
        return BillingService(db)

    @staticmethod
    def report_to_dict(report: BillingReports, with_lines: bool = True) -> Dict[str, Any]:
        totals = report.totals or None
        return {
            "id": report.id,
            "name": report.name,
            "period_start": report.period_start,
            "period_end": report.period_end,
            "group_by": list(report.group_by or []),
            "filters": report.filters or {},
            "status": report.status,
            "currency": (report.currency or "USD").strip(),
            "totals": totals if totals else None,
            "line_items": list(report.line_items or []) if with_lines else [],
            "generated_at": report.generated_at,
            "error_message": report.error_message,
            "created_at": report.created_at,
            "updated_at": report.updated_at,
        }

    async def _get(self, report_id: uuid.UUID) -> BillingReports:
        report = await self.reports.get_active(report_id)
        if report is None:
            raise ResourceNotFoundError(resource_type="Billing report")
        return report

    async def list_reports(self, page, page_size, search=None, sort=None, status=None):
        rows, total = await self.reports.list_reports(page, page_size, search, sort, status)
        return [self.report_to_dict(r, with_lines=False) for r in rows], total

    async def get_report(self, report_id: uuid.UUID) -> Dict[str, Any]:
        return self.report_to_dict(await self._get(report_id))

    async def create_report(self, data: Dict[str, Any], created_by: Any = None) -> Dict[str, Any]:
        group_by = [g.value if hasattr(g, "value") else str(g) for g in data.get("group_by") or []]
        group_by = [g for g in dict.fromkeys(group_by) if g in REPORT_GROUPS] or [
            "api_key",
            "model",
        ]
        report_filters(data["period_start"], data["period_end"], data.get("filters") or {})
        report = await self.reports.create(
            name=data["name"],
            period_start=data["period_start"],
            period_end=data["period_end"],
            group_by=group_by,
            filters=data.get("filters") or {},
            status="pending",
            currency=settings.BILLING_CURRENCY[:3],
            created_by=created_by,
        )
        audit.record(
            "billing_report.create",
            resource_type="billing_report",
            resource_id=report.id,
            resource_name=report.name,
            after_state={
                "period_start": report.period_start,
                "period_end": report.period_end,
                "group_by": group_by,
            },
        )
        return self.report_to_dict(report)

    async def mark_regenerate(self, report_id: uuid.UUID) -> Dict[str, Any]:
        report = await self._get(report_id)
        report = await self.reports.update(report.id, status="pending", error_message=None)
        audit.record(
            "billing_report.regenerate",
            resource_type="billing_report",
            resource_id=report.id,
            resource_name=report.name,
        )
        return self.report_to_dict(report)

    async def delete_report(self, report_id: uuid.UUID) -> None:
        report = await self._get(report_id)
        await self.reports.soft_delete(report.id)
        audit.record(
            "billing_report.delete",
            resource_type="billing_report",
            resource_id=report.id,
            resource_name=report.name,
        )

    async def generate(self, report_id: uuid.UUID) -> Dict[str, Any]:
        """Compute line items now (jobs process or inline); status -> ready | failed."""
        report = await self._get(report_id)
        await self.reports.update(report.id, status="generating", error_message=None)
        try:
            group_by = list(report.group_by or ["api_key", "model"])
            filters = report_filters(report.period_start, report.period_end, report.filters or {})
            lines = await self.rollups.priced_lines(filters, group_by)
            resolver = PlanResolver(await self.plans.pricing_snapshot())
            items, totals = price_lines(lines, group_by, resolver)
            report = await self.reports.update(
                report.id,
                status="ready",
                totals=totals,
                line_items=json.loads(json.dumps(items, default=str)),
                generated_at=datetime.now(timezone.utc),
                currency=settings.BILLING_CURRENCY[:3],
            )
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).error(f"billing report {report_id} failed")
            report = await self.reports.update(
                report.id, status="failed", error_message=str(exc)[:2000]
            )
        return self.report_to_dict(report)

    async def export_csv(self, report_id: uuid.UUID) -> Tuple[str, str]:
        report = await self._get(report_id)
        if report.status != "ready":
            from app.core.exceptions import ConflictError

            raise ConflictError(f"report is {report.status}; export is available once it is ready")
        group_by = list(report.group_by or [])
        buf = io.StringIO()
        writer = csv.writer(buf)
        total_fields = [
            "request_count",
            "success_count",
            "error_count",
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "reasoning_tokens",
            "media_units",
            "gpu_minutes",
            "cost_usd",
        ]
        breakdown = [
            "gpu_time_usd",
            "input_tokens_usd",
            "output_tokens_usd",
            "cached_tokens_usd",
        ]
        writer.writerow(
            [
                *group_by,
                *(f"{g}_name" for g in group_by),
                "price_plan",
                *total_fields,
                *breakdown,
                "currency",
            ]
        )
        currency = (report.currency or "USD").strip()
        for item in report.line_items or []:
            t = item.get("totals") or {}
            b = item.get("cost_breakdown") or {}
            writer.writerow(
                [
                    *(item.get("group", {}).get(g, "") for g in group_by),
                    *(item.get("labels", {}).get(g, "") for g in group_by),
                    item.get("price_plan_name") or "",
                ]
                + [t.get(f, 0) for f in total_fields]
                + [b.get(f, 0) for f in breakdown]
                + [currency]
            )
        t = report.totals or {}
        writer.writerow(
            ["TOTAL", *([""] * (2 * len(group_by))), ""][: 2 * len(group_by) + 1]
            + [t.get(f, 0) for f in total_fields]
            + [""] * len(breakdown)
            + [currency]
        )
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in report.name)[:80] or "report"
        return buf.getvalue(), f"billing-{safe}-{report.period_start:%Y%m%d}.csv"

    async def estimate(
        self,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
        cluster_id: Optional[uuid.UUID] = None,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        default_start, default_end = month_bounds(now)
        start = period_start or default_start
        end = period_end or default_end
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if end <= start:
            raise ValidationError("period_end must be after period_start")
        filters: Dict[str, Any] = {"from": start, "to": end}
        if cluster_id:
            filters["cluster_id"] = cluster_id
        totals = await self.rollups.totals(filters)
        elapsed = (min(now, end) - start).total_seconds()
        span = (end - start).total_seconds()
        projected = (
            totals["cost_usd"] * span / elapsed if elapsed > 0 and now < end else totals["cost_usd"]
        )
        return {
            "period_start": start,
            "period_end": end,
            "as_of": now,
            "totals": totals,
            "projected_cost_usd": round(projected, 6),
            "by_api_key": await self.rollups.grouped(filters, ["api_key"]),
            "by_model": await self.rollups.grouped(filters, ["model"]),
            "by_node": await self.rollups.grouped(filters, ["node"]),
        }


# ==========================================
# Jobs: generation, monthly reports
# ==========================================


async def generate_report(session: AsyncSession, report_id: str) -> Dict[str, Any]:
    service = BillingService(session)
    result = await service.generate(uuid.UUID(str(report_id)))
    await session.commit()
    return result


async def ensure_monthly_report(
    session: AsyncSession, now: Optional[datetime] = None
) -> Optional[str]:
    """On BILLING_MONTHLY_REPORT_DAY, create + generate last month's report once."""
    now = now or datetime.now(timezone.utc)
    day = int(settings.BILLING_MONTHLY_REPORT_DAY or 0)
    if day <= 0 or now.day != day:
        return None
    this_month, _ = month_bounds(now)
    last_start, last_end = month_bounds(this_month - timedelta(days=1))
    name = f"Monthly {last_start:%Y-%m}"
    repo = BillingReportRepository(session)
    if await repo.find_period(name, last_start, last_end) is not None:
        return None
    service = BillingService(session)
    created = await service.create_report(
        {
            "name": name,
            "period_start": last_start,
            "period_end": last_end,
            "group_by": ["api_key", "model"],
            "filters": {},
        }
    )
    await session.commit()
    await service.generate(created["id"])
    await session.commit()
    return str(created["id"])
