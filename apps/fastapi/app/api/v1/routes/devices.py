"""
Device routes (admin.yaml: Devices tag).

GET /devices                        list (cluster, node, vendor, health, free VRAM filters)
GET /devices/{device_id}            get (with slices, instances, live sample)
GET /devices/{device_id}/slices     slices of one device
POST /devices/{device_id}/slices    direct slice (standalone or one more deployment replica)
PATCH /devices/{device_id}/slices/{slice_id}   resize / rename
DELETE /devices/{device_id}/slices/{slice_id}  release (force: stop what uses it)
GET /device-slices                  active slices across the fleet
"""

import uuid
from typing import Optional

from app.core.logger import get_logger
from app.middlewares.session_verification import get_current_admin
from app.schemas.fleet_schema import (
    DeviceHealth,
    DeviceSliceCreate,
    DeviceSliceUpdate,
    DeviceVendor,
)
from app.services.nodes.devices import DeviceService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Devices"]


@router.get("/devices", summary="List devices")
@handle_exceptions
async def list_devices(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: Optional[str] = Query(None),
    cluster_id: Optional[uuid.UUID] = Query(None),
    node_id: Optional[uuid.UUID] = Query(None),
    vendor: Optional[DeviceVendor] = Query(None),
    health: Optional[DeviceHealth] = Query(None),
    free_vram_min_bytes: Optional[int] = Query(None, ge=0),
    admin=Depends(get_current_admin),
    service: DeviceService = Depends(DeviceService.as_dependency),
):
    items, total = await service.list(
        page, page_size, sort, cluster_id, node_id, vendor, health, free_vram_min_bytes
    )
    return success_response(data=items, meta=paginated_meta(total, page, page_size))


@router.get("/devices/{device_id}", summary="Get a device")
@handle_exceptions
async def get_device(
    request: Request,
    device_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: DeviceService = Depends(DeviceService.as_dependency),
):
    return success_response(data=await service.get(device_id))


@router.get("/devices/{device_id}/slices", summary="Slices of a device")
@handle_exceptions
async def list_device_slices(
    request: Request,
    device_id: uuid.UUID,
    include_released: bool = Query(False),
    admin=Depends(get_current_admin),
    service: DeviceService = Depends(DeviceService.as_dependency),
):
    return success_response(data=await service.list_slices(device_id, include_released))


@router.post(
    "/devices/{device_id}/slices",
    status_code=status.HTTP_201_CREATED,
    summary="Create a slice on a device",
)
@handle_exceptions
async def create_device_slice(
    request: Request,
    device_id: uuid.UUID,
    body: DeviceSliceCreate,
    admin=Depends(get_current_admin),
    service: DeviceService = Depends(DeviceService.as_dependency),
):
    data = await service.create_slice(device_id, body, admin.id)
    return JSONResponse(status_code=201, content=jsonable_encoder(success_response(data=data)))


@router.patch("/devices/{device_id}/slices/{slice_id}", summary="Resize or rename a slice")
@handle_exceptions
async def update_device_slice(
    request: Request,
    device_id: uuid.UUID,
    slice_id: uuid.UUID,
    body: DeviceSliceUpdate,
    admin=Depends(get_current_admin),
    service: DeviceService = Depends(DeviceService.as_dependency),
):
    data = await service.update_slice(device_id, slice_id, body, admin.id)
    code = 202 if data.rolling_update else 200
    return JSONResponse(status_code=code, content=jsonable_encoder(success_response(data=data)))


@router.delete("/devices/{device_id}/slices/{slice_id}", summary="Delete a slice")
@handle_exceptions
async def delete_device_slice(
    request: Request,
    device_id: uuid.UUID,
    slice_id: uuid.UUID,
    force: bool = Query(False),
    admin=Depends(get_current_admin),
    service: DeviceService = Depends(DeviceService.as_dependency),
):
    return success_response(data=await service.delete_slice(device_id, slice_id, force, admin.id))


@router.get("/device-slices", summary="Active device slices")
@handle_exceptions
async def list_all_device_slices(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    deployment_id: Optional[uuid.UUID] = Query(None),
    node_id: Optional[uuid.UUID] = Query(None),
    admin=Depends(get_current_admin),
    service: DeviceService = Depends(DeviceService.as_dependency),
):
    items, total = await service.list_all_slices(page, page_size, deployment_id, node_id)
    return success_response(data=items, meta=paginated_meta(total, page, page_size))
