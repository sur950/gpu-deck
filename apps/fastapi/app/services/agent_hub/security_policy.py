"""
Node security policy (protocol.md §13, ``security_policy.json``): what a node may connect to
and accept, signed per node with the internal CA key and carried in ``config_push.security``
and the join response.

- ``hub`` / ``api_layer``: key pins (SubjectPublicKeyInfo sha256) from the PKI service:
  current key, the pre-published next key and the previous key during a rotation grace period;
- ``tunnel``: the API-layer tunnel endpoints agents dial (serving mode tunnel);
- ``direct``: source CIDRs the direct-mode proxy accepts (cluster override
  ``clusters.config.node_security.proxy_allowed_source_cidrs``, else
  ``NODE_PROXY_ALLOWED_SOURCE_CIDRS``, else private ranges on the agent);
- ``ssh``: default source CIDRs of SSH instances started without their own (cluster override
  ``ssh_default_allowed_cidrs``, else ``SSH_INSTANCE_DEFAULT_ALLOWED_CIDRS``, else nobody);
- ``ingress_report``: how often agents report rejected inbound attempts and the burst that
  makes the report critical;
- ``release``: the release signing keys agent update manifests must verify with
  (``AGENT_RELEASE_PUBLIC_KEYS``, protocol.md §14).

``serial`` is monotonic per node (``nodes.metadata.security_serial``), independent of hub
clocks: agents refuse a lower serial (rollback).
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.models.generated_models import Clusters, Nodes
from app.db.postgres.repositories.node_repository import NodeRepository
from app.services.pki.service import PkiService
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

API_LAYER_SUBJECT = "CN=api-layer,O=gpudeck-services"
NODE_SECURITY_KEY = "node_security"


def clean_cidrs(values: Any) -> List[str]:
    """Valid CIDRs / addresses only (normalised), invalid entries dropped."""
    out: List[str] = []
    for v in values or []:
        try:
            net = str(ipaddress.ip_network(str(v).strip(), strict=False))
        except ValueError:
            continue
        if net not in out:
            out.append(net)
    return out


def cluster_security(cluster: Optional[Clusters]) -> Dict[str, Any]:
    cfg = (cluster.config if cluster is not None else None) or {}
    sec = cfg.get(NODE_SECURITY_KEY) if isinstance(cfg, dict) else None
    return sec if isinstance(sec, dict) else {}


async def policy_document(
    session: AsyncSession, node: Nodes, pki: PkiService, serial: int
) -> Dict[str, Any]:
    cluster = await session.get(Clusters, node.cluster_id) if node.cluster_id else None
    sec = cluster_security(cluster)
    proxy_cidrs = clean_cidrs(
        sec.get("proxy_allowed_source_cidrs")
        or settings.split_cidrs(settings.NODE_PROXY_ALLOWED_SOURCE_CIDRS)
    )
    ssh_cidrs = clean_cidrs(
        sec.get("ssh_default_allowed_cidrs")
        or settings.split_cidrs(settings.SSH_INSTANCE_DEFAULT_ALLOWED_CIDRS)
    )
    doc: Dict[str, Any] = {
        "v": 1,
        "node_id": str(node.id),
        "serial": serial,
        "issued_at": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "hub": {"spki_sha256": await pki.hub_pins()},
        "api_layer": {
            "spki_sha256": await pki.api_layer_pins(),
            "subjects": [API_LAYER_SUBJECT],
        },
        "tunnel": {
            "endpoints": settings.get_tunnel_endpoints(),
            "connections": int(settings.AGENT_TUNNEL_CONNECTIONS),
            "idle_timeout_seconds": int(settings.AGENT_TUNNEL_IDLE_TIMEOUT_SECONDS),
        },
        "direct": {"allowed_source_cidrs": proxy_cidrs},
        "ssh": {"default_allowed_cidrs": ssh_cidrs},
        "ingress_report": {
            "interval_seconds": int(settings.NODE_INGRESS_REPORT_SECONDS),
            "burst_threshold": int(settings.NODE_INGRESS_ALERT_THRESHOLD),
        },
    }
    # protocol.md §14: the keys release manifests must be signed with (agent self-update).
    release_keys = settings.get_release_public_keys()
    if release_keys:
        doc["release"] = {"public_keys": release_keys[:8]}
    return doc


async def next_serial(session: AsyncSession, node: Nodes) -> int:
    """Monotonic per node: max(now in ms, last serial + 1), stored in nodes.metadata."""
    meta = dict(node.metadata_ or {})
    serial = max(int(time.time() * 1000), int(meta.get("security_serial") or 0) + 1)
    await NodeRepository(session).merge_metadata(node.id, {"security_serial": serial})
    node.metadata_ = {**meta, "security_serial": serial}
    return serial


async def build_signed_policy(
    session: AsyncSession, node: Nodes, pki: Optional[PkiService] = None
) -> Dict[str, str]:
    """The signed policy for ``node`` (``common.json#/$defs/SignedPolicy``)."""
    pki = pki or PkiService.from_session(session)
    serial = await next_serial(session, node)
    doc = await policy_document(session, node, pki, serial)
    return await pki.sign_policy(doc)


async def pins_signature(session: AsyncSession) -> str:
    """Changes whenever a pin or a global policy input changes (the hub then re-pushes)."""
    pki = PkiService.from_session(session)
    material = {
        "hub": await pki.hub_pins(),
        "api_layer": await pki.api_layer_pins(),
        "tunnel": settings.get_tunnel_endpoints(),
        "proxy": settings.NODE_PROXY_ALLOWED_SOURCE_CIDRS,
        "ssh": settings.SSH_INSTANCE_DEFAULT_ALLOWED_CIDRS,
        "release": settings.get_release_public_keys(),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
