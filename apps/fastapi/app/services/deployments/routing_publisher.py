"""
Routing table publisher (control plane -> API layer, serving.md §7).

Builds the JSON the API layer keeps in memory (model -> deployment -> live
instances with weights, health, accepting flag and live session counts, plus
nodes, CRL and CA bundle), writes it to ``gpu-deck:routing:v1`` and announces
the new version on ``gpu-deck:routing:updated``.

Who calls it:
* ``DeploymentService`` after every change it makes (best effort);
* the reconciler after each tick;
* the ``routing.publish`` scheduled job every ``ROUTING_PUBLISH_INTERVAL_SECONDS``
  (live session counts).

``set_instance_accepting(instance_id, accepting)`` is the drain switch for the
swap manager: a non-accepting instance stays listed (in-flight requests
finish) but the API layer stops sending new ones. Weight 0 does the same
through ``instances.traffic_weight``.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.models.generated_models import (
    Deployments,
    Engines,
    EngineVersions,
    Instances,
    Models,
    ModelVersions,
    Nodes,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

ROUTING_KEY_PARTS = ("routing", "v1")
ROUTING_CHANNEL_PARTS = ("routing", "updated")
VERSION_KEY_PARTS = ("routing", "version")
NOT_ACCEPTING_KEY_PARTS = ("routing", "not_accepting")
ROUTED_STATES = ("loading", "ready", "draining")
DEFAULT_TRAFFIC = {
    "strategy": "least_sessions",
    "failover": True,
    "max_retries": 1,
    "health_eject": True,
}

_pki_cache: Dict[str, Any] = {"at": 0.0, "ca": None, "crl": None}
_PKI_TTL_SECONDS = 60.0


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


TUNNEL_SCHEME = "tunnel://"


def proxy_url(node: Any) -> Optional[str]:
    """
    Where the API layer reaches a node: ``tunnel://<node_id>`` when the agent serves over its
    outbound tunnel (protocol.md §13, the default; no inbound port on the node), else the
    direct-mode proxy ``https://<proxy_address>:<proxy_port>``.
    """
    meta = getattr(node, "metadata_", None) or {}
    if isinstance(meta, dict) and meta.get("serving_mode") == "tunnel":
        return f"{TUNNEL_SCHEME}{node.id}"
    host = getattr(node, "proxy_address", None) or getattr(node, "ip_address", None)
    host = str(host) if host else getattr(node, "hostname", None)
    if not host:
        return None
    if "/" in host:  # INET with a prefix length
        host = host.split("/")[0]
    if ":" in host and not host.startswith("["):  # bare IPv6
        host = f"[{host}]"
    return f"https://{host}:{int(getattr(node, 'proxy_port', None) or 9443)}"


async def _redis():
    from app.db.redis.client import get_redis

    return await get_redis()


async def set_instance_accepting(instance_id: uuid.UUID, accepting: bool) -> None:
    """Drain switch used by the swap manager; republishes the table."""
    from app.db.redis.client import redis_key

    client = await _redis()
    key = redis_key(*NOT_ACCEPTING_KEY_PARTS)
    if accepting:
        await client.srem(key, str(instance_id))
    else:
        await client.sadd(key, str(instance_id))
    await publish_routing_table()


async def _not_accepting() -> Set[str]:
    from app.db.redis.client import redis_key

    try:
        client = await _redis()
        return set(await client.smembers(redis_key(*NOT_ACCEPTING_KEY_PARTS)))
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"not-accepting set unavailable: {exc}")
        return set()


async def _live_engine(instance_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    from app.services.deployments import live

    found = await live.samples("engine", instance_ids)
    return {i: s for i, s in zip(instance_ids, found) if s and live.is_fresh(s)}


async def _pki(session: AsyncSession) -> Dict[str, Any]:
    now = time.monotonic()
    if _pki_cache["ca"] is not None and now - _pki_cache["at"] < _PKI_TTL_SECONDS:
        return {"ca": _pki_cache["ca"], "crl": _pki_cache["crl"]}
    ca_pem, crl = None, {
        "generated_at": _iso(datetime.now(timezone.utc)),
        "serials": [],
        "fingerprints_sha256": [],
    }
    try:
        from app.services.pki.service import PkiService

        pki = PkiService.from_session(session)
        raw = await pki.crl(with_pem=False)
        crl = {
            "generated_at": _iso(raw.get("generated_at")),
            "serials": list(raw.get("serials") or []),
            "fingerprints_sha256": list(raw.get("fingerprints_sha256") or []),
        }
        ca_pem = (await pki.ensure_ca()).bundle_pem
    except Exception as exc:  # noqa: BLE001 - PKI optional for the table
        logger.debug(f"routing table: PKI material unavailable: {type(exc).__name__}: {exc}")
    _pki_cache.update(at=now, ca=ca_pem, crl=crl)
    return {"ca": ca_pem, "crl": crl}


async def build_routing_table(session: AsyncSession, version: int = 0) -> Dict[str, Any]:
    """The full table as a dict (no Redis writes)."""
    deps = (
        await session.execute(
            select(Deployments, Models, ModelVersions, EngineVersions, Engines)
            .join(Models, Models.id == Deployments.model_id)
            .join(ModelVersions, ModelVersions.id == Deployments.model_version_id)
            .join(EngineVersions, EngineVersions.id == Deployments.engine_version_id)
            .join(Engines, Engines.id == EngineVersions.engine_id)
            .where(
                Deployments.deleted_at.is_(None),
                Models.deleted_at.is_(None),
                # External (observed) servers are never routed to.
                Deployments.origin == "managed",
            )
            .order_by(Deployments.created_at)
        )
    ).all()
    dep_ids = [row[0].id for row in deps]
    inst_rows: List[Instances] = []
    if dep_ids:
        inst_rows = list(
            (
                await session.execute(
                    select(Instances)
                    .where(
                        Instances.deployment_id.in_(dep_ids),
                        Instances.state.in_(ROUTED_STATES),
                        Instances.origin == "managed",
                    )
                    .order_by(Instances.created_at)
                )
            )
            .scalars()
            .all()
        )
    nodes = list(
        (
            await session.execute(
                select(Nodes).where(Nodes.deleted_at.is_(None), Nodes.status != "removed")
            )
        )
        .scalars()
        .all()
    )
    node_by_id = {n.id: n for n in nodes}
    not_accepting = await _not_accepting()
    live = await _live_engine([str(i.id) for i in inst_rows])

    by_dep: Dict[uuid.UUID, List[Instances]] = {}
    for inst in inst_rows:
        by_dep.setdefault(inst.deployment_id, []).append(inst)

    models: Dict[str, Dict[str, Any]] = {}
    per_deployment: List[Any] = []
    for dep, model, mv, ev, engine in deps:
        caps = engine.capabilities or {}
        traffic = {**DEFAULT_TRAFFIC, **(dep.traffic_config or {})}
        max_conc = traffic.get("max_concurrent_per_instance")
        instances = []
        for inst in by_dep.get(dep.id, []):
            node = node_by_id.get(inst.node_id)
            sample = live.get(str(inst.id)) or {}
            # A draining node keeps serving until its instances are moved or unloaded
            # (hello_ack.drain: no new loads); each instance leaves routing on its own drain.
            accepting = (
                inst.state == "ready"
                and str(inst.id) not in not_accepting
                and (node is None or node.status in ("online", "unhealthy", "draining"))
            )
            instances.append(
                {
                    "instance_id": str(inst.id),
                    "node_id": str(inst.node_id),
                    "proxy_url": proxy_url(node) if node else None,
                    "served_model_name": inst.served_model_name or model.name,
                    "engine": engine.name,
                    "state": inst.state,
                    "health": inst.health,
                    "accepting": accepting,
                    "weight": int(inst.traffic_weight),
                    "active_sessions": int(sample.get("active_sessions") or 0),
                    "waiting_sessions": int(sample.get("waiting_sessions") or 0),
                    "max_concurrent": max_conc if isinstance(max_conc, int) else None,
                    "pd_role": inst.pd_role,
                    "deployment_id": str(dep.id),
                }
            )
        key = model.name
        entry = models.get(key)
        base = {
            "model_id": str(model.id),
            "type": model.type,
            "family": model.family,
            "display_name": model.display_name,
            "deployment_id": str(dep.id),
            "priority": dep.priority_override or model.priority,
            "desired_running": bool(dep.desired_running),
            "traffic": traffic,
            "native_formats": list(caps.get("native_formats") or ["openai"]),
            "tool_parser": dep.tool_parser,
            "context_size": dep.context_size or model.default_context_size,
            "gpu_count": dep.gpu_count,
            "slice_compute_pct": dep.slice_compute_pct,
            "cluster_id": str(dep.cluster_id) if dep.cluster_id else None,
            "engine": engine.name,
            "created_at": _iso(dep.created_at),
            "version": {
                "model_version_id": str(mv.id),
                "version": mv.version,
                "format": mv.format,
                "quantization": mv.quantization,
                "size_bytes": int(mv.total_size_bytes or 0),
                "parameter_count": mv.parameter_count,
            },
        }
        per_deployment.append((dep.name, model.name, base, list(instances)))
        if entry is None:
            models[key] = {
                "model_id": str(model.id),
                "type": model.type,
                "aliases": list(model.aliases or []),
                "family": model.family,
                "display_name": model.display_name,
                "deployment_id": str(dep.id),
                "deployment_ids": [str(dep.id)],
                "priority": dep.priority_override or model.priority,
                "desired_running": bool(dep.desired_running),
                "traffic": traffic,
                "native_formats": list(caps.get("native_formats") or ["openai"]),
                "tool_parser": dep.tool_parser,
                "context_size": dep.context_size or model.default_context_size,
                "gpu_count": dep.gpu_count,
                "slice_compute_pct": dep.slice_compute_pct,
                "cluster_id": str(dep.cluster_id) if dep.cluster_id else None,
                "engine": engine.name,
                "created_at": _iso(dep.created_at),
                "version": {
                    "model_version_id": str(mv.id),
                    "version": mv.version,
                    "format": mv.format,
                    "quantization": mv.quantization,
                    "size_bytes": int(mv.total_size_bytes or 0),
                    "parameter_count": mv.parameter_count,
                },
                "instances": instances,
            }
        else:
            entry["deployment_ids"].append(str(dep.id))
            entry["desired_running"] = entry["desired_running"] or bool(dep.desired_running)
            entry["instances"].extend(instances)
        models[key].setdefault("deployments", []).append(
            {"id": str(dep.id), "name": dep.name, "engine": engine.name}
        )

    # Every deployment is also addressable by its own (unique) name, so two deployments of
    # one model (e.g. SGLang and TRT-LLM) can be targeted one by one; the model name keeps
    # balancing across all of them. Listed in /v1/models only when the model name is shared.
    taken = {k.lower() for k in models} | {
        str(a).lower() for e in models.values() for a in e.get("aliases") or []
    }
    for dep_name, model_name, base, instances in per_deployment:
        if not dep_name or dep_name.lower() in taken:
            continue
        taken.add(dep_name.lower())
        shared = len(models[model_name]["deployment_ids"]) > 1
        models[dep_name] = {
            **base,
            "aliases": [],
            "deployment_ids": [base["deployment_id"]],
            "deployment_name": dep_name,
            "model_name": model_name,
            "listed": shared,
            "instances": instances,
        }

    pki = await _pki(session)
    return {
        "version": version,
        "generated_at": _iso(datetime.now(timezone.utc)),
        "models": models,
        "nodes": {
            str(n.id): {
                "name": n.name,
                "proxy_url": proxy_url(n),
                "status": n.status,
                "cert_fingerprint": n.cert_fingerprint,
            }
            for n in nodes
        },
        "crl": pki["crl"],
        "ca_bundle_pem": pki["ca"],
    }


async def publish_routing_table(
    session: Optional[AsyncSession] = None,
) -> Optional[int]:
    """Rebuild, store and announce the table. Returns the version (None on failure)."""
    from app.db.redis.client import redis_key

    try:
        if session is None:
            from app.db.postgres.connection import get_session_maker

            async with get_session_maker()() as own:
                table = await build_routing_table(own)
        else:
            table = await build_routing_table(session)
        client = await _redis()
        version = int(await client.incr(redis_key(*VERSION_KEY_PARTS)))
        table["version"] = version
        await client.set(redis_key(*ROUTING_KEY_PARTS), json.dumps(table, default=str))
        await client.publish(redis_key(*ROUTING_CHANNEL_PARTS), str(version))
        return version
    except Exception as exc:  # noqa: BLE001 - never break the caller
        logger.warning(f"routing table publish failed: {type(exc).__name__}: {exc}")
        return None


def publish_interval_seconds() -> int:
    return int(getattr(settings, "ROUTING_PUBLISH_INTERVAL_SECONDS", 2) or 2)
