"""
PKI lifecycle hooks.

- admin API: make sure the internal CA exists, so join tokens can show the CA
  fingerprint and the hub can be started independently (the hub process does
  the same on its own startup).
- API layer, worker, scheduler: provision the API layer's client certificate
  for the node proxies (:9443) from the vault when ``API_LAYER_CLIENT_CERT_PATH``
  / ``_KEY_PATH`` are not set, so a fresh stack needs no manual
  ``gpudeck pki issue`` step. When the database is unreachable at start the
  files a previous start wrote to ``PKI_CA_DIR`` are reused.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.core.lifecycle import on_startup
from app.core.logger import get_logger
from app.core.settings import settings

logger = get_logger(__name__)


@on_startup("admin")
async def ensure_internal_ca() -> None:
    from app.services.pki.service import ensure_ca_standalone

    material = await ensure_ca_standalone()
    logger.info(f"PKI: internal CA ready fingerprint={material.fingerprint[:16]}…")


def _configured_files_exist() -> bool:
    cert, key = settings.API_LAYER_CLIENT_CERT_PATH, settings.API_LAYER_CLIENT_KEY_PATH
    return bool(cert and key and os.path.exists(cert) and os.path.exists(key))


@on_startup("serving", "worker", "scheduler")
async def provision_api_layer_client_cert() -> None:
    """Point ``API_LAYER_CLIENT_CERT_PATH/KEY_PATH`` at a vault-issued pair when unset."""
    if _configured_files_exist():
        return
    if settings.API_LAYER_CLIENT_CERT_PATH or settings.API_LAYER_CLIENT_KEY_PATH:
        logger.warning(
            "PKI: API_LAYER_CLIENT_CERT_PATH/KEY_PATH point at missing files; "
            "using the vault-issued API layer certificate instead"
        )
    from app.db.postgres.connection import get_session_maker
    from app.services.pki.service import PkiService

    paths = None
    try:
        async with get_session_maker()() as session:
            paths = await PkiService.from_session(session).materialize_api_layer_tls()
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - never block boot; reuse what is on disk
        logger.warning(f"PKI: could not provision the API layer certificate: {exc}")
        out = Path(settings.PKI_CA_DIR)
        if (out / "api-layer.crt").is_file() and (out / "api-layer.key").is_file():
            paths = {
                "certfile": str(out / "api-layer.crt"),
                "keyfile": str(out / "api-layer.key"),
            }
    if not paths:
        return
    settings.API_LAYER_CLIENT_CERT_PATH = paths["certfile"]
    settings.API_LAYER_CLIENT_KEY_PATH = paths["keyfile"]
    from app.services.serving.upstream import close_upstream_client

    await close_upstream_client()  # rebuilt with the certificate on next use
    logger.info(f"PKI: API layer client certificate ready ({paths['certfile']})")
