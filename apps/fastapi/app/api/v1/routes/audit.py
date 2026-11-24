"""
Audit log routes (admin.yaml: Audit tag).

GET /audit          search (page, page_size, sort, from, to, event_type, actor_type,
                    actor_id, resource_type, resource_id, outcome, request_id, search)
GET /audit/export   CSV (from, to, event_type, actor_type, resource_type)
GET /audit/{id}     one entry
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from app.core.logger import get_logger
from app.core.settings import settings
from app.middlewares.session_verification import get_current_admin
from app.schemas.audit_schema import AuditActorType, AuditLog, AuditOutcome
from app.services.audit import AuditService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Audit"]


@router.get("/audit", summary="Search the audit log")
@handle_exceptions
async def list_audit_logs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: Optional[str] = Query(None),
    from_: Optional[datetime] = Query(None, alias="from"),
    to: Optional[datetime] = Query(None),
    event_type: Optional[str] = Query(None),
    actor_type: Optional[AuditActorType] = Query(None),
    actor_id: Optional[uuid.UUID] = Query(None),
    resource_type: Optional[str] = Query(None),
    resource_id: Optional[uuid.UUID] = Query(None),
    outcome: Optional[AuditOutcome] = Query(None),
    request_id: Optional[uuid.UUID] = Query(None),
    search: Optional[str] = Query(None),
    admin=Depends(get_current_admin),
    service: AuditService = Depends(AuditService.as_dependency),
):
    items, total = await service.list_logs(
        page=page,
        page_size=page_size,
        sort=sort,
        from_=from_,
        to=to,
        event_type=event_type,
        actor_type=actor_type.value if actor_type else None,
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome.value if outcome else None,
        request_id=request_id,
        search=search,
    )
    return success_response(
        data=[AuditLog.model_validate(i) for i in items],
        meta=paginated_meta(total, page, page_size),
    )


@router.get("/audit/export", summary="Export matching entries as CSV")
@handle_exceptions
async def export_audit_csv(
    request: Request,
    from_: Optional[datetime] = Query(None, alias="from"),
    to: Optional[datetime] = Query(None),
    event_type: Optional[str] = Query(None),
    actor_type: Optional[AuditActorType] = Query(None),
    resource_type: Optional[str] = Query(None),
    admin=Depends(get_current_admin),
    service: AuditService = Depends(AuditService.as_dependency),
):
    chunks = []
    async for chunk in service.export_csv(
        max_rows=int(settings.AUDIT_EXPORT_MAX_ROWS),
        from_=from_,
        to=to,
        event_type=event_type,
        actor_type=actor_type.value if actor_type else None,
        resource_type=resource_type,
    ):
        chunks.append(chunk)
    filename = f"audit-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.csv"
    return StreamingResponse(
        iter(chunks),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/audit/{log_id}", summary="Get one entry")
@handle_exceptions
async def get_audit_log(
    request: Request,
    log_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: AuditService = Depends(AuditService.as_dependency),
):
    return success_response(data=AuditLog.model_validate(await service.get_log(log_id)))
