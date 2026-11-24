"""
Agent download routes (admin.yaml tag "Agent Downloads").

GET /agent/downloads                              available builds (admin)
GET /agent/downloads/{version}/{target}           binary (admin session or usable join token)
GET /agent/downloads/{version}/{target}/sha256    "<sha256>  <file>" (same access)
GET /agent/install.sh                             installer for the join token (token only)

The token travels as ``X-GPUDeck-Join-Token`` (or ``?token=``). Builds missing
locally are fetched from the matching GitHub release and cached
(``app/services/nodes/agent_downloads.py``). GPU machines use the hub's copies
of these routes (``/agent/v1/downloads/...``, ``/agent/v1/install.sh``,
``app/services/agent_hub/routes.py``), so they never need the admin API.
"""

from typing import Optional

from app.core.logger import get_logger
from app.db.postgres.connection import get_db
from app.middlewares.session_verification import get_current_admin, get_optional_admin
from app.services.nodes import agent_downloads as dl
from app.utils.api_wrapper import handle_exceptions, success_response
from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Agent Downloads"]

# Documented in the served spec as in admin.yaml; read by dl.token_from_request.
JOIN_TOKEN_HEADER = Header(
    None,
    alias="X-GPUDeck-Join-Token",
    description="Usable join token (gdj_...); or ?token=",
)
JOIN_TOKEN_QUERY = Query(None, description="Join token (alternative to the header)")


@router.get("/agent/downloads", summary="List node-agent builds")
@handle_exceptions
async def list_agent_downloads(request: Request, admin=Depends(get_current_admin)):
    items = [a.as_dict() for a in await dl.list_with_release()]
    return success_response(
        data=items,
        meta={
            "latest_version": await dl.latest_available_version(),
            "base_url": dl.download_base_url(),
            "release_url": dl.download_settings.AGENT_RELEASE_URL or None,
        },
    )


@router.get(
    "/agent/downloads/{version}/{target}",
    summary="Download a node-agent build",
    response_model=None,
)
@handle_exceptions
async def download_agent(
    request: Request,
    version: str,
    target: str,
    x_gpudeck_join_token: Optional[str] = JOIN_TOKEN_HEADER,
    token: Optional[str] = JOIN_TOKEN_QUERY,
    admin=Depends(get_optional_admin),
    db: AsyncSession = Depends(get_db),
):
    return await dl.download_response(request, db, version, target, admin)


@router.get(
    "/agent/downloads/{version}/{target}/sha256",
    summary="sha256 of a node-agent build",
    response_model=None,
)
@handle_exceptions
async def download_agent_sha256(
    request: Request,
    version: str,
    target: str,
    x_gpudeck_join_token: Optional[str] = JOIN_TOKEN_HEADER,
    token: Optional[str] = JOIN_TOKEN_QUERY,
    admin=Depends(get_optional_admin),
    db: AsyncSession = Depends(get_db),
):
    return await dl.download_sha256_response(request, db, version, target, admin)


@router.get("/agent/install.sh", summary="Install script for a join token", response_model=None)
@handle_exceptions
async def agent_install_script(
    request: Request,
    x_gpudeck_join_token: Optional[str] = JOIN_TOKEN_HEADER,
    token: Optional[str] = JOIN_TOKEN_QUERY,
    version: Optional[str] = Query(
        None, max_length=64, description="Build version (default latest)"
    ),
    db: AsyncSession = Depends(get_db),
):
    return await dl.install_script_response(request, db, version)
