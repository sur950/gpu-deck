"""
Hub ingest: turns agent frames into rows and live state.

- ``heartbeat``  -> nodes.last_heartbeat_at, compact instance state, node live cache
- ``inventory``  -> devices / node_model_cache / instances reconcile, node columns
- ``metrics_batch`` -> metric_samples_* (partition retry), Redis latest-sample
  cache, ``live:events`` metrics frame
- ``event``      -> log_chunks (kind=log), instance/device/cache updates, audit +
  alert rows for the kinds that warrant it, ``live:events`` frames

Every handler opens its own session (the hub has no request scope) and
commits once. Redis is best effort: a cache failure is logged, never raised.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_session_maker
from app.db.postgres.repositories.agent_command_repository import AgentCommandRepository
from app.db.postgres.repositories.fleet_event_repository import FleetEventRepository
from app.db.postgres.repositories.inventory_repository import InventoryRepository
from app.db.postgres.repositories.log_repository import LogRepository
from app.db.postgres.repositories.metric_repository import MetricRepository
from app.db.postgres.repositories.node_repository import NodeRepository
from app.db.redis.client import get_redis, redis_key
from app.services.metrics import live_cache
from app.services.metrics.live import publish_live_event

logger = get_logger(__name__)

ALERT_KINDS = {
    "engine_crashed": ("critical", "Engine crashed"),
    "device_error": ("critical", "Device error"),
    "device_health_changed": ("warning", "Device health changed"),
    "cache_pressure": ("warning", "Model cache under pressure"),
    "egress_blocked": ("warning", "Egress blocked"),
    "cert_expiring": ("warning", "Node certificate expiring"),
    "config_rejected": ("warning", "Agent rejected configuration"),
}
STATE_KINDS = {
    "instance_state_changed",
    "instance_health_changed",
    "engine_crashed",
    "engine_stopped",
    "engine_started",
    "drain_completed",
}
AUDIT_KINDS = {
    "cert_rotated",
    "config_applied",
    "config_rejected",
    "agent_started",
    "agent_stopping",
}

_GPU_COLUMNS = (
    "utilization_pct",
    "memory_util_pct",
    "memory_used_bytes",
    "memory_total_bytes",
    "power_watts",
    "power_limit_watts",
    "temperature_c",
    "memory_temperature_c",
    "sm_clock_mhz",
    "memory_clock_mhz",
    "fan_pct",
    "encoder_pct",
    "decoder_pct",
    "pcie_tx_bytes_per_s",
    "pcie_rx_bytes_per_s",
    "ecc_errors_total",
    "error_count",
    "throttle_reasons",
    "process_count",
    "extra",
)
_ENGINE_COLUMNS = (
    "active_sessions",
    "waiting_sessions",
    "slots_total",
    "slots_idle",
    "requests_total",
    "requests_success_total",
    "errors_total",
    "prompt_tokens_total",
    "generated_tokens_total",
    "cached_tokens_total",
    "prompt_tps",
    "generation_tps",
    "ttft_ms_avg",
    "ttft_ms_p50",
    "ttft_ms_p95",
    "e2e_ms_avg",
    "e2e_ms_p50",
    "e2e_ms_p95",
    "tpot_ms_avg",
    "queue_ms_avg",
    "kv_cache_used_tokens",
    "kv_cache_total_tokens",
    "kv_cache_usage_pct",
    "kv_cache_bytes",
    "prefix_cache_hit_rate",
    "spec_accept_rate",
    "process_vram_bytes",
    "process_rss_bytes",
    "health",
    "extra",
)
_SESSION_COLUMNS = (
    "slot_id",
    "session_key",
    "request_id",
    "state",
    "prompt_tokens",
    "generated_tokens",
    "cached_tokens",
    "kv_cache_tokens",
    "kv_cache_bytes",
    "context_size",
    "ttft_ms",
    "elapsed_ms",
    "tokens_per_second",
    "extra",
)


def _ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _uuid(value: Any) -> Optional[uuid.UUID]:
    if value in (None, ""):
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


async def _sync_ssh_instances(
    session: Any, node_id: uuid.UUID, payload: Dict[str, Any]
) -> Dict[str, int]:
    """``inventory.ssh_instances`` -> gpu_instances. Never fails the inventory."""
    try:
        from app.services.gpu_instances.lifecycle import sync_from_inventory

        async with session.begin_nested():  # a failure must not poison the inventory commit
            return await sync_from_inventory(session, node_id, payload.get("ssh_instances") or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"inventory: ssh instance sync failed for node={node_id}: {exc}")
        return {}


async def _sync_external(
    session: Any, node_id: uuid.UUID, payload: Dict[str, Any]
) -> Dict[str, int]:
    """``inventory.observed_engines`` -> external deployments / instances. Never fails the
    inventory. Independent of the node's status (a drained node keeps its observed servers).
    """
    try:
        from app.services.deployments.external import sync_from_inventory

        async with session.begin_nested():
            return await sync_from_inventory(
                session, node_id, payload.get("observed_engines") or []
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"inventory: observed engine sync failed for node={node_id}: {exc}")
        return {}


async def _remap_device_ids(
    session: Any,
    node_id: uuid.UUID,
    payload: Dict[str, Any],
    known: Iterable[uuid.UUID],
) -> Dict[uuid.UUID, uuid.UUID]:
    """
    Samples whose ``device_id`` is not a known device of this node (devices
    created before the stable uuid5 scheme, or an agent that has not learned
    the assignment yet) are mapped by ``extra.vendor`` + ``extra.index``.
    """
    known_set = set(known)
    unknown = [
        s
        for s in payload.get("gpu") or []
        if _uuid(s.get("device_id")) not in known_set
        and isinstance(s.get("extra"), dict)
        and s["extra"].get("vendor") is not None
        and s["extra"].get("index") is not None
    ]
    if not unknown:
        return {}
    by_index = await InventoryRepository(session).device_index_map(node_id)
    out: Dict[uuid.UUID, uuid.UUID] = {}
    for s in unknown:
        try:
            key = (str(s["extra"]["vendor"]), int(s["extra"]["index"]))
        except (TypeError, ValueError):
            continue
        if key in by_index:
            out[_uuid(s["device_id"])] = by_index[key]
    return out


def _row(sample: Dict[str, Any], columns: Iterable[str]) -> Dict[str, Any]:
    return {col: sample.get(col) for col in columns}


async def _live_publish(event: str, data: Dict[str, Any]) -> None:
    await publish_live_event(event, data)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class HubIngest:
    """Every call opens its own session. Instantiated once per hub process."""

    def __init__(self, runtime: Any = None):
        self.runtime = runtime

    def _routing_changed(self) -> None:
        if self.runtime is not None:
            self.runtime.mark_routing_dirty()

    # ---------- heartbeat ----------

    async def heartbeat(self, node_id: uuid.UUID, payload: Dict[str, Any]) -> None:
        async with get_session_maker()() as session:
            nodes = NodeRepository(session)
            inventory = InventoryRepository(session)
            recovered = await nodes.touch_heartbeat(node_id)
            changed = recovered
            if payload.get("instances"):
                changed = (
                    await inventory.heartbeat_instances(node_id, payload["instances"]) or changed
                )
            await session.commit()
        if recovered:
            logger.info(f"node {node_id} back online (heartbeat on a live connection)")
            await _live_publish("node", {"node_id": str(node_id), "status": "online"})
        await live_cache.put(
            "node",
            node_id,
            {
                "ts": _now_iso(),
                "node_id": str(node_id),
                "uptime_seconds": payload.get("uptime_seconds"),
                "config_version": payload.get("config_version"),
                "load": payload.get("load") or {},
                "running_commands": payload.get("running_commands") or [],
                "instances": payload.get("instances") or [],
            },
        )
        if changed:
            self._routing_changed()
        if payload.get("instances"):
            await _live_publish(
                "instance", {"node_id": str(node_id), "instances": payload["instances"]}
            )

    # ---------- inventory ----------

    async def inventory(self, node_id: uuid.UUID, payload: Dict[str, Any]) -> Dict[str, Any]:
        async with get_session_maker()() as session:
            nodes = NodeRepository(session)
            inventory = InventoryRepository(session)
            commands = AgentCommandRepository(session)
            device_map = await inventory.reconcile_devices(node_id, payload.get("devices") or [])
            cache = payload.get("cache") or {}
            written = await inventory.reconcile_cache(node_id, cache.get("items") or [])
            in_flight = [
                c.instance_id for c in await commands.in_flight_for_node(node_id) if c.instance_id
            ]
            updated, errored = await inventory.reconcile_instances(
                node_id, payload.get("instances") or [], in_flight
            )
            ssh_summary = await _sync_ssh_instances(session, node_id, payload)
            external_summary = await _sync_external(session, node_id, payload)
            system = payload.get("system") or {}
            values: Dict[str, Any] = {
                "disks": payload.get("disks") or [],
                "cache_dir": cache.get("dir"),
                "cache_limit_bytes": cache.get("limit_bytes"),
                "last_inventory_at": datetime.now(timezone.utc),
            }
            for key in ("cpu_cores", "memory_total_bytes"):
                if system.get(key) is not None:
                    values[key] = system[key]
            await session.execute(
                nodes.model.__table__.update().where(nodes.model.id == node_id).values(**values)
            )
            meta: Dict[str, Any] = {
                "cache_used_bytes": cache.get("used_bytes"),
                "engine_processes": payload.get("engine_processes") or [],
            }
            if payload.get("config_version") is not None:
                meta["config_applied_version"] = payload["config_version"]
            await nodes.merge_metadata(node_id, meta)
            await session.commit()
        summary = {
            "devices": len(device_map),
            "cache_items": written,
            "instances_updated": updated,
            "instances_errored": [str(i) for i in errored],
            "ssh_instances": ssh_summary,
            "observed_engines": external_summary,
        }
        self._routing_changed()
        await _live_publish("device", {"node_id": str(node_id), **summary})
        logger.info(f"inventory node={node_id} {summary}")
        return summary

    # ---------- metrics ----------

    async def metrics_batch(self, node_id: uuid.UUID, payload: Dict[str, Any]) -> Dict[str, int]:
        gpu_rows: List[Dict[str, Any]] = []
        for s in payload.get("gpu") or []:
            gpu_rows.append(
                {
                    "ts": _ts(s["ts"]),
                    "node_id": node_id,
                    "device_id": _uuid(s["device_id"]),
                    **_row(s, _GPU_COLUMNS),
                }
            )
        engine_rows: List[Dict[str, Any]] = []
        for s in payload.get("engine") or []:
            engine_rows.append(
                {
                    "ts": _ts(s["ts"]),
                    "node_id": node_id,
                    "instance_id": _uuid(s["instance_id"]),
                    "deployment_id": _uuid(s["deployment_id"]),
                    **_row(s, _ENGINE_COLUMNS),
                }
            )
        session_rows: List[Dict[str, Any]] = []
        for s in payload.get("sessions") or []:
            row = {
                "ts": _ts(s["ts"]),
                "node_id": node_id,
                "instance_id": _uuid(s["instance_id"]),
                **_row(s, _SESSION_COLUMNS),
            }
            row["request_id"] = _uuid(row.get("request_id"))
            session_rows.append(row)
        batch = settings.DB_METRICS_INGEST_BATCH_SIZE
        days_ahead = settings.PARTITIONS_DAYS_AHEAD
        counts = {"gpu": 0, "engine": 0, "sessions": 0}
        async with get_session_maker()() as session:
            repo = MetricRepository(session)
            # No FKs on the sample tables: drop samples for ids that are not this node's.
            known_devices = await repo.device_ids_for_node(node_id)
            known_instances = await repo.instance_ids_for_node(node_id)
            remap = await _remap_device_ids(session, node_id, payload, known_devices)
            for r in gpu_rows:
                r["device_id"] = remap.get(r["device_id"], r["device_id"])
            gpu_rows = [r for r in gpu_rows if r["device_id"] in known_devices]
            engine_rows = [r for r in engine_rows if r["instance_id"] in known_instances]
            session_rows = [r for r in session_rows if r["instance_id"] in known_instances]
            for i in range(0, len(gpu_rows), batch):
                counts["gpu"] += await repo.insert_gpu_samples(gpu_rows[i : i + batch], days_ahead)
            for i in range(0, len(engine_rows), batch):
                counts["engine"] += await repo.insert_engine_samples(
                    engine_rows[i : i + batch], days_ahead
                )
            for i in range(0, len(session_rows), batch):
                counts["sessions"] += await repo.insert_session_samples(
                    session_rows[i : i + batch], days_ahead
                )
            await session.commit()
        for x in payload.get("gpu") or []:
            mapped = remap.get(_uuid(x["device_id"]))
            if mapped is not None:
                x["device_id"] = str(mapped)
        known_dev = {str(d) for d in known_devices}
        known_inst = {str(i) for i in known_instances}
        await self._cache_latest(
            node_id,
            {
                "gpu": [x for x in payload.get("gpu") or [] if str(x["device_id"]) in known_dev],
                "engine": [
                    x for x in payload.get("engine") or [] if str(x["instance_id"]) in known_inst
                ],
                "sessions": [
                    x for x in payload.get("sessions") or [] if str(x["instance_id"]) in known_inst
                ],
                "agent": payload.get("agent"),
            },
        )
        return counts

    async def _cache_latest(self, node_id: uuid.UUID, payload: Dict[str, Any]) -> None:
        nid = str(node_id)
        gpu: Dict[str, Dict[str, Any]] = {}
        for sample in payload.get("gpu") or []:
            current = gpu.get(str(sample["device_id"]))
            if current is None or str(sample["ts"]) >= str(current["ts"]):
                gpu[str(sample["device_id"])] = {**sample, "node_id": nid}
        engine: Dict[str, Dict[str, Any]] = {}
        for sample in payload.get("engine") or []:
            current = engine.get(str(sample["instance_id"]))
            if current is None or str(sample["ts"]) >= str(current["ts"]):
                engine[str(sample["instance_id"])] = {**sample, "node_id": nid}
        sessions: Dict[str, List[Dict[str, Any]]] = {}
        for sample in payload.get("sessions") or []:
            sessions.setdefault(str(sample["instance_id"]), []).append(sample)
        snapshots = {}
        for instance_id, rows in sessions.items():
            latest = max(str(r["ts"]) for r in rows)
            snapshots[instance_id] = {
                "ts": latest,
                "node_id": nid,
                "instance_id": instance_id,
                "sessions": [r for r in rows if str(r["ts"]) == latest],
            }
        await live_cache.put_many("gpu", gpu)
        await live_cache.put_many("engine", engine)
        await live_cache.put_many("sessions", snapshots)
        if payload.get("agent"):
            await live_cache.put(
                "agent", nid, {"ts": _now_iso(), "node_id": nid, **payload["agent"]}
            )

    # ---------- events ----------

    async def event(self, node_id: uuid.UUID, frame_id: str, payload: Dict[str, Any]) -> bool:
        """At-least-once: dedup on envelope id for 10 minutes. Returns False on duplicate."""
        try:
            client = await get_redis()
            fresh = await client.set(redis_key("hub", "event_seen", frame_id), "1", ex=600, nx=True)
            if not fresh:
                return False
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"event dedup unavailable: {exc}")
        kind = payload.get("kind")
        async with get_session_maker()() as session:
            inventory = InventoryRepository(session)
            events = FleetEventRepository(session)
            nodes = NodeRepository(session)
            if kind == "log" and payload.get("log"):
                await self._store_log(session, node_id, payload)
            elif kind in (
                "instance_state_changed",
                "instance_health_changed",
            ) and payload.get("instance_id"):
                state = payload.get("state") or {}
                await inventory.apply_instance_event(
                    _uuid(payload["instance_id"]),
                    state=state.get("to") if kind == "instance_state_changed" else None,
                    health=(state.get("to") if kind == "instance_health_changed" else None),
                    message=state.get("reason") or payload.get("message"),
                    node_id=node_id,
                )
            elif kind == "ssh_instance_state_changed" and payload.get("details"):
                from app.services.gpu_instances.lifecycle import apply_status

                details = dict(payload["details"])
                details.setdefault("ssh_instance_id", payload.get("ssh_instance_id"))
                try:
                    async with session.begin_nested():
                        await apply_status(session, node_id, details)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"ssh instance event from node={node_id} failed: {exc}")
            elif kind == "device_health_changed" and payload.get("device_id"):
                state = payload.get("state") or {}
                await inventory.set_device_health(
                    _uuid(payload["device_id"]),
                    state.get("to") or "unknown",
                    state.get("reason"),
                )
            elif kind == "cache_changed" and payload.get("cache_item"):
                await inventory.reconcile_cache(
                    node_id, [payload["cache_item"]], authoritative=False
                )
            elif kind == "config_applied":
                details = payload.get("details") or {}
                await nodes.merge_metadata(
                    node_id, {"config_applied_version": details.get("config_version")}
                )
            elif kind in ("engine_crashed", "engine_stopped") and payload.get("instance_id"):
                await inventory.apply_instance_event(
                    _uuid(payload["instance_id"]),
                    state="error" if kind == "engine_crashed" else "stopped",
                    health="unhealthy" if kind == "engine_crashed" else "unknown",
                    message=payload.get("message"),
                    node_id=node_id,
                )
            subject = payload.get("instance_id") or payload.get("device_id") or ""
            if kind in ALERT_KINDS:
                severity, title = ALERT_KINDS[kind]
                await events.alert(
                    severity if payload.get("severity") != "critical" else "critical",
                    f"node.{kind}",
                    title,
                    message=payload.get("message"),
                    resource_type="node",
                    resource_id=node_id,
                    payload={k: v for k, v in payload.items() if k != "log"},
                    dedupe_key=f"{kind}:{node_id}:{subject}",
                )
            if kind in AUDIT_KINDS:
                await events.audit(
                    f"node.{kind}",
                    actor_type="node_agent",
                    actor_id=node_id,
                    resource_type="node",
                    resource_id=node_id,
                    message=payload.get("message"),
                    metadata=payload.get("details") or {},
                )
            await session.commit()
        if kind in STATE_KINDS:
            self._routing_changed()
        if kind != "log":
            await _live_publish(
                "instance" if payload.get("instance_id") else "node",
                {
                    "node_id": str(node_id),
                    "kind": kind,
                    **{k: v for k, v in payload.items() if k != "log"},
                },
            )
        return True

    async def _store_log(self, session, node_id: uuid.UUID, payload: Dict[str, Any]) -> None:
        log = payload["log"]
        lines = log.get("lines") or []
        if not lines:
            return
        max_lines = max(1, settings.LOG_CHUNK_MAX_LINES)
        rows: List[Dict[str, Any]] = []
        order = ["debug", "info", "warn", "error"]
        for i in range(0, len(lines), max_lines):
            chunk = lines[i : i + max_lines]
            content = "\n".join(line["text"] for line in chunk)
            level = max(
                (line.get("level") or "info" for line in chunk),
                key=lambda lv: order.index(lv) if lv in order else 1,
            )
            rows.append(
                {
                    "ts": _ts(chunk[0]["ts"]),
                    "ts_end": _ts(chunk[-1]["ts"]),
                    "node_id": node_id,
                    "instance_id": _uuid(payload.get("instance_id")),
                    "deployment_id": _uuid(payload.get("deployment_id")),
                    "source": log.get("source") or "agent",
                    "stream": log.get("stream") or chunk[0].get("stream") or "stdout",
                    "level": level,
                    "logger": None,
                    "request_id": None,
                    "line_count": len(chunk),
                    "byte_size": len(content.encode()),
                    "content": content,
                    "attributes": (
                        {"engine_process_id": payload.get("engine_process_id")}
                        if payload.get("engine_process_id")
                        else None
                    ),
                }
            )
        await LogRepository(session).insert_chunks(rows, settings.PARTITIONS_DAYS_AHEAD)
        for row in rows:
            await _live_publish(
                "log",
                {
                    **row,
                    "node_id": str(node_id),
                    "instance_id": (str(row["instance_id"]) if row["instance_id"] else None),
                    "deployment_id": (str(row["deployment_id"]) if row["deployment_id"] else None),
                },
            )
