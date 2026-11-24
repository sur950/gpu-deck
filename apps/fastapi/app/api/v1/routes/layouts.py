"""
GPU layout routes (admin.yaml: Layouts tag).

GET    /layouts                        list (search, sort, page)
POST   /layouts                        create                                       201
POST   /layouts/capture                snapshot the current placement as a layout   201
GET    /layouts/{layout_id}            get
PATCH  /layouts/{layout_id}            update (items replaced when present)
DELETE /layouts/{layout_id}            delete (409 while a time_schedule rule uses it)  204
POST   /layouts/{layout_id}/preview    diff against the running placement (no effect)
POST   /layouts/{layout_id}/apply      apply as swap operations (or prefetch only)   202
"""

import uuid
from typing import Optional

from app.core.logger import get_logger
from app.core.rate_limiter import rate_limit
from app.middlewares.session_verification import get_current_admin
from app.schemas.layouts_schema import (
    LayoutApplyRequest,
    LayoutCaptureRequest,
    LayoutCreate,
    LayoutUpdate,
)
from app.services.layouts.service import LayoutService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Body, Depends, Query, Request, Response, status

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Layouts"]


@router.get("/layouts", summary="List layouts")
@handle_exceptions
async def list_layouts(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    search: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    admin=Depends(get_current_admin),
    service: LayoutService = Depends(LayoutService.as_dependency),
):
    items, total = await service.list_layouts(
        page=page, page_size=page_size, search=search, sort=sort
    )
    return success_response(data=items, meta=paginated_meta(total, page, page_size))


@router.post("/layouts", summary="Create a layout", status_code=201)
@handle_exceptions
async def create_layout(
    request: Request,
    payload: LayoutCreate,
    admin=Depends(get_current_admin),
    service: LayoutService = Depends(LayoutService.as_dependency),
):
    return success_response(data=await service.create_layout(payload, created_by=admin.id))


@router.post(
    "/layouts/capture",
    summary="Snapshot the current placement as a new layout",
    status_code=201,
)
@handle_exceptions
async def capture_layout(
    request: Request,
    payload: LayoutCaptureRequest,
    admin=Depends(get_current_admin),
    service: LayoutService = Depends(LayoutService.as_dependency),
):
    return success_response(data=await service.capture(payload, created_by=admin.id))


@router.get("/layouts/{layout_id}", summary="Get a layout")
@handle_exceptions
async def get_layout(
    request: Request,
    layout_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: LayoutService = Depends(LayoutService.as_dependency),
):
    return success_response(data=await service.get_layout(layout_id))


@router.patch("/layouts/{layout_id}", summary="Update (items are replaced when present)")
@handle_exceptions
async def update_layout(
    request: Request,
    layout_id: uuid.UUID,
    payload: LayoutUpdate,
    admin=Depends(get_current_admin),
    service: LayoutService = Depends(LayoutService.as_dependency),
):
    return success_response(data=await service.update_layout(layout_id, payload))


@router.delete(
    "/layouts/{layout_id}",
    summary="Delete a layout",
    status_code=status.HTTP_204_NO_CONTENT,
)
@handle_exceptions
async def delete_layout(
    request: Request,
    layout_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: LayoutService = Depends(LayoutService.as_dependency),
):
    await service.delete_layout(layout_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/layouts/{layout_id}/preview", summary="Diff against the current placement")
@handle_exceptions
async def preview_layout(
    request: Request,
    layout_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: LayoutService = Depends(LayoutService.as_dependency),
):
    diff = await service.diff(layout_id)
    return success_response(data=diff.model_dump(mode="json", by_alias=True))


@router.post(
    "/layouts/{layout_id}/apply",
    summary="Apply the layout (swap operations)",
    status_code=202,
)
@rate_limit("10/minute")
@handle_exceptions
async def apply_layout(
    request: Request,
    layout_id: uuid.UUID,
    payload: Optional[LayoutApplyRequest] = Body(None),
    admin=Depends(get_current_admin),
    service: LayoutService = Depends(LayoutService.as_dependency),
):
    body = payload or LayoutApplyRequest()
    result = await service.apply(
        layout_id,
        drain_timeout_seconds=body.drain_timeout_seconds,
        prefetch_only=body.prefetch_only,
        trigger="layout",
        created_by=admin.id,
    )
    return success_response(data=result.model_dump(mode="json", by_alias=True))
