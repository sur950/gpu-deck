"""
Clusters service: CRUD for every cluster type (bare metal, VM,
Kubernetes, cloud AWS/Azure/GCP/DO). Cloud/Kubernetes credentials are vault
references (``credential_id``); the price plan is a FK managed by billing (we only
store it). Deleting requires an empty cluster.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.core.exceptions import (
    ConflictError,
    DuplicateResourceError,
    ResourceNotFoundError,
    ValidationError,
)
from app.core.logger import get_logger
from app.db.postgres.connection import get_db
from app.db.postgres.models.generated_models import Clusters
from app.db.postgres.repositories.cluster_repository import ClusterRepository
from app.db.postgres.repositories.credential_repository import CredentialRepository
from app.db.postgres.repositories.fleet_event_repository import FleetEventRepository
from app.db.postgres.repositories.node_repository import NodeRepository
from app.schemas.fleet_schema import (
    ClusterCounts,
    ClusterCreate,
    ClusterResponse,
    ClusterUpdate,
)
from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class ClusterService:
    def __init__(
        self,
        repo: ClusterRepository,
        nodes: NodeRepository,
        events: FleetEventRepository,
    ):
        self.repo = repo
        self.nodes = nodes
        self.events = events

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db, scope="function")
    ) -> "ClusterService":
        return ClusterService(ClusterRepository(db), NodeRepository(db), FleetEventRepository(db))

    async def _with_counts(self, rows: List[Clusters]) -> List[ClusterResponse]:
        counts = await self.repo.counts_for([r.id for r in rows])
        used = await self.repo.vram_used_for([r.id for r in rows])
        out = []
        for row in rows:
            item = ClusterResponse.model_validate(row)
            c = counts.get(row.id, {})
            c["vram_used_bytes"] = used.get(row.id, 0)
            item.counts = ClusterCounts(**c)
            out.append(item)
        return out

    async def list(
        self,
        page: int,
        page_size: int,
        search: Optional[str],
        sort: Optional[str],
        type_: Optional[str],
        status: Optional[str],
    ) -> Tuple[List[ClusterResponse], int]:
        rows, total = await self.repo.list(page, page_size, search, sort, type_, status)
        return await self._with_counts(rows), total

    async def get(self, cluster_id: uuid.UUID) -> ClusterResponse:
        row = await self.repo.get_active(cluster_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Cluster")
        return (await self._with_counts([row]))[0]

    async def _validate_refs(
        self,
        credential_id: Optional[uuid.UUID],
        price_plan_id: Optional[uuid.UUID],
        cluster_type: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Credential must be an active vault entry of a kind that fits the cluster type
        (AWS key for cloud_aws, kubeconfig for kubernetes, SSH key for bare metal / VM);
        config SSH / kubeconfig references must be SSH keys / kubeconfigs; the price plan
        must exist."""
        from app.services.vault import types as credential_kinds
        from app.services.vault.service import require_compatible

        session = self.repo.session
        if credential_id is not None:
            row = await CredentialRepository(session).get_active(credential_id)
            if row is None or row.status != "active":
                raise ValidationError("credential_id does not reference an active vault credential")
            accepted = credential_kinds.CLUSTER_TYPE_KINDS.get(cluster_type or "")
            if accepted:
                await require_compatible(
                    session, credential_id, accepted, f"A {cluster_type} cluster"
                )
        for key, accepted, what in (
            ("ssh_key_credential_id", ["ssh_key"], "config.ssh_key_credential_id"),
            (
                "kubeconfig_credential_id",
                ["kubernetes"],
                "config.kubeconfig_credential_id",
            ),
        ):
            ref = (config or {}).get(key)
            if ref:
                try:
                    ref_id = uuid.UUID(str(ref))
                except ValueError:
                    raise ValidationError(f"{what} must be a credential id") from None
                await require_compatible(session, ref_id, accepted, what)
        if price_plan_id is not None:
            found = (
                await session.execute(
                    text("SELECT 1 FROM price_plans WHERE id = :id AND deleted_at IS NULL"),
                    {"id": price_plan_id},
                )
            ).first()
            if found is None:
                raise ValidationError("price_plan_id does not reference an existing price plan")

    async def create(self, body: ClusterCreate, actor_id: Optional[uuid.UUID]) -> ClusterResponse:
        if await self.repo.get_by_name(body.name):
            raise DuplicateResourceError(resource_type="Cluster", field="name")
        await self._validate_refs(
            body.credential_id,
            body.price_plan_id,
            str(body.type.value if hasattr(body.type, "value") else body.type),
            body.config,
        )
        row = await self.repo.create(
            name=body.name,
            type=body.type,
            description=body.description,
            region=body.region,
            status="active",
            credential_id=body.credential_id,
            price_plan_id=body.price_plan_id,
            config=body.config,
            egress_allowlist=body.egress_allowlist,
            labels=body.labels,
            created_by=actor_id,
        )
        await self.events.audit(
            "cluster.created",
            actor_type="admin",
            actor_id=actor_id,
            resource_type="cluster",
            resource_id=row.id,
            resource_name=row.name,
            after_state={"type": row.type, "region": row.region},
        )
        logger.info(f"cluster created id={row.id} name={row.name} type={row.type}")
        return (await self._with_counts([row]))[0]

    async def update(
        self, cluster_id: uuid.UUID, body: ClusterUpdate, actor_id: Optional[uuid.UUID]
    ) -> ClusterResponse:
        row = await self.repo.get_active(cluster_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Cluster")
        changes: Dict[str, Any] = body.model_dump(exclude_unset=True)
        if "name" in changes and await self.repo.get_by_name(
            changes["name"], exclude_id=cluster_id
        ):
            raise DuplicateResourceError(resource_type="Cluster", field="name")
        new_type = changes.get("type") or row.type
        await self._validate_refs(
            changes.get("credential_id"),
            changes.get("price_plan_id"),
            str(new_type.value if hasattr(new_type, "value") else new_type),
            changes.get("config"),
        )
        before = {k: getattr(row, k) for k in changes}
        row = await self.repo.update(cluster_id, **changes)
        await self.events.audit(
            "cluster.updated",
            actor_type="admin",
            actor_id=actor_id,
            resource_type="cluster",
            resource_id=cluster_id,
            resource_name=row.name,
            before_state=_jsonable(before),
            after_state=_jsonable(changes),
        )
        if "egress_allowlist" in changes:
            await self.repo.session.commit()
            await self._push_egress(cluster_id)
        if "config" in changes:
            # config.node_security (proxy / SSH source CIDRs) is part of the signed policy.
            await self.repo.session.commit()
            await self._push_sections(cluster_id, ["security"])
        return (await self._with_counts([row]))[0]

    async def delete(self, cluster_id: uuid.UUID, actor_id: Optional[uuid.UUID]) -> None:
        row = await self.repo.get_active(cluster_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Cluster")
        if await self.repo.count_nodes(cluster_id) > 0:
            raise ConflictError("cluster still has nodes; remove them first")
        await self.repo.soft_delete(cluster_id)
        await self.events.audit(
            "cluster.deleted",
            actor_type="admin",
            actor_id=actor_id,
            resource_type="cluster",
            resource_id=cluster_id,
            resource_name=row.name,
        )
        logger.info(f"cluster deleted id={cluster_id}")

    async def _push_egress(self, cluster_id: uuid.UUID) -> None:
        await self._push_sections(cluster_id, ["egress_allowlist"])

    async def _push_sections(self, cluster_id: uuid.UUID, sections: List[str]) -> None:
        from app.services.agent_hub.service import AgentHub

        for node in await self.nodes.list_active(cluster_id):
            try:
                await AgentHub.push_config(node.id, full=False, sections=sections)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"{','.join(sections)} push failed node={node.id}: {exc}")


def _jsonable(data: Dict[str, Any]) -> Dict[str, Any]:
    return {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in data.items()}
