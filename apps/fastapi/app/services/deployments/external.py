"""
External (observed) deployments: model servers GPU Deck did not start and only
watches (agent ``[[observe]]``, agent protocol §12).

The node agent lists them in ``inventory.observed_engines``; the hub keeps one
``deployments`` row (``origin = 'external'``, no catalog model / engine
version) and one ``instances`` row per observed server, with the agent's
deterministic ids (``uuid5(node_id, "observe-deployment:<name>")`` and
``uuid5(node_id, "observe:<name>")``) so engine samples land in the normal
metric tables. A server the agent stops reporting (its ``[[observe]]`` entry was
removed: the list is authoritative, an unreachable server is still reported, as
unhealthy) is marked stale and soft-deleted at once; so are the servers of a removed
node. Reported again later, it is revived under the same ids (metrics history kept).
``purge_stale`` still soft-deletes leftovers (stale rows from older versions, nodes
removed while the hub was down).

GPU Deck never acts on them: the scheduler, reconciler, rules engine, swap
manager, layouts, routing table, benchmarks and command dispatch skip
``origin = 'external'`` rows, and every mutating API answers 409
``EXTERNAL_DEPLOYMENT`` (``ensure_managed``). Registration does not depend on
the node's status: a drained node keeps reporting and showing them.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.exceptions import ExternalDeploymentError
from app.core.logger import get_logger
from app.db.postgres.repositories.external_deployment_repository import (
    ExternalDeploymentRepository,
)
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

STALE_PURGE_AFTER = timedelta(hours=24)
ORIGIN_EXTERNAL = "external"
# observed_engine_kind -> engine_name where GPU Deck has the same engine.
ENGINE_NAMES = {"vllm": "vllm", "sglang": "sglang", "llamacpp": "llama_cpp"}
ENGINE_LABELS = {
    "vllm": "vLLM",
    "sglang": "SGLang",
    "llamacpp": "llama.cpp",
    "tgi": "TGI",
    "openai": "OpenAI-compatible",
    "generic": "Generic HTTP",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_external(row: Any) -> bool:
    """True for an external deployment / instance row (or schema object)."""
    origin = getattr(row, "origin", None)
    return str(getattr(origin, "value", origin)) == ORIGIN_EXTERNAL


def ensure_managed(row: Any, what: str = "deployment") -> None:
    """Raise 409 ``EXTERNAL_DEPLOYMENT`` when ``row`` is external."""
    if is_external(row):
        raise ExternalDeploymentError(
            f"{what} {getattr(row, 'name', None) or getattr(row, 'id', '')} is external: GPU "
            "Deck only observes it and never changes, starts, stops, scales, swaps, routes or "
            "benchmarks it"
        )


def observe_ids(node_id: uuid.UUID, name: str) -> tuple:
    """(instance id, deployment id) the agent derives for observed engine ``name``."""
    nid = uuid.UUID(str(node_id))
    return uuid.uuid5(nid, f"observe:{name}"), uuid.uuid5(nid, f"observe-deployment:{name}")


def _health_status(health: str) -> str:
    return "running" if health == "healthy" else "degraded"


async def _unique_name(repo: ExternalDeploymentRepository, base: str, dep_id: uuid.UUID) -> str:
    name = base[:255]
    if not await repo.name_taken(name, dep_id):
        return name
    return f"{base[:240]}-ext-{dep_id.hex[:6]}"


async def sync_from_inventory(
    session: AsyncSession,
    node_id: uuid.UUID,
    observed: Optional[List[Dict[str, Any]]],
    *,
    authoritative: bool = True,
) -> Dict[str, int]:
    """
    Mirror ``inventory.observed_engines`` of ``node_id``: create / update / revive the
    external deployment + instance of each entry; with ``authoritative`` the ones no
    longer reported become stale. Ids that are not the uuid5 of the node and name are
    ignored, so an agent can only ever describe its own observed servers.
    """
    repo = ExternalDeploymentRepository(session)
    node = await repo.node(node_id)
    node_name = node.name if node is not None else str(node_id)[:8]
    existing = await repo.for_node(node_id)
    now = utcnow()
    seen: set = set()
    for item in observed or []:
        name = str(item.get("name") or "")
        try:
            inst_id = uuid.UUID(str(item["observe_id"]))
            dep_id = uuid.UUID(str(item["deployment_id"]))
        except (KeyError, ValueError):
            continue
        if (inst_id, dep_id) != observe_ids(node_id, name):
            logger.warning(f"observed engine {name!r} from node={node_id}: ids do not match")
            continue
        if await repo.origin_of(dep_id) == "managed" or (
            await repo.instance_origin_of(inst_id) == "managed"
        ):
            logger.warning(f"observed engine {name!r}: id collides with a managed row")
            continue
        seen.add(dep_id)
        health = str(item.get("health") or "unknown")
        models = [str(m) for m in (item.get("models") or [])][:64]
        device_ids = [str(d) for d in (item.get("device_ids") or [])]
        prev = existing.get(dep_id)
        ext = {
            "node_id": str(node_id),
            "node_name": node_name,
            "name": name,
            "engine": item.get("engine"),
            "endpoint": item.get("endpoint"),
            "models": models,
            "version": item.get("version"),
            "sources": list(item.get("sources") or []),
            "pid": item.get("pid"),
            "device_ids": device_ids,
            "health": health,
            "message": item.get("message"),
            "last_ok_at": item.get("last_ok_at"),
            "last_seen_at": now.isoformat(),
            "first_seen_at": ((prev.external or {}).get("first_seen_at") if prev else None)
            or now.isoformat(),
        }
        label = ENGINE_LABELS.get(str(item.get("engine")), str(item.get("engine")))
        values: Dict[str, Any] = {
            "external": ext,
            "status": _health_status(health),
            "status_message": item.get("message"),
            "deleted_at": None,
        }
        create: Dict[str, Any] = {
            "description": (
                f"External {label} server observed on {node_name}"
                + (f" ({item.get('endpoint')})" if item.get("endpoint") else "")
                + ". GPU Deck only reads its metrics and logs."
            )[:2048],
        }
        if prev is not None and prev.deleted_at is None:
            create["name"] = prev.name
        else:
            create["name"] = await _unique_name(repo, f"{name}@{node_name}", dep_id)
            if prev is not None:  # revived: its old name may have been taken meanwhile
                values["name"] = create["name"]
        await repo.upsert_deployment(dep_id, values, create)
        await repo.upsert_instance(
            inst_id,
            dep_id,
            {
                "node_id": node_id,
                "state": "ready" if health != "unhealthy" else "error",
                "state_message": None if health != "unhealthy" else item.get("message"),
                "health": health,
                "health_message": item.get("message"),
                "served_model_name": models[0][:255] if models else None,
                "pid": item.get("pid"),
                "device_ids": [uuid.UUID(d) for d in device_ids],
                "port": None,
                "last_health_at": now,
                "stopped_at": None,
            },
        )
    stale = 0
    if authoritative:
        gone: List[uuid.UUID] = []
        for dep_id, dep in existing.items():
            if dep_id in seen or dep.deleted_at is not None:
                continue
            await repo.mark_stale(dep, now)
            gone.append(dep_id)
            stale += 1
        if gone:
            await repo.soft_delete(gone)
            logger.info(f"observed engines no longer reported by node={node_id}: {len(gone)}")
    return {"external": len(seen), "stale": stale}


async def remove_for_node(session: AsyncSession, node_id: uuid.UUID) -> int:
    """Node removed: soft-delete its external deployments (bookkeeping only, no commit)."""
    repo = ExternalDeploymentRepository(session)
    ids = [dep_id for dep_id, dep in (await repo.for_node(node_id)).items() if not dep.deleted_at]
    return await repo.soft_delete(ids)


async def purge_stale(session: AsyncSession, older_than: timedelta = STALE_PURGE_AFTER) -> int:
    """Soft-delete external deployments stale for longer than ``older_than`` or whose node
    is gone. Bookkeeping only: nothing is sent to any node."""
    repo = ExternalDeploymentRepository(session)
    ids = await repo.stale_or_orphaned(utcnow() - older_than)
    return await repo.soft_delete(ids)


async def ensure_instance_managed(session: AsyncSession, instance_id: Any) -> None:
    """409 when ``instance_id`` is an external instance (command dispatch guard)."""
    if instance_id in (None, ""):
        return
    try:
        iid = uuid.UUID(str(instance_id))
    except ValueError:
        return
    if await ExternalDeploymentRepository(session).instance_origin_of(iid) == ORIGIN_EXTERNAL:
        raise ExternalDeploymentError(
            f"instance {iid} is external: GPU Deck never sends commands for it"
        )


async def ensure_deployment_managed(session: AsyncSession, deployment_id: Any) -> None:
    if deployment_id in (None, ""):
        return
    try:
        did = uuid.UUID(str(deployment_id))
    except ValueError:
        return
    if await ExternalDeploymentRepository(session).origin_of(did) == ORIGIN_EXTERNAL:
        raise ExternalDeploymentError()


async def ensure_command_targets_managed(
    session: AsyncSession, instance_id: Any, args: Optional[Dict[str, Any]]
) -> None:
    """Command dispatch guard: no agent command may target an external instance or
    deployment (409 ``EXTERNAL_DEPLOYMENT`` before anything is persisted or sent)."""
    args = args or {}
    for iid in {instance_id, args.get("instance_id")}:
        await ensure_instance_managed(session, iid)
    await ensure_deployment_managed(session, args.get("deployment_id"))
