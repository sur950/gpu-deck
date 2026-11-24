"""
Cluster routes (admin.yaml: Clusters tag).

GET    /clusters                       list (page, page_size, search, sort, type, status)
POST   /clusters                       create                                     201
GET    /clusters/{cluster_id}          get (with counts)
PATCH  /clusters/{cluster_id}          partial update (egress change -> config_push)
DELETE /clusters/{cluster_id}          soft delete (must have no nodes)            204
GET    /clusters/{cluster_id}/nodes    nodes of the cluster
"""

import uuid
from typing import Optional

from app.core.logger import get_logger
from app.middlewares.session_verification import get_current_admin
from app.schemas.fleet_schema import (
    ClusterCreate,
    ClusterStatus,
    ClusterType,
    ClusterUpdate,
)
from app.services.clusters.service import ClusterService
from app.services.nodes.service import NodeService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import JSONResponse

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Clusters"]


@router.get("/clusters", summary="List clusters")
@handle_exceptions
async def list_clusters(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    search: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    type_: Optional[ClusterType] = Query(None, alias="type"),
    status_: Optional[ClusterStatus] = Query(None, alias="status"),
    admin=Depends(get_current_admin),
    service: ClusterService = Depends(ClusterService.as_dependency),
):
    items, total = await service.list(page, page_size, search, sort, type_, status_)
    return success_response(data=items, meta=paginated_meta(total, page, page_size))


@router.post("/clusters", summary="Create a cluster", status_code=201)
@handle_exceptions
async def create_cluster(
    request: Request,
    payload: ClusterCreate,
    admin=Depends(get_current_admin),
    service: ClusterService = Depends(ClusterService.as_dependency),
):
    item = await service.create(payload, admin.id)
    return JSONResponse(status_code=201, content=_json(success_response(data=item)))


@router.get("/clusters/{cluster_id}", summary="Get a cluster")
@handle_exceptions
async def get_cluster(
    request: Request,
    cluster_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: ClusterService = Depends(ClusterService.as_dependency),
):
    return success_response(data=await service.get(cluster_id))


@router.patch("/clusters/{cluster_id}", summary="Update a cluster")
@handle_exceptions
async def update_cluster(
    request: Request,
    cluster_id: uuid.UUID,
    payload: ClusterUpdate,
    admin=Depends(get_current_admin),
    service: ClusterService = Depends(ClusterService.as_dependency),
):
    return success_response(data=await service.update(cluster_id, payload, admin.id))


@router.delete(
    "/clusters/{cluster_id}",
    summary="Delete an empty cluster",
    status_code=status.HTTP_204_NO_CONTENT,
)
@handle_exceptions
async def delete_cluster(
    request: Request,
    cluster_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: ClusterService = Depends(ClusterService.as_dependency),
):
    await service.delete(cluster_id, admin.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/clusters/{cluster_id}/nodes", summary="Nodes of a cluster")
@handle_exceptions
async def list_cluster_nodes(
    request: Request,
    cluster_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    search: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    admin=Depends(get_current_admin),
    service: NodeService = Depends(NodeService.as_dependency),
):
    items, total = await service.list(page, page_size, search, sort, cluster_id=cluster_id)
    return success_response(data=items, meta=paginated_meta(total, page, page_size))


def _json(body):
    from fastapi.encoders import jsonable_encoder

    return jsonable_encoder(body)
