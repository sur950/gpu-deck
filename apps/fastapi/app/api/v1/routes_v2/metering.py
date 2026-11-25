"""
Metering routes (admin.yaml: Metering tag).

GET /usage           raw usage events (drill-down), paginated
GET /usage/summary   totals grouped by key/model/node/deployment/cluster/format/endpoint/day/hour
GET /usage/export    CSV of events or of a summary

Summaries come from ``usage_rollups_hourly`` (billable GPU minutes incl. the
``unallocated`` bucket) unless a format/status/endpoint filter or grouping
needs the raw events.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from app.core.logger import get_logger
from app.middlewares.session_verification import get_current_admin
from app.schemas.metering_schema import (
    UsageEvent,
    UsageFormat,
    UsageGroupBy,
    UsageStatus,
    UsageSummary,
)
from app.services.metering.service import MeteringService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import Response

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Metering"]


def _filters(
    service: MeteringService,
    from_ts: Optional[datetime],
    to_ts: Optional[datetime],
    **kwargs,
):
    start, end = service.default_range(from_ts, to_ts)
    out = {"from": start, "to": end}
    for key, value in kwargs.items():
        if value is not None:
            out[key] = value.value if hasattr(value, "value") else value
    return out


@router.get("/usage", summary="Raw usage events (drill-down)")
@handle_exceptions
async def list_usage_events(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: Optional[str] = Query(None),
    from_ts: Optional[datetime] = Query(None, alias="from"),
    to_ts: Optional[datetime] = Query(None, alias="to"),
    api_key_id: Optional[uuid.UUID] = Query(None),
    model_id: Optional[uuid.UUID] = Query(None),
    node_id: Optional[uuid.UUID] = Query(None),
    deployment_id: Optional[uuid.UUID] = Query(None),
    cluster_id: Optional[uuid.UUID] = Query(None),
    format: Optional[UsageFormat] = Query(None),
    status: Optional[UsageStatus] = Query(None),
    request_id: Optional[uuid.UUID] = Query(None),
    endpoint: Optional[str] = Query(None),
    admin=Depends(get_current_admin),
    service: MeteringService = Depends(MeteringService.as_dependency),
):
    filters = _filters(
        service,
        from_ts,
        to_ts,
        api_key_id=api_key_id,
        model_id=model_id,
        node_id=node_id,
        deployment_id=deployment_id,
        cluster_id=cluster_id,
        format=format,
        status=status,
        request_id=request_id,
        endpoint=endpoint,
    )
    rows, total = await service.list_events(filters, page, page_size, sort or "-ts")
    return success_response(
        data=[UsageEvent.model_validate(r).model_dump(mode="json") for r in rows],
        meta=paginated_meta(total, page, page_size),
    )


@router.get(
    "/usage/summary",
    summary="Totals grouped by key/model/node/deployment/cluster/format",
)
@handle_exceptions
async def get_usage_summary(
    request: Request,
    from_ts: Optional[datetime] = Query(None, alias="from"),
    to_ts: Optional[datetime] = Query(None, alias="to"),
    api_key_id: Optional[uuid.UUID] = Query(None),
    model_id: Optional[uuid.UUID] = Query(None),
    node_id: Optional[uuid.UUID] = Query(None),
    deployment_id: Optional[uuid.UUID] = Query(None),
    cluster_id: Optional[uuid.UUID] = Query(None),
    format: Optional[UsageFormat] = Query(None),
    status: Optional[UsageStatus] = Query(None),
    group_by: Optional[List[UsageGroupBy]] = Query(None),
    admin=Depends(get_current_admin),
    service: MeteringService = Depends(MeteringService.as_dependency),
):
    filters = _filters(
        service,
        from_ts,
        to_ts,
        api_key_id=api_key_id,
        model_id=model_id,
        node_id=node_id,
        deployment_id=deployment_id,
        cluster_id=cluster_id,
        format=format,
        status=status,
    )
    keys = [g.value for g in group_by] if group_by else ["api_key"]
    data = await service.summary(filters, keys)
    return success_response(data=UsageSummary.model_validate(data))


@router.get("/usage/export", summary="CSV export of events or summary")
@handle_exceptions
async def export_usage_csv(
    request: Request,
    from_ts: Optional[datetime] = Query(None, alias="from"),
    to_ts: Optional[datetime] = Query(None, alias="to"),
    api_key_id: Optional[uuid.UUID] = Query(None),
    model_id: Optional[uuid.UUID] = Query(None),
    node_id: Optional[uuid.UUID] = Query(None),
    deployment_id: Optional[uuid.UUID] = Query(None),
    cluster_id: Optional[uuid.UUID] = Query(None),
    format: Optional[UsageFormat] = Query(None),
    status: Optional[UsageStatus] = Query(None),
    mode: str = Query("events", pattern="^(events|summary)$"),
    group_by: Optional[List[str]] = Query(None),
    admin=Depends(get_current_admin),
    service: MeteringService = Depends(MeteringService.as_dependency),
):
    filters = _filters(
        service,
        from_ts,
        to_ts,
        api_key_id=api_key_id,
        model_id=model_id,
        node_id=node_id,
        deployment_id=deployment_id,
        cluster_id=cluster_id,
        format=format,
        status=status,
    )
    body = await service.export_csv(filters, mode, list(group_by or []))
    stamp = filters["from"].strftime("%Y%m%dT%H%M")
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="usage-{mode}-{stamp}.csv"'},
    )
