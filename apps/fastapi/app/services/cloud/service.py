"""
Cloud service: catalog browsing and the admin actions on provisioned
cloud GPU VMs. The state machine itself lives in ``lifecycle.py``; this
module validates requests, writes ``cloud_instances`` rows and hands work to
the lifecycle (inline for stop/start/terminate, via the ``cloud`` Celery queue
+ the ``cloud.reconcile`` job for creates, which can take minutes).

Credentials: browsing takes ``credential_id`` or ``cluster_id`` (the cluster's
vault credential); provisioning always uses the cluster's credential.
Limits: ``clusters.config.cloud.max_nodes`` (default
``CLOUD_MAX_NODES_PER_CLUSTER``) caps live cloud VMs + other nodes.
"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.exceptions import ConflictError, ResourceNotFoundError, ValidationError
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_db
from app.db.postgres.repositories.cloud_instance_repository import (
    CloudInstanceRepository,
    CloudInstances,
)
from app.db.postgres.repositories.cluster_repository import ClusterRepository
from app.db.postgres.repositories.node_repository import NodeRepository
from app.schemas.cloud_schema import (
    CloudNodeActionRequest,
    CloudNodeCreate,
    CloudNodeResponse,
    CloudNodeUpdate,
    CloudProvisionPreview,
)
from app.services.audit import audit
from app.services.cloud import catalog
from app.services.cloud.base import CloudInvalidRequestError, CloudProvider
from app.services.cloud.credentials import load_provider
from app.services.cloud.lifecycle import (
    CloudLifecycle,
    apply_price_plan,
    cloud_config,
    instance_tags,
    price_plan_suggestion,
)
from app.services.cloud.providers import PROVIDERS, provider_for_cluster_type
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

HOURS_PER_MONTH = 730
# Launch options that may come from clusters.config.cloud (everything else there is ours).
OWN_CONFIG_KEYS = {
    "max_nodes",
    "auto_terminate_idle_minutes",
    "region",
    "zone",
    "image",
    "disk_gb",
    "default_instance_type",
}


def enqueue_advance(row_id: uuid.UUID) -> None:
    """Ask a worker to run the next lifecycle step now (never raises)."""
    try:
        from app.workers.tasks.cloud_tasks import advance

        advance.apply_async(args=[str(row_id)], queue="cloud")
    except Exception as exc:  # noqa: BLE001 - the cloud.reconcile job picks it up anyway
        logger.debug(f"cloud advance not enqueued ({type(exc).__name__}); the job will run it")


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")


class CloudService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = CloudInstanceRepository(session)
        self.clusters = ClusterRepository(session)
        self.nodes = NodeRepository(session)

    @staticmethod
    async def as_dependency(request: Request, db: AsyncSession = Depends(get_db)) -> "CloudService":
        return CloudService(db)

    # ---------- provider clients ----------

    async def _client_for(
        self,
        provider: str,
        *,
        credential_id: Optional[uuid.UUID],
        cluster_id: Optional[uuid.UUID],
        purpose: str,
    ) -> Tuple[CloudProvider, uuid.UUID, Optional[Any]]:
        catalog.ensure_enabled(provider)
        cluster = None
        if cluster_id is not None:
            cluster = await self.clusters.get_active(cluster_id)
            if cluster is None:
                raise ResourceNotFoundError(resource_type="Cluster")
            if provider_for_cluster_type(cluster.type) != provider:
                raise ValidationError(f"cluster {cluster.name} is not a {provider} cluster")
            credential_id = credential_id or cluster.credential_id
        if credential_id is None:
            raise ValidationError("credential_id or cluster_id (with a credential) is required")
        project = cloud_config(cluster).get("project")
        client = await load_provider(
            self.session, provider, credential_id, purpose=purpose, project=project
        )
        # The vault read is done; do not hold the pooled connection over provider I/O.
        await self.session.commit()
        return client, credential_id, cluster

    # ---------- catalog ----------

    async def check_credential(self, provider: str, credential_id: uuid.UUID) -> Dict[str, Any]:
        client, _, _ = await self._client_for(
            provider, credential_id=credential_id, cluster_id=None, purpose="check"
        )
        result = await catalog.run_provider(client.check_credentials)
        audit.record(
            "cloud.credential_checked",
            resource_type="credential",
            resource_id=credential_id,
            metadata={"provider": provider, "ok": result.ok},
        )
        return {"provider": provider, **result.to_dict()}

    async def regions(
        self,
        provider: str,
        credential_id: Optional[uuid.UUID],
        cluster_id: Optional[uuid.UUID],
        refresh: bool,
    ) -> List[Dict[str, Any]]:
        client, cred, _ = await self._client_for(
            provider,
            credential_id=credential_id,
            cluster_id=cluster_id,
            purpose="catalog",
        )
        return await catalog.regions(client, cred, refresh=refresh)

    async def instance_types(
        self,
        provider: str,
        region: str,
        credential_id: Optional[uuid.UUID],
        cluster_id: Optional[uuid.UUID],
        refresh: bool,
        **filters: Any,
    ) -> List[Dict[str, Any]]:
        client, cred, _ = await self._client_for(
            provider,
            credential_id=credential_id,
            cluster_id=cluster_id,
            purpose="catalog",
        )
        items = await catalog.instance_types(client, cred, region, refresh=refresh)
        return catalog.filter_instance_types(items, **filters)

    async def quotas(
        self,
        provider: str,
        region: str,
        credential_id: Optional[uuid.UUID],
        cluster_id: Optional[uuid.UUID],
    ) -> List[Dict[str, Any]]:
        client, _, _ = await self._client_for(
            provider,
            credential_id=credential_id,
            cluster_id=cluster_id,
            purpose="quotas",
        )
        return await catalog.quotas(client, region)

    # ---------- reads ----------

    async def _get(self, row_id: uuid.UUID) -> CloudInstances:
        row = await self.repo.get_active(row_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Cloud node")
        return row

    async def to_response(self, rows: List[CloudInstances]) -> List[CloudNodeResponse]:
        cluster_names: Dict[uuid.UUID, str] = {}
        out = []
        for row in rows:
            if row.cluster_id not in cluster_names:
                cluster = await self.clusters.get_by_id(row.cluster_id)
                cluster_names[row.cluster_id] = cluster.name if cluster else None
            node = await self.nodes.get_by_id(row.node_id) if row.node_id else None
            data = {c: getattr(row, c) for c in CloudNodeResponse.model_fields if hasattr(row, c)}
            data["public_ip"] = str(row.public_ip) if row.public_ip else None
            data["private_ip"] = str(row.private_ip) if row.private_ip else None
            data["price_per_hour_usd"] = (
                float(row.price_per_hour_usd) if row.price_per_hour_usd is not None else None
            )
            data["cluster_name"] = cluster_names[row.cluster_id]
            data["node_name"] = node.name if node else None
            data["node_status"] = node.status if node else None
            data["price_plan_suggestion"] = price_plan_suggestion(row)
            data["labels"] = dict(row.labels or {})
            data["tags"] = dict(row.tags or {})
            data["options"] = dict(row.options or {})
            out.append(CloudNodeResponse.model_validate(data))
        return out

    async def list(
        self,
        page: int,
        page_size: int,
        *,
        cluster_id: Optional[uuid.UUID],
        provider: Optional[str],
        status: Optional[List[str]],
        search: Optional[str],
        sort: Optional[str],
        include_terminated: bool,
    ) -> Tuple[List[CloudNodeResponse], int]:
        rows, total = await self.repo.list(
            page,
            page_size,
            cluster_id=cluster_id,
            provider=provider,
            status=status,
            search=search,
            sort=sort,
            include_terminated=include_terminated,
        )
        return await self.to_response(rows), total

    async def get(self, row_id: uuid.UUID) -> CloudNodeResponse:
        return (await self.to_response([await self._get(row_id)]))[0]

    # ---------- provision ----------

    async def _plan(self, body: CloudNodeCreate) -> Dict[str, Any]:
        cluster = await self.clusters.get_active(body.cluster_id)
        if cluster is None:
            raise ResourceNotFoundError(resource_type="Cluster")
        if cluster.status != "active":
            raise ConflictError("cluster is disabled")
        provider = provider_for_cluster_type(cluster.type)
        if provider is None:
            raise ValidationError(
                f"cluster type {cluster.type} is not a cloud cluster "
                f"(use one of {', '.join(spec[2] for spec in PROVIDERS.values())})"
            )
        catalog.ensure_enabled(provider)
        if cluster.credential_id is None:
            raise ValidationError("the cluster has no credential_id (vault) for its cloud account")
        cfg = cloud_config(cluster)
        region = body.region or cluster.region or cfg.get("region")
        if not region:
            raise ValidationError(
                "region is required (request, cluster region or config.cloud.region)"
            )
        max_nodes = int(cfg.get("max_nodes") or settings.CLOUD_MAX_NODES_PER_CLUSTER)
        current = await self.repo.count_active_for_cluster(cluster.id)
        current += await self.repo.count_unmanaged_nodes(cluster.id)
        if current + body.count > max_nodes:
            exc = ConflictError(
                f"cluster {cluster.name} allows {max_nodes} nodes; it has {current} and "
                f"{body.count} more were requested"
            )
            exc.error_code = "CLOUD_MAX_NODES"
            raise exc
        options = {k: v for k, v in cfg.items() if k not in OWN_CONFIG_KEYS}
        options.update(body.options or {})
        options.pop("apply_price_plan", None)
        await self._ssh_key_option(cluster, cfg, options)
        if body.apply_price_plan:
            options["apply_price_plan"] = True
        client, cred, _ = await self._client_for(
            provider,
            credential_id=cluster.credential_id,
            cluster_id=None,
            purpose="provision",
        )
        itype = await catalog.find_instance_type(client, cred, region, body.instance_type)
        if not itype.get("gpu_count") and not itype.get("gpu_model"):
            raise CloudInvalidRequestError(f"{body.instance_type} has no GPU", provider=provider)
        zone = body.zone or cfg.get("zone") or options.pop("zone", None)
        options.pop("zone", None)
        if provider == "gcp" and not zone:
            zones = (itype.get("extra") or {}).get("zones") or []
            if not zones:
                raise ValidationError(f"no zone in {region} offers {body.instance_type}")
            zone = sorted(zones)[0]
        if provider == "azure":
            missing = [k for k in ("resource_group", "subnet_id") if not options.get(k)]
            if missing:
                raise ValidationError(
                    f"Azure needs {', '.join(missing)} in the cluster config (config.cloud) "
                    "or the request options"
                )
        idle = body.auto_terminate_idle_minutes
        if idle is None and cfg.get("auto_terminate_idle_minutes"):
            idle = int(cfg["auto_terminate_idle_minutes"])
        base = (
            body.name or f"{slug(cluster.name)}-{slug(body.instance_type)}-{secrets.token_hex(2)}"
        )
        base = slug(base)[:60] or "gpu-node"
        names = [base] if body.count == 1 else [f"{base}-{i + 1}" for i in range(body.count)]
        for name in names:
            if await self.repo.name_taken(cluster.id, name):
                raise ConflictError(f"a cloud node named {name} already exists in this cluster")
            if await self.nodes.get_by_name(cluster.id, name) is not None:
                raise ConflictError(f"a node named {name} already exists in this cluster")
        return {
            "cluster": cluster,
            "provider": provider,
            "region": region,
            "zone": zone,
            "itype": itype,
            "options": options,
            "names": names,
            "idle": idle,
            "image": body.image or cfg.get("image"),
            "disk_gb": int(body.disk_gb or cfg.get("disk_gb") or settings.CLOUD_DEFAULT_DISK_GB),
            "max_nodes": max_nodes,
            "current": current,
        }

    async def _ssh_key_option(
        self, cluster: Any, cfg: Dict[str, Any], options: Dict[str, Any]
    ) -> None:
        """The cluster's vault SSH key (``config.ssh_key_credential_id``) becomes the
        ``ssh_public_key`` launch option (Azure admin user, GCP instance ssh-keys). Only the
        public key is read; it is not a secret."""
        ref = options.pop("ssh_key_credential_id", None) or (cluster.config or {}).get(
            "ssh_key_credential_id"
        )
        if not ref or options.get("ssh_public_key"):
            return
        from app.services.vault.service import VaultService

        fields = await VaultService(self.session).get_public_fields(str(ref))
        if fields and fields.get("public_key"):
            options["ssh_public_key"] = fields["public_key"]
        else:
            logger.warning(f"cluster {cluster.name}: SSH key credential {ref} is not usable")

    async def provision(
        self, body: CloudNodeCreate, actor_id: Optional[uuid.UUID]
    ) -> Tuple[List[CloudNodeResponse], Optional[CloudProvisionPreview]]:
        plan = await self._plan(body)
        itype = plan["itype"]
        price = itype.get("price_per_hour_usd")
        if body.dry_run:
            per_gpu_min = (
                round(price / 60.0 / max(int(itype.get("gpu_count") or 0), 1), 6)
                if price is not None
                else None
            )
            preview = CloudProvisionPreview(
                cluster_id=plan["cluster"].id,
                provider=plan["provider"],
                region=plan["region"],
                zone=plan["zone"],
                instance_type=itype,
                count=body.count,
                names=plan["names"],
                image=plan["image"],
                disk_gb=plan["disk_gb"],
                options=plan["options"],
                auto_terminate_idle_minutes=plan["idle"],
                max_nodes=plan["max_nodes"],
                current_nodes=plan["current"],
                price_per_hour_usd=price,
                estimated_monthly_usd=(
                    round(price * HOURS_PER_MONTH * body.count, 2) if price is not None else None
                ),
                price_plan_suggestion=(
                    {
                        "name": f"cloud {plan['provider']} {plan['region']} {body.instance_type}",
                        "usd_per_hour": price,
                        "usd_per_gpu_minute": per_gpu_min,
                        "source": itype.get("price_source"),
                    }
                    if price is not None
                    else None
                ),
            )
            return [], preview
        rows: List[CloudInstances] = []
        for name in plan["names"]:
            row_id = uuid.uuid4()
            row = await self.repo.create(
                id=row_id,
                cluster_id=plan["cluster"].id,
                credential_id=plan["cluster"].credential_id,
                provider=plan["provider"],
                name=name,
                region=plan["region"],
                zone=plan["zone"],
                instance_type=body.instance_type,
                image=plan["image"],
                disk_gb=plan["disk_gb"],
                status="pending",
                status_message="queued",
                gpu_model=itype.get("gpu_model"),
                gpu_count=int(itype.get("gpu_count") or 0),
                gpu_vram_bytes=itype.get("gpu_vram_bytes"),
                vcpus=itype.get("vcpus"),
                memory_bytes=itype.get("memory_bytes"),
                price_per_hour_usd=price,
                price_source=itype.get("price_source"),
                auto_terminate_idle_minutes=plan["idle"],
                labels=dict(body.labels or {}),
                options=plan["options"],
                created_by=actor_id,
            )
            row = await self.repo.update(row.id, tags=instance_tags(row)) or row
            rows.append(row)
            audit.record(
                "cloud.instance_requested",
                actor_type="admin" if actor_id else "system",
                actor_id=actor_id,
                resource_type="cloud_instance",
                resource_id=row.id,
                resource_name=row.name,
                metadata={
                    "provider": row.provider,
                    "region": row.region,
                    "zone": row.zone,
                    "instance_type": row.instance_type,
                    "cluster_id": str(row.cluster_id),
                },
            )
        await self.session.commit()
        for row in rows:
            enqueue_advance(row.id)
        logger.info(
            f"cloud provision requested cluster={plan['cluster'].id} type={body.instance_type} "
            f"count={len(rows)}"
        )
        return await self.to_response(rows), None

    # ---------- updates / actions ----------

    async def update(
        self, row_id: uuid.UUID, body: CloudNodeUpdate, actor_id: Optional[uuid.UUID]
    ) -> CloudNodeResponse:
        row = await self._get(row_id)
        changes = body.model_dump(exclude_unset=True)
        values: Dict[str, Any] = {}
        if "auto_terminate_idle_minutes" in changes:
            values["auto_terminate_idle_minutes"] = changes["auto_terminate_idle_minutes"]
            values["idle_since"] = None
        if "labels" in changes:
            values["labels"] = changes["labels"] or {}
        if values:
            row = await self.repo.update(row_id, **values) or row
            audit.record(
                "cloud.instance_updated",
                actor_type="admin",
                actor_id=actor_id,
                resource_type="cloud_instance",
                resource_id=row.id,
                resource_name=row.name,
                after_state={k: v for k, v in changes.items()},
            )
        return await self.get(row.id)

    async def stop(
        self,
        row_id: uuid.UUID,
        body: CloudNodeActionRequest,
        actor_id: Optional[uuid.UUID],
    ) -> CloudNodeResponse:
        row = await self._get(row_id)
        if row.status not in ("running", "booting", "error") or not row.provider_instance_id:
            raise ConflictError(f"cannot stop a cloud node in status {row.status}")
        lifecycle = CloudLifecycle(self.session)
        audit.record(
            "cloud.instance_stop_requested",
            actor_type="admin",
            actor_id=actor_id,
            resource_type="cloud_instance",
            resource_id=row.id,
            resource_name=row.name,
            metadata={"force": body.force, "reason": body.reason},
        )
        if await self._needs_drain(row, body.force):
            row = await lifecycle.begin_drain(
                row,
                "stop",
                reason=body.reason,
                timeout_seconds=body.drain_timeout_seconds,
                actor_id=actor_id,
            )
            # A node without instances is drained at once; continue right away then.
            row = await lifecycle.step_drain(row)
        else:
            row = await lifecycle.execute(row, "stop")
        await self.session.commit()
        return await self.get(row.id)

    async def start(self, row_id: uuid.UUID, actor_id: Optional[uuid.UUID]) -> CloudNodeResponse:
        row = await self._get(row_id)
        if row.status != "stopped":
            raise ConflictError(f"cannot start a cloud node in status {row.status}")
        lifecycle = CloudLifecycle(self.session)
        provider = await lifecycle.provider_for(row, "start")
        await self.session.commit()
        await catalog.run_provider(
            provider.start_vm,
            row.provider_instance_id,
            region=row.region,
            zone=row.zone,
        )
        row = (
            await self.repo.update(
                row.id,
                status="starting",
                status_message="starting the VM",
                last_error=None,
            )
            or row
        )
        audit.record(
            "cloud.instance_start_requested",
            actor_type="admin",
            actor_id=actor_id,
            resource_type="cloud_instance",
            resource_id=row.id,
            resource_name=row.name,
        )
        await self.session.commit()
        enqueue_advance(row.id)
        return await self.get(row.id)

    async def terminate(
        self,
        row_id: uuid.UUID,
        body: CloudNodeActionRequest,
        actor_id: Optional[uuid.UUID],
    ) -> CloudNodeResponse:
        row = await self._get(row_id)
        if row.status in ("terminated", "terminating"):
            raise ConflictError(f"cloud node is already {row.status}")
        audit.record(
            "cloud.instance_terminate_requested",
            actor_type="admin",
            actor_id=actor_id,
            resource_type="cloud_instance",
            resource_id=row.id,
            resource_name=row.name,
            metadata={"force": body.force, "reason": body.reason},
        )
        lifecycle = CloudLifecycle(self.session)
        if not row.provider_instance_id:
            fresh = datetime.now(timezone.utc) - row.updated_at
            if row.status == "provisioning" and fresh.total_seconds() < 600:
                raise ConflictError("the VM is being created; retry in a minute")
            row = await lifecycle.execute(row, "terminate")
        elif row.status == "draining" and not body.force:
            row = await lifecycle.set(row, pending_action="terminate")
        elif await self._needs_drain(row, body.force):
            row = await lifecycle.begin_drain(
                row,
                "terminate",
                reason=body.reason,
                timeout_seconds=body.drain_timeout_seconds,
                actor_id=actor_id,
            )
            row = await lifecycle.step_drain(row)
        else:
            row = await lifecycle.execute(row, "terminate")
        await self.session.commit()
        if row.status == "terminating":
            enqueue_advance(row.id)
        return await self.get(row.id)

    async def _needs_drain(self, row: CloudInstances, force: bool) -> bool:
        if force or row.node_id is None:
            return False
        node = await self.nodes.get_by_id(row.node_id)
        return node is not None and node.status in (
            "online",
            "unhealthy",
            "pending",
            "draining",
        )

    async def refresh(self, row_id: uuid.UUID) -> CloudNodeResponse:
        row = await self._get(row_id)
        await self.session.commit()
        await CloudLifecycle(self.session).advance(row.id)
        return await self.get(row.id)

    async def apply_price_plan(
        self, row_id: uuid.UUID, actor_id: Optional[uuid.UUID]
    ) -> CloudNodeResponse:
        row = await self._get(row_id)
        await apply_price_plan(self.session, row)
        await self.session.commit()
        return await self.get(row.id)

    async def delete(self, row_id: uuid.UUID, actor_id: Optional[uuid.UUID]) -> None:
        row = await self._get(row_id)
        if row.status != "terminated" and not (
            row.status == "error" and not row.provider_instance_id
        ):
            raise ConflictError("terminate the VM first; only terminated records can be deleted")
        await self.repo.update(row.id, deleted_at=datetime.now(timezone.utc))
        audit.record(
            "cloud.instance_deleted",
            actor_type="admin",
            actor_id=actor_id,
            resource_type="cloud_instance",
            resource_id=row.id,
            resource_name=row.name,
        )
