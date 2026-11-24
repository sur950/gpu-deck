"""
Repositories for the secrets vault: credentials + credential_usage_history.

Only ciphertext ever reaches this layer; encryption lives in
app/services/vault/crypto.py.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.core.logger import get_logger
from app.db.postgres.models.generated_models import Credentials, CredentialUsageHistory
from app.db.postgres.repositories.base_repository import BaseRepository
from sqlalchemy import func, not_, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

SORTABLE = {
    "created_at",
    "updated_at",
    "name",
    "type",
    "provider",
    "status",
    "expires_at",
    "last_used_at",
    "usage_count",
}
SYSTEM_PROVIDERS = ("internal_ca", "agent_hub_tls", "api_layer_tls")
SYSTEM_TYPES = ("ca_key", "tls_key")
REFERENCE_TYPES = ("clusters", "alert_channels", "models", "cloud_instances")


def _system_condition():
    """Rows the PKI service manages (internal CA, hub / API-layer TLS keys)."""
    provider = func.coalesce(Credentials.provider, "")
    return or_(
        provider.in_(SYSTEM_PROVIDERS),
        provider.like("%\\_tls"),
        Credentials.type.in_(SYSTEM_TYPES),
        func.coalesce(Credentials.extra_data["managed_by"].astext, "") == "pki",
    )


class CredentialRepository(BaseRepository[Credentials]):
    """CRUD + lookups for credentials (non-deleted rows unless stated)."""

    def __init__(self, session: AsyncSession):
        super().__init__(Credentials, session)

    async def get_active(self, cred_id: uuid.UUID) -> Optional[Credentials]:
        result = await self.session.execute(
            select(Credentials).where(Credentials.id == cred_id, Credentials.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_by_name(
        self, name: str, exclude_id: Optional[uuid.UUID] = None
    ) -> Optional[Credentials]:
        query = select(Credentials).where(
            func.lower(Credentials.name) == name.lower(),
            Credentials.deleted_at.is_(None),
        )
        if exclude_id is not None:
            query = query.where(Credentials.id != exclude_id)
        return (await self.session.execute(query)).scalar_one_or_none()

    async def get_active_by_provider(self, provider: str) -> Optional[Credentials]:
        result = await self.session.execute(
            select(Credentials)
            .where(
                Credentials.provider == provider,
                Credentials.status == "active",
                Credentials.deleted_at.is_(None),
            )
            .order_by(Credentials.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def search(
        self,
        *,
        skip: int,
        limit: int,
        type_: Optional[str] = None,
        provider: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
        sort: Optional[str] = None,
        include_system: bool = False,
    ) -> Tuple[List[Credentials], int]:
        conds = [Credentials.deleted_at.is_(None)]
        if not include_system:
            conds.append(not_(_system_condition()))
        if type_:
            conds.append(Credentials.type == type_)
        if provider:
            providers = [p.strip() for p in provider.split(",") if p.strip()]
            if providers:
                conds.append(Credentials.provider.in_(providers))
        if status:
            conds.append(Credentials.status == status)
        if search:
            like = f"%{search}%"
            conds.append(
                or_(
                    Credentials.name.ilike(like),
                    Credentials.description.ilike(like),
                    Credentials.provider.ilike(like),
                )
            )
        total = (
            await self.session.execute(select(func.count()).select_from(Credentials).where(*conds))
        ).scalar_one()
        rows = await self.session.execute(
            select(Credentials).where(*conds).order_by(*_order(sort)).offset(skip).limit(limit)
        )
        return list(rows.scalars().all()), int(total)

    async def list_all_active(self) -> List[Credentials]:
        rows = await self.session.execute(
            select(Credentials).where(Credentials.deleted_at.is_(None))
        )
        return list(rows.scalars().all())

    async def providers(self) -> List[str]:
        rows = await self.session.execute(
            select(Credentials.provider)
            .where(Credentials.deleted_at.is_(None), Credentials.provider.is_not(None))
            .distinct()
            .order_by(Credentials.provider)
        )
        return [r[0] for r in rows.all()]

    async def stats(
        self, expiring_within_days: int = 7, include_system: bool = False
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        soon = now + timedelta(days=expiring_within_days)
        base = [Credentials.deleted_at.is_(None)]
        if not include_system:
            base.append(not_(_system_condition()))
        total = (
            await self.session.execute(select(func.count()).select_from(Credentials).where(*base))
        ).scalar_one()
        active = (
            await self.session.execute(
                select(func.count())
                .select_from(Credentials)
                .where(*base, Credentials.status == "active")
            )
        ).scalar_one()
        expiring = (
            await self.session.execute(
                select(func.count())
                .select_from(Credentials)
                .where(
                    *base,
                    Credentials.status == "active",
                    Credentials.expires_at.is_not(None),
                    Credentials.expires_at > now,
                    Credentials.expires_at <= soon,
                )
            )
        ).scalar_one()
        expired = (
            await self.session.execute(
                select(func.count())
                .select_from(Credentials)
                .where(*base, Credentials.status == "expired")
            )
        ).scalar_one()
        by_kind_rows = await self.session.execute(
            select(Credentials.provider, func.count()).where(*base).group_by(Credentials.provider)
        )
        by_kind: Dict[str, int] = {}
        for provider, count in by_kind_rows.all():
            key = provider or "untyped"
            by_kind[key] = by_kind.get(key, 0) + int(count)
        return {
            "total": int(total),
            "active": int(active),
            "expiring_soon": int(expiring),
            "expired": int(expired),
            "by_kind": by_kind,
        }

    async def newest_active_by_provider(self, provider: str) -> Optional[Credentials]:
        """Most recently created active credential of a kind (implicit default)."""
        return await self.get_active_by_provider(provider)

    async def soft_delete(self, cred_id: uuid.UUID) -> None:
        now = datetime.now(timezone.utc)
        await self.session.execute(
            update(Credentials)
            .where(Credentials.id == cred_id)
            .values(status="revoked", deleted_at=now)
        )
        await self.session.flush()

    async def expire_expired(self, now: Optional[datetime] = None) -> int:
        now = now or datetime.now(timezone.utc)
        result = await self.session.execute(
            update(Credentials)
            .where(
                Credentials.deleted_at.is_(None),
                Credentials.status == "active",
                Credentials.expires_at.is_not(None),
                Credentials.expires_at <= now,
            )
            .values(status="expired")
        )
        await self.session.flush()
        return result.rowcount

    async def list_due_for_expiry(self, now: Optional[datetime] = None) -> List[Credentials]:
        """Active rows whose expires_at has passed (so the caller can drop their cache)."""
        now = now or datetime.now(timezone.utc)
        result = await self.session.execute(
            select(Credentials).where(
                Credentials.deleted_at.is_(None),
                Credentials.status == "active",
                Credentials.expires_at.is_not(None),
                Credentials.expires_at <= now,
            )
        )
        return list(result.scalars().all())

    async def reference_list(
        self, cred_ids: Iterable[uuid.UUID]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Rows in other domains that point at these credentials, keyed by credential id:
        clusters (credential_id, config ssh / kubeconfig refs), alert channels, models
        (metadata.credential_id) and cloud VMs that are not terminated."""
        ids = [str(i) for i in cred_ids]
        out: Dict[str, List[Dict[str, Any]]] = {i: [] for i in ids}
        if not ids:
            return out
        params = {"ids": ids}
        clusters = await self.session.execute(
            text(
                "SELECT id, name, type::text AS type, credential_id::text AS cred,"
                " config->>'ssh_key_credential_id' AS ssh,"
                " config->>'kubeconfig_credential_id' AS kube,"
                " config->'cloud'->>'ssh_key_credential_id' AS cloud_ssh"
                " FROM clusters WHERE deleted_at IS NULL AND ("
                " credential_id::text = ANY(:ids)"
                " OR config->>'ssh_key_credential_id' = ANY(:ids)"
                " OR config->>'kubeconfig_credential_id' = ANY(:ids)"
                " OR config->'cloud'->>'ssh_key_credential_id' = ANY(:ids))"
            ),
            params,
        )
        for row in clusters.mappings().all():
            seen = set()
            for column, field in (
                ("cred", "credential_id"),
                ("kube", "config.kubeconfig_credential_id"),
                ("ssh", "config.ssh_key_credential_id"),
                ("cloud_ssh", "config.cloud.ssh_key_credential_id"),
            ):
                cid = row[column]
                if cid in out and cid not in seen:
                    seen.add(cid)
                    out[cid].append(
                        {
                            "resource_type": "cluster",
                            "resource_id": str(row["id"]),
                            "name": row["name"],
                            "field": field,
                            "detail": row["type"],
                        }
                    )
        channels = await self.session.execute(
            text(
                "SELECT id, name, type::text AS type, credential_id::text AS cred"
                " FROM alert_channels WHERE deleted_at IS NULL AND credential_id::text = ANY(:ids)"
            ),
            params,
        )
        for row in channels.mappings().all():
            out[row["cred"]].append(
                {
                    "resource_type": "alert_channel",
                    "resource_id": str(row["id"]),
                    "name": row["name"],
                    "field": "credential_id",
                    "detail": row["type"],
                }
            )
        models = await self.session.execute(
            text(
                "SELECT id, name, source::text AS source, metadata->>'credential_id' AS cred"
                " FROM models WHERE deleted_at IS NULL AND metadata->>'credential_id' = ANY(:ids)"
            ),
            params,
        )
        for row in models.mappings().all():
            out[row["cred"]].append(
                {
                    "resource_type": "model",
                    "resource_id": str(row["id"]),
                    "name": row["name"],
                    "field": "metadata.credential_id",
                    "detail": row["source"],
                }
            )
        has_vms = (
            await self.session.execute(text("SELECT to_regclass('cloud_instances') IS NOT NULL"))
        ).scalar_one()
        if not has_vms:  # schemas built from 001_init only (older test suites)
            return out
        vms = await self.session.execute(
            text(
                "SELECT id, name, status::text AS status, credential_id::text AS cred"
                " FROM cloud_instances WHERE deleted_at IS NULL"
                " AND status::text NOT IN ('terminated') AND credential_id::text = ANY(:ids)"
            ),
            params,
        )
        for row in vms.mappings().all():
            out[row["cred"]].append(
                {
                    "resource_type": "cloud_instance",
                    "resource_id": str(row["id"]),
                    "name": row["name"],
                    "field": "credential_id",
                    "detail": row["status"],
                }
            )
        return out

    async def usage_counts(self, cred_ids: Iterable[uuid.UUID]) -> Dict[str, Dict[str, int]]:
        """Per credential: how many clusters / alert channels / models / cloud VMs use it."""
        refs = await self.reference_list(cred_ids)
        plural = {
            "cluster": "clusters",
            "alert_channel": "alert_channels",
            "model": "models",
            "cloud_instance": "cloud_instances",
        }
        out: Dict[str, Dict[str, int]] = {}
        for cid, items in refs.items():
            counts = {k: 0 for k in REFERENCE_TYPES}
            for item in items:
                counts[plural[item["resource_type"]]] += 1
            counts["total"] = sum(counts.values())
            out[cid] = counts
        return out

    async def references(self, cred_id: uuid.UUID) -> Dict[str, int]:
        """Rows in other tables that still point at this credential (delete guard)."""
        counts = (await self.usage_counts([cred_id]))[str(cred_id)]
        return {k: v for k, v in counts.items() if k != "total"}


