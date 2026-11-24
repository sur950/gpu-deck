"""
Fleet maintenance: idempotent housekeeping run by the hub sweeper every
few seconds and by the jobs process (``app/workers/tasks/fleet_tasks.py``),
so it keeps happening when either one is down.

- ``mark_stale_nodes_offline``: no heartbeat for ``NODE_OFFLINE_AFTER_SECONDS``
  -> ``nodes.status = offline``, its live instances -> ``health = unknown``,
  live cache entries dropped, ``node`` live event.
- ``expire_commands``: non-terminal commands past ``expires_at`` -> ``timed_out``
  (result published on ``hub:results`` so waiters wake up).
- ``revoke_superseded_certs``: after a rotation the previous node cert is
  revoked ``NODE_CERT_OLD_REVOKE_AFTER_SECONDS`` after the new one connected.
- ``request_cert_rotations``: ``rotate_cert`` for node certs expiring within
  ``NODE_CERT_ROTATE_BEFORE_DAYS`` (once per cert).
- ``reconcile_drains``: draining nodes with nothing left become ``drained``.
- ``alert_offline_nodes``: a ``node.offline`` alert once per disconnect, for any node
  (drained ones too) still offline ``NODE_OFFLINE_AFTER_SECONDS`` after it went away (an
  agent restart that reconnects within that window does not alert).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Set

from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_session_maker
from app.db.postgres.repositories.agent_command_repository import AgentCommandRepository
from app.db.postgres.repositories.fleet_event_repository import FleetEventRepository
from app.db.postgres.repositories.inventory_repository import InventoryRepository
from app.db.postgres.repositories.node_certificate_repository import (
    NodeCertificateRepository,
)
from app.db.postgres.repositories.node_repository import NodeRepository
from app.services.metrics import live_cache
from app.services.metrics.live import publish_live_event

logger = get_logger(__name__)


async def mark_stale_nodes_offline(
    connected: Optional[Set[str]] = None,
) -> List[uuid.UUID]:
    """Returns the ids that went offline."""
    went_offline: List[uuid.UUID] = []
    async with get_session_maker()() as session:
        nodes = NodeRepository(session)
        inventory = InventoryRepository(session)
        for node in await nodes.find_stale(settings.NODE_OFFLINE_AFTER_SECONDS):
            await nodes.set_status(
                node.id,
                "offline",
                f"no heartbeat for {settings.NODE_OFFLINE_AFTER_SECONDS}s",
                disconnected_at=datetime.now(timezone.utc),
            )
            await inventory.mark_node_instances_unknown(node.id)
            went_offline.append(node.id)
        await session.commit()
    for node_id in went_offline:
        await live_cache.drop_node(node_id)
        await publish_live_event("node", {"node_id": str(node_id), "status": "offline"})
        logger.warning(f"node {node_id} marked offline (heartbeat timeout)")
    return went_offline


async def expire_commands() -> int:
    from app.services.agent_hub.service import _publish_result

    async with get_session_maker()() as session:
        repo = AgentCommandRepository(session)
        rows = await repo.expire_overdue()
        await session.commit()
        ids = [r.id for r in rows]
        refreshed = [await repo.get_by_id(i) for i in ids]
    for row in refreshed:
        await _publish_result(row, "timed_out", error_code="timeout")
    if ids:
        logger.info(f"{len(ids)} agent command(s) timed out")
    return len(ids)


async def revoke_superseded_certs() -> int:
    now = datetime.now(timezone.utc).timestamp()
    revoked: List[str] = []
    async with get_session_maker()() as session:
        nodes = NodeRepository(session)
        certs = NodeCertificateRepository(session)
        events = FleetEventRepository(session)
        for node in await nodes.list_active():
            pending = (node.metadata_ or {}).get("pending_cert_revocation")
            if not pending or float(pending.get("after") or 0) > now:
                continue
            row = await certs.get_by_fingerprint(pending.get("fingerprint") or "")
            if row is not None and row.revoked_at is None and row.node_id == node.id:
                await certs.revoke(row.id, "superseded by rotation")
                revoked.append(row.fingerprint_sha256)
                await events.audit(
                    "node.cert_revoked",
                    actor_type="system",
                    resource_type="certificate",
                    resource_id=row.id,
                    resource_name=row.subject,
                    message="superseded by rotation",
                    after_state={"serial": row.serial, "node_id": str(node.id)},
                )
            await nodes.merge_metadata(node.id, {"pending_cert_revocation": None})
        await session.commit()
    if revoked:
        from app.services.agent_hub.service import AgentHub

        await AgentHub.broadcast_crl(revoked)
    return len(revoked)


async def request_cert_rotations() -> int:
    """Ask agents to rotate certs that expire soon; one rotate_cert per cert."""
    from app.services.agent_hub.service import AgentHub

    targets = []
    async with get_session_maker()() as session:
        certs = NodeCertificateRepository(session)
        nodes = NodeRepository(session)
        commands = AgentCommandRepository(session)
        for cert in await certs.expiring_node_certs(settings.NODE_CERT_ROTATE_BEFORE_DAYS):
            node = await nodes.get_active(cert.node_id)
            if node is None or node.cert_fingerprint != cert.fingerprint_sha256:
                continue
            if (node.metadata_ or {}).get("rotation_requested_for") == cert.fingerprint_sha256:
                continue
            if await commands.pending_for_node(node.id, ("rotate_cert", "install_cert")) > 0:
                continue
            await nodes.merge_metadata(node.id, {"rotation_requested_for": cert.fingerprint_sha256})
            targets.append(node.id)
        await session.commit()
    for node_id in targets:
        await AgentHub.send_command(
            node_id,
            "rotate_cert",
            {"key_type": "ecdsa_p256", "reason": "certificate expiring"},
            timeout=3600,
            wait=False,
        )
    return len(targets)


async def reconcile_drains() -> int:
    from app.services.nodes.service import check_drain_complete

    flipped = 0
    async with get_session_maker()() as session:
        nodes = NodeRepository(session)
        for node in await nodes.list_active():
            if node.status == "draining" and await check_drain_complete(session, node.id):
                flipped += 1
        await session.commit()
    return flipped


async def alert_offline_nodes() -> int:
    """Emit ``node.offline`` for nodes offline longer than ``NODE_OFFLINE_AFTER_SECONDS``."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=max(1, settings.NODE_OFFLINE_AFTER_SECONDS))
    async with get_session_maker()() as session:
        # Disconnects older than a day are not alerted (no alert storm for long-dead nodes).
        rows = await NodeRepository(session).claim_offline_alerts(cutoff, now - timedelta(days=1))
        await session.commit()
    if not rows:
        return 0
    from app.services.alerts import alerts

    for row in rows:
        drained = row.get("drain_requested_at") is not None
        since = row["disconnected_at"]
        message = (
            f"The node agent disconnected at {since.isoformat()} "
            f"({row.get('status_message') or 'connection lost'}) and has not come back."
        )
        if drained:
            message += " The node is drained; the drain stays in place when it reconnects."
        await alerts.emit(
            "node.offline",
            "warning",
            f"Node {row['name']} offline",
            message,
            resource_type="node",
            resource_id=row["id"],
            resource_name=row["name"],
            payload={
                "disconnected_at": since.isoformat(),
                "last_heartbeat_at": (
                    row["last_heartbeat_at"].isoformat() if row.get("last_heartbeat_at") else None
                ),
                "drained": drained,
                "cluster_id": str(row["cluster_id"]) if row.get("cluster_id") else None,
            },
            dedupe_key=f"node.offline:{row['id']}",
        )
    return len(rows)


async def run_hub_maintenance(connected: Optional[Iterable[str]] = None) -> bool:
    """Hub sweeper entry point; True when routing-relevant state changed."""
    changed = False
    for step in (mark_stale_nodes_offline, expire_commands, reconcile_drains):
        try:
            result = await (
                step(set(connected or [])) if step is mark_stale_nodes_offline else step()
            )
            changed = changed or bool(result)
        except Exception as exc:  # noqa: BLE001 - one failing step must not stop the others
            logger.error(f"fleet maintenance {step.__name__} failed: {exc}")
    try:
        await alert_offline_nodes()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"fleet maintenance alert_offline_nodes failed: {exc}")
    try:
        await revoke_superseded_certs()
        await live_cache.drop_stale()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"fleet maintenance cert/live cleanup failed: {exc}")
    return changed
