"""
Node routes (admin.yaml: Nodes tag).

GET    /nodes                                        list (cluster, status, os, label=k=v filters)
GET    /nodes/join-tokens                            list join tokens
POST   /nodes/join-tokens                            create (plaintext gdj_ shown once)     201
DELETE /nodes/join-tokens/{token_id}                 revoke                                 204
GET    /nodes/{node_id}                              get
PATCH  /nodes/{node_id}                              update name/labels/price plan/proxy/cache
DELETE /nodes/{node_id}?force=                       remove (revokes certs)                 204
POST   /nodes/{node_id}/drain                        drain -> CommandRefs                   202
POST   /nodes/{node_id}/undrain                      undrain
GET    /nodes/{node_id}/devices|instances|cache|commands|certificates
POST   /nodes/{node_id}/certificates/rotate          rotate_cert command                    202
POST   /nodes/{node_id}/certificates/{cert_id}/revoke

The agent-facing join endpoint (token + CSR) lives on the hub listener
(``POST /agent/v1/join`` on :8443, ``app/services/agent_hub/routes.py``).
"""

import uuid
from typing import List, Optional

from app.core.logger import get_logger
from app.middlewares.session_verification import get_current_admin
from app.schemas.fleet_schema import (
    AgentCommandState,
    DrainRequest,
    JoinTokenCreate,
    NodeHashRequest,
    NodeOs,
    NodeScanRequest,
    NodeStatus,
    NodeUpdate,
    RevokeRequest,
)
from app.services.nodes import files as node_files
from app.services.nodes.service import NodeService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Nodes"]


def _created(data, code: int = 201) -> JSONResponse:
    return JSONResponse(status_code=code, content=jsonable_encoder(success_response(data=data)))


@router.get("/nodes", summary="List nodes")
@handle_exceptions
async def list_nodes(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    search: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    cluster_id: Optional[uuid.UUID] = Query(None),
    status_: Optional[NodeStatus] = Query(None, alias="status"),
    os: Optional[NodeOs] = Query(None),
    label: Optional[List[str]] = Query(None, description="key=value (repeatable)"),
    admin=Depends(get_current_admin),
    service: NodeService = Depends(NodeService.as_dependency),
):
    items, total = await service.list(page, page_size, search, sort, cluster_id, status_, os, label)
    return success_response(data=items, meta=paginated_meta(total, page, page_size))


@router.get("/nodes/join-tokens", summary="List join tokens")
@handle_exceptions
async def list_join_tokens(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    cluster_id: Optional[uuid.UUID] = Query(None),
    active_only: bool = Query(True),
    admin=Depends(get_current_admin),
    service: NodeService = Depends(NodeService.as_dependency),
):
    items, total = await service.list_join_tokens(page, page_size, cluster_id, active_only)
    return success_response(data=items, meta=paginated_meta(total, page, page_size))


@router.post("/nodes/join-tokens", summary="Create a one-time join token", status_code=201)
@handle_exceptions
async def create_join_token(
    request: Request,
    payload: JoinTokenCreate,
    admin=Depends(get_current_admin),
    service: NodeService = Depends(NodeService.as_dependency),
):
    return _created(await service.create_join_token(payload, admin.id))


