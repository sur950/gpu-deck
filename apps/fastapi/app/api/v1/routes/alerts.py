"""
Alert routes (admin.yaml: Alerts tag).

GET    /alert-channels                         list (type, search, sort, page)
POST   /alert-channels                         create (secret stored in the vault)       201
GET    /alert-channels/{channel_id}            get
PATCH  /alert-channels/{channel_id}            update
DELETE /alert-channels/{channel_id}            delete                                    204
POST   /alert-channels/{channel_id}/test       send a test alert now
GET    /alert-events                           list (severity, status, event_type prefix, resource)
GET    /alert-events/{event_id}                get (with deliveries)
POST   /alert-events/{event_id}/acknowledge    acknowledge
POST   /alert-events/{event_id}/resolve        resolve
POST   /alert-events/{event_id}/resend         re-deliver (all matching channels or one)
"""

import uuid
from datetime import datetime
from typing import Optional

from app.core.logger import get_logger
from app.core.rate_limiter import rate_limit
from app.middlewares.session_verification import get_current_admin
from app.schemas.alerts_schema import (
    AlertChannelCreate,
    AlertChannelType,
    AlertChannelUpdate,
    AlertEventStatus,
    AlertSeverity,
)
from app.services.alerts.service import AlertService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Depends, Query, Request, Response, status

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Alerts"]


@router.get("/alert-channels", summary="List alert channels")
@handle_exceptions
async def list_alert_channels(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    search: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    type: Optional[AlertChannelType] = Query(None),
    admin=Depends(get_current_admin),
    service: AlertService = Depends(AlertService.as_dependency),
):
    items, total = await service.list_channels(
        page=page,
        page_size=page_size,
        search=search,
        sort=sort,
        channel_type=type.value if type else None,
    )
    return success_response(data=items, meta=paginated_meta(total, page, page_size))


@router.post("/alert-channels", summary="Create an alert channel", status_code=201)
@handle_exceptions
async def create_alert_channel(
    request: Request,
    payload: AlertChannelCreate,
    admin=Depends(get_current_admin),
    service: AlertService = Depends(AlertService.as_dependency),
):
    return success_response(data=await service.create_channel(payload, created_by=admin.id))


@router.get("/alert-channels/{channel_id}", summary="Get an alert channel")
@handle_exceptions
async def get_alert_channel(
    request: Request,
    channel_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: AlertService = Depends(AlertService.as_dependency),
):
    return success_response(data=await service.get_channel(channel_id))


@router.patch("/alert-channels/{channel_id}", summary="Update an alert channel")
@handle_exceptions
async def update_alert_channel(
    request: Request,
    channel_id: uuid.UUID,
    payload: AlertChannelUpdate,
    admin=Depends(get_current_admin),
    service: AlertService = Depends(AlertService.as_dependency),
):
    return success_response(data=await service.update_channel(channel_id, payload))


@router.delete(
    "/alert-channels/{channel_id}",
    summary="Delete an alert channel",
    status_code=status.HTTP_204_NO_CONTENT,
)
@handle_exceptions
async def delete_alert_channel(
    request: Request,
    channel_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: AlertService = Depends(AlertService.as_dependency),
):
    await service.delete_channel(channel_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/alert-channels/{channel_id}/test", summary="Send a test alert")
@rate_limit("10/minute")
@handle_exceptions
async def test_alert_channel(
    request: Request,
    channel_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: AlertService = Depends(AlertService.as_dependency),
):
    return success_response(data=await service.test_channel(channel_id))


@router.get("/alert-events", summary="List alert events")
@handle_exceptions
async def list_alert_events(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: Optional[str] = Query(None),
    from_: Optional[datetime] = Query(None, alias="from"),
    to: Optional[datetime] = Query(None),
    severity: Optional[AlertSeverity] = Query(None),
    status_: Optional[AlertEventStatus] = Query(None, alias="status"),
    event_type: Optional[str] = Query(None, description="Prefix match, e.g. 'swap.'"),
    resource_type: Optional[str] = Query(None),
    resource_id: Optional[uuid.UUID] = Query(None),
    admin=Depends(get_current_admin),
    service: AlertService = Depends(AlertService.as_dependency),
):
    items, total = await service.list_events(
        page=page,
        page_size=page_size,
        sort=sort,
        from_ts=from_,
        to_ts=to,
        severity=severity.value if severity else None,
        status=status_.value if status_ else None,
        event_type_prefix=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    return success_response(data=items, meta=paginated_meta(total, page, page_size))


@router.get("/alert-events/{event_id}", summary="Get an alert event")
@handle_exceptions
async def get_alert_event(
    request: Request,
    event_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: AlertService = Depends(AlertService.as_dependency),
):
    return success_response(data=await service.get_event(event_id))


@router.post("/alert-events/{event_id}/acknowledge", summary="Acknowledge an alert event")
@handle_exceptions
async def acknowledge_alert_event(
    request: Request,
    event_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: AlertService = Depends(AlertService.as_dependency),
):
    return success_response(data=await service.set_status(event_id, "acknowledged"))


@router.post("/alert-events/{event_id}/resolve", summary="Resolve an alert event")
@handle_exceptions
async def resolve_alert_event(
    request: Request,
    event_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: AlertService = Depends(AlertService.as_dependency),
):
    return success_response(data=await service.set_status(event_id, "resolved"))


@router.post("/alert-events/{event_id}/resend", summary="Re-deliver an alert event")
@rate_limit("10/minute")
@handle_exceptions
async def resend_alert_event(
    request: Request,
    event_id: uuid.UUID,
    channel_id: Optional[uuid.UUID] = Query(None),
    admin=Depends(get_current_admin),
    service: AlertService = Depends(AlertService.as_dependency),
):
    return success_response(data=await service.resend(event_id, channel_id))
