"""
Hub listener routes, mounted by ``app/agent_hub_main.py`` on :8443:

- ``POST /agent/v1/join``  — one-time token + CSR -> node certificate (plain TLS)
- ``WS   /agent/v1/ws``    — mTLS agent connection (client cert required)
- ``GET  /agent/v1/ca``    — CA bundle (public; lets ``--ca-fingerprint`` pin it)
- ``GET  /agent/v1/ca.pem`` — the same CA as plain PEM (the install one-liner pins its sha256)
- ``GET  /agent/v1/downloads/{version}/{target}[/sha256]`` — agent builds (join token)
- ``GET  /agent/v1/install.sh`` — installer for a join token
- ``GET  /agent/v1/updates/{version}/{target}`` — a staged agent update (download token from
  ``stage_update``, agent protocol §14)

A GPU machine needs nothing but these (outbound 8443): install script, build,
checksum, CA, join and the WebSocket.

Not part of the admin API, so this is not an ``app/api/v1/routes`` module.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.logger import get_logger
from app.core.rate_limiter import rate_limit
from app.core.request_context import get_client_ip_from_request
from app.core.settings import settings
from app.db.postgres.connection import get_db
from app.services.agent_hub.connection import AgentConnection
from app.services.agent_hub.protocol import CLOSE_REJECTED
from app.services.nodes.service import JoinRequest, NodeJoinService
from app.services.pki import ca as cax
from app.services.pki.service import PkiService
from app.utils.api_wrapper import handle_exceptions, success_response
from fastapi import APIRouter, Depends, Query, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Agent Hub"]


class JoinBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    join_token: str = Field(min_length=10, max_length=128)
    csr_pem: str
    hostname: str = Field(max_length=255)
    name: Optional[str] = Field(None, max_length=255)
    os: str
    arch: str
    agent_version: str
    protocol_version: int = 1
    proxy_port: int = Field(default=9443, ge=1, le=65535)
    labels: Dict[str, str] = Field(default_factory=dict)
    ip_addresses: List[str] = Field(default_factory=list)


@router.post("/agent/v1/join", summary="Join a node (one-time token + CSR)")
@rate_limit(settings.JOIN_RATE_LIMIT)
@handle_exceptions
async def join(
    request: Request,
    body: JoinBody,
    service: NodeJoinService = Depends(NodeJoinService.as_dependency),
):
    result = await service.join(
        JoinRequest(**body.model_dump()), client_ip=get_client_ip_from_request(request)
    )
    return JSONResponse(status_code=200, content=success_response(result))


@router.get("/agent/v1/ca", summary="Internal CA certificate", include_in_schema=False)
@handle_exceptions
async def ca_bundle(request: Request, pki: PkiService = Depends(PkiService.as_dependency)):
    return success_response(await pki.ca_certificate())


@router.get("/agent/v1/ca.pem", summary="Internal CA certificate (PEM)", include_in_schema=False)
@handle_exceptions
async def ca_pem(request: Request, pki: PkiService = Depends(PkiService.as_dependency)):
    from fastapi.responses import PlainTextResponse

    ca = await pki.ensure_ca()
    return PlainTextResponse(
        ca.cert_pem,
        media_type="application/x-pem-file",
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    "/agent/v1/downloads/{version}/{target}",
    summary="Agent build (join token)",
    include_in_schema=False,
    response_model=None,
)
@handle_exceptions
async def hub_download_agent(
    request: Request,
    version: str,
    target: str,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    from app.services.nodes import agent_downloads as dl

    return await dl.download_response(request, db, version, target)


@router.get(
    "/agent/v1/downloads/{version}/{target}/sha256",
    summary="Agent build sha256 (join token)",
    include_in_schema=False,
    response_model=None,
)
@handle_exceptions
async def hub_download_agent_sha256(
    request: Request,
    version: str,
    target: str,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    from app.services.nodes import agent_downloads as dl

    return await dl.download_sha256_response(request, db, version, target)


@router.get(
    "/agent/v1/install.sh",
    summary="Install script (join token)",
    include_in_schema=False,
    response_model=None,
)
@handle_exceptions
async def hub_install_script(
    request: Request,
    version: Optional[str] = Query(None, max_length=64),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    from app.services.nodes import agent_downloads as dl

    return await dl.install_script_response(request, db, version)


@router.get(
    "/agent/v1/updates/{version}/{target}",
    summary="Agent update binary (stage_update download token)",
    include_in_schema=False,
    response_model=None,
)
@handle_exceptions
async def hub_agent_update(request: Request, version: str, target: str):
    """
    The ``gpudeck-agent`` binary a node stages (agent protocol §14). Only for the holder of the
    download token sent in that node's ``stage_update`` (bound to the version and target); the
    binary was checked against the signed release manifest, and the node checks it again.
    """
    from app.core.exceptions import AuthenticationError
    from app.services.agent_updates import releases, tokens

    node_id = await tokens.check(request.headers.get(tokens.HEADER), version, target)
    if node_id is None:
        raise AuthenticationError("a valid update download token is required")
    path = await releases.binary_for(version, target)
    logger.info(f"hub: node={node_id} downloads agent {version} ({target})")
    return releases.binary_response(path, version, target)


@router.websocket("/agent/v1/ws")
async def agent_ws(websocket: WebSocket):
    """Agent connection; identity from the mTLS client certificate (CN = node id)."""
    tls: Dict[str, Any] = (websocket.scope.get("extensions") or {}).get("tls") or {}
    der = tls.get("client_cert_der")
    cert: Optional[cax.CertInfo] = None
    if der:
        try:
            cert = cax.cert_info_from_der(der)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"hub: unreadable client certificate: {exc}")
    if cert is None or cert.organization != cax.NODE_ORG or not _is_uuid(cert.common_name):
        await websocket.accept()
        await websocket.close(code=CLOSE_REJECTED, reason="client certificate required")
        return
    runtime = websocket.app.state.hub_runtime
    await AgentConnection(websocket, cert, runtime).run()


def _is_uuid(value: Optional[str]) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError):
        return False
