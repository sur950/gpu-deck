"""
PKI service: internal certificate authority for the fleet.

- ``ensure_ca()``: load the CA from the vault (``internal-ca``), import it from
  ``PKI_CA_CERT_PATH``/``PKI_CA_KEY_PATH`` on first boot, or generate one
  (``CN=GPU Deck Internal CA``, ECDSA P-384, 10 years).
- sign node CSRs at join and on rotation (``node_certificates.kind=node_agent``)
- issue service certificates (api_layer client cert, agent_hub server cert,
  control_plane client cert), server-generated key or bring-your-own CSR
- revoke certificates and build the CRL (serials + fingerprints + X.509 PEM)
- materialise the hub TLS files for uvicorn and the built-in client identities
  (control plane, API layer) from the vault into ``PKI_CA_DIR``, so a fresh
  stack needs no manual certificate steps
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.exceptions import ConflictError, ResourceNotFoundError, ValidationError
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_db, get_session_maker
from app.db.postgres.models.generated_models import NodeCertificates
from app.db.postgres.repositories.fleet_event_repository import FleetEventRepository
from app.db.postgres.repositories.node_certificate_repository import (
    NodeCertificateRepository,
)
from app.services.pki import ca as cax
from app.services.pki.secret_store import (
    SecretStore,
    build_secret_store,
    dumps_secret,
    loads_secret,
)
from cryptography import x509
from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

CA_SECRET_NAME = "internal-ca"
HUB_TLS_SECRET_NAME = "agent-hub-tls"
CONTROL_PLANE_TLS_SECRET_NAME = "control-plane-tls"
API_LAYER_TLS_SECRET_NAME = "api-layer-tls"


class CaMaterial:
    def __init__(self, key: cax.PrivateKey, cert: x509.Certificate):
        self.key = key
        self.cert = cert
        self.cert_pem = cax.cert_to_pem(cert)
        self.fingerprint = cax.fingerprint_sha256(cert)

    @property
    def bundle_pem(self) -> str:
        return self.cert_pem


_ca_cache: Optional[CaMaterial] = None


class PkiService:
    def __init__(
        self,
        cert_repo: NodeCertificateRepository,
        store: SecretStore,
        events: FleetEventRepository,
        session: Optional[AsyncSession] = None,
    ):
        self.cert_repo = cert_repo
        self.store = store
        self.events = events
        self.session = session

    @staticmethod
    async def as_dependency(request: Request, db: AsyncSession = Depends(get_db)) -> "PkiService":
        return PkiService.from_session(db)

    @staticmethod
    def from_session(session: AsyncSession) -> "PkiService":
        return PkiService(
            NodeCertificateRepository(session),
            build_secret_store(session),
            FleetEventRepository(session),
            session,
        )

    # ---------- CA ----------

    async def ensure_ca(self) -> CaMaterial:
        """Load (or import / generate once) the CA. Cached per process."""
        global _ca_cache
        if _ca_cache is not None:
            return _ca_cache
        raw = await self.store.get_secret(CA_SECRET_NAME)
        if not raw and self.session is not None:
            # Admin API and hub may boot together: serialise first-boot CA creation.
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext('gpudeck:pki:internal-ca'))")
            )
            raw = await self.store.get_secret(CA_SECRET_NAME)
        if raw:
            data = loads_secret(raw)
            material = CaMaterial(
                cax.key_from_pem(data["key_pem"]), cax.cert_from_pem(data["cert_pem"])
            )
        else:
            material = await self._create_ca()
        _ca_cache = material
        return material

    async def _create_ca(self) -> CaMaterial:
        if settings.PKI_CA_CERT_PATH and settings.PKI_CA_KEY_PATH:
            key = cax.key_from_pem(Path(settings.PKI_CA_KEY_PATH).read_text())
            cert = cax.cert_from_pem(Path(settings.PKI_CA_CERT_PATH).read_text())
            logger.info(f"PKI: importing CA from {settings.PKI_CA_CERT_PATH}")
        else:
            key, cert = cax.generate_ca(
                settings.PKI_CA_KEY_TYPE, validity_days=settings.PKI_CA_VALIDITY_DAYS
            )
            logger.info("PKI: generated a new internal CA")
        await self.store.put_secret(
            CA_SECRET_NAME,
            dumps_secret({"key_pem": cax.key_to_pem(key), "cert_pem": cax.cert_to_pem(cert)}),
            "ca_key",
            "internal_ca",
            "Internal CA private key and certificate (managed by the PKI service)",
        )
        await self.events.audit(
            "pki.ca_created",
            resource_type="ca",
            message="internal CA created",
            after_state={"fingerprint_sha256": cax.fingerprint_sha256(cert)},
        )
        return CaMaterial(key, cert)

    async def ca_certificate(self) -> Dict[str, Any]:
        ca = await self.ensure_ca()
        info = cax.cert_info(ca.cert)
        return {
            "certificate_pem": ca.cert_pem,
            "fingerprint_sha256": ca.fingerprint,
            "subject": info.subject,
            "not_after": info.not_after,
        }

    # ---------- issuing ----------

    async def _record(
        self, cert: x509.Certificate, kind: str, node_id: Optional[uuid.UUID]
    ) -> NodeCertificates:
        info = cax.cert_info(cert)
        return await self.cert_repo.create(
            kind=kind,
            node_id=node_id,
            serial=info.serial,
            fingerprint_sha256=info.fingerprint_sha256,
            subject=info.subject,
            certificate_pem=info.certificate_pem,
            not_before=info.not_before,
            not_after=info.not_after,
            issued_at=datetime.now(timezone.utc),
        )

    async def sign_node_csr(self, node_id: uuid.UUID, csr_pem: str) -> NodeCertificates:
        ca = await self.ensure_ca()
        try:
            cert = cax.sign_node_csr(
                ca.key, ca.cert, csr_pem, node_id, settings.NODE_CERT_VALIDITY_DAYS
            )
        except Exception as exc:  # noqa: BLE001
            raise ValidationError(f"CSR invalid: {exc}") from exc
        row = await self._record(cert, "node_agent", node_id)
        logger.info(f"PKI: issued node cert node={node_id} serial={row.serial}")
        return row

    async def issue_service_certificate(
        self,
        kind: str,
        common_name: Optional[str] = None,
        sans: Optional[List[str]] = None,
        validity_days: Optional[int] = None,
        csr_pem: Optional[str] = None,
        actor_id: Optional[uuid.UUID] = None,
    ) -> Dict[str, Any]:
        if kind not in cax.SERVICE_DEFAULT_CN:
            raise ValidationError(f"unknown service certificate kind {kind}")
        ca = await self.ensure_ca()
        private_key_pem: Optional[str] = None
        if csr_pem is None:
            key = cax.generate_private_key("ecdsa_p256")
            private_key_pem = cax.key_to_pem(key)
            csr_pem = cax.build_csr(
                key, common_name or cax.SERVICE_DEFAULT_CN[kind], cax.SERVICE_ORG
            )
        try:
            cert = cax.sign_service_csr(
                ca.key,
                ca.cert,
                csr_pem,
                kind,
                common_name,
                sans or [],
                validity_days or settings.SERVICE_CERT_VALIDITY_DAYS,
            )
        except Exception as exc:  # noqa: BLE001
            raise ValidationError(f"CSR invalid: {exc}") from exc
        row = await self._record(cert, kind, None)
        await self.events.audit(
            "pki.service_cert_issued",
            actor_type="admin" if actor_id else "system",
            actor_id=actor_id,
            resource_type="certificate",
            resource_id=row.id,
            resource_name=row.subject,
            after_state={
                "kind": kind,
                "serial": row.serial,
                "not_after": row.not_after.isoformat(),
            },
        )
        return {
            "certificate": row,
            "certificate_pem": row.certificate_pem,
            "private_key_pem": private_key_pem,
            "ca_bundle_pem": ca.bundle_pem,
        }

    # ---------- listing / revocation ----------

    async def list_certificates(
        self,
        page: int,
        page_size: int,
        kind: Optional[str],
        revoked: Optional[bool],
        expiring_within_days: Optional[int],
    ) -> Tuple[List[NodeCertificates], int]:
        return await self.cert_repo.list(page, page_size, kind, revoked, expiring_within_days)

    async def revoke(
        self,
        cert_id: uuid.UUID,
        reason: Optional[str],
        actor_id: Optional[uuid.UUID] = None,
        node_id: Optional[uuid.UUID] = None,
    ) -> NodeCertificates:
        row = await self.cert_repo.get_by_id(cert_id)
        if row is None or (node_id is not None and row.node_id != node_id):
            raise ResourceNotFoundError(resource_type="Certificate")
        if row.revoked_at is not None:
            raise ConflictError("certificate already revoked")
        row = await self.cert_repo.revoke(cert_id, reason or "revoked by admin")
        await self.events.audit(
            "node.cert_revoked" if row.kind == "node_agent" else "pki.cert_revoked",
            actor_type="admin" if actor_id else "system",
            actor_id=actor_id,
            resource_type="certificate",
            resource_id=row.id,
            resource_name=row.subject,
            message=reason,
            after_state={
                "serial": row.serial,
                "node_id": str(row.node_id) if row.node_id else None,
            },
        )
        logger.warning(f"PKI: revoked cert serial={row.serial} kind={row.kind} node={row.node_id}")
        return row

    async def revoke_and_publish(
        self,
        cert_id: uuid.UUID,
        reason: Optional[str],
        actor_id: Optional[uuid.UUID] = None,
    ) -> NodeCertificates:
        """Revoke, commit, then push the CRL (agents close / drop the revoked identity)."""
        row = await self.revoke(cert_id, reason, actor_id=actor_id)
        if self.session is not None:
            await self.session.commit()
        from app.services.agent_hub.service import AgentHub

        await AgentHub.broadcast_crl([row.fingerprint_sha256])
        return row

    async def crl(self, with_pem: bool = True) -> Dict[str, Any]:
        revoked = await self.cert_repo.revoked()
        payload: Dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc),
            "serials": [r.serial for r in revoked],
            "fingerprints_sha256": [r.fingerprint_sha256 for r in revoked],
            "crl_pem": None,
        }
        if with_pem:
            ca = await self.ensure_ca()
            payload["crl_pem"] = cax.build_crl(
                ca.key, ca.cert, [(r.serial, r.revoked_at) for r in revoked]
            )
        return payload

    async def is_revoked(self, fingerprint: str) -> bool:
        row = await self.cert_repo.get_by_fingerprint(fingerprint)
        return row is not None and row.revoked_at is not None

    # ---------- runtime TLS material ----------

    async def ensure_service_keypair(
        self, secret_name: str, kind: str, sans: List[str]
    ) -> Dict[str, str]:
        """Key + cert of a built-in service (hub / control plane / API layer) from the vault."""
        raw = await self.store.get_secret(secret_name)
        if raw and await self._usable_keypair(raw, sans):
            return loads_secret(raw)
        if self.session is not None:
            # Several replicas may start together: one issues, the others reuse its pair.
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                {"k": f"gpudeck:pki:{secret_name}"},
            )
            raw = await self.store.get_secret(secret_name)
            if raw and await self._usable_keypair(raw, sans):
                return loads_secret(raw)
        issued = await self.issue_service_certificate(kind, sans=sans)
        data = {
            "key_pem": issued["private_key_pem"],
            "cert_pem": issued["certificate_pem"],
        }
        await self.store.put_secret(
            secret_name,
            dumps_secret(data),
            "tls_key",
            f"{kind}_tls",
            f"{kind} TLS key + certificate (managed by the PKI service)",
        )
        return data

    async def _usable_keypair(self, raw: str, sans: List[str]) -> bool:
        """Stored pair is unexpired, from the current CA, covers ``sans`` and is not revoked."""
        data = loads_secret(raw)
        cert = cax.cert_from_pem(data["cert_pem"])
        row = await self.cert_repo.get_by_fingerprint(cax.fingerprint_sha256(cert))
        still_valid = cert.not_valid_after_utc > datetime.now(timezone.utc)
        ca = await self.ensure_ca()
        same_ca = cert.issuer == ca.cert.subject
        covers = set(s.strip() for s in sans if s.strip()) <= set(cax.cert_sans(cert))
        return still_valid and same_ca and covers and (row is None or row.revoked_at is None)

    async def materialize_hub_tls(self) -> Dict[str, str]:
        """
        Write the files uvicorn needs for the mTLS hub listener and return their paths.
        Env overrides (``AGENT_HUB_TLS_CERT_FILE``/``_KEY_FILE``) win; the CA file is
        always written so client certs can be verified against the vault CA.
        """
        ca = await self.ensure_ca()
        out_dir = Path(settings.PKI_CA_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        ca_path = out_dir / "ca.pem"
        _write_private(ca_path, ca.cert_pem, 0o644)
        cert_file = settings.AGENT_HUB_TLS_CERT_FILE
        key_file = settings.AGENT_HUB_TLS_KEY_FILE
        if not (cert_file and key_file):
            sans = ["localhost", "127.0.0.1"] + settings.get_hub_sans()
            public_host = (
                settings.AGENT_HUB_PUBLIC_URL.split("://", 1)[-1].split("/")[0].split(":")[0]
            )
            if public_host and public_host not in sans:
                sans.append(public_host)
            data = await self.ensure_service_keypair(HUB_TLS_SECRET_NAME, "agent_hub", sans)
            cert_path, key_path = out_dir / "hub.crt", out_dir / "hub.key"
            _write_private(cert_path, data["cert_pem"], 0o644)
            _write_private(key_path, data["key_pem"], 0o600)
            cert_file, key_file = str(cert_path), str(key_path)
        return {
            "certfile": cert_file,
            "keyfile": key_file,
            "ca_certs": settings.AGENT_HUB_CLIENT_CA_FILE or str(ca_path),
        }

    async def materialize_client_tls(self) -> Dict[str, str]:
        """
        Control-plane client identity (``CN=control-plane``, ``O=gpudeck-services``)
        for calls to node proxies (health probes, benchmarks): env paths
        ``CONTROL_PLANE_CLIENT_CERT_PATH/KEY_PATH`` win, else a vault-held cert
        written to ``PKI_CA_DIR``. Returns ``{"certfile", "keyfile", "ca_certs"}``.
        """
        ca = await self.ensure_ca()
        out_dir = Path(settings.PKI_CA_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        ca_path = out_dir / "ca.pem"
        _write_private(ca_path, ca.cert_pem, 0o644)
        if settings.CONTROL_PLANE_CLIENT_CERT_PATH and settings.CONTROL_PLANE_CLIENT_KEY_PATH:
            return {
                "certfile": settings.CONTROL_PLANE_CLIENT_CERT_PATH,
                "keyfile": settings.CONTROL_PLANE_CLIENT_KEY_PATH,
                "ca_certs": str(ca_path),
            }
        data = await self.ensure_service_keypair(CONTROL_PLANE_TLS_SECRET_NAME, "control_plane", [])
        cert_path, key_path = (
            out_dir / "control-plane.crt",
            out_dir / "control-plane.key",
        )
        _write_private(cert_path, data["cert_pem"], 0o644)
        _write_private(key_path, data["key_pem"], 0o600)
        return {
            "certfile": str(cert_path),
            "keyfile": str(key_path),
            "ca_certs": str(ca_path),
        }

    async def materialize_api_layer_tls(self) -> Dict[str, str]:
        """
        API layer client identity (``CN=api-layer``) for the node proxies (:9443): a
        vault-held pair issued on first use and written to ``PKI_CA_DIR``
        (``api-layer.crt`` / ``api-layer.key``). Used when
        ``API_LAYER_CLIENT_CERT_PATH/KEY_PATH`` are not set.
        """
        ca = await self.ensure_ca()
        out_dir = Path(settings.PKI_CA_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        ca_path = out_dir / "ca.pem"
        _write_private(ca_path, ca.cert_pem, 0o644)
        data = await self.ensure_service_keypair(API_LAYER_TLS_SECRET_NAME, "api_layer", [])
        cert_path, key_path = out_dir / "api-layer.crt", out_dir / "api-layer.key"
        _write_private(cert_path, data["cert_pem"], 0o644)
        _write_private(key_path, data["key_pem"], 0o600)
        return {
            "certfile": str(cert_path),
            "keyfile": str(key_path),
            "ca_certs": str(ca_path),
        }


def _write_private(path: Path, content: str, mode: int) -> None:
    """Atomic write (processes sharing PKI_CA_DIR never see a half-written file)."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


async def ensure_ca_standalone() -> CaMaterial:
    """Startup helper usable outside a request (hooks, hub process, CLI)."""
    async with get_session_maker()() as session:
        service = PkiService.from_session(session)
        material = await service.ensure_ca()
        await session.commit()
        return material


def reset_ca_cache_for_tests() -> None:
    global _ca_cache
    _ca_cache = None
