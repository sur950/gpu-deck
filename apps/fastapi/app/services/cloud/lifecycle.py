"""
Cloud VM lifecycle: the state machine behind ``cloud_instances``.

::

    pending ──create──▶ provisioning ──VM up──▶ booting ──agent joined──▶ running
                                                                         │
      stop/terminate (node online) ─▶ draining ─(node drained | offline | timeout)─┐
                                                                         ▼          ▼
                     starting ◀──start── stopped ◀── stopping      terminating ─▶ terminated
    any failure ─▶ error (last_error, alert)

* ``advance(id)`` runs one step for one row. Steps are serialised with a
  Redis lock per row (Celery task + scheduler job may both try) and claimed
  with a conditional status update, so a VM is never created twice; creates
  are idempotent on the provider side anyway (AWS ``ClientToken``, GCP
  ``request_id``, Azure name, DigitalOcean tag lookup).
* ``tick()`` (job ``cloud.reconcile``) advances every in-flight row, re-reads
  running/stopped VMs every ``CLOUD_STATUS_POLL_SECONDS`` (external stop /
  termination), and applies the idle auto-terminate option.
* Terminate drains the node first through ``NodeService.drain`` (which
  hands instances to the swap manager); once drained, offline or past
  ``CLOUD_DRAIN_TIMEOUT_SECONDS`` the VM is terminated, then the node is
  removed with ``NodeService.remove(force=True)`` and its certs revoked.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.core.exceptions import BaseAPIException, ConflictError
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.repositories.cloud_instance_repository import (
    CloudInstanceRepository,
    CloudInstances,
)
from app.db.postgres.repositories.cluster_repository import ClusterRepository
from app.db.postgres.repositories.inventory_repository import InventoryRepository
from app.db.postgres.repositories.node_repository import (
    JoinTokenRepository,
    NodeRepository,
)
from app.db.redis.client import get_redis, redis_key
from app.services.audit import audit
from app.services.cloud.base import (
    MANAGED_TAG,
    VM_RUNNING,
    VM_STOPPED,
    VM_TERMINATED,
    CloudError,
    CloudProvider,
    CloudVm,
    VmSpec,
)
from app.services.cloud.catalog import run_provider
from app.services.cloud.credentials import load_provider
from app.services.cloud.user_data import BootstrapParams, render_bootstrap
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

LOCK_SECONDS = 600
STALE_PROVISIONING = timedelta(minutes=10)
ACTOR = "system"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ==========================================
# Small helpers shared with the service
# ==========================================


def cloud_config(cluster) -> Dict[str, Any]:
    """``clusters.config.cloud`` (provider launch defaults, limits)."""
    config = dict(cluster.config or {}) if cluster is not None else {}
    cloud = config.get("cloud")
    return dict(cloud) if isinstance(cloud, dict) else {}


def price_plan_suggestion(row: CloudInstances) -> Optional[Dict[str, Any]]:
    if row.price_per_hour_usd is None:
        return None
    per_hour = float(row.price_per_hour_usd)
    gpus = max(int(row.gpu_count or 0), 1)
    return {
        "name": f"cloud {row.provider} {row.region} {row.instance_type}",
        "usd_per_hour": round(per_hour, 6),
        "usd_per_gpu_minute": round(per_hour / 60.0 / gpus, 6),
        "source": row.price_source,
    }


def instance_tags(row: CloudInstances) -> Dict[str, str]:
    return {
        MANAGED_TAG: "true",
        "gpu-deck:cluster-id": str(row.cluster_id),
        "gpu-deck:cloud-instance-id": str(row.id),
        "gpu-deck:name": row.name,
    }


async def _lock(row_id: uuid.UUID) -> Optional[str]:
    """Per-row Redis lock; returns the token, or None when someone else holds it."""
    token = uuid.uuid4().hex
    try:
        redis = await get_redis()
        ok = await redis.set(
            redis_key("cloud", "lock", str(row_id)), token, nx=True, ex=LOCK_SECONDS
        )
        return token if ok else None
    except Exception as exc:  # noqa: BLE001 - no Redis: the status claim still guards creates
        logger.warning(f"cloud lock unavailable ({type(exc).__name__}); continuing without it")
        return "no-redis"


async def _unlock(row_id: uuid.UUID, token: str) -> None:
    if token == "no-redis":
        return
    try:
        redis = await get_redis()
        key = redis_key("cloud", "lock", str(row_id))
        if await redis.get(key) == token:
            await redis.delete(key)
    except Exception:  # noqa: BLE001
        pass


async def _emit_alert(row: CloudInstances, event: str, severity: str, title: str, message: str):
    try:
        from app.services.alerts import alerts

        await alerts.emit(
            event,
            severity,
            title,
            message,
            resource_type="cloud_instance",
            resource_id=row.id,
            resource_name=row.name,
            payload={
                "provider": row.provider,
                "region": row.region,
                "instance_type": row.instance_type,
                "provider_instance_id": row.provider_instance_id,
                "cluster_id": str(row.cluster_id),
            },
            dedupe_key=f"{event}:{row.id}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"cloud alert {event} not emitted: {type(exc).__name__}")


async def _live(row: CloudInstances) -> None:
    try:
        from app.services.metrics.live import publish_live_event

        await publish_live_event(
            "node",
            {
                "kind": "cloud_instance",
                "id": str(row.id),
                "cluster_id": str(row.cluster_id),
                "node_id": str(row.node_id) if row.node_id else None,
                "status": row.status,
                "status_message": row.status_message,
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _audit(event: str, row: CloudInstances, actor_id=None, **extra: Any) -> None:
    audit.record(
        event,
        actor_type="admin" if actor_id else ACTOR,
        actor_id=actor_id,
        resource_type="cloud_instance",
        resource_id=row.id,
        resource_name=row.name,
        metadata={
            "provider": row.provider,
            "region": row.region,
            "instance_type": row.instance_type,
            "provider_instance_id": row.provider_instance_id,
            **extra,
        },
    )


# ==========================================
# Lifecycle
# ==========================================


class CloudLifecycle:
    def __init__(self, session: AsyncSession, provider: Optional[CloudProvider] = None):
        self.session = session
        self.repo = CloudInstanceRepository(session)
        self.nodes = NodeRepository(session)
        self.clusters = ClusterRepository(session)
        self.tokens = JoinTokenRepository(session)
        self.inventory = InventoryRepository(session)
        self._provider = provider

    async def provider_for(self, row: CloudInstances, purpose: str) -> CloudProvider:
        if self._provider is not None:
            return self._provider
        cluster = await self.clusters.get_active(row.cluster_id)
        credential_id = row.credential_id or (cluster.credential_id if cluster else None)
        project = cloud_config(cluster).get("project") or (row.options or {}).get("project")
        return await load_provider(
            self.session, row.provider, credential_id, purpose=purpose, project=project
        )

    async def commit(self) -> None:
        await self.session.commit()

    async def set(self, row: CloudInstances, **values: Any) -> CloudInstances:
        updated = await self.repo.update(row.id, **values)
        return updated or row

    async def fail(self, row: CloudInstances, err: Exception, *, stage: str) -> CloudInstances:
        if isinstance(err, CloudError):
            info = err.to_dict()
        elif isinstance(err, BaseAPIException):
            info = {"code": err.error_code, "message": str(err.detail)}
        else:
            info = {
                "code": "CLOUD_LIFECYCLE_ERROR",
                "message": f"{type(err).__name__}: {err}",
            }
        info.update({"stage": stage, "at": utcnow().isoformat()})
        message = f"{stage} failed: {info.get('message')}"[:2048]
        row = await self.set(row, status="error", status_message=message, last_error=info)
        await self.commit()
        logger.warning(
            f"cloud instance {row.id} {stage} failed: {info.get('code')} {info.get('message')}"
        )
        _audit("cloud.instance_failed", row, stage=stage, error=info)
        await _emit_alert(
            row,
            "cloud.instance_failed",
            "critical",
            f"Cloud VM {row.name}: {stage} failed",
            str(info.get("message")),
        )
        await _live(row)
        return row

    # ---------- entry points ----------

    async def advance(self, row_id: uuid.UUID) -> Optional[str]:
        """One lifecycle step for one row; returns the resulting status (None = skipped)."""
        token = await _lock(row_id)
        if token is None:
            return None
        try:
            row = await self.repo.get_active(row_id)
            if row is None:
                return None
            before = row.status
            row = await self._step(row)
            await self.commit()
            if row.status != before:
                await _live(row)
            return row.status
        finally:
            await _unlock(row_id, token)

    async def _step(self, row: CloudInstances) -> CloudInstances:
        status = row.status
        if status == "pending":
            return await self.step_create(row)
        if status in ("provisioning", "booting", "starting"):
            return await self.step_boot(row)
        if status == "draining":
            return await self.step_drain(row)
        if status == "stopping":
            return await self.step_stopping(row)
        if status == "terminating":
            return await self.step_terminating(row)
        if status in ("running", "stopped"):
            return await self.step_poll(row)
        return row

    async def tick(self) -> Dict[str, int]:
        """Advance in-flight rows, poll due running/stopped rows, apply idle termination."""
        summary = {"advanced": 0, "polled": 0, "idle_terminated": 0}
        working = [r.id for r in await self.repo.list_working()]
        due_before = utcnow() - timedelta(seconds=int(settings.CLOUD_STATUS_POLL_SECONDS))
        due = [r.id for r in await self.repo.list_due_for_poll(due_before)]
        await self.commit()
        for row_id in working:
            try:
                if await self.advance(row_id) is not None:
                    summary["advanced"] += 1
            except Exception as exc:  # noqa: BLE001 - one bad row must not stop the sweep
                await self.session.rollback()
                logger.error(f"cloud advance {row_id} failed: {exc}")
        for row_id in due:
            try:
                if await self.advance(row_id) is not None:
                    summary["polled"] += 1
            except Exception as exc:  # noqa: BLE001
                await self.session.rollback()
                logger.error(f"cloud poll {row_id} failed: {exc}")
        try:
            summary["idle_terminated"] = await self.idle_sweep()
        except Exception as exc:  # noqa: BLE001
            await self.session.rollback()
            logger.error(f"cloud idle sweep failed: {exc}")
        return summary

    # ---------- create ----------

    async def _claim(self, row: CloudInstances, from_status: str, to_status: str) -> bool:
        result = await self.session.execute(
            update(CloudInstances)
            .where(CloudInstances.id == row.id, CloudInstances.status == from_status)
            .values(status=to_status, updated_at=utcnow())
        )
        await self.commit()
        return result.rowcount == 1

    async def _mint_token(self, row: CloudInstances) -> Dict[str, Any]:
        from app.schemas.fleet_schema import JoinTokenCreate
        from app.services.nodes.service import NodeService
        from app.services.pki.service import PkiService

        if row.join_token_id:
            await self.tokens.revoke(row.join_token_id)
        labels = {
            "gpu-deck/cloud-provider": row.provider,
            "gpu-deck/cloud-region": row.region,
            "gpu-deck/cloud-instance-type": row.instance_type,
            **{str(k): str(v) for k, v in (row.labels or {}).items()},
        }
        created = await NodeService(self.session).create_join_token(
            JoinTokenCreate(
                cluster_id=row.cluster_id,
                node_name_hint=row.name,
                labels=labels,
                ttl_minutes=int(settings.CLOUD_JOIN_TOKEN_TTL_MINUTES),
            ),
            row.created_by,
        )
        ca = await PkiService.from_session(self.session).ca_certificate()
        return {
            "token_id": created.id,
            "token": created.token,
            "ca_fingerprint": created.ca_fingerprint_sha256 or ca["fingerprint_sha256"],
            "ca_pem": ca["certificate_pem"],
            "labels": labels,
        }

    def build_spec(self, row: CloudInstances, user_data: str) -> VmSpec:
        return VmSpec(
            name=row.name,
            region=row.region,
            zone=row.zone,
            instance_type=row.instance_type,
            image=row.image,
            disk_gb=int(row.disk_gb),
            user_data=user_data,
            tags=dict(row.tags or {}) or instance_tags(row),
            client_token=str(row.id),
            gpu_count=int(row.gpu_count or 0),
            gpu_model=row.gpu_model,
            options=dict(row.options or {}),
        )

    async def step_create(self, row: CloudInstances, *, from_status: str = "pending"):
        if not await self._claim(row, from_status, "provisioning"):
            return await self.repo.get_active(row.id) or row
        row = await self.repo.get_active(row.id) or row
        try:
            provider = await self.provider_for(row, "provision")
            minted = await self._mint_token(row)
            user_data = render_bootstrap(
                BootstrapParams(
                    instance_id=str(row.id),
                    provider=row.provider,
                    join_token=minted["token"],
                    ca_fingerprint=minted["ca_fingerprint"],
                    ca_pem=minted["ca_pem"],
                    node_name=row.name,
                    labels=minted["labels"],
                )
            )
            row = await self.set(
                row,
                join_token_id=minted["token_id"],
                status_message="creating the VM",
                tags=dict(row.tags or {}) or instance_tags(row),
            )
            await self.commit()  # token + claim visible before the slow provider call
            vm: CloudVm = await run_provider(provider.create_vm, self.build_spec(row, user_data))
        except Exception as exc:  # noqa: BLE001
            if row.join_token_id:
                await self.tokens.revoke(row.join_token_id)
            return await self.fail(row, exc, stage="create")
        now = utcnow()
        row = await self.set(
            row,
            provider_instance_id=vm.id,
            provider_state=vm.provider_state,
            zone=vm.zone or row.zone,
            public_ip=vm.public_ip,
            private_ip=vm.private_ip,
            image=vm.image or row.image,
            status="booting" if vm.state == VM_RUNNING else "provisioning",
            status_message="VM created; waiting for it to boot and the agent to join",
            launched_at=now,
            last_polled_at=now,
            last_error=None,
        )
        await self.commit()
        _audit("cloud.instance_created", row, actor_id=row.created_by)
        logger.info(
            f"cloud VM created id={row.id} provider={row.provider} vm={vm.id} "
            f"type={row.instance_type}"
        )
        return row

    # ---------- boot / join ----------

    async def _apply_vm(self, row: CloudInstances, vm: CloudVm) -> CloudInstances:
        return await self.set(
            row,
            provider_state=vm.provider_state,
            public_ip=vm.public_ip or row.public_ip,
            private_ip=vm.private_ip or row.private_ip,
            zone=vm.zone or row.zone,
            last_polled_at=utcnow(),
        )

    async def step_boot(self, row: CloudInstances) -> CloudInstances:
        if not row.provider_instance_id:
            if row.status == "provisioning" and row.updated_at < utcnow() - STALE_PROVISIONING:
                # The create call died before we stored the id: retry (idempotent).
                return await self.step_create(row, from_status="provisioning")
            return row
        try:
            provider = await self.provider_for(row, "poll")
            vm = await run_provider(
                provider.get_vm,
                row.provider_instance_id,
                region=row.region,
                zone=row.zone,
            )
        except CloudError as exc:
            row = await self.set(row, status_message=f"status poll failed: {exc.detail}"[:2048])
            return row
        if vm is None or vm.state == VM_TERMINATED:
            if row.launched_at and row.launched_at > utcnow() - timedelta(minutes=5) and vm is None:
                return row  # not visible yet (eventual consistency)
            return await self._finalize_terminated(row, "VM no longer exists on the provider")
        row = await self._apply_vm(row, vm)
        if vm.state != VM_RUNNING:
            return row
        if row.status == "provisioning":
            row = await self.set(row, status="booting", status_message="VM running; agent joining")
        node_id = row.node_id or await self._joined_node(row)
        if node_id is None:
            deadline = (row.launched_at or row.created_at) + timedelta(
                minutes=int(settings.CLOUD_BOOT_TIMEOUT_MINUTES)
            )
            if utcnow() > deadline:
                return await self.fail(
                    row,
                    CloudError(
                        f"the node agent did not join within {settings.CLOUD_BOOT_TIMEOUT_MINUTES}"
                        " minutes; check /var/log/gpudeck-bootstrap.log on the VM (the VM keeps "
                        "running until terminated)",
                        provider=row.provider,
                        error_code="CLOUD_AGENT_JOIN_TIMEOUT",
                    ),
                    stage="join",
                )
            return row
        if row.node_id is None:
            return await self._link_node(row, node_id)
        node = await self.nodes.get_by_id(node_id)
        if row.status == "starting":
            if node is not None and node.status in ("draining", "drained"):
                await self._undrain(node_id)
                node = await self.nodes.get_by_id(node_id)
            if node is not None and node.status == "online":
                row = await self.set(
                    row,
                    status="running",
                    status_message="running",
                    stopped_at=None,
                    pending_action=None,
                )
                _audit("cloud.instance_started", row)
        elif node is not None and node.status in ("online", "pending"):
            row = await self.set(row, status="running", status_message="running")
        return row

    async def _joined_node(self, row: CloudInstances) -> Optional[uuid.UUID]:
        if not row.join_token_id:
            return None
        token = await self.tokens.get_by_id(row.join_token_id)
        return token.used_by_node_id if token is not None else None

    async def _link_node(self, row: CloudInstances, node_id: uuid.UUID) -> CloudInstances:
        node = await self.nodes.get_by_id(node_id)
        now = utcnow()
        suggestion = price_plan_suggestion(row)
        if node is not None:
            metadata = dict(node.metadata_ or {})
            metadata["cloud"] = {
                "cloud_instance_id": str(row.id),
                "provider": row.provider,
                "region": row.region,
                "zone": row.zone,
                "instance_type": row.instance_type,
                "price_per_hour_usd": (
                    float(row.price_per_hour_usd) if row.price_per_hour_usd is not None else None
                ),
            }
            if suggestion:
                metadata["price_plan_suggestion"] = suggestion
            values: Dict[str, Any] = {
                "cloud_instance_id": row.provider_instance_id,
                "cloud_instance_type": row.instance_type,
                "metadata_": metadata,
            }
            await self.nodes.update(node_id, **values)
        row = await self.set(
            row,
            node_id=node_id,
            joined_at=now,
            status="running",
            status_message="agent joined",
            last_error=None,
        )
        _audit("cloud.instance_joined", row, node_id=str(node_id))
        logger.info(f"cloud VM {row.id} joined as node {node_id}")
        if (row.options or {}).get("apply_price_plan") and suggestion:
            try:
                await apply_price_plan(self.session, row)
            except Exception as exc:  # noqa: BLE001 - billing hint, not lifecycle critical
                logger.warning(f"price plan for cloud VM {row.id} not applied: {exc}")
        return row

    # ---------- drain / stop / terminate ----------

    async def begin_drain(
        self,
        row: CloudInstances,
        action: str,
        *,
        reason: Optional[str],
        timeout_seconds: Optional[int],
        actor_id: Optional[uuid.UUID],
    ) -> CloudInstances:
        """Drain the node (node drain → swap manager) and stop/terminate afterwards."""
        from app.schemas.fleet_schema import DrainRequest
        from app.services.nodes.service import NodeService

        timeout = int(
            timeout_seconds if timeout_seconds is not None else settings.CLOUD_DRAIN_TIMEOUT_SECONDS
        )
        node_id = row.node_id
        row = await self.set(
            row,
            status="draining",
            pending_action=action,
            drain_started_at=utcnow(),
            status_message=f"draining the node before {action}",
            options={**(row.options or {}), "drain_timeout_seconds": timeout},
        )
        await self.commit()
        try:
            await NodeService(self.session).drain(
                node_id,
                DrainRequest(
                    reason=(reason or f"cloud {action}")[:512],
                    timeout_seconds=min(timeout, 3600),
                    move_instances=True,
                ),
                actor_id,
            )
        except ConflictError:
            pass  # already draining / drained
        except BaseAPIException as exc:
            logger.warning(f"drain of node {node_id} for cloud VM {row.id} failed: {exc.detail}")
        await self.commit()
        return await self.repo.get_active(row.id) or row

    async def step_drain(self, row: CloudInstances) -> CloudInstances:
        node = await self.nodes.get_by_id(row.node_id) if row.node_id else None
        timeout = int(
            (row.options or {}).get("drain_timeout_seconds") or settings.CLOUD_DRAIN_TIMEOUT_SECONDS
        )
        started = row.drain_started_at or row.updated_at
        timed_out = utcnow() > started + timedelta(seconds=timeout)
        done = node is None or node.status in ("drained", "offline", "removed") or timed_out
        if not done:
            return row
        if timed_out and node is not None and node.status not in ("drained", "offline", "removed"):
            logger.warning(f"cloud VM {row.id}: drain timed out after {timeout}s; proceeding")
        action = row.pending_action or "terminate"
        return await self.execute(row, action)

    async def execute(self, row: CloudInstances, action: str) -> CloudInstances:
        """Call the provider for stop/terminate (no drain)."""
        if not row.provider_instance_id:
            return await self._finalize_terminated(row, "no VM was created")
        try:
            provider = await self.provider_for(row, action)
            if action == "stop":
                await run_provider(
                    provider.stop_vm,
                    row.provider_instance_id,
                    region=row.region,
                    zone=row.zone,
                )
                row = await self.set(
                    row,
                    status="stopping",
                    pending_action=None,
                    status_message="stopping the VM",
                )
            else:
                await run_provider(
                    provider.terminate_vm,
                    row.provider_instance_id,
                    region=row.region,
                    zone=row.zone,
                )
                row = await self.set(
                    row,
                    status="terminating",
                    pending_action="terminate",
                    status_message="terminating the VM",
                )
        except Exception as exc:  # noqa: BLE001
            return await self.fail(row, exc, stage=action)
        _audit(
            f"cloud.instance_{'stop' if action == 'stop' else 'terminate'}_requested",
            row,
        )
        return row

    async def step_stopping(self, row: CloudInstances) -> CloudInstances:
        vm = await self._poll(row)
        if vm is False:
            return row
        if vm is None or vm.state == VM_TERMINATED:
            return await self._finalize_terminated(row, "VM no longer exists on the provider")
        row = await self._apply_vm(row, vm)
        if vm.state == VM_STOPPED:
            row = await self.set(
                row, status="stopped", stopped_at=utcnow(), status_message="stopped"
            )
            _audit("cloud.instance_stopped", row)
        return row

    async def step_terminating(self, row: CloudInstances) -> CloudInstances:
        if not row.provider_instance_id:
            return await self._finalize_terminated(row, "no VM was created")
        vm = await self._poll(row)
        if vm is False:
            return row
        if vm is None or vm.state == VM_TERMINATED:
            return await self._finalize_terminated(row, "terminated")
        return await self._apply_vm(row, vm)

    async def step_poll(self, row: CloudInstances) -> CloudInstances:
        """running / stopped rows: notice VMs stopped, started or deleted outside GPU Deck."""
        vm = await self._poll(row)
        if vm is False:
            return row
        if vm is None or vm.state == VM_TERMINATED:
            return await self._finalize_terminated(row, "VM was deleted outside GPU Deck")
        row = await self._apply_vm(row, vm)
        if row.status == "running" and vm.state == VM_STOPPED:
            row = await self.set(
                row,
                status="stopped",
                stopped_at=utcnow(),
                status_message="VM was stopped outside GPU Deck",
            )
        elif row.status == "stopped" and vm.state == VM_RUNNING:
            row = await self.set(
                row, status="starting", status_message="VM was started outside GPU Deck"
            )
        return row

    async def _poll(self, row: CloudInstances):
        """CloudVm, None (gone), or False when the provider could not be read."""
        try:
            provider = await self.provider_for(row, "poll")
            return await run_provider(
                provider.get_vm,
                row.provider_instance_id,
                region=row.region,
                zone=row.zone,
            )
        except CloudError as exc:
            await self.set(
                row,
                status_message=f"status poll failed: {exc.error_code}: {exc.detail}"[:2048],
                last_polled_at=utcnow(),
            )
            return False

    async def _finalize_terminated(self, row: CloudInstances, message: str) -> CloudInstances:
        if row.join_token_id:
            await self.tokens.revoke(row.join_token_id)
        node_id = row.node_id
        row = await self.set(
            row,
            status="terminated",
            pending_action=None,
            terminated_at=utcnow(),
            status_message=message,
            idle_since=None,
        )
        await self.commit()
        if node_id is not None:
            await self._remove_node(node_id)
        _audit("cloud.instance_terminated", row, reason=message)
        logger.info(f"cloud VM {row.id} terminated: {message}")
        return row

    async def _remove_node(self, node_id: uuid.UUID) -> None:
        from app.services.nodes.service import NodeService

        try:
            await NodeService(self.session).remove(node_id, True, None)
        except BaseAPIException as exc:
            logger.info(f"node {node_id} removal after termination skipped: {exc.detail}")
            await self.session.rollback()
        except Exception as exc:  # noqa: BLE001 - hub unreachable etc.; the row is removed
            logger.warning(f"node {node_id} removal after termination incomplete: {exc}")

    async def _undrain(self, node_id: uuid.UUID) -> None:
        from app.services.nodes.service import NodeService

        try:
            await NodeService(self.session).undrain(node_id, None)
        except BaseAPIException:
            await self.session.rollback()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"undrain of node {node_id} failed: {exc}")

    # ---------- idle auto-terminate ----------

    async def idle_sweep(self) -> int:
        """Rule hook: terminate (after drain) VMs whose node ran nothing for N minutes."""
        terminated = 0
        now = utcnow()
        for row in await self.repo.list_idle_candidates():
            live = await self.inventory.instance_ids_for_node(row.node_id)
            if live:
                if row.idle_since is not None:
                    await self.set(row, idle_since=None)
                continue
            if row.idle_since is None:
                await self.set(row, idle_since=now)
                continue
            if now - row.idle_since < timedelta(minutes=int(row.auto_terminate_idle_minutes)):
                continue
            token = await _lock(row.id)
            if token is None:
                continue
            try:
                reason = f"idle for {row.auto_terminate_idle_minutes} minutes"
                await self.begin_drain(
                    row, "terminate", reason=reason, timeout_seconds=None, actor_id=None
                )
                terminated += 1
                _audit(
                    "cloud.instance_idle_terminate",
                    row,
                    idle_since=row.idle_since.isoformat(),
                )
                await _emit_alert(
                    row,
                    "cloud.instance_idle_terminated",
                    "info",
                    f"Cloud VM {row.name} is being terminated",
                    reason,
                )
            finally:
                await _unlock(row.id, token)
        await self.commit()
        return terminated


async def apply_price_plan(session: AsyncSession, row: CloudInstances) -> Optional[uuid.UUID]:
    """Create (or reuse) a price plan from the provider list price and attach it to the node."""
    from app.db.postgres.models.generated_models import PricePlans
    from app.db.postgres.repositories.price_plan_repository import PricePlanRepository
    from sqlalchemy import func, select

    suggestion = price_plan_suggestion(row)
    if suggestion is None:
        raise ConflictError("the provider exposes no list price for this instance type")
    if row.node_id is None:
        raise ConflictError("the VM has not joined yet; the plan is attached once it joins")
    plans = PricePlanRepository(session)
    existing = (
        await session.execute(
            select(PricePlans).where(
                func.lower(PricePlans.name) == suggestion["name"].lower(),
                PricePlans.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = await plans.create(
            name=suggestion["name"][:255],
            description=(
                f"Provider list price {suggestion['usd_per_hour']} USD/hour "
                f"({suggestion.get('source') or 'provider'}) for {row.gpu_count or 1} GPU(s)"
            )[:2048],
            usd_per_gpu_minute=Decimal(str(suggestion["usd_per_gpu_minute"])),
        )
    await NodeRepository(session).update(row.node_id, price_plan_id=existing.id)
    _audit("cloud.price_plan_applied", row, price_plan_id=str(existing.id))
    return existing.id


__all__: List[str] = [
    "CloudLifecycle",
    "apply_price_plan",
    "price_plan_suggestion",
    "cloud_config",
]
