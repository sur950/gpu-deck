"""
Metrics and live routes (admin.yaml: Metrics + Live tags).

GET /metrics/overview        counts, live totals, cost today/month, recent activity
GET /metrics/gpu             GPU series (raw < 1 m step, else 1-minute rollups)
GET /metrics/inference       engine series (sessions, throughput, latency, KV, errors)
GET /metrics/sessions        per-session series for one instance
GET /metrics/sessions/live   current slot/session snapshot per instance
GET /metrics/usage           usage rollups (usage_rollups_hourly)
GET /metrics/catalog         metric catalog
GET /metrics/prometheus      fleet exposition (PROMETHEUS_SCRAPE_TOKEN bearer or admin session)
GET /live/events             SSE: metrics/instance/node/device/swap/rule_run/alert/log/heartbeat
GET /live/ws                 WebSocket equivalent (session cookie or Bearer session token)
"""

import asyncio
import uuid
from typing import List, Optional

from app.core.exceptions import AuthenticationError
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_db, get_session_maker
from app.middlewares.session_verification import get_current_admin, get_optional_admin
from app.services.metrics.live import (
    LiveFilter,
    live_event_stream,
    parse_topics,
    sse_format,
    ws_format,
)
from app.services.metrics.prometheus import check_scrape_token, render_fleet_exposition
from app.services.metrics.service import MetricsService
from app.utils.api_wrapper import handle_exceptions, success_response
from fastapi import (
    APIRouter,
    Depends,
    Header,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Metrics"]

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _ts_response(data) -> dict:
    return success_response(data=data)


@router.get("/metrics/overview", summary="Dashboard overview")
@handle_exceptions
async def get_overview(
    request: Request,
    admin=Depends(get_current_admin),
    service: MetricsService = Depends(MetricsService.as_dependency),
):
    return success_response(data=await service.overview())


@router.get("/metrics/gpu", summary="GPU metrics")
@handle_exceptions
async def query_gpu_metrics(
    request: Request,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    step: str = Query("1m"),
    metrics: Optional[List[str]] = Query(None),
    cluster_id: Optional[uuid.UUID] = Query(None),
    node_id: Optional[uuid.UUID] = Query(None),
    device_id: Optional[List[uuid.UUID]] = Query(None),
    aggregate: str = Query("none", pattern="^(none|sum|avg|max)$"),
    admin=Depends(get_current_admin),
    service: MetricsService = Depends(MetricsService.as_dependency),
):
    data = await service.gpu(from_, to, step, metrics, cluster_id, node_id, device_id, aggregate)
    return _ts_response(data)


@router.get("/metrics/inference", summary="Engine / inference metrics")
@handle_exceptions
async def query_inference_metrics(
    request: Request,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    step: str = Query("1m"),
    metrics: Optional[List[str]] = Query(None),
    model_id: Optional[uuid.UUID] = Query(None),
    deployment_id: Optional[uuid.UUID] = Query(None),
    instance_id: Optional[List[uuid.UUID]] = Query(None),
    node_id: Optional[uuid.UUID] = Query(None),
    aggregate: str = Query("none", pattern="^(none|sum|avg|max)$"),
    admin=Depends(get_current_admin),
    service: MetricsService = Depends(MetricsService.as_dependency),
):
    data = await service.inference(
        from_,
        to,
        step,
        metrics,
        model_id,
        deployment_id,
        instance_id,
        node_id,
        aggregate,
    )
    return _ts_response(data)


@router.get("/metrics/sessions", summary="Per-session series for one instance")
@handle_exceptions
async def query_session_metrics(
    request: Request,
    instance_id: uuid.UUID = Query(...),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    step: str = Query("1m"),
    session_key: Optional[str] = Query(None, max_length=128),
    request_id: Optional[uuid.UUID] = Query(None),
    admin=Depends(get_current_admin),
    service: MetricsService = Depends(MetricsService.as_dependency),
):
    data = await service.sessions(instance_id, from_, to, step, session_key, request_id)
    return _ts_response(data)


@router.get("/metrics/sessions/live", summary="Current sessions across instances")
@handle_exceptions
async def get_live_sessions(
    request: Request,
    deployment_id: Optional[uuid.UUID] = Query(None),
    node_id: Optional[uuid.UUID] = Query(None),
    instance_id: Optional[uuid.UUID] = Query(None),
    admin=Depends(get_current_admin),
    service: MetricsService = Depends(MetricsService.as_dependency),
):
    return success_response(data=await service.sessions_live(deployment_id, node_id, instance_id))


@router.get("/metrics/usage", summary="Usage over time")
@handle_exceptions
async def query_usage_metrics(
    request: Request,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    step: str = Query("1h"),
    metrics: Optional[List[str]] = Query(None),
    api_key_id: Optional[uuid.UUID] = Query(None),
    model_id: Optional[uuid.UUID] = Query(None),
    node_id: Optional[uuid.UUID] = Query(None),
    deployment_id: Optional[uuid.UUID] = Query(None),
    group_by: str = Query("none", pattern="^(none|api_key|model|node|deployment)$"),
    admin=Depends(get_current_admin),
    service: MetricsService = Depends(MetricsService.as_dependency),
):
    data = await service.usage(
        from_, to, step, metrics, api_key_id, model_id, node_id, deployment_id, group_by
    )
    return _ts_response(data)


@router.get("/metrics/catalog", summary="Metric catalog")
@handle_exceptions
async def list_metric_names(request: Request, admin=Depends(get_current_admin)):
    return success_response(data=MetricsService.catalog())


@router.get("/metrics/prometheus", summary="Prometheus exposition of the fleet")
@handle_exceptions
async def get_prometheus_exposition(
    request: Request,
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    if not check_scrape_token(authorization, settings.PROMETHEUS_SCRAPE_TOKEN):
        credentials = None
        if authorization and authorization.lower().startswith("bearer "):
            credentials = HTTPAuthorizationCredentials(
                scheme="Bearer", credentials=authorization[7:].strip()
            )
        admin = await get_optional_admin(request, Response(), credentials, db)
        if admin is None:
            raise AuthenticationError("Scrape token or admin session required")
    body = await render_fleet_exposition(db)
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")


# ==========================================
# Live (SSE / WebSocket)
# ==========================================


def _live_filter(
    topics: Optional[List[str]],
    cluster_id: Optional[uuid.UUID],
    node_id: Optional[uuid.UUID],
    deployment_id: Optional[uuid.UUID],
    instance_id: Optional[uuid.UUID],
) -> LiveFilter:
    picked, unknown = parse_topics(topics)
    if unknown:
        from app.core.exceptions import ValidationError

        raise ValidationError(f"unknown topics: {', '.join(unknown)}")
    return LiveFilter(
        topics=picked,
        cluster_id=cluster_id,
        node_id=node_id,
        deployment_id=deployment_id,
        instance_id=instance_id,
    )


@router.get(
    "/live/events",
    summary="SSE stream of live metrics and state changes",
    tags=["Live"],
)
async def stream_live_events(
    request: Request,
    topics: Optional[List[str]] = Query(None),
    cluster_id: Optional[uuid.UUID] = Query(None),
    node_id: Optional[uuid.UUID] = Query(None),
    deployment_id: Optional[uuid.UUID] = Query(None),
    instance_id: Optional[uuid.UUID] = Query(None),
    interval_seconds: float = Query(2.0, ge=1.0, le=60.0),
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    admin=Depends(get_current_admin),
):
    flt = _live_filter(topics, cluster_id, node_id, deployment_id, instance_id)

    async def _gen():
        async for event in live_event_stream(
            flt, interval_seconds, last_event_id, request.is_disconnected
        ):
            yield sse_format(event)

    return StreamingResponse(_gen(), media_type="text/event-stream", headers=SSE_HEADERS)


def ws_origin_allowed(origin: Optional[str], host: Optional[str]) -> bool:
    """
    Cross-site WebSocket hijacking guard. Browsers always send ``Origin`` on a WebSocket
    handshake and attach the session cookie even from another site's page (SameSite does
    not stop same-site subdomains), so a browser handshake must come from the web UI
    (CORS_ORIGINS), the admin API's own public URL or the same host. Clients without an
    Origin header (CLI, scripts) authenticate with a bearer token as before.
    """
    if not origin:
        return True
    from urllib.parse import urlsplit

    origin = origin.strip().rstrip("/")
    allowed = set(settings.get_cors_origins())
    public = urlsplit(settings.PUBLIC_API_URL or "")
    if public.scheme and public.netloc:
        allowed.add(f"{public.scheme}://{public.netloc}")
    if origin in allowed:
        return True
    return bool(host) and urlsplit(origin).netloc.lower() == str(host).lower()


async def _ws_authenticate(websocket: WebSocket) -> bool:
    from app.services.auth.session_manager import SessionManager

    if not ws_origin_allowed(websocket.headers.get("origin"), websocket.headers.get("host")):
        logger.warning(f"live ws: refused origin {websocket.headers.get('origin')!r}")
        return False

    token = None
    auth = websocket.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = websocket.cookies.get(settings.SESSION_COOKIE_NAME)
    if not token:
        return False
    try:
        async with get_session_maker()() as session:
            data = await SessionManager(session).validate_session(token)
            await session.commit()
        return data is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"live ws auth failed: {exc}")
        return False


@router.websocket("/live/ws", name="live_ws")
async def connect_live_websocket(websocket: WebSocket):
    """
    Live events over WebSocket. A regular FastAPI WebSocket route: the global
    ``set_request_context_dep`` takes an ``HTTPConnection``, so it resolves on
    WebSocket scopes too.
    """
    if not await _ws_authenticate(websocket):
        # Accept first so browsers see the close code (a pre-accept close is a bare 403).
        await websocket.accept()
        await websocket.close(code=4401, reason="authentication required")
        return
    params = websocket.query_params
    try:
        flt = _live_filter(
            params.getlist("topics") or None,
            _opt_uuid(params.get("cluster_id")),
            _opt_uuid(params.get("node_id")),
            _opt_uuid(params.get("deployment_id")),
            _opt_uuid(params.get("instance_id")),
        )
        interval = min(max(float(params.get("interval_seconds") or 2.0), 1.0), 60.0)
    except Exception as exc:  # noqa: BLE001
        await websocket.close(code=1008, reason=str(exc)[:120])
        return
    await websocket.accept()
    closed = {"flag": False}

    async def _is_closed() -> bool:
        return closed["flag"]

    async def _reader() -> None:
        try:
            while True:
                await websocket.receive_text()  # client pings / ignored messages
        except (WebSocketDisconnect, RuntimeError):
            closed["flag"] = True

    reader = asyncio.create_task(_reader())
    try:
        async for event in live_event_stream(flt, interval, None, _is_closed):
            await websocket.send_text(ws_format(event))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        reader.cancel()


def _opt_uuid(value: Optional[str]) -> Optional[uuid.UUID]:
    return uuid.UUID(value) if value else None
