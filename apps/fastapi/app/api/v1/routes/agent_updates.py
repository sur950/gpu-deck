"""
Agent update routes (admin.yaml: Agent updates tag; agent protocol §14).

GET  /agent-updates                                   overview: target version, keys, counts
GET  /agent-updates/runs                              update runs (node, cluster, rollout, state)
GET  /agent-updates/runs/{run_id}                     one run with its steps
GET  /agent-updates/rollouts                          rolling updates
GET  /agent-updates/rollouts/{rollout_id}             one rolling update
POST /agent-updates/rollouts/{rollout_id}/cancel      stop starting nodes
GET  /nodes/{node_id}/agent-update                    update status of a node
POST /nodes/{node_id}/agent-update                    Update button: update one node         202
POST /nodes/{node_id}/agent-update/stage              stage the target version now          202
GET  /clusters/{cluster_id}/agent-update              policy, rollout, counts of a cluster
POST /clusters/{cluster_id}/agent-update              rolling update of a cluster            202
PUT  /clusters/{cluster_id}/agent-update/policy       manual / auto in a maintenance window
"""

import uuid
from typing import Optional

from app.core.logger import get_logger
from app.core.rate_limiter import rate_limit
from app.middlewares.session_verification import get_current_admin
from app.schemas.agent_update_schema import (
    AgentRolloutRequest,
    AgentUpdatePolicy,
    AgentUpdateRequest,
    AgentUpdateRunState,
)
from app.services.agent_updates.service import AgentUpdateService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Agent updates"]


def _accepted(data) -> JSONResponse:
    return JSONResponse(status_code=202, content=jsonable_encoder(success_response(data=data)))


@router.get("/agent-updates", summary="Agent update overview")
@handle_exceptions
async def get_agent_update_overview(
    request: Request,
    admin=Depends(get_current_admin),
    service: AgentUpdateService = Depends(AgentUpdateService.as_dependency),
):
    return success_response(data=await service.overview())


@router.get("/agent-updates/runs", summary="List agent update runs")
@handle_exceptions
async def list_agent_update_runs(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    node_id: Optional[uuid.UUID] = Query(None),
    cluster_id: Optional[uuid.UUID] = Query(None),
    rollout_id: Optional[uuid.UUID] = Query(None),
    state: Optional[AgentUpdateRunState] = Query(None),
    admin=Depends(get_current_admin),
    service: AgentUpdateService = Depends(AgentUpdateService.as_dependency),
):
    items, total = await service.list_runs(page, page_size, node_id, cluster_id, rollout_id, state)
    return success_response(data=items, meta=paginated_meta(total, page, page_size))


@router.get("/agent-updates/runs/{run_id}", summary="Get an agent update run")
@handle_exceptions
async def get_agent_update_run(
    request: Request,
    run_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: AgentUpdateService = Depends(AgentUpdateService.as_dependency),
):
    return success_response(data=await service.get_run(run_id))


@router.get("/agent-updates/rollouts", summary="List rolling agent updates")
@handle_exceptions
async def list_agent_rollouts(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    cluster_id: Optional[uuid.UUID] = Query(None),
    admin=Depends(get_current_admin),
    service: AgentUpdateService = Depends(AgentUpdateService.as_dependency),
):
    items, total = await service.list_rollouts(page, page_size, cluster_id)
    return success_response(data=items, meta=paginated_meta(total, page, page_size))


@router.get("/agent-updates/rollouts/{rollout_id}", summary="Get a rolling agent update")
@handle_exceptions
async def get_agent_rollout(
    request: Request,
    rollout_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: AgentUpdateService = Depends(AgentUpdateService.as_dependency),
):
    return success_response(data=await service.get_rollout(rollout_id))


@router.post(
    "/agent-updates/rollouts/{rollout_id}/cancel",
    summary="Cancel a rolling agent update",
)
@rate_limit("30/minute")
@handle_exceptions
async def cancel_agent_rollout(
    request: Request,
    rollout_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: AgentUpdateService = Depends(AgentUpdateService.as_dependency),
):
    return success_response(data=await service.cancel_rollout(rollout_id, admin.id))


@router.get("/nodes/{node_id}/agent-update", summary="Agent update status of a node")
@handle_exceptions
async def get_node_agent_update(
    request: Request,
    node_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: AgentUpdateService = Depends(AgentUpdateService.as_dependency),
):
    return success_response(data=await service.node(node_id))


@router.post("/nodes/{node_id}/agent-update", summary="Update the agent of a node")
@rate_limit("30/minute")
@handle_exceptions
async def update_node_agent(
    request: Request,
    node_id: uuid.UUID,
    payload: Optional[AgentUpdateRequest] = Body(None),
    admin=Depends(get_current_admin),
    service: AgentUpdateService = Depends(AgentUpdateService.as_dependency),
):
    return _accepted(await service.start_node(node_id, payload or AgentUpdateRequest(), admin.id))


@router.post("/nodes/{node_id}/agent-update/stage", summary="Stage an agent update on a node")
@rate_limit("30/minute")
@handle_exceptions
async def stage_node_agent_update(
    request: Request,
    node_id: uuid.UUID,
    payload: Optional[AgentUpdateRequest] = Body(None),
    admin=Depends(get_current_admin),
    service: AgentUpdateService = Depends(AgentUpdateService.as_dependency),
):
    return _accepted(await service.stage_node(node_id, (payload or AgentUpdateRequest()).version))


@router.get("/clusters/{cluster_id}/agent-update", summary="Agent updates of a cluster")
@handle_exceptions
async def get_cluster_agent_update(
    request: Request,
    cluster_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: AgentUpdateService = Depends(AgentUpdateService.as_dependency),
):
    return success_response(data=await service.cluster(cluster_id))


@router.post("/clusters/{cluster_id}/agent-update", summary="Rolling agent update of a cluster")
@rate_limit("10/minute")
@handle_exceptions
async def start_cluster_agent_rollout(
    request: Request,
    cluster_id: uuid.UUID,
    payload: Optional[AgentRolloutRequest] = Body(None),
    admin=Depends(get_current_admin),
    service: AgentUpdateService = Depends(AgentUpdateService.as_dependency),
):
    return _accepted(
        await service.start_cluster(cluster_id, payload or AgentRolloutRequest(), admin.id)
    )


@router.put(
    "/clusters/{cluster_id}/agent-update/policy",
    summary="Set a cluster's agent update policy",
)
@rate_limit("30/minute")
@handle_exceptions
async def set_cluster_agent_update_policy(
    request: Request,
    cluster_id: uuid.UUID,
    payload: AgentUpdatePolicy,
    admin=Depends(get_current_admin),
    service: AgentUpdateService = Depends(AgentUpdateService.as_dependency),
):
    return success_response(data=await service.set_policy(cluster_id, payload, admin.id))
