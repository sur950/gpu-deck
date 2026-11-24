"""
PKI routes of the agent hub domain (admin.yaml: PKI tag).

GET  /pki/ca                              internal CA certificate + fingerprint
GET  /pki/crl                             revoked serials / fingerprints + X.509 CRL PEM
GET  /pki/certificates                    every issued cert (kind, revoked, expiring filters)
POST /pki/service-certificates            issue api_layer / control_plane / agent_hub cert  201
POST /pki/certificates/{cert_id}/revoke   revoke any cert (CRL pushed to agents + API layer)

The agent hub itself is **not** served by the admin API: it is its own mTLS
process on :8443 (``python -m app.agent_hub_main``) with ``POST /agent/v1/join``
and ``WS /agent/v1/ws`` (see ``app/agent_hub_main.py`` for why). There is no
``/api/v1/agent-hub`` WebSocket on the admin API.
"""

import uuid
from typing import Optional

from app.core.logger import get_logger
from app.middlewares.session_verification import get_current_admin
from app.schemas.fleet_schema import (
    CaCertificate,
    CertificateKind,
    Crl,
    RevokeRequest,
    ServiceCertificateIssued,
    ServiceCertificateRequest,
)
from app.services.nodes.service import cert_response
from app.services.pki.service import PkiService
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["PKI"]


@router.get("/pki/ca", summary="Internal CA certificate")
@handle_exceptions
async def get_ca_certificate(
    request: Request,
    admin=Depends(get_current_admin),
    pki: PkiService = Depends(PkiService.as_dependency),
):
    return success_response(data=CaCertificate(**await pki.ca_certificate()))


@router.get("/pki/crl", summary="Certificate revocation list")
@handle_exceptions
async def get_crl(
    request: Request,
    admin=Depends(get_current_admin),
    pki: PkiService = Depends(PkiService.as_dependency),
):
    return success_response(data=Crl(**await pki.crl(with_pem=True)))


@router.get("/pki/certificates", summary="List issued certificates")
@handle_exceptions
async def list_certificates(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    kind: Optional[CertificateKind] = Query(None),
    revoked: Optional[bool] = Query(None),
    expiring_within_days: Optional[int] = Query(None, ge=0, le=3650),
    admin=Depends(get_current_admin),
    pki: PkiService = Depends(PkiService.as_dependency),
):
    rows, total = await pki.list_certificates(page, page_size, kind, revoked, expiring_within_days)
    return success_response(
        data=[cert_response(r) for r in rows],
        meta=paginated_meta(total, page, page_size),
    )


@router.post("/pki/service-certificates", summary="Issue a service certificate", status_code=201)
@handle_exceptions
async def issue_service_certificate(
    request: Request,
    payload: ServiceCertificateRequest,
    admin=Depends(get_current_admin),
    pki: PkiService = Depends(PkiService.as_dependency),
):
    issued = await pki.issue_service_certificate(
        payload.kind,
        common_name=payload.common_name,
        sans=payload.sans,
        validity_days=payload.validity_days,
        csr_pem=payload.csr_pem,
        actor_id=admin.id,
    )
    body = ServiceCertificateIssued(
        certificate=cert_response(issued["certificate"]),
        certificate_pem=issued["certificate_pem"],
        private_key_pem=issued["private_key_pem"],
        ca_bundle_pem=issued["ca_bundle_pem"],
    )
    return JSONResponse(status_code=201, content=jsonable_encoder(success_response(data=body)))


@router.post("/pki/certificates/{cert_id}/revoke", summary="Revoke a certificate")
@handle_exceptions
async def revoke_certificate(
    request: Request,
    cert_id: uuid.UUID,
    payload: Optional[RevokeRequest] = Body(None),
    admin=Depends(get_current_admin),
    pki: PkiService = Depends(PkiService.as_dependency),
):
    row = await pki.revoke_and_publish(cert_id, payload.reason if payload else None, admin.id)
    return success_response(data=cert_response(row))
