"""
Instance routes (admin.yaml tag "Instances").

GET              /instances
GET/PATCH/DELETE /instances/{instance_id}
POST             /instances/{instance_id}/drain | restart
GET              /instances/{instance_id}/sessions
"""

import uuid
from typing import Optional

from app.core.logger import get_logger
from app.schemas.deployments_schema import (
    DeploymentOrigin,
    InstanceDrainRequest,
    InstanceHealth,
    InstanceState,
    InstanceUpdate,
)
from app.services.catalog.auth import get_current_admin
from app.services.deployments.service import DeploymentService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Body, Depends, Query, Request, status

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Instances"]


@router.get("/instances", summary="List instances")
@handle_exceptions
async def list_instances(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: Optional[str] = Query(None),
    deployment_id: Optional[uuid.UUID] = Query(None),
    node_id: Optional[uuid.UUID] = Query(None),
    state: Optional[InstanceState] = Query(None),
    health: Optional[InstanceHealth] = Query(None),
    include_stopped: bool = Query(False),
    origin: Optional[DeploymentOrigin] = Query(
        None, description="managed | external (observed servers GPU Deck did not start)"
    ),
    admin=Depends(get_current_admin),
    service: DeploymentService = Depends(DeploymentService.as_dependency),
):
    rows, total = await service.list_instances(
        page=page,
        page_size=page_size,
        sort=sort,
        deployment_id=deployment_id,
        node_id=node_id,
        state=state.value if state else None,
        health=health.value if health else None,
        include_stopped=include_stopped,
        origin=origin.value if origin else None,
    )
    return success_response(data=rows, meta=paginated_meta(total, page, page_size))


@router.get("/instances/{instance_id}", summary="Get an instance")
@handle_exceptions
async def get_instance(
    request: Request,
    instance_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: DeploymentService = Depends(DeploymentService.as_dependency),
):
    return success_response(data=await service.get_instance(instance_id))


@router.patch("/instances/{instance_id}", summary="Set traffic weight")
@handle_exceptions
async def update_instance(
    request: Request,
    instance_id: uuid.UUID,
    body: InstanceUpdate,
    admin=Depends(get_current_admin),
    service: DeploymentService = Depends(DeploymentService.as_dependency),
):
    return success_response(data=await service.update_instance(instance_id, body))


@router.delete(
    "/instances/{instance_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Unload this instance (deployment replicas are reduced by one)",
)
@handle_exceptions
async def stop_instance(
    request: Request,
    instance_id: uuid.UUID,
    force: bool = Query(False),
    admin=Depends(get_current_admin),
    service: DeploymentService = Depends(DeploymentService.as_dependency),
):
    return success_response(data=await service.remove_instance(instance_id, force=force))


@router.post(
    "/instances/{instance_id}/drain",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Drain (stop new traffic, wait for sessions)",
)
@handle_exceptions
async def drain_instance(
    request: Request,
    instance_id: uuid.UUID,
    body: Optional[InstanceDrainRequest] = Body(None),
    admin=Depends(get_current_admin),
    service: DeploymentService = Depends(DeploymentService.as_dependency),
):
    return success_response(
        data=await service.drain_instance(instance_id, body or InstanceDrainRequest())
    )


@router.post(
    "/instances/{instance_id}/restart",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Drain, unload and load on the same devices",
)
@handle_exceptions
async def restart_instance(
    request: Request,
    instance_id: uuid.UUID,
    body: Optional[InstanceDrainRequest] = Body(None),
    admin=Depends(get_current_admin),
    service: DeploymentService = Depends(DeploymentService.as_dependency),
):
    return success_response(
        data=await service.restart_instance(instance_id, body or InstanceDrainRequest())
    )


@router.get("/instances/{instance_id}/sessions", summary="Live per-session / per-slot snapshot")
@handle_exceptions
async def get_instance_sessions(
    request: Request,
    instance_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: DeploymentService = Depends(DeploymentService.as_dependency),
):
    return success_response(data=await service.instance_sessions(instance_id))