class CredentialUsageRepository(BaseRepository[CredentialUsageHistory]):
    """Usage history rows (the DB trigger bumps credentials.usage_count/last_used_at)."""

    def __init__(self, session: AsyncSession):
        super().__init__(CredentialUsageHistory, session)

    async def record_usage(
        self,
        credential_id: uuid.UUID,
        *,
        used_by: Optional[uuid.UUID] = None,
        purpose: Optional[str] = None,
        request_id: Optional[uuid.UUID] = None,
    ) -> CredentialUsageHistory:
        return await self.create(
            credential_id=credential_id,
            used_by=used_by,
            purpose=purpose,
            request_id=request_id,
        )

    async def history_with_users(
        self, credential_id: uuid.UUID, limit: int = 20
    ) -> List[Dict[str, Any]]:
        rows = await self.session.execute(
            text(
                "SELECT h.used_at, h.purpose, h.used_by, a.username"
                " FROM credential_usage_history h"
                " LEFT JOIN admin_users a ON a.id = h.used_by"
                " WHERE h.credential_id = :cid ORDER BY h.used_at DESC LIMIT :limit"
            ),
            {"cid": credential_id, "limit": limit},
        )
        return [dict(r) for r in rows.mappings().all()]

    async def history(
        self, credential_id: uuid.UUID, limit: int = 50
    ) -> List[CredentialUsageHistory]:
        rows = await self.session.execute(
            select(CredentialUsageHistory)
            .where(CredentialUsageHistory.credential_id == credential_id)
            .order_by(CredentialUsageHistory.used_at.desc())
            .limit(limit)
        )
        return list(rows.scalars().all())


def _order(sort: Optional[str]):
    sort = sort or "-created_at"
    desc = sort.startswith("-")
    field = sort.lstrip("-+")
    if field not in SORTABLE:
        field, desc = "created_at", True
    column = getattr(Credentials, field)
    return [
        column.desc().nulls_last() if desc else column.asc().nulls_last(),
        Credentials.id,
    ]
