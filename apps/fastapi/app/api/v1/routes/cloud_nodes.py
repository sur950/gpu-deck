"""
Provisioned cloud node routes (admin.yaml: Cloud tag).

GET    /cloud/nodes                                  list (cluster, provider, status, search)
POST   /cloud/nodes                                  provision VM(s) (201) | dry_run preview (200)
GET    /cloud/nodes/{cloud_node_id}                  get
PATCH  /cloud/nodes/{cloud_node_id}                  idle auto-terminate, labels
DELETE /cloud/nodes/{cloud_node_id}                  forget a terminated record (204)
POST   /cloud/nodes/{cloud_node_id}/stop             drain node, stop VM
POST   /cloud/nodes/{cloud_node_id}/start            start a stopped VM
POST   /cloud/nodes/{cloud_node_id}/terminate        drain node, terminate VM, remove node
POST   /cloud/nodes/{cloud_node_id}/refresh          run one lifecycle step now
POST   /cloud/nodes/{cloud_node_id}/apply-price-plan create/attach the list-price plan
"""

import uuid
from typing import List, Optional

from app.core.logger import get_logger
from app.core.rate_limiter import rate_limit
from app.middlewares.session_verification import get_current_admin
from app.schemas.cloud_schema import (
    CloudNodeActionRequest,
    CloudNodeCreate,
    CloudNodeStatus,
    CloudNodeUpdate,
    CloudProviderName,
)
from app.services.cloud.service import CloudService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Cloud"]


@router.get("/cloud/nodes", summary="List provisioned cloud nodes")
@handle_exceptions
async def list_cloud_nodes(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    cluster_id: Optional[uuid.UUID] = Query(None),
    provider: Optional[CloudProviderName] = Query(None),
    status_: Optional[List[CloudNodeStatus]] = Query(None, alias="status"),
    include_terminated: bool = Query(True),
    search: Optional[str] = Query(None, max_length=255),
    sort: Optional[str] = Query(None),
    admin=Depends(get_current_admin),
    service: CloudService = Depends(CloudService.as_dependency),
):
    items, total = await service.list(
        page,
        page_size,
        cluster_id=cluster_id,
        provider=provider,
        status=list(status_) if status_ else None,
        search=search,
        sort=sort,
        include_terminated=include_terminated,
    )
    return success_response(data=items, meta=paginated_meta(total, page, page_size))


@router.post("/cloud/nodes", summary="Provision cloud GPU VMs", status_code=201)
@rate_limit("30/minute")
@handle_exceptions
async def create_cloud_nodes(
    request: Request,
    payload: CloudNodeCreate,
    admin=Depends(get_current_admin),
    service: CloudService = Depends(CloudService.as_dependency),
):
    items, preview = await service.provision(payload, getattr(admin, "id", None))
    if preview is not None:
        return JSONResponse(
            status_code=200, content=jsonable_encoder(success_response(data=preview))
        )
    return JSONResponse(status_code=201, content=jsonable_encoder(success_response(data=items)))


@router.get("/cloud/nodes/{cloud_node_id}", summary="Get a cloud node")
@handle_exceptions
async def get_cloud_node(
    request: Request,
    cloud_node_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: CloudService = Depends(CloudService.as_dependency),
):
    return success_response(data=await service.get(cloud_node_id))


@router.patch("/cloud/nodes/{cloud_node_id}", summary="Update a cloud node")
@handle_exceptions
async def update_cloud_node(
    request: Request,
    cloud_node_id: uuid.UUID,
    payload: CloudNodeUpdate,
    admin=Depends(get_current_admin),
    service: CloudService = Depends(CloudService.as_dependency),
):
    return success_response(
        data=await service.update(cloud_node_id, payload, getattr(admin, "id", None))
    )


@router.delete(
    "/cloud/nodes/{cloud_node_id}",
    summary="Delete a terminated cloud node record",
    status_code=status.HTTP_204_NO_CONTENT,
)
@handle_exceptions
async def delete_cloud_node(
    request: Request,
    cloud_node_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: CloudService = Depends(CloudService.as_dependency),
):
    await service.delete(cloud_node_id, getattr(admin, "id", None))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/cloud/nodes/{cloud_node_id}/stop", summary="Drain the node and stop the VM")
@handle_exceptions
async def stop_cloud_node(
    request: Request,
    cloud_node_id: uuid.UUID,
    payload: Optional[CloudNodeActionRequest] = Body(None),
    admin=Depends(get_current_admin),
    service: CloudService = Depends(CloudService.as_dependency),
):
    body = payload or CloudNodeActionRequest()
    return success_response(
        data=await service.stop(cloud_node_id, body, getattr(admin, "id", None))
    )


@router.post("/cloud/nodes/{cloud_node_id}/start", summary="Start a stopped VM")
@handle_exceptions
async def start_cloud_node(
    request: Request,
    cloud_node_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: CloudService = Depends(CloudService.as_dependency),
):
    return success_response(data=await service.start(cloud_node_id, getattr(admin, "id", None)))


@router.post(
    "/cloud/nodes/{cloud_node_id}/terminate",
    summary="Drain the node, terminate the VM and remove the node",
)
@handle_exceptions
async def terminate_cloud_node(
    request: Request,
    cloud_node_id: uuid.UUID,
    payload: Optional[CloudNodeActionRequest] = Body(None),
    admin=Depends(get_current_admin),
    service: CloudService = Depends(CloudService.as_dependency),
):
    body = payload or CloudNodeActionRequest()
    return success_response(
        data=await service.terminate(cloud_node_id, body, getattr(admin, "id", None))
    )


@router.post("/cloud/nodes/{cloud_node_id}/refresh", summary="Run one lifecycle step now")
@rate_limit("60/minute")
@handle_exceptions
async def refresh_cloud_node(
    request: Request,
    cloud_node_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: CloudService = Depends(CloudService.as_dependency),
):
    return success_response(data=await service.refresh(cloud_node_id))


@router.post(
    "/cloud/nodes/{cloud_node_id}/apply-price-plan",
    summary="Create/attach a price plan from the provider list price",
)
@handle_exceptions
async def apply_cloud_price_plan(
    request: Request,
    cloud_node_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: CloudService = Depends(CloudService.as_dependency),
):
    return success_response(
        data=await service.apply_price_plan(cloud_node_id, getattr(admin, "id", None))
    )
