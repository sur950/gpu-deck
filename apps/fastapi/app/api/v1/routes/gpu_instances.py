"""
SSH GPU instance routes (admin.yaml tag "GPU Instances").

GET/POST          /gpu-instances
GET/PATCH/DELETE  /gpu-instances/{instance_id}
GET               /gpu-instances/{instance_id}/connection
POST              /gpu-instances/{instance_id}/stop | start
"""

import uuid
from typing import List, Optional

from app.core.logger import get_logger
from app.middlewares.session_verification import get_current_admin
from app.schemas.gpu_instances_schema import (
    GpuInstanceCreate,
    GpuInstanceState,
    GpuInstanceStopRequest,
    GpuInstanceUpdate,
)
from app.services.gpu_instances.service import GpuInstanceService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["GPU Instances"]


@router.get("/gpu-instances", summary="List SSH GPU instances")
@handle_exceptions
async def list_gpu_instances(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    state: Optional[List[GpuInstanceState]] = Query(None),
    node_id: Optional[uuid.UUID] = Query(None),
    cluster_id: Optional[uuid.UUID] = Query(None),
    search: Optional[str] = Query(None, max_length=255),
    include_stopped: bool = Query(True),
    admin=Depends(get_current_admin),
    service: GpuInstanceService = Depends(GpuInstanceService.as_dependency),
):
    items, total = await service.list(
        page,
        page_size,
        state=state,
        node_id=node_id,
        cluster_id=cluster_id,
        search=search,
        include_stopped=include_stopped,
    )
    return success_response(data=items, meta=paginated_meta(total, page, page_size))


@router.post(
    "/gpu-instances",
    status_code=status.HTTP_201_CREATED,
    summary="Request an SSH GPU instance (placed and started immediately)",
)
@handle_exceptions
async def create_gpu_instance(
    request: Request,
    body: GpuInstanceCreate,
    admin=Depends(get_current_admin),
    service: GpuInstanceService = Depends(GpuInstanceService.as_dependency),
):
    data = await service.create(body, admin.id)
    return JSONResponse(status_code=201, content=_json(data))


@router.get("/gpu-instances/{instance_id}", summary="Get an SSH GPU instance")
@handle_exceptions
async def get_gpu_instance(
    request: Request,
    instance_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: GpuInstanceService = Depends(GpuInstanceService.as_dependency),
):
    return success_response(data=await service.get(instance_id))


@router.patch("/gpu-instances/{instance_id}", summary="Update TTL, idle timeout, labels, keys")
@handle_exceptions
async def update_gpu_instance(
    request: Request,
    instance_id: uuid.UUID,
    body: GpuInstanceUpdate,
    admin=Depends(get_current_admin),
    service: GpuInstanceService = Depends(GpuInstanceService.as_dependency),
):
    return success_response(data=await service.update(instance_id, body, admin.id))


@router.delete(
    "/gpu-instances/{instance_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an SSH GPU instance (force stops a live one)",
)
@handle_exceptions
async def delete_gpu_instance(
    request: Request,
    instance_id: uuid.UUID,
    force: bool = Query(False),
    admin=Depends(get_current_admin),
    service: GpuInstanceService = Depends(GpuInstanceService.as_dependency),
):
    await service.delete(instance_id, force, admin.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/gpu-instances/{instance_id}/connection",
    summary="Connection details (host, port, user, host key, ssh command)",
)
@handle_exceptions
async def gpu_instance_connection(
    request: Request,
    instance_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: GpuInstanceService = Depends(GpuInstanceService.as_dependency),
):
    return success_response(data=await service.connection(instance_id))


@router.post(
    "/gpu-instances/{instance_id}/stop",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Stop an SSH GPU instance",
)
@handle_exceptions
async def stop_gpu_instance(
    request: Request,
    instance_id: uuid.UUID,
    body: Optional[GpuInstanceStopRequest] = Body(None),
    admin=Depends(get_current_admin),
    service: GpuInstanceService = Depends(GpuInstanceService.as_dependency),
):
    body = body or GpuInstanceStopRequest()
    data = await service.stop(
        instance_id,
        force=body.force,
        remove_volumes=body.remove_volumes,
        reason="user",
        actor_id=admin.id,
    )
    return JSONResponse(status_code=202, content=_json(data))


@router.post(
    "/gpu-instances/{instance_id}/start",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a stopped SSH GPU instance again (new placement)",
)
@handle_exceptions
async def start_gpu_instance(
    request: Request,
    instance_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: GpuInstanceService = Depends(GpuInstanceService.as_dependency),
):
    data = await service.start(instance_id, admin.id)
    return JSONResponse(status_code=202, content=_json(data))


def _json(data):
    from fastapi.encoders import jsonable_encoder

    return jsonable_encoder(success_response(data=data))
