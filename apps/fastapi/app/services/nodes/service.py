"""
Nodes service: node list/detail/update, drain/undrain/remove, join tokens,
the agent join flow (token + CSR -> certificate), per-node commands,
certificates (rotate / revoke), devices, instances and cache summary.

Side effects that need a socket go through ``AgentHub`` (Redis bus); this
service never talks to an agent directly.

Drain semantics (admin.yaml ``DrainRequest``):

- ``move_instances=true``: the swap manager moves every live instance to
  another node (``app.services.swap.node_drain.plan_node_drain``, resolved
  lazily). Until the swap manager provides it, the hub falls back to ``unload_model``.
- ``move_instances=false``: every live instance is drained and unloaded on
  the node (``unload_model`` with ``drain_timeout_seconds``).

The node becomes ``drained`` when no live instance and no in-flight
unload/drain command remains (checked on every terminal command result and
by the ``fleet.reconcile_drains`` job).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.core.exceptions import (
    BaseAPIException,
    ConflictError,
    DuplicateResourceError,
    ResourceNotFoundError,
    ValidationError,
)
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_db
from app.db.postgres.models.generated_models import Nodes
from app.db.postgres.repositories.agent_command_repository import AgentCommandRepository
from app.db.postgres.repositories.cluster_repository import ClusterRepository
from app.db.postgres.repositories.device_repository import (
    DeviceRepository,
    DeviceSliceRepository,
)
from app.db.postgres.repositories.fleet_event_repository import FleetEventRepository
from app.db.postgres.repositories.inventory_repository import InventoryRepository
from app.db.postgres.repositories.node_certificate_repository import (
    NodeCertificateRepository,
)
from app.db.postgres.repositories.node_repository import (
    JoinTokenRepository,
    NodeRepository,
)
from app.schemas.fleet_schema import (
    AgentCommandResponse,
    CacheSummary,
    CommandRefs,
    DeviceLive,
    DeviceProcess,
    DeviceResponse,
    DeviceSliceResponse,
    DrainRequest,
    InstanceLive,
    InstanceResponse,
    JoinTokenCreate,
    JoinTokenCreatedResponse,
    JoinTokenResponse,
    NodeCertificateResponse,
    NodeResponse,
    NodeUpdate,
)
from app.services.metrics import live_cache
from app.services.nodes import agent_downloads
from app.services.pki import ca as cax
from app.services.pki.service import PkiService
from fastapi import Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

JOIN_TOKEN_PREFIX = "gdj_"
DRAIN_COMMANDS = ("unload_model", "drain_instance")


# ==========================================
# Join-specific errors (protocol.md §2)
# ==========================================


class JoinTokenInvalidError(BaseAPIException):
    def __init__(self, detail: str = "Join token is invalid"):
        super().__init__(status.HTTP_401_UNAUTHORIZED, detail, error_code="JOIN_TOKEN_INVALID")


class JoinTokenExpiredError(BaseAPIException):
    def __init__(self, detail: str = "Join token has expired"):
        super().__init__(status.HTTP_410_GONE, detail, error_code="JOIN_TOKEN_EXPIRED")


class NodeNameTakenError(BaseAPIException):
    def __init__(self, detail: str = "A node with this name exists in the cluster"):
        super().__init__(status.HTTP_409_CONFLICT, detail, error_code="NODE_NAME_TAKEN")


class CsrInvalidError(BaseAPIException):
    def __init__(self, detail: str = "CSR is invalid"):
        super().__init__(422, detail, error_code="CSR_INVALID")


@dataclass
class JoinRequest:
    join_token: str
    csr_pem: str
    hostname: str
    os: str
    arch: str
    agent_version: str
    name: Optional[str] = None
    protocol_version: int = 1
    proxy_port: int = 9443
    labels: Dict[str, str] = field(default_factory=dict)
    ip_addresses: List[str] = field(default_factory=list)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_join_token() -> Tuple[str, str, str]:
    """(plaintext, prefix, sha256) — ``gdj_`` + 40 hex."""
    token = JOIN_TOKEN_PREFIX + secrets.token_hex(20)
    return token, token[:12], hash_token(token)


_NODE_FIELDS = [name for name in NodeResponse.model_fields if name not in ("metadata",)]


def node_to_dict(node: Nodes) -> Dict[str, Any]:
    """Column values for NodeResponse (``metadata`` lives on ``metadata_`` in the ORM)."""
    data = {name: getattr(node, name, None) for name in _NODE_FIELDS if hasattr(Nodes, name)}
    data["metadata"] = dict(node.metadata_ or {})
    data["ip_address"] = str(node.ip_address) if node.ip_address else None
    data["cache_used_bytes"] = data["metadata"].get("cache_used_bytes")
    data["labels"] = dict(node.labels or {})
    data["capabilities"] = dict(node.capabilities or {})
    data["disks"] = list(node.disks or [])
    return data


def node_response(node: Nodes, counts: Optional[Dict[str, int]] = None) -> NodeResponse:
    item = NodeResponse.model_validate(node_to_dict(node))
    if counts:
        item.device_count = counts.get("device_count", 0)
        item.instance_count = counts.get("instance_count", 0)
    return item


async def with_activity(
    session: AsyncSession, rows: Sequence[Nodes], items: List[NodeResponse]
) -> List[NodeResponse]:
    """Fill ``activity`` / ``outside_processes`` / ``detached`` from live data (never raises)."""
    from app.services.nodes.activity import node_detached, nodes_live_activity

    try:
        live = await nodes_live_activity(session, rows)
    except Exception as exc:  # noqa: BLE001 - the list still renders without it
        logger.debug(f"node activity unavailable: {exc}")
        live = {}
    for row, item in zip(rows, items):
        info = live.get(row.id)
        item.detached = info["detached"] if info else node_detached(row)
        item.activity = info["activity"] if info else ("detached" if item.detached else None)
        item.outside_processes = info["outside_processes"] if info else 0
    return items


def cert_response(row) -> NodeCertificateResponse:
    item = NodeCertificateResponse.model_validate(row)
    item.is_active = row.revoked_at is None and row.not_after > datetime.now(timezone.utc)
    return item


def _node_drain_planner() -> Optional[Callable[..., Any]]:
    """
    Swap manager hook ``app.services.swap.node_drain.plan_node_drain(session, node_id,
    instance_ids, *, timeout_seconds, reason, actor_id) -> [swap_id]``.
    """
    try:
        from app.services.swap import node_drain  # type: ignore

        fn = getattr(node_drain, "plan_node_drain", None)
        return fn if callable(fn) else None
    except Exception:  # noqa: BLE001 - swap manager not present yet
        return None


# ==========================================
# Admin-facing service
# ==========================================


class NodeService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.nodes = NodeRepository(session)
        self.clusters = ClusterRepository(session)
        self.tokens = JoinTokenRepository(session)
        self.certs = NodeCertificateRepository(session)
        self.devices = DeviceRepository(session)
        self.slices = DeviceSliceRepository(session)
        self.commands = AgentCommandRepository(session)
        self.inventory = InventoryRepository(session)
        self.events = FleetEventRepository(session)
        self.pki = PkiService.from_session(session)

    @staticmethod
    async def as_dependency(request: Request, db: AsyncSession = Depends(get_db)) -> "NodeService":
        return NodeService(db)

    async def _get(self, node_id: uuid.UUID) -> Nodes:
        node = await self.nodes.get_active(node_id)
        if node is None or node.status == "removed":
            raise ResourceNotFoundError(resource_type="Node")
        return node

    # ---------- list / get / update ----------

    async def list(
        self,
        page: int,
        page_size: int,
        search: Optional[str] = None,
        sort: Optional[str] = None,
        cluster_id: Optional[uuid.UUID] = None,
        status_: Optional[str] = None,
        os: Optional[str] = None,
        labels: Optional[Sequence[str]] = None,
    ) -> Tuple[List[NodeResponse], int]:
        if cluster_id and await self.clusters.get_active(cluster_id) is None:
            raise ResourceNotFoundError(resource_type="Cluster")
        rows, total = await self.nodes.list(
            page, page_size, search, sort, cluster_id, status_, os, labels
        )
        counts = await self.nodes.counts_for([r.id for r in rows])
        items = [node_response(r, counts.get(r.id)) for r in rows]
        return await with_activity(self.session, rows, items), total

    async def get(self, node_id: uuid.UUID) -> NodeResponse:
        node = await self._get(node_id)
        counts = await self.nodes.counts_for([node.id])
        return (
            await with_activity(self.session, [node], [node_response(node, counts.get(node.id))])
        )[0]

    async def update(
        self, node_id: uuid.UUID, body: NodeUpdate, actor_id: Optional[uuid.UUID]
    ) -> NodeResponse:
        node = await self._get(node_id)
        changes: Dict[str, Any] = body.model_dump(exclude_unset=True)
        if "name" in changes:
            other = await self.nodes.get_by_name(node.cluster_id, changes["name"])
            if other is not None and other.id != node_id:
                raise DuplicateResourceError(resource_type="Node", field="name")
        if "proxy_port" in changes and changes["proxy_port"] is None:
            changes.pop("proxy_port")
        if "labels" in changes and changes["labels"] is None:
            changes["labels"] = {}
        values = {k: v for k, v in changes.items() if k != "metadata"}
        if "metadata" in changes:
            merged = dict(node.metadata_ or {})
            merged.update(changes["metadata"] or {})
            values["metadata_"] = merged
        before = {k: getattr(node, k) for k in changes if k != "metadata"}
        node = await self.nodes.update(node_id, **values)
        await self.events.audit(
            "node.updated",
            actor_type="admin",
            actor_id=actor_id,
            resource_type="node",
            resource_id=node_id,
            resource_name=node.name,
            before_state=_jsonable(before),
            after_state=_jsonable({k: v for k, v in changes.items() if k != "metadata"}),
        )
        if "cache_limit_bytes" in changes:
            await self.session.commit()
            await _push_config(node_id, ["settings"])
        counts = await self.nodes.counts_for([node.id])
        return node_response(node, counts.get(node.id))

    # ---------- drain / undrain / remove ----------

    async def drain(
        self, node_id: uuid.UUID, body: DrainRequest, actor_id: Optional[uuid.UUID]
    ) -> CommandRefs:
        node = await self._get(node_id)
        if node.status in ("draining", "drained") or node.drain_requested_at is not None:
            raise ConflictError(f"node is already {node.status}")
        # Drain is a scheduling state: a disconnected node stays offline and picks the drain
        # up when it reconnects (NodeRepository.mark_connected).
        offline = node.status == "offline"
        instance_ids = await self.inventory.instance_ids_for_node(node_id)
        await self.nodes.set_status(
            node_id,
            "offline" if offline else "draining",
            node.status_message if offline else body.reason,
            drain_requested_at=datetime.now(timezone.utc),
            drain_reason=body.reason,
            drained_at=None,
        )
        await self.events.audit(
            "node.drain_requested",
            actor_type="admin",
            actor_id=actor_id,
            resource_type="node",
            resource_id=node_id,
            resource_name=node.name,
            message=body.reason,
            metadata={
                "instances": [str(i) for i in instance_ids],
                "move": body.move_instances,
            },
        )
        refs = CommandRefs()
        if not instance_ids:
            await self.nodes.set_status(
                node_id,
                "offline" if offline else "drained",
                node.status_message if offline else "no instances",
                drained_at=datetime.now(timezone.utc),
            )
            await self.session.commit()
            await _push_drain_state(node_id)
            return refs

        planner = _node_drain_planner() if body.move_instances else None
        if planner is not None:
            swap_ids = await planner(
                self.session,
                node_id,
                instance_ids,
                timeout_seconds=body.timeout_seconds,
                reason=body.reason,
                actor_id=actor_id,
            )
            refs.swap_operation_ids.extend(swap_ids or [])
            await self.session.commit()
            await _push_drain_state(node_id)
            return refs
        if body.move_instances:
            logger.warning(
                f"node drain {node_id}: swap manager hook unavailable, unloading instances instead"
            )
        # The hub must see the draining status before the commands arrive.
        await self.session.commit()
        await _push_drain_state(node_id)
        from app.services.agent_hub.service import AgentHub

        for instance_id in instance_ids:
            outcome = await AgentHub.send_command(
                node_id,
                "unload_model",
                {
                    "instance_id": str(instance_id),
                    "force": False,
                    "drain_timeout_seconds": body.timeout_seconds,
                },
                timeout=body.timeout_seconds + 120,
                instance_id=instance_id,
                wait=False,
            )
            refs.command_ids.append(outcome.command_id)
        return refs

    async def undrain(self, node_id: uuid.UUID, actor_id: Optional[uuid.UUID]) -> NodeResponse:
        node = await self._get(node_id)
        if node.status not in ("draining", "drained") and node.drain_requested_at is None:
            raise ConflictError("node is not draining")
        from app.services.agent_hub.service import AgentHub

        connected = await AgentHub.is_connected(node_id)
        await self.nodes.set_status(
            node_id,
            "online" if connected else "offline",
            None,
            drain_requested_at=None,
            drain_reason=None,
            drained_at=None,
        )
        await self.events.audit(
            "node.undrained",
            actor_type="admin",
            actor_id=actor_id,
            resource_type="node",
            resource_id=node_id,
            resource_name=node.name,
        )
        await self.session.commit()
        await _push_config(node_id, ["routing"])
        await _push_drain_state(node_id)
        return await self.get(node_id)

    async def remove(self, node_id: uuid.UUID, force: bool, actor_id: Optional[uuid.UUID]) -> None:
        node = await self._get(node_id)
        live = await self.inventory.instance_ids_for_node(node_id)
        if live and not force:
            raise ConflictError("node still runs instances; drain it first or pass force=true")
        if node.status == "online" and not force:
            raise ConflictError("node is online; drain it first or pass force=true")
        revoked = await self.certs.active_for_node(node_id)
        await self.certs.revoke_all_for_node(node_id, "node removed")
        await self.slices.release_for_instances(live)
        await self.inventory.stop_node_instances(node_id, "node removed")
        await self.nodes.soft_remove(node_id)
        from app.services.deployments.external import remove_for_node

        await remove_for_node(self.session, node_id)
        await self.events.audit(
            "node.removed",
            actor_type="admin",
            actor_id=actor_id,
            resource_type="node",
            resource_id=node_id,
            resource_name=node.name,
            metadata={"force": force, "instances": [str(i) for i in live]},
        )
        await self.session.commit()
        from app.services.agent_hub.service import AgentHub

        await AgentHub.disconnect_node(node_id, reason="node_removed")
        if revoked:
            await AgentHub.broadcast_crl([c.fingerprint_sha256 for c in revoked])
        logger.warning(f"node removed id={node_id} name={node.name} force={force}")

    # ---------- join tokens ----------

    async def list_join_tokens(
        self,
        page: int,
        page_size: int,
        cluster_id: Optional[uuid.UUID],
        active_only: bool,
    ) -> Tuple[List[JoinTokenResponse], int]:
        rows, total = await self.tokens.list(page, page_size, cluster_id, active_only)
        return [JoinTokenResponse.model_validate(r) for r in rows], total

    async def create_join_token(
        self, body: JoinTokenCreate, actor_id: Optional[uuid.UUID]
    ) -> JoinTokenCreatedResponse:
        cluster = await self.clusters.get_active(body.cluster_id)
        if cluster is None:
            raise ResourceNotFoundError(resource_type="Cluster")
        if cluster.status != "active":
            raise ConflictError("cluster is disabled")
        token, prefix, token_hash = generate_join_token()
        ttl = body.ttl_minutes or settings.JOIN_TOKEN_TTL_MINUTES
        row = await self.tokens.create(
            cluster_id=body.cluster_id,
            token_prefix=prefix,
            token_hash=token_hash,
            node_name_hint=body.node_name_hint,
            labels=body.labels,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl),
            max_uses=body.max_uses,
            created_by=actor_id,
        )
        ca = await self.pki.ensure_ca()
        await self.events.audit(
            "node.join_token_created",
            actor_type="admin",
            actor_id=actor_id,
            resource_type="join_token",
            resource_id=row.id,
            resource_name=prefix,
            metadata={
                "cluster_id": str(body.cluster_id),
                "ttl_minutes": ttl,
                "max_uses": body.max_uses,
            },
        )
        join_command = f"gpudeck-agent join --hub {settings.AGENT_HUB_PUBLIC_URL} --token {token}"
        if body.node_name_hint:
            join_command += f" --name {body.node_name_hint}"
        join_command += f" --ca-fingerprint {ca.fingerprint}"
        return JoinTokenCreatedResponse.model_validate(
            {
                **{c: getattr(row, c) for c in JoinTokenResponse.model_fields},
                "token": token,
                "join_command": join_command,
                "ca_fingerprint_sha256": ca.fingerprint,
                "ca_pem_sha256": agent_downloads.pem_sha256(ca.cert_pem),
                "hub_url": settings.AGENT_HUB_PUBLIC_URL.rstrip("/"),
                "install_command": agent_downloads.install_command(token, ca.cert_pem),
                "install_script_url": agent_downloads.install_script_url(),
            }
        )

    async def revoke_join_token(self, token_id: uuid.UUID, actor_id: Optional[uuid.UUID]) -> None:
        row = await self.tokens.get_by_id(token_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Join token")
        if row.used_at is not None:
            raise ConflictError("token already used")
        if row.revoked_at is not None:
            raise ConflictError("token already revoked")
        await self.tokens.revoke(token_id)
        await self.events.audit(
            "node.join_token_revoked",
            actor_type="admin",
            actor_id=actor_id,
            resource_type="join_token",
            resource_id=token_id,
            resource_name=row.token_prefix,
            metadata={"max_uses": row.max_uses, "use_count": row.use_count},
        )

    # ---------- sub-resources ----------

    async def list_devices(self, node_id: uuid.UUID) -> List[DeviceResponse]:
        node = await self._get(node_id)
        rows = await self.devices.list_for_node(node_id)
        return await decorate_devices(self.devices, self.slices, rows, {node.id: node})

    async def list_instances(
        self, node_id: uuid.UUID, page: int, page_size: int
    ) -> Tuple[List[InstanceResponse], int]:
        await self._get(node_id)
        rows, total = await self.inventory.list_instances_for_node(node_id, page, page_size)
        return await decorate_instances(self.session, rows), total

    async def cache_summary(self, node_id: uuid.UUID) -> CacheSummary:
        node = await self._get(node_id)
        rows = await self.inventory.cache_summary_rows(node_id)
        disks = node.disks or []
        cache_disk = next((d for d in disks if d.get("is_cache_dir")), disks[0] if disks else None)
        used = (node.metadata_ or {}).get("cache_used_bytes")
        return CacheSummary(
            node_id=node.id,
            node_name=node.name,
            cache_dir=node.cache_dir,
            limit_bytes=node.cache_limit_bytes,
            used_bytes=(int(used) if used is not None else sum(int(r["size_bytes"]) for r in rows)),
            free_disk_bytes=cache_disk.get("free_bytes") if cache_disk else None,
            pinned_bytes=sum(int(r["pinned_bytes"]) for r in rows),
            item_count=sum(int(r["item_count"]) for r in rows),
            by_model=[
                {
                    "model_id": r["model_id"],
                    "model_name": r["model_name"],
                    "model_version_id": r["model_version_id"],
                    "version": r["version"],
                    "size_bytes": int(r["size_bytes"]),
                    "state": r["state"],
                    "pinned": bool(r["pinned"]),
                    "in_use": bool(r["in_use"]),
                    "last_used_at": r["last_used_at"],
                }
                for r in rows
            ],
        )

    async def list_commands(
        self, node_id: uuid.UUID, page: int, page_size: int, state: Optional[str]
    ) -> Tuple[List[AgentCommandResponse], int]:
        await self._get(node_id)
        rows, total = await self.commands.list_for_node(node_id, page, page_size, state)
        return [AgentCommandResponse.model_validate(r) for r in rows], total

    async def list_certificates(self, node_id: uuid.UUID) -> List[NodeCertificateResponse]:
        await self._get(node_id)
        return [cert_response(r) for r in await self.certs.list_for_node(node_id)]

    async def rotate_certificate(
        self, node_id: uuid.UUID, actor_id: Optional[uuid.UUID]
    ) -> CommandRefs:
        node = await self._get(node_id)
        await self.events.audit(
            "node.cert_rotation_requested",
            actor_type="admin",
            actor_id=actor_id,
            resource_type="node",
            resource_id=node_id,
            resource_name=node.name,
        )
        await self.session.commit()
        from app.services.agent_hub.service import AgentHub

        outcome = await AgentHub.send_command(
            node_id,
            "rotate_cert",
            {"key_type": "ecdsa_p256", "reason": "admin request"},
            timeout=300,
            wait=False,
        )
        return CommandRefs(command_ids=[outcome.command_id])

    async def revoke_certificate(
        self,
        node_id: uuid.UUID,
        cert_id: uuid.UUID,
        reason: Optional[str],
        actor_id: Optional[uuid.UUID],
    ) -> NodeCertificateResponse:
        await self._get(node_id)
        row = await self.pki.revoke(cert_id, reason, actor_id=actor_id, node_id=node_id)
        await self.session.commit()
        from app.services.agent_hub.service import AgentHub

        await AgentHub.broadcast_crl([row.fingerprint_sha256])
        return cert_response(row)


async def _push_drain_state(node_id: uuid.UUID) -> None:
    from app.services.agent_hub.service import AgentHub

    try:
        await AgentHub.push_drain_state(node_id)
    except Exception as exc:  # noqa: BLE001 - the next reconnect sends hello_ack.drain anyway
        logger.warning(f"drain state push failed node={node_id}: {exc}")


async def _push_config(node_id: uuid.UUID, sections: List[str]) -> None:
    from app.services.agent_hub.service import AgentHub

    try:
        await AgentHub.push_config(node_id, full=False, sections=sections)
    except Exception as exc:  # noqa: BLE001 - the next reconnect sends a full push anyway
        logger.warning(f"config push failed node={node_id}: {exc}")


# ==========================================
# Join flow (hub process)
# ==========================================


class NodeJoinService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.nodes = NodeRepository(session)
        self.tokens = JoinTokenRepository(session)
        self.clusters = ClusterRepository(session)
        self.events = FleetEventRepository(session)
        self.pki = PkiService.from_session(session)

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db)
    ) -> "NodeJoinService":
        return NodeJoinService(db)

    async def join(self, req: JoinRequest, client_ip: Optional[str]) -> Dict[str, Any]:
        try:
            return await self._join(req, client_ip)
        except BaseAPIException:
            # Routes turn API errors into responses and get_db would then commit:
            # make sure a half-done join (node row, token claim) never persists.
            await self.session.rollback()
            raise

    async def _join(self, req: JoinRequest, client_ip: Optional[str]) -> Dict[str, Any]:
        if not req.join_token.startswith(JOIN_TOKEN_PREFIX):
            raise JoinTokenInvalidError()
        token = await self.tokens.get_by_hash(hash_token(req.join_token))
        if (
            token is None
            or token.revoked_at is not None
            or token.used_at is not None
            or (token.use_count or 0) >= (token.max_uses or 1)
        ):
            raise JoinTokenInvalidError()
        if token.expires_at < datetime.now(timezone.utc):
            raise JoinTokenExpiredError()
        if int(req.protocol_version) != 1:
            raise ValidationError("unsupported protocol_version")
        if req.os not in ("linux", "macos") or req.arch not in ("x86_64", "aarch64"):
            raise ValidationError("os must be linux|macos and arch x86_64|aarch64")
        try:
            csr = cax.csr_from_pem(req.csr_pem)
            if not csr.is_signature_valid:
                raise ValueError("signature does not verify")
        except Exception as exc:  # noqa: BLE001
            raise CsrInvalidError(f"CSR is invalid: {exc}") from exc
        cluster = await self.clusters.get_active(token.cluster_id)
        if cluster is None or cluster.status != "active":
            raise JoinTokenInvalidError("cluster no longer accepts nodes")
        name = (req.name or token.node_name_hint or req.hostname or "").strip()[:255]
        if not name:
            raise ValidationError("node name is required")
        if await self.nodes.get_by_name(cluster.id, name) is not None:
            raise NodeNameTakenError()
        labels = dict(req.labels or {})
        labels.update(token.labels or {})  # labels chosen by the admin win
        node_id = uuid.uuid4()
        ip = (req.ip_addresses[0] if req.ip_addresses else client_ip) or None
        node = await self.nodes.create(
            id=node_id,
            cluster_id=cluster.id,
            name=name,
            hostname=req.hostname,
            ip_address=ip,
            proxy_address=ip,
            proxy_port=req.proxy_port,
            os=req.os,
            arch=req.arch,
            agent_version=req.agent_version,
            protocol_version=req.protocol_version,
            status="pending",
            labels=labels,
            metadata_={"joined_from": client_ip},
        )
        try:
            cert = await self.pki.sign_node_csr(node.id, req.csr_pem)
        except ValidationError as exc:
            raise CsrInvalidError(exc.detail) from exc
        token_use = {
            "join_token_id": str(token.id),
            "join_token_prefix": token.token_prefix,
            "join_token_use": (token.use_count or 0) + 1,
            "join_token_max_uses": token.max_uses,
        }
        if not await self.tokens.mark_used(token.id, node.id):
            raise JoinTokenInvalidError("token was used concurrently")
        await self.nodes.update(node.id, cert_fingerprint=cert.fingerprint_sha256)
        ca = await self.pki.ensure_ca()
        await self.events.audit(
            "node.joined",
            actor_type="node_agent",
            actor_id=node.id,
            resource_type="node",
            resource_id=node.id,
            resource_name=node.name,
            ip_address=client_ip,
            after_state={"cluster_id": str(cluster.id), "serial": cert.serial},
            metadata=token_use,
        )
        logger.info(f"node joined id={node.id} name={name} cluster={cluster.id} from={client_ip}")
        return {
            "node_id": str(node.id),
            "node_name": node.name,
            "cluster_id": str(cluster.id),
            "certificate_pem": cert.certificate_pem,
            "ca_bundle_pem": ca.bundle_pem,
            "serial": cert.serial,
            "not_after": cert.not_after.isoformat().replace("+00:00", "Z"),
            "hub_ws_url": settings.hub_ws_url,
            "intervals": {
                "heartbeat_seconds": settings.HEARTBEAT_INTERVAL_SECONDS,
                "metrics_seconds": settings.METRICS_INTERVAL_SECONDS,
                "inventory_seconds": settings.INVENTORY_INTERVAL_SECONDS,
            },
        }


# ==========================================
# Drain completion (hub + jobs)
# ==========================================


async def check_drain_complete(session: AsyncSession, node_id: uuid.UUID) -> bool:
    """Mark a draining node drained when nothing is left on it. Returns True when it flipped."""
    nodes = NodeRepository(session)
    node = await nodes.get_by_id(node_id)
    if node is None or node.status != "draining":
        return False
    commands = AgentCommandRepository(session)
    inventory = InventoryRepository(session)
    if await commands.pending_for_node(node_id, DRAIN_COMMANDS) > 0:
        return False
    if await inventory.instance_ids_for_node(node_id):
        return False
    if await inventory.open_swaps_for_node(node_id) > 0:
        return False
    await nodes.set_status(
        node_id,
        "drained",
        "all instances drained",
        drained_at=datetime.now(timezone.utc),
    )
    await FleetEventRepository(session).audit(
        "node.drained",
        resource_type="node",
        resource_id=node_id,
        resource_name=node.name,
    )
    logger.info(f"node drained id={node_id} name={node.name}")
    return True


# ==========================================
# Shared helpers
# ==========================================


async def decorate_devices(
    devices: DeviceRepository,
    slices: DeviceSliceRepository,
    rows,
    nodes_by_id: Dict[uuid.UUID, Nodes],
) -> List[DeviceResponse]:
    from app.services.nodes.activity import device_activity, node_detached
    from app.services.scheduler.usage import free_bytes, usage_from_sample

    ids = [r.id for r in rows]
    reserved = await devices.reserved_vram_for(ids)
    slice_map = await slices.list_for_devices(ids)
    instance_map = await devices.instance_ids_for(ids)
    samples = await live_cache.get_many(ids, "gpu")
    owners = await _process_owners(devices.session, samples)
    managed = {k for k, v in owners.items() if v["origin"] == "managed"}
    origins = await _instance_origins(
        devices.session, [i for ids_ in instance_map.values() for i in ids_]
    )
    out = []
    for row, sample in zip(rows, samples):
        item = DeviceResponse.model_validate(row)
        node = nodes_by_id.get(row.node_id)
        item.node_name = node.name if node else None
        item.cluster_id = node.cluster_id if node else None
        item.vram_reserved_bytes = reserved.get(row.id, 0)
        item.slices = [DeviceSliceResponse.model_validate(s) for s in slice_map.get(row.id, [])]
        item.instance_ids = instance_map.get(row.id, [])
        item.live = DeviceLive(**live_cache.device_live(sample))
        fresh = bool(sample) and live_cache.is_fresh(sample)
        usage = usage_from_sample(sample, managed, fresh)
        item.vram_used_bytes = usage.used_bytes
        item.vram_used_outside_bytes = usage.external_bytes
        item.vram_usage_stale = not fresh
        total = int(row.vram_total_bytes or 0)
        item.vram_headroom_bytes = int(total * max(settings.SCHEDULER_VRAM_HEADROOM_PCT, 0) / 100)
        item.vram_free_bytes = max(
            free_bytes(
                total,
                item.vram_reserved_bytes,
                usage.used_bytes,
                usage.external_bytes,
                settings.SCHEDULER_VRAM_HEADROOM_PCT,
            ),
            0,
        )
        item.processes = [_device_process(p, owners) for p in usage.processes if "pid" in p]
        item.processes_ts = usage.ts if usage.processes else None
        detached = node_detached(node)
        item.node_detached = detached
        item.activity, item.outside_processes = device_activity(
            detached=detached,
            fresh=fresh,
            managed_instances=sum(
                1 for i in item.instance_ids if origins.get(str(i), "managed") == "managed"
            ),
            sample=sample,
            managed=managed | {str(i) for i, o in origins.items() if o == "managed"},
            reserved_bytes=item.vram_reserved_bytes,
        )
        out.append(item)
    return out


async def _instance_origins(session: AsyncSession, ids: List[Any]) -> Dict[str, str]:
    """instance id -> origin (managed | external)."""
    from sqlalchemy import text

    if not ids:
        return {}
    try:
        rows = await session.execute(
            text("SELECT id::text, origin::text FROM instances WHERE id::text = ANY(:ids)"),
            {"ids": [str(i) for i in ids]},
        )
        return {r[0]: r[1] for r in rows.all()}
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"instance origins unavailable: {exc}")
        return {}


async def _process_owners(
    session: AsyncSession, samples: List[Optional[Dict[str, Any]]]
) -> Dict[str, Dict[str, Any]]:
    """instance id -> {deployment_id, deployment_name, origin} for tagged GPU processes."""
    from sqlalchemy import text

    ids = {
        str(p["instance_id"])
        for s in samples
        if s
        for p in (s.get("processes") or [])
        if isinstance(p, dict) and p.get("instance_id")
    }
    if not ids:
        return {}
    try:
        rows = await session.execute(
            text(
                "SELECT i.id::text AS id, i.deployment_id, d.name, i.origin::text AS origin "
                "FROM instances i JOIN deployments d ON d.id = i.deployment_id "
                "WHERE i.id::text = ANY(:ids)"
            ),
            {"ids": list(ids)},
        )
    except Exception as exc:  # noqa: BLE001 - the table still renders without owners
        logger.debug(f"process owners unavailable: {exc}")
        return {}
    return {
        r["id"]: {
            "deployment_id": r["deployment_id"],
            "deployment_name": r["name"],
            "origin": r["origin"],
        }
        for r in rows.mappings().all()
    }


def _device_process(p: Dict[str, Any], owners: Dict[str, Dict[str, Any]]) -> DeviceProcess:
    owner = owners.get(str(p.get("instance_id") or ""), {})
    return DeviceProcess(
        pid=int(p["pid"]),
        name=p.get("name"),
        used_memory_bytes=p.get("used_memory_bytes"),
        instance_id=p.get("instance_id") if owner else None,
        deployment_id=owner.get("deployment_id"),
        deployment_name=owner.get("deployment_name"),
        origin=owner.get("origin"),
    )


async def decorate_instances(
    session: AsyncSession, rows: List[Dict[str, Any]]
) -> List[InstanceResponse]:
    """Attach ``live`` (latest sample + last minute rollup) to instance dicts."""
    from app.db.postgres.repositories.metric_repository import MetricRepository

    ids = [r["id"] for r in rows]
    samples = await live_cache.get_many(ids, "engine")
    rollups = await MetricRepository(session).last_minute_engine_rollups(ids)
    out = []
    for row, sample in zip(rows, samples):
        item = InstanceResponse.model_validate(row)
        item.live = InstanceLive(**live_cache.instance_live(sample, rollups.get(row["id"])))
        out.append(item)
    return out


def _jsonable(data: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in data.items():
        if isinstance(v, uuid.UUID):
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out
