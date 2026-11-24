"""
Inventory reconcile repository: applies an agent ``inventory`` snapshot
to ``devices``, ``node_model_cache`` and ``instances`` (authoritative, per
``agent-protocol/inventory.json``), plus the instance reads the fleet
endpoints need (nodes/{id}/instances, topology, cache summary).

Instances and the model cache are written by the deployments services through the API;
this module only mirrors what the agent reports.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import (
    Deployments,
    Devices,
    Engines,
    EngineVersions,
    Instances,
    ModelFiles,
    Models,
    ModelVersions,
    NodeModelCache,
    Nodes,
)
from sqlalchemy import case, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

LIVE_INSTANCE_STATES = ("pending", "loading", "ready", "draining", "unloading")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stable_device_id(node_id: uuid.UUID, vendor: str, index: int) -> uuid.UUID:
    """
    The agent's stable device id: UUIDv5 in the node-id namespace of
    ``"<vendor>:<index>"`` (vendor = the protocol's lowercase ``device_vendor``).
    The Rust agent computes the same value (``Agent::device_id``), so samples
    for a device carry the right id even before any command assigned one.
    """
    return uuid.uuid5(uuid.UUID(str(node_id)), f"{vendor}:{int(index)}")


class InventoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def release_connection(self) -> None:
        await self.session.commit()

    # ---------- devices ----------

    async def reconcile_devices(
        self, node_id: uuid.UUID, devices: List[Dict[str, Any]]
    ) -> Dict[int, uuid.UUID]:
        """
        Upsert by (node_id, index); soft-delete devices no longer reported. Returns index->id.

        New rows get ``stable_device_id(node, vendor, index)`` as primary key; a
        soft-deleted row with that id is revived. Rows created before the
        stable scheme keep their random id (the agent learns it from command
        ``DeviceAssignment``s, and metrics ingest maps by ``extra.vendor/index``).
        """
        existing = {
            row.index: row
            for row in (
                await self.session.execute(
                    select(Devices).where(Devices.node_id == node_id, Devices.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        }
        seen: Set[int] = set()
        mapping: Dict[int, uuid.UUID] = {}
        now = utcnow()
        for dev in devices:
            index = int(dev["index"])
            seen.add(index)
            fields = dict(
                vendor=dev["vendor"],
                uuid=dev.get("uuid"),
                name=dev["name"],
                vram_total_bytes=int(dev.get("vram_total_bytes") or 0),
                is_unified_memory=bool(dev.get("is_unified_memory", False)),
                driver_version=dev.get("driver_version"),
                runtime_version=dev.get("runtime_version"),
                compute_capability=dev.get("compute_capability"),
                pci_bus_id=dev.get("pci_bus_id"),
                numa_node=dev.get("numa_node"),
                max_power_watts=dev.get("max_power_watts"),
                core_count=dev.get("core_count"),
                health=dev.get("health") or "unknown",
                health_reason=dev.get("health_reason"),
                extra=dev.get("extra") or {},
                last_seen_at=now,
            )
            row = existing.get(index)
            if row is None:
                stable_id = stable_device_id(node_id, dev["vendor"], index)
                if dev.get("device_id"):  # agent >= 1.1.0 sends the same uuid5
                    stable_id = uuid.UUID(str(dev["device_id"]))
                revived = (
                    await self.session.execute(select(Devices).where(Devices.id == stable_id))
                ).scalar_one_or_none()
                if revived is not None:
                    fields["labels"] = {
                        **(dev.get("labels") or {}),
                        **(revived.labels or {}),
                    }
                    await self.session.execute(
                        update(Devices)
                        .where(Devices.id == stable_id)
                        .values(node_id=node_id, index=index, deleted_at=None, **fields)
                    )
                    row = revived
                else:
                    row = Devices(id=stable_id, node_id=node_id, index=index, **fields)
                    row.labels = dev.get("labels") or {}
                    self.session.add(row)
                    await self.session.flush()
            else:
                # Agent labels merge under UI labels (UI wins).
                merged = dict(dev.get("labels") or {})
                merged.update(row.labels or {})
                fields["labels"] = merged
                await self.session.execute(
                    update(Devices).where(Devices.id == row.id).values(**fields)
                )
            mapping[index] = row.id
        gone = [row.id for index, row in existing.items() if index not in seen]
        if gone:
            await self.session.execute(
                update(Devices)
                .where(Devices.id.in_(gone))
                .values(deleted_at=now, health="unknown", health_reason="not reported")
            )
        return mapping

    async def device_index_map(self, node_id: uuid.UUID) -> Dict[Tuple[str, int], uuid.UUID]:
        """``{(vendor, index): device_id}`` for the node's live devices."""
        rows = await self.session.execute(
            select(Devices.vendor, Devices.index, Devices.id).where(
                Devices.node_id == node_id, Devices.deleted_at.is_(None)
            )
        )
        return {(str(v), int(i)): d for v, i, d in rows.all()}

    async def set_device_health(
        self, device_id: uuid.UUID, health: str, reason: Optional[str]
    ) -> None:
        await self.session.execute(
            update(Devices)
            .where(Devices.id == device_id)
            .values(health=health, health_reason=reason, last_seen_at=utcnow())
        )

    # ---------- model cache ----------

    async def reconcile_cache(
        self,
        node_id: uuid.UUID,
        items: List[Dict[str, Any]],
        authoritative: bool = True,
    ) -> int:
        """Upsert node_model_cache rows by (node_id, model_file_id); unknown files are skipped."""
        if items:
            file_ids = list({uuid.UUID(str(i["model_file_id"])) for i in items})
            known = set(
                (
                    await self.session.execute(
                        select(ModelFiles.id).where(ModelFiles.id.in_(file_ids))
                    )
                ).scalars()
            )
        else:
            known = set()
        existing = {
            row.model_file_id: row
            for row in (
                await self.session.execute(
                    select(NodeModelCache).where(NodeModelCache.node_id == node_id)
                )
            )
            .scalars()
            .all()
        }
        seen: Set[uuid.UUID] = set()
        written = 0
        for item in items:
            file_id = uuid.UUID(str(item["model_file_id"]))
            if file_id not in known:
                logger.warning(f"inventory: node={node_id} reports unknown model_file {file_id}")
                continue
            seen.add(file_id)
            fields = dict(
                model_version_id=uuid.UUID(str(item["model_version_id"])),
                local_path=item["local_path"],
                size_bytes=int(item.get("size_bytes") or 0),
                state=item.get("state") or "ready",
                progress_pct=float(item.get("progress_pct") or 0),
                sha256_verified=bool(item.get("sha256_verified", False)),
                error_message=item.get("error_message"),
                last_used_at=_dt(item.get("last_used_at")),
            )
            row = existing.get(file_id)
            if row is None:
                row = NodeModelCache(
                    id=uuid.uuid4(),
                    node_id=node_id,
                    model_file_id=file_id,
                    pinned=bool(item.get("pinned", False)),
                    **fields,
                )
                self.session.add(row)
            else:
                await self.session.execute(
                    update(NodeModelCache).where(NodeModelCache.id == row.id).values(**fields)
                )
            written += 1
        if authoritative:
            gone = [row.id for fid, row in existing.items() if fid not in seen]
            if gone:
                await self.session.execute(
                    update(NodeModelCache)
                    .where(NodeModelCache.id.in_(gone), NodeModelCache.state != "evicted")
                    .values(state="evicted")
                )
        await self.session.flush()
        return written

    async def cache_summary_rows(self, node_id: uuid.UUID) -> List[Dict[str, Any]]:
        sql = text("""
            SELECT c.model_version_id, mv.version, m.id AS model_id, m.name AS model_name,
                   SUM(c.size_bytes) AS size_bytes,
                   MAX(c.state::text) AS state, BOOL_OR(c.pinned) AS pinned,
                   MAX(c.last_used_at) AS last_used_at,
                   SUM(CASE WHEN c.pinned THEN c.size_bytes ELSE 0 END) AS pinned_bytes,
                   COUNT(*) AS item_count,
                   EXISTS (
                       SELECT 1 FROM instances i
                       JOIN deployments d ON d.id = i.deployment_id
                       WHERE i.node_id = c.node_id AND d.model_version_id = c.model_version_id
                         AND i.state IN ('loading','ready','draining')
                   ) AS in_use
            FROM node_model_cache c
            JOIN model_versions mv ON mv.id = c.model_version_id
            JOIN models m ON m.id = mv.model_id
            WHERE c.node_id = :node_id AND c.state <> 'evicted'
            GROUP BY c.node_id, c.model_version_id, mv.version, m.id, m.name
            ORDER BY m.name
            """)
        rows = (await self.session.execute(sql, {"node_id": node_id})).mappings().all()
        return [dict(r) for r in rows]

    # ---------- instances ----------

    async def reconcile_instances(
        self,
        node_id: uuid.UUID,
        reported: List[Dict[str, Any]],
        in_flight_instance_ids: Iterable[uuid.UUID],
    ) -> Tuple[int, List[uuid.UUID]]:
        """
        Mirror reported instance state; instances the hub expects (live states)
        that the agent does not report become ``error`` unless a command for
        them is in flight. Returns (updated, errored_ids).
        """
        expected = {
            row.id: row
            for row in (
                await self.session.execute(
                    select(Instances).where(
                        Instances.node_id == node_id,
                        Instances.state.in_(LIVE_INSTANCE_STATES),
                        # External (observed) instances come from inventory.observed_engines.
                        Instances.origin == "managed",
                    )
                )
            )
            .scalars()
            .all()
        }
        reported_ids: Set[uuid.UUID] = set()
        updated = 0
        now = utcnow()
        for item in reported:
            instance_id = uuid.UUID(str(item["instance_id"]))
            reported_ids.add(instance_id)
            if int(item.get("distributed_rank") or 0) > 0:
                # Headless worker rank of a cross-node pipeline-parallel instance whose
                # head lives on another node: never move node_id / device_ids / state.
                await self._record_pp_worker(node_id, instance_id, item, now)
                continue
            row = expected.get(instance_id)
            if row is None:
                row = (
                    await self.session.execute(select(Instances).where(Instances.id == instance_id))
                ).scalar_one_or_none()
                if row is None:
                    logger.warning(
                        f"inventory: node={node_id} reports unknown instance {instance_id}"
                    )
                    continue
            values: Dict[str, Any] = dict(
                state=item["state"],
                health=item["health"],
                port=item.get("port", row.port),
                served_model_name=item.get("served_model_name") or row.served_model_name,
                engine_process_id=item.get("engine_process_id") or row.engine_process_id,
                pid=item.get("pid"),
                container_id=item.get("container_id"),
                last_health_at=now,
            )
            if item.get("message"):
                values["state_message"] = item["message"]
            if item.get("device_ids") and not (row.resources or {}).get("pp_shards"):
                # Cross-node pipeline-parallel heads reserve every stage's devices
                # (instances.resources.pp_shards); the agent only reports its own.
                values["device_ids"] = [uuid.UUID(str(d)) for d in item["device_ids"]]
            if item["state"] != row.state:
                values["state_changed_at"] = now
                if item["state"] == "ready" and row.ready_at is None:
                    values["ready_at"] = now
                if item["state"] == "stopped":
                    values["stopped_at"] = now
            try:
                async with self.session.begin_nested():
                    await self.session.execute(
                        update(Instances).where(Instances.id == instance_id).values(**values)
                    )
            except IntegrityError as exc:
                # One conflicting row (e.g. two instances claiming the same router port and
                # served name) must not fail the whole inventory or the agent's connection.
                logger.warning(
                    f"inventory: node={node_id} instance {instance_id} not updated: "
                    f"{getattr(exc, 'orig', exc)}"
                )
                continue
            updated += 1
        in_flight = set(in_flight_instance_ids)
        errored: List[uuid.UUID] = []
        for instance_id, row in expected.items():
            if instance_id in reported_ids or instance_id in in_flight:
                continue
            if row.state == "pending":
                continue  # scheduler placed it; the load command has not reached the agent yet
            errored.append(instance_id)
        if errored:
            await self.session.execute(
                update(Instances)
                .where(Instances.id.in_(errored))
                .values(
                    state="error",
                    health="unknown",
                    state_message="not reported by agent inventory",
                    state_changed_at=now,
                )
            )
        return updated, errored

    async def _record_pp_worker(
        self,
        node_id: uuid.UUID,
        instance_id: uuid.UUID,
        item: Dict[str, Any],
        now: datetime,
    ) -> None:
        """Keep the worker rank's state in ``instances.resources.pp_workers[node_id]``."""
        row = (
            await self.session.execute(select(Instances).where(Instances.id == instance_id))
        ).scalar_one_or_none()
        if row is None:
            return
        resources = dict(row.resources or {})
        workers = dict(resources.get("pp_workers") or {})
        workers[str(node_id)] = {
            "rank": int(item.get("distributed_rank") or 0),
            "state": item.get("state"),
            "health": item.get("health"),
            "pid": item.get("pid"),
            "message": item.get("message"),
            "seen_at": now.isoformat(),
        }
        resources["pp_workers"] = workers
        values: Dict[str, Any] = {"resources": resources}
        if item.get("state") == "error" and row.state in ("loading", "ready"):
            values.update(
                state="error",
                state_message=f"pipeline stage on node {node_id} failed",
                state_changed_at=now,
            )
        await self.session.execute(
            update(Instances).where(Instances.id == instance_id).values(**values)
        )

    async def apply_instance_event(
        self,
        instance_id: uuid.UUID,
        state: Optional[str] = None,
        health: Optional[str] = None,
        message: Optional[str] = None,
        node_id: Optional[uuid.UUID] = None,
    ) -> bool:
        """
        Instance state/health from an agent event. When ``node_id`` is not the
        instance's node the event comes from a cross-node pipeline-parallel
        worker rank: only failures propagate (the group is reloaded), the head
        node stays authoritative for everything else.
        """
        if node_id is not None:
            owner = (
                await self.session.execute(
                    select(Instances.node_id, Instances.resources).where(
                        Instances.id == instance_id
                    )
                )
            ).first()
            if owner is not None and owner[0] != node_id:
                if state != "error" and health != "unhealthy":
                    return False
                state = "error" if state == "error" else None
                message = f"pipeline stage on node {node_id} failed: {message or ''}".strip()
        values: Dict[str, Any] = {}
        now = utcnow()
        if state:
            # A repeated event (e.g. engine_crashed for an instance already in error) must not
            # restart the error grace period the reconciler measures from state_changed_at.
            values.update(
                state=state,
                state_changed_at=case(
                    (Instances.state == state, Instances.state_changed_at), else_=now
                ),
            )
            if state == "ready":
                values["ready_at"] = now
            if state == "stopped":
                values["stopped_at"] = now
            if message is not None:
                values["state_message"] = message
        if health:
            values.update(health=health, last_health_at=now)
            if message is not None:
                values["health_message"] = message
        if not values:
            return False
        result = await self.session.execute(
            update(Instances).where(Instances.id == instance_id).values(**values)
        )
        return result.rowcount == 1

    async def heartbeat_instances(self, node_id: uuid.UUID, items: List[Dict[str, Any]]) -> bool:
        """Compact per-heartbeat state (state/health only). True when anything changed."""
        now = utcnow()
        changed = 0
        for item in items:
            result = await self.session.execute(
                update(Instances)
                .where(
                    Instances.id == uuid.UUID(str(item["instance_id"])),
                    Instances.node_id == node_id,
                    (Instances.state != item["state"]) | (Instances.health != item["health"]),
                )
                .values(state=item["state"], health=item["health"], last_health_at=now)
            )
            changed += result.rowcount or 0
        return changed > 0

    async def mark_node_instances_unknown(self, node_id: uuid.UUID) -> int:
        result = await self.session.execute(
            update(Instances)
            .where(Instances.node_id == node_id, Instances.state.in_(LIVE_INSTANCE_STATES))
            .values(health="unknown", health_message="node offline")
        )
        return result.rowcount

    async def list_instances_for_node(
        self,
        node_id: uuid.UUID,
        page: int = 1,
        page_size: int = 50,
        include_stopped: bool = True,
    ) -> Tuple[List[Dict[str, Any]], int]:
        query = (
            select(
                Instances,
                Deployments.name,
                func.coalesce(Models.name, Deployments.external["models"][0].astext),
                Engines.name,
                Nodes.name,
            )
            .join(Deployments, Deployments.id == Instances.deployment_id)
            # outer joins: external (observed) deployments have no model / engine version
            .outerjoin(Models, Models.id == Deployments.model_id)
            .outerjoin(EngineVersions, EngineVersions.id == Deployments.engine_version_id)
            .outerjoin(Engines, Engines.id == EngineVersions.engine_id)
            .join(Nodes, Nodes.id == Instances.node_id)
            .where(Instances.node_id == node_id)
        )
        if not include_stopped:
            query = query.where(Instances.state != "stopped")
        total = (
            await self.session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        rows = (
            await self.session.execute(
                query.order_by(Instances.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return [_instance_dict(*row) for row in rows], total

    async def list_instances_for_nodes(
        self, node_ids: List[uuid.UUID], include_stopped: bool = False
    ) -> List[Dict[str, Any]]:
        if not node_ids:
            return []
        query = (
            select(
                Instances,
                Deployments.name,
                func.coalesce(Models.name, Deployments.external["models"][0].astext),
                Engines.name,
                Nodes.name,
            )
            .join(Deployments, Deployments.id == Instances.deployment_id)
            # outer joins: external (observed) deployments have no model / engine version
            .outerjoin(Models, Models.id == Deployments.model_id)
            .outerjoin(EngineVersions, EngineVersions.id == Deployments.engine_version_id)
            .outerjoin(Engines, Engines.id == EngineVersions.engine_id)
            .join(Nodes, Nodes.id == Instances.node_id)
            .where(Instances.node_id.in_(node_ids))
        )
        if not include_stopped:
            query = query.where(Instances.state != "stopped")
        rows = (await self.session.execute(query.order_by(Instances.created_at))).all()
        return [_instance_dict(*row) for row in rows]

    async def routing_entries_for_node(self, node_id: uuid.UUID) -> List[Dict[str, Any]]:
        """Instances the node proxy must expose (config_push.routing)."""
        sql = text("""
            SELECT i.id AS instance_id, i.deployment_id, i.port, i.served_model_name,
                   i.state, i.traffic_weight, m.name AS model_name, m.type AS model_type,
                   e.name AS engine, d.launch_flags
            FROM instances i
            JOIN deployments d ON d.id = i.deployment_id
            JOIN models m ON m.id = d.model_id
            JOIN engine_versions ev ON ev.id = d.engine_version_id
            JOIN engines e ON e.id = ev.engine_id
            WHERE i.node_id = :node_id AND i.state IN ('ready','draining') AND i.port IS NOT NULL
            ORDER BY i.created_at
            """)
        rows = (await self.session.execute(sql, {"node_id": node_id})).mappings().all()
        return [dict(r) for r in rows]

    async def engine_catalog_for(
        self,
        os: Optional[str],
        arch: Optional[str],
        node_id: Optional[uuid.UUID] = None,
    ) -> List[Dict[str, Any]]:
        """Available engine versions with the artifact selected for the node (platform + GPU)."""
        from app.services.engines import artifacts as arts

        devices: List[Dict[str, Any]] = []
        capabilities: Dict[str, Any] = {}
        if node_id is not None:
            caps_row = await self.session.execute(
                text("SELECT capabilities FROM nodes WHERE id = :node_id"),
                {"node_id": node_id},
            )
            capabilities = caps_row.scalar_one_or_none() or {}
            dev_rows = await self.session.execute(
                text(
                    "SELECT vendor::text AS vendor, driver_version, runtime_version FROM devices"
                    " WHERE node_id = :node_id AND deleted_at IS NULL ORDER BY index LIMIT 1"
                ),
                {"node_id": node_id},
            )
            devices = [dict(r) for r in dev_rows.mappings().all()]
        vendor, driver, runtime = arts.first_device(devices)
        glibc, container = arts.node_host(capabilities)
        sql = text("""
            SELECT ev.id AS engine_version_id, e.name, ev.version, e.runtime, ev.image,
                   ev.artifacts, ev.launch_template, ev.health_config, ev.metrics_config,
                   lower(ev.port_range) AS port_lo, upper(ev.port_range) AS port_hi,
                   e.capabilities, e.supported_os::text[] AS supported_os
            FROM engine_versions ev
            JOIN engines e ON e.id = ev.engine_id
            WHERE ev.deleted_at IS NULL AND e.deleted_at IS NULL AND e.is_enabled
              AND ev.status = 'available'
            ORDER BY e.name, ev.version
            """)
        rows = (await self.session.execute(sql)).mappings().all()
        platform = f"{os}/{arch}" if os and arch else None
        out = []
        for r in rows:
            supported = [str(v) for v in (r["supported_os"] or [])]
            if os and supported and os not in supported:
                continue
            artifacts = r["artifacts"] or {}
            artifact = (
                arts.select(artifacts, os, arch, vendor, driver, runtime, glibc, container)
                if platform
                else None
            )
            if r["runtime"] == "native" and artifacts and artifact is None:
                continue  # no pinned binary for this platform
            out.append(
                {
                    "engine_version_id": str(r["engine_version_id"]),
                    "name": r["name"],
                    "version": r["version"],
                    "runtime": r["runtime"],
                    "image": r["image"],
                    "artifact": artifact,
                    "launch_template": _pick(
                        r["launch_template"] or {},
                        ("command", "env", "working_dir", "router_mode"),
                        {"command": ["{{engine}}"]},
                    ),
                    "health": _pick(
                        r["health_config"] or {},
                        (
                            "path",
                            "method",
                            "interval_seconds",
                            "timeout_seconds",
                            "healthy_statuses",
                            "startup_timeout_seconds",
                            "failure_threshold",
                        ),
                    ),
                    "metrics": _pick(
                        r["metrics_config"] or {},
                        (
                            "path",
                            "format",
                            "slots_path",
                            "scrape_interval_seconds",
                            "derive_from_proxy",
                            "metric_prefix",
                        ),
                    ),
                    "port_range": [r["port_lo"] or 10240, (r["port_hi"] or 10341) - 1],
                    "capabilities": r["capabilities"] or {},
                }
            )
        return out

    async def stop_node_instances(self, node_id: uuid.UUID, reason: str) -> int:
        """Node removed: every live instance on it is gone."""
        now = utcnow()
        result = await self.session.execute(
            update(Instances)
            .where(Instances.node_id == node_id, Instances.state.in_(LIVE_INSTANCE_STATES))
            .values(
                state="stopped",
                health="unknown",
                state_message=reason,
                state_changed_at=now,
                stopped_at=now,
            )
        )
        return result.rowcount

    async def open_swaps_for_node(self, node_id: uuid.UUID) -> int:
        """Swap operations still running for this node (move/drain driven by the swap manager)."""
        result = await self.session.execute(
            text(
                "SELECT COUNT(*) FROM swap_operations WHERE node_id = :node_id AND state NOT IN "
                "('completed','failed','rolled_back','cancelled')"
            ),
            {"node_id": node_id},
        )
        return int(result.scalar_one())

    async def instance_ids_for_node(self, node_id: uuid.UUID) -> List[uuid.UUID]:
        """Live managed instances on the node (drain / remove / drain completion). External
        (observed) servers are not GPU Deck's to drain: a drained node keeps them."""
        result = await self.session.execute(
            select(Instances.id).where(
                Instances.node_id == node_id,
                Instances.state.in_(LIVE_INSTANCE_STATES),
                Instances.origin == "managed",
            )
        )
        return list(result.scalars().all())

    async def model_file_ids_for_version(self, model_version_id: uuid.UUID) -> List[uuid.UUID]:
        result = await self.session.execute(
            select(ModelFiles.id).where(ModelFiles.model_version_id == model_version_id)
        )
        return list(result.scalars().all())

    async def model_versions_exist(self, ids: List[uuid.UUID]) -> Set[uuid.UUID]:
        if not ids:
            return set()
        result = await self.session.execute(
            select(ModelVersions.id).where(ModelVersions.id.in_(ids))
        )
        return set(result.scalars().all())


def _instance_dict(
    inst: Instances,
    deployment_name: str,
    model_name: str,
    engine_name: str,
    node_name: str,
) -> Dict[str, Any]:
    return {
        "id": inst.id,
        "deployment_id": inst.deployment_id,
        "deployment_name": deployment_name,
        "model_name": model_name,
        "node_id": inst.node_id,
        "node_name": node_name,
        "device_ids": list(inst.device_ids or []),
        "port": inst.port,
        "served_model_name": inst.served_model_name,
        "engine_process_id": inst.engine_process_id,
        "engine_name": engine_name,
        "state": inst.state,
        "state_message": inst.state_message,
        "health": inst.health,
        "health_message": inst.health_message,
        "traffic_weight": inst.traffic_weight,
        "pd_role": inst.pd_role,
        "pid": inst.pid,
        "container_id": inst.container_id,
        "resources": inst.resources or {},
        "restart_count": inst.restart_count,
        "state_changed_at": inst.state_changed_at,
        "started_at": inst.started_at,
        "ready_at": inst.ready_at,
        "stopped_at": inst.stopped_at,
        "last_health_at": inst.last_health_at,
        "last_request_at": inst.last_request_at,
        "origin": getattr(inst, "origin", None) or "managed",
        "created_at": inst.created_at,
        "updated_at": inst.updated_at,
    }


def _pick(
    source: Dict[str, Any],
    keys: Iterable[str],
    defaults: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Keep only the keys the agent-protocol schema allows (agents deny unknown fields)."""
    out = dict(defaults or {})
    for key in keys:
        if key in source and source[key] is not None:
            out[key] = source[key]
    if "command" in out and not out["command"]:
        out["command"] = ["{{engine}}"]
    return out


def _dt(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
