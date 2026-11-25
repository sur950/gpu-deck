"""
Billing routes (admin.yaml: Billing tag).

GET    /billing/reports                         list
POST   /billing/reports                         generate (async, 202)
GET    /billing/estimate                        live cost for the current (or given) period
GET    /billing/reports/{report_id}             get with line items
DELETE /billing/reports/{report_id}             delete (204)
POST   /billing/reports/{report_id}/regenerate  regenerate with current prices (202)
GET    /billing/reports/{report_id}/export      CSV
"""

import uuid
from datetime import datetime
from typing import Optional

from app.core.logger import get_logger
from app.middlewares.session_verification import get_current_admin
from app.schemas.billing_schema import (
    BillingEstimate,
    BillingReport,
    BillingReportCreate,
    BillingReportStatus,
)
from app.services.billing.service import BillingService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Depends, Query, Request, Response, status

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Billing"]


def _dump(report) -> dict:
    return BillingReport.model_validate(report).model_dump(mode="json")


async def _queue_generation(service: BillingService, report_id) -> dict:
    """Commit, then hand generation to the billing queue (inline if the broker is down)."""
    import asyncio

    from app.workers.tasks.billing_tasks import enqueue_billing_report

    await service.session.commit()
    if await asyncio.to_thread(enqueue_billing_report, str(report_id)):
        return await service.get_report(report_id)
    return await service.generate(report_id)


@router.get("/billing/reports", summary="List reports")
@handle_exceptions
async def list_billing_reports(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    search: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    status_filter: Optional[BillingReportStatus] = Query(None, alias="status"),
    admin=Depends(get_current_admin),
    service: BillingService = Depends(BillingService.as_dependency),
):
    items, total = await service.list_reports(
        page, page_size, search, sort, status_filter.value if status_filter else None
    )
    return success_response(
        data=[_dump(i) for i in items], meta=paginated_meta(total, page, page_size)
    )


@router.post("/billing/reports", summary="Generate a report (async)", status_code=202)
@handle_exceptions
async def create_billing_report(
    request: Request,
    payload: BillingReportCreate,
    admin=Depends(get_current_admin),
    service: BillingService = Depends(BillingService.as_dependency),
):
    created = await service.create_report(
        payload.model_dump(), created_by=getattr(admin, "id", None)
    )
    return success_response(data=_dump(await _queue_generation(service, created["id"])))


@router.get("/billing/estimate", summary="Live cost for the current (or given) period")
@handle_exceptions
async def get_billing_estimate(
    request: Request,
    period_start: Optional[datetime] = Query(None),
    period_end: Optional[datetime] = Query(None),
    cluster_id: Optional[uuid.UUID] = Query(None),
    admin=Depends(get_current_admin),
    service: BillingService = Depends(BillingService.as_dependency),
):
    data = await service.estimate(period_start, period_end, cluster_id)
    return success_response(data=BillingEstimate.model_validate(data).model_dump(mode="json"))


@router.get("/billing/reports/{report_id}", summary="Get a report with line items")
@handle_exceptions
async def get_billing_report(
    request: Request,
    report_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: BillingService = Depends(BillingService.as_dependency),
):
    return success_response(data=_dump(await service.get_report(report_id)))


@router.delete(
    "/billing/reports/{report_id}",
    summary="Delete a report",
    status_code=status.HTTP_204_NO_CONTENT,
)
@handle_exceptions
async def delete_billing_report(
    request: Request,
    report_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: BillingService = Depends(BillingService.as_dependency),
):
    await service.delete_report(report_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/billing/reports/{report_id}/regenerate",
    summary="Regenerate with current prices",
    status_code=202,
)
@handle_exceptions
async def regenerate_billing_report(
    request: Request,
    report_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: BillingService = Depends(BillingService.as_dependency),
):
    await service.mark_regenerate(report_id)
    return success_response(data=_dump(await _queue_generation(service, report_id)))


@router.get("/billing/reports/{report_id}/export", summary="CSV export")
@handle_exceptions
async def export_billing_report_csv(
    request: Request,
    report_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: BillingService = Depends(BillingService.as_dependency),
):
    body, filename = await service.export_csv(report_id)
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