@router.delete(
    "/nodes/join-tokens/{token_id}",
    summary="Revoke an unused join token",
    status_code=status.HTTP_204_NO_CONTENT,
)
@handle_exceptions
async def revoke_join_token(
    request: Request,
    token_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: NodeService = Depends(NodeService.as_dependency),
):
    await service.revoke_join_token(token_id, admin.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/nodes/{node_id}", summary="Get a node")
@handle_exceptions
async def get_node(
    request: Request,
    node_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: NodeService = Depends(NodeService.as_dependency),
):
    return success_response(data=await service.get(node_id))


@router.patch("/nodes/{node_id}", summary="Update a node")
@handle_exceptions
async def update_node(
    request: Request,
    node_id: uuid.UUID,
    payload: NodeUpdate,
    admin=Depends(get_current_admin),
    service: NodeService = Depends(NodeService.as_dependency),
):
    return success_response(data=await service.update(node_id, payload, admin.id))


@router.delete("/nodes/{node_id}", summary="Remove a node", status_code=status.HTTP_204_NO_CONTENT)
@handle_exceptions
async def remove_node(
    request: Request,
    node_id: uuid.UUID,
    force: bool = Query(False),
    admin=Depends(get_current_admin),
    service: NodeService = Depends(NodeService.as_dependency),
):
    await service.remove(node_id, force, admin.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/nodes/{node_id}/drain", summary="Drain a node", status_code=202)
@handle_exceptions
async def drain_node(
    request: Request,
    node_id: uuid.UUID,
    payload: Optional[DrainRequest] = Body(None),
    admin=Depends(get_current_admin),
    service: NodeService = Depends(NodeService.as_dependency),
):
    return _created(await service.drain(node_id, payload or DrainRequest(), admin.id), 202)


@router.post("/nodes/{node_id}/undrain", summary="Undrain a node")
@handle_exceptions
async def undrain_node(
    request: Request,
    node_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: NodeService = Depends(NodeService.as_dependency),
):
    return success_response(data=await service.undrain(node_id, admin.id))


@router.get("/nodes/{node_id}/devices", summary="Devices of a node")
@handle_exceptions
async def list_node_devices(
    request: Request,
    node_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: NodeService = Depends(NodeService.as_dependency),
):
    return success_response(data=await service.list_devices(node_id))


@router.get("/nodes/{node_id}/instances", summary="Instances on a node")
@handle_exceptions
async def list_node_instances(
    request: Request,
    node_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    admin=Depends(get_current_admin),
    service: NodeService = Depends(NodeService.as_dependency),
):
    items, total = await service.list_instances(node_id, page, page_size)
    return success_response(data=items, meta=paginated_meta(total, page, page_size))


@router.get("/nodes/{node_id}/cache", summary="Model cache summary of a node")
@handle_exceptions
async def get_node_cache(
    request: Request,
    node_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: NodeService = Depends(NodeService.as_dependency),
):
    return success_response(data=await service.cache_summary(node_id))


@router.get("/nodes/{node_id}/commands", summary="Agent commands of a node")
@handle_exceptions
async def list_node_commands(
    request: Request,
    node_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    state: Optional[AgentCommandState] = Query(None),
    admin=Depends(get_current_admin),
    service: NodeService = Depends(NodeService.as_dependency),
):
    items, total = await service.list_commands(node_id, page, page_size, state)
    return success_response(data=items, meta=paginated_meta(total, page, page_size))


@router.get("/nodes/{node_id}/certificates", summary="Certificates of a node")
@handle_exceptions
async def list_node_certificates(
    request: Request,
    node_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: NodeService = Depends(NodeService.as_dependency),
):
    return success_response(data=await service.list_certificates(node_id))


@router.post(
    "/nodes/{node_id}/certificates/rotate",
    summary="Rotate the node certificate",
    status_code=202,
)
@handle_exceptions
async def rotate_node_certificate(
    request: Request,
    node_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: NodeService = Depends(NodeService.as_dependency),
):
    return _created(await service.rotate_certificate(node_id, admin.id), 202)


@router.post(
    "/nodes/{node_id}/certificates/{cert_id}/revoke",
    summary="Revoke a node certificate",
)
@handle_exceptions
async def revoke_node_certificate(
    request: Request,
    node_id: uuid.UUID,
    cert_id: uuid.UUID,
    payload: Optional[RevokeRequest] = Body(None),
    admin=Depends(get_current_admin),
    service: NodeService = Depends(NodeService.as_dependency),
):
    reason = payload.reason if payload else None
    return success_response(
        data=await service.revoke_certificate(node_id, cert_id, reason, admin.id)
    )


def _file_response(data):
    code = (
        200
        if data["state"] in ("succeeded", "failed", "rejected", "timed_out", "cancelled")
        else 202
    )
    return JSONResponse(status_code=code, content=jsonable_encoder(success_response(data=data)))


@router.post("/nodes/{node_id}/scan", summary="Scan a path on the node for model files")
@handle_exceptions
async def scan_node_path(
    request: Request,
    node_id: uuid.UUID,
    body: NodeScanRequest,
    admin=Depends(get_current_admin),
):
    args = node_files.scan_args(
        body.path, body.recursive, body.max_depth, body.max_entries, body.read_metadata
    )
    return _file_response(
        await node_files.run_file_command(node_id, "scan_path", args, body.wait_seconds)
    )


@router.post("/nodes/{node_id}/hash", summary="sha256 of files on the node")
@handle_exceptions
async def hash_node_files(
    request: Request,
    node_id: uuid.UUID,
    body: NodeHashRequest,
    admin=Depends(get_current_admin),
):
    args = node_files.hash_args(body.paths, body.root)
    return _file_response(
        await node_files.run_file_command(node_id, "hash_files", args, body.wait_seconds)
    )


@router.get("/nodes/{node_id}/commands/{command_id}", summary="One agent command (poll results)")
@handle_exceptions
async def get_node_command(
    request: Request,
    node_id: uuid.UUID,
    command_id: uuid.UUID,
    admin=Depends(get_current_admin),
):
    from app.core.exceptions import ResourceNotFoundError
    from app.db.postgres.connection import get_session_maker
    from app.db.postgres.repositories.agent_command_repository import (
        AgentCommandRepository,
    )
    from app.services.agent_hub.service import _outcome_from_row

    async with get_session_maker()() as session:
        row = await AgentCommandRepository(session).get_by_id(command_id)
    if row is None or row.node_id != node_id:
        raise ResourceNotFoundError(resource_type="Agent command")
    return success_response(data=node_files.outcome_dict(_outcome_from_row(row), row.command_type))
