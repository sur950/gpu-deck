"""
Builds ``config_push`` payloads for a node: proxy routing table, egress
allowlist, engine catalog for the node platform, CRL, CA bundle, agent
settings, model-source mirrors and the CA-signed node security policy
(``security``, protocol.md §13). ``config_version`` is a per-node monotonic
counter kept in ``nodes.metadata.config_version``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.models.generated_models import Clusters, Nodes
from app.db.postgres.repositories.inventory_repository import InventoryRepository
from app.db.postgres.repositories.node_repository import NodeRepository
from app.services.pki.service import PkiService
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

ALL_SECTIONS = (
    "routing",
    "egress_allowlist",
    "engine_catalog",
    "crl",
    "settings",
    "model_sources",
    "security",
)

# Paths the agent proxy may forward for each model type (engine admin paths never appear).
_ALLOWED_PATHS: Dict[str, List[str]] = {
    "chat": ["/v1/chat/completions", "/v1/completions", "/v1/responses", "/health"],
    "thinking": ["/v1/chat/completions", "/v1/completions", "/v1/responses", "/health"],
    "vision": ["/v1/chat/completions", "/v1/responses", "/health"],
    "embedding": ["/v1/embeddings", "/health"],
    "rerank": ["/v1/rerank", "/v1/reranking", "/rerank", "/health"],
    "stt": [
        "/v1/audio/transcriptions",
        "/v1/audio/translations",
        "/inference",
        "/health",
    ],
    "tts": ["/v1/audio/speech", "/health"],
    "image": [
        "/v1/images/generations",
        "/v1/images/edits",
        "/txt2img",
        "/img2img",
        "/health",
    ],
    "video": ["/v1/videos", "/health"],
}


def hub_egress_entry() -> Optional[str]:
    parsed = urlparse(settings.AGENT_HUB_PUBLIC_URL)
    if not parsed.hostname:
        return None
    port = parsed.port or (443 if parsed.scheme in ("https", "wss") else 80)
    return f"{parsed.hostname}:{port}"


def routing_entry(row: Dict[str, Any], node_accepting: bool = True) -> Dict[str, Any]:
    """One ``config_push.routing`` item (optional keys omitted rather than null)."""
    flags = row.get("launch_flags") or {}
    max_concurrent = flags.get("max_concurrency") or flags.get("parallel")
    entry: Dict[str, Any] = {
        "instance_id": str(row["instance_id"]),
        "deployment_id": str(row["deployment_id"]),
        "port": int(row["port"]),
        "served_model_name": row.get("served_model_name") or row.get("model_name") or "",
        "rewrite_model": True,
        "allowed_path_prefixes": _ALLOWED_PATHS.get(
            row.get("model_type") or "chat", ["/v1/", "/health"]
        ),
        "accepting": bool(
            node_accepting
            and row.get("state") == "ready"
            and int(row.get("traffic_weight") or 0) > 0
        ),
        "max_concurrent": (int(max_concurrent) if isinstance(max_concurrent, int) else None),
    }
    for key in ("model_name", "model_type", "engine"):
        if row.get(key):
            entry[key] = row[key]
    return entry


def agent_settings() -> Dict[str, Any]:
    port_range = getattr(settings, "ENGINE_PORT_RANGE", "10240-10340")
    try:
        lo, hi = [int(p) for p in str(port_range).split("-")]
    except ValueError:
        lo, hi = 10240, 10340
    return {
        "heartbeat_interval_seconds": settings.HEARTBEAT_INTERVAL_SECONDS,
        "metrics_interval_seconds": settings.METRICS_INTERVAL_SECONDS,
        "inventory_interval_seconds": settings.INVENTORY_INTERVAL_SECONDS,
        "log_forward": settings.LOG_FORWARD_ENGINE_OUTPUT,
        "log_chunk_max_lines": settings.LOG_CHUNK_MAX_LINES,
        "log_flush_interval_ms": settings.LOG_FLUSH_INTERVAL_MS,
        "engine_port_range": [lo, hi],
        "proxy": {
            "allowed_client_subjects": ["O=gpudeck-services"],
            # Just under the API layer's total budget: the node proxy gives up first and
            # records the 504 as an engine error (auto_heal's error rate sees hung engines).
            "request_timeout_seconds": float(max(1, settings.SERVING_REQUEST_TIMEOUT_SECONDS - 2)),
        },
    }


def model_sources() -> Dict[str, Any]:
    return {
        "huggingface_endpoint": getattr(settings, "HF_ENDPOINT", "https://huggingface.co"),
        "modelscope_endpoint": getattr(
            settings, "MODELSCOPE_ENDPOINT", "https://www.modelscope.cn"
        ),
    }


class ConfigBuilder:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.nodes = NodeRepository(session)
        self.inventory = InventoryRepository(session)
        self.pki = PkiService.from_session(session)

    async def build(
        self,
        node: Nodes,
        full: bool = True,
        sections: Optional[List[str]] = None,
        bump_version: bool = True,
    ) -> Dict[str, Any]:
        wanted = list(ALL_SECTIONS) if full or not sections else list(sections)
        meta = dict(node.metadata_ or {})
        version = int(meta.get("config_version") or 0)
        if bump_version:
            version += 1
            await self.nodes.merge_metadata(node.id, {"config_version": version})
        payload: Dict[str, Any] = {"config_version": version, "full": bool(full)}
        if "routing" in wanted:
            rows = await self.inventory.routing_entries_for_node(node.id)
            # Draining keeps serving (hello_ack.drain only refuses new loads): each instance
            # stops accepting through its own drain (move swap route_off / unload_model).
            node_accepting = node.status != "drained"
            payload["routing"] = [routing_entry(r, node_accepting) for r in rows]
        if "egress_allowlist" in wanted:
            cluster = await self.session.get(Clusters, node.cluster_id)
            entries = list(settings.get_egress_allowlist_default())
            hub = hub_egress_entry()
            if hub and hub not in entries:
                entries.insert(0, hub)
            for extra in (cluster.egress_allowlist if cluster else None) or []:
                if extra not in entries:
                    entries.append(extra)
            payload["egress_allowlist"] = entries
        if "engine_catalog" in wanted:
            payload["engine_catalog"] = await self.inventory.engine_catalog_for(
                node.os, node.arch, node.id
            )
        if "crl" in wanted:
            crl = await self.pki.crl(with_pem=False)
            payload["crl"] = {
                "generated_at": crl["generated_at"],
                "serials": crl["serials"],
                "fingerprints_sha256": crl["fingerprints_sha256"],
            }
            ca = await self.pki.ensure_ca()
            payload["ca_bundle_pem"] = ca.bundle_pem
        if "settings" in wanted:
            payload["settings"] = agent_settings()
            if node.cache_limit_bytes is not None:
                payload["settings"]["cache_limit_bytes"] = int(node.cache_limit_bytes)
        if "model_sources" in wanted:
            payload["model_sources"] = model_sources()
        if "security" in wanted:
            from app.services.agent_hub.security_policy import build_signed_policy

            try:
                payload["security"] = await build_signed_policy(self.session, node, self.pki)
            except Exception as exc:  # noqa: BLE001 - never block routing / CRL pushes
                logger.error(f"security policy for node={node.id} not built: {exc}")
        return payload

    async def crl_delta(self, node: Nodes) -> Dict[str, Any]:
        return await self.build(node, full=False, sections=["crl"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())
