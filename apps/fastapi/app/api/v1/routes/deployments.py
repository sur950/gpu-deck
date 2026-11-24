"""
Deployment routes (admin.yaml tag "Deployments").

GET/POST          /deployments
POST              /deployments/preview
GET/PATCH/DELETE  /deployments/{deployment_id}
POST              /deployments/{deployment_id}/scale | restart | stop | start
PUT               /deployments/{deployment_id}/traffic
GET               /deployments/{deployment_id}/instances | swaps
"""

import uuid
from typing import Optional

from app.core.logger import get_logger
from app.schemas.catalog_schema import ModelPriority
from app.schemas.deployments_schema import (
    DeploymentCreate,
    DeploymentOrigin,
    DeploymentStatus,
    DeploymentUpdate,
    InstanceDrainRequest,
    PlacementPreview,
    PlacementPreviewRequest,
    ScaleRequest,
    TrafficWeightsRequest,
)
from app.schemas.engines_schema import EngineName
from app.services.catalog.auth import admin_id, get_current_admin
from app.services.deployments.service import DeploymentService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Body, Depends, Query, Request, status
from fastapi.responses import JSONResponse

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Deployments"]


@router.get("/deployments", summary="List deployments")
@handle_exceptions
async def list_deployments(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    search: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    status_: Optional[DeploymentStatus] = Query(None, alias="status"),
    model_id: Optional[uuid.UUID] = Query(None),
    cluster_id: Optional[uuid.UUID] = Query(None),
    engine: Optional[EngineName] = Query(None),
    priority: Optional[ModelPriority] = Query(None),
    origin: Optional[DeploymentOrigin] = Query(
        None, description="managed | external (observed servers GPU Deck did not start)"
    ),
    admin=Depends(get_current_admin),
    service: DeploymentService = Depends(DeploymentService.as_dependency),
):
    rows, total = await service.list_deployments(
        page=page,
        page_size=page_size,
        search=search,
        sort=sort,
        status=status_.value if status_ else None,
        model_id=model_id,
        cluster_id=cluster_id,
        engine=engine.value if engine else None,
        priority=priority.value if priority else None,
        origin=origin.value if origin else None,
    )
    return success_response(data=rows, meta=paginated_meta(total, page, page_size))


@router.post(
    "/deployments",
    status_code=status.HTTP_201_CREATED,
    summary="Create (and by default start) a deployment",
)
@handle_exceptions
async def create_deployment(
    request: Request,
    body: DeploymentCreate,
    admin=Depends(get_current_admin),
    service: DeploymentService = Depends(DeploymentService.as_dependency),
):
    """``dry_run=true`` validates and returns the placement preview (200) without creating."""
    result = await service.create_deployment(body, created_by=admin_id(admin))
    if isinstance(result, PlacementPreview):
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=success_response(data=result.model_dump(mode="json")),
        )
    return success_response(data=result)


@router.post("/deployments/preview", summary="Placement preview (scheduler dry run)")
@handle_exceptions
async def preview_placement(
    request: Request,
    body: PlacementPreviewRequest,
    admin=Depends(get_current_admin),
    service: DeploymentService = Depends(DeploymentService.as_dependency),
):
    return success_response(data=await service.preview(body))


@router.get("/deployments/{deployment_id}", summary="Get a deployment with instances")
@handle_exceptions
async def get_deployment(
    request: Request,
    deployment_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: DeploymentService = Depends(DeploymentService.as_dependency),
):
    return success_response(data=await service.get_deployment(deployment_id))


@router.patch("/deployments/{deployment_id}", summary="Update a deployment")
@handle_exceptions
async def update_deployment(
    request: Request,
    deployment_id: uuid.UUID,
    body: DeploymentUpdate,
    admin=Depends(get_current_admin),
    service: DeploymentService = Depends(DeploymentService.as_dependency),
):
    """Replica / weight / traffic changes apply live; engine, model or launch changes roll."""
    return success_response(data=await service.update_deployment(deployment_id, body))


@router.delete(
    "/deployments/{deployment_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Unload all instances and soft-delete",
)
@handle_exceptions
async def delete_deployment(
    request: Request,
    deployment_id: uuid.UUID,
    force: bool = Query(False),
    admin=Depends(get_current_admin),
    service: DeploymentService = Depends(DeploymentService.as_dependency),
):
    return success_response(data=await service.delete_deployment(deployment_id, force=force))


@router.post(
    "/deployments/{deployment_id}/scale",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Set replicas",
)
@handle_exceptions
async def scale_deployment(
    request: Request,
    deployment_id: uuid.UUID,
    body: ScaleRequest,
    admin=Depends(get_current_admin),
    service: DeploymentService = Depends(DeploymentService.as_dependency),
):
    return success_response(data=await service.scale(deployment_id, body))


@router.post(
    "/deployments/{deployment_id}/restart",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Rolling restart of every instance",
)
@handle_exceptions
async def restart_deployment(
    request: Request,
    deployment_id: uuid.UUID,
    body: Optional[InstanceDrainRequest] = Body(None),
    admin=Depends(get_current_admin),
    service: DeploymentService = Depends(DeploymentService.as_dependency),
):
    return success_response(
        data=await service.restart(deployment_id, body or InstanceDrainRequest())
    )


@router.post(
    "/deployments/{deployment_id}/stop",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Unload all instances, keep the deployment",
)
@handle_exceptions
async def stop_deployment(
    request: Request,
    deployment_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: DeploymentService = Depends(DeploymentService.as_dependency),
):
    return success_response(data=await service.stop(deployment_id))


@router.post(
    "/deployments/{deployment_id}/start",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Schedule and load instances",
)
@handle_exceptions
async def start_deployment(
    request: Request,
    deployment_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: DeploymentService = Depends(DeploymentService.as_dependency),
):
    return success_response(data=await service.start(deployment_id))


@router.put("/deployments/{deployment_id}/traffic", summary="Set traffic weights and strategy")
@handle_exceptions
async def set_traffic_weights(
    request: Request,
    deployment_id: uuid.UUID,
    body: TrafficWeightsRequest,
    admin=Depends(get_current_admin),
    service: DeploymentService = Depends(DeploymentService.as_dependency),
):
    return success_response(data=await service.set_traffic(deployment_id, body))


@router.get("/deployments/{deployment_id}/instances", summary="Instances of the deployment")
@handle_exceptions
async def list_deployment_instances(
    request: Request,
    deployment_id: uuid.UUID,
    include_stopped: bool = Query(False),
    admin=Depends(get_current_admin),
    service: DeploymentService = Depends(DeploymentService.as_dependency),
):
    await service.get_deployment(deployment_id)
    rows, total = await service.list_instances(
        deployment_id=deployment_id,
        include_stopped=include_stopped,
        page=1,
        page_size=500,
    )
    return success_response(data=rows, meta=paginated_meta(total, 1, 500))


@router.get("/deployments/{deployment_id}/swaps", summary="Swap history of the deployment")
@handle_exceptions
async def list_deployment_swaps(
    request: Request,
    deployment_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    admin=Depends(get_current_admin),
    service: DeploymentService = Depends(DeploymentService.as_dependency),
):
    rows, total = await service.list_swaps(deployment_id, page, page_size)
    return success_response(data=rows, meta=paginated_meta(total, page, page_size))
