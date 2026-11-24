"""
VaultService — the secrets vault other domains use (typed).

Invariants: plaintext is encrypted before any persistence call; decrypted
values are materialised in memory only for authorised reveal/use paths and
are never logged; ``masked_value`` and the public (non-secret) fields of a
typed credential are the only representations returned by list/get, written
to history records or put in audit metadata.

Typed credentials (``app/services/vault/types.py``): the kind lives in
``credentials.provider`` (aws, azure, gcp, digitalocean, huggingface,
modelscope, slack, smtp, webhook, ssh_key, container_registry, kubernetes,
generic). Create/update validate the fields against the kind's JSON Schema;
public fields, the names of the secret fields that are set and a one-line
summary are kept in ``extra_data.typed``. System credentials (internal CA,
TLS keys written by the PKI service) are hidden from listings and refused by
the admin write / reveal paths.

Admin API: list/get/create/update/delete/reveal/test/usage/stats/metadata.

Other domains (cloud keys, HF/ModelScope tokens, Slack/SMTP secrets, the
internal CA key ``internal-ca``)::

    from app.services.vault import VaultService

    vault = VaultService(db)                               # any AsyncSession
    secret = await vault.get_secret("internal-ca", purpose="join_sign")
    fields = await vault.get_fields(cred_id, purpose="alert delivery")   # typed dict
    secret = await vault.get_secret_by_provider("huggingface", purpose="hf_download")
    cred = await vault.upsert_secret("internal-ca", pem, type="ca_key", provider="internal_ca")

    # without a session (workers): await get_secret_value("smtp-password", purpose="alert_send")

Ciphertext (never plaintext) is cached in Redis ``cred:by_id:{id}`` /
``cred:by_name:{name}`` / ``cred:by_provider:{provider}`` for
CACHE_TTL_CREDENTIALS_SECONDS and dropped on update/delete.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from app.core.exceptions import (
    AuthorizationError,
    ConflictError,
    DuplicateResourceError,
    ResourceNotFoundError,
    ValidationError,
)
from app.core.logger import get_logger
from app.core.request_context import get_request_id
from app.core.settings import settings
from app.db.postgres.connection import get_db, get_session_maker
from app.db.postgres.models.generated_models import Credentials
from app.db.postgres.repositories.credential_repository import (
    CredentialRepository,
    CredentialUsageRepository,
)
from app.db.redis.client import get_redis, redis_key
from app.services.audit import audit
from app.services.vault import types as kinds
from app.services.vault.crypto import (
    InvalidToken,
    decrypt_value,
    encrypt_value,
    key_version_of,
    mask_value,
    needs_rotation,
    rotate_ciphertext,
)
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

CREDENTIAL_TYPES = [
    "api_key",
    "token",
    "oauth",
    "service_account",
    "ssh_key",
    "cloud_account",
    "smtp",
    "webhook",
    "tls_key",
    "ca_key",
    "other",
]
CREDENTIAL_STATUSES = ["active", "expired", "revoked"]
KNOWN_PROVIDERS = [
    "huggingface",
    "modelscope",
    "aws",
    "azure",
    "gcp",
    "digitalocean",
    "kubernetes",
    "slack",
    "smtp",
    "webhook",
    "ssh_key",
    "container_registry",
    "generic",
    "internal_ca",
    "agent_hub_tls",
    "api_layer_tls",
]
CATEGORIES = [
    {"id": "cloud", "label": "Cloud accounts"},
    {"id": "model_source", "label": "Model sources"},
    {"id": "notification", "label": "Notifications"},
    {"id": "access", "label": "Access"},
    {"id": "other", "label": "Other"},
]
TYPED_KEY = "typed"

# Settings naming a credential another domain reads implicitly, and what for.
DEFAULT_CREDENTIAL_SETTINGS: List[Tuple[str, str, Optional[str]]] = [
    ("HF_TOKEN_CREDENTIAL", "Hugging Face downloads (default token)", "huggingface"),
    (
        "MODELSCOPE_TOKEN_CREDENTIAL",
        "ModelScope downloads (default token)",
        "modelscope",
    ),
    ("SMTP_PASSWORD_CREDENTIAL", "Email alerts (default SMTP password)", None),
    ("SLACK_DEFAULT_WEBHOOK_CREDENTIAL", "Slack alerts (default webhook)", None),
    (
        "WEBHOOK_SIGNING_SECRET_CREDENTIAL",
        "Webhook alerts (default signing secret)",
        None,
    ),
    ("BENCHMARK_API_KEY_CREDENTIAL", "Benchmark runner API key", None),
]


def _in_use_error(refs: Dict[str, int], names: Optional[List[str]] = None) -> ConflictError:
    used = ", ".join(f"{count} {name.replace('_', ' ')}" for name, count in refs.items() if count)
    detail = f"Credential is in use by {used}"
    if names:
        detail += f" ({', '.join(names[:5])}{', …' if len(names) > 5 else ''})"
    exc = ConflictError(detail)
    exc.error_code = "CREDENTIAL_IN_USE"
    return exc


def _system_error(action: str) -> AuthorizationError:
    exc = AuthorizationError(
        f"System credentials are managed by GPU Deck and cannot be {action} through the API"
    )
    exc.error_code = "CREDENTIAL_SYSTEM"
    return exc


def row_is_system(row: Credentials) -> bool:
    return kinds.is_system(row.provider, row.type, row.extra_data or {})


def _typed(row: Credentials) -> Dict[str, Any]:
    extra = row.extra_data or {}
    typed = extra.get(TYPED_KEY) if isinstance(extra, dict) else None
    return typed if isinstance(typed, dict) else {}


def _user_metadata(extra: Any) -> Dict[str, Any]:
    if not isinstance(extra, dict):
        return {}
    return {k: v for k, v in extra.items() if k != TYPED_KEY}


def compatible(row: Credentials, accepted: Iterable[str]) -> bool:
    """True when the credential's kind is one of ``accepted``; untyped (legacy) rows pass when
    their category matches one of the accepted kinds."""
    accepted = list(accepted)
    if row.provider in accepted:
        return True
    if kinds.get_kind(row.provider) is not None or row_is_system(row):
        return False
    categories = {kinds.KINDS[k].credential_type for k in accepted if k in kinds.KINDS}
    return row.type in categories or row.type == "other"


async def require_compatible(
    session: AsyncSession, credential_id: uuid.UUID, accepted: Iterable[str], what: str
) -> Credentials:
    """Active credential of an accepted kind, else 422 with a message naming what fits."""
    accepted = list(accepted)
    row = await CredentialRepository(session).get_active(credential_id)
    if row is None or row.status != "active":
        raise ValidationError(
            f"{what}: credential_id does not reference an active vault credential"
        )
    if not compatible(row, accepted):
        have = kinds.KINDS[row.provider].label if row.provider in kinds.KINDS else row.type
        want = " or ".join(kinds.KINDS[k].label for k in accepted if k in kinds.KINDS)
        exc = ValidationError(f"{what} needs a {want} credential; '{row.name}' is a {have}")
        exc.error_code = "CREDENTIAL_KIND_MISMATCH"
        raise exc
    return row


class VaultService:
    """Encrypted credential storage with Redis ciphertext cache and audited reveal."""

    def __init__(
        self,
        db: Union[AsyncSession, CredentialRepository],
        *,
        usage_repo: Optional[CredentialUsageRepository] = None,
    ):
        # Accept a CredentialRepository too (older callers pass the repo first).
        if isinstance(db, CredentialRepository):
            db = db.session
        self.db = db
        self.cred_repo = CredentialRepository(db)
        self.usage_repo = usage_repo or CredentialUsageRepository(db)

    @staticmethod
    async def as_dependency(request: Request, db: AsyncSession = Depends(get_db)) -> "VaultService":
        return VaultService(db)

    # ---------- cache ----------

    @staticmethod
    def _keys_for(row: Credentials) -> List[str]:
        keys = [
            redis_key("cred", "by_id", str(row.id)),
            redis_key("cred", "by_name", row.name.lower()),
        ]
        if row.provider:
            keys.append(redis_key("cred", "by_provider", row.provider))
        return keys

    async def _cache_get(self, key: str) -> Optional[str]:
        try:
            redis = await get_redis()
            return await redis.get(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"vault cache read failed: {exc}")
            return None

    async def _cache_set(self, key: str, ciphertext: str) -> None:
        try:
            redis = await get_redis()
            await redis.set(key, ciphertext, ex=int(settings.CACHE_TTL_CREDENTIALS_SECONDS))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"vault cache write failed: {exc}")

    async def _cache_drop(self, row: Credentials) -> None:
        try:
            redis = await get_redis()
            await redis.delete(*self._keys_for(row))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"vault cache invalidation failed: {exc}")

    # ---------- representation ----------

    @staticmethod
    def to_dict(row: Credentials) -> Dict[str, Any]:
        typed = _typed(row)
        kind = kinds.get_kind(row.provider)
        return {
            "id": row.id,
            "name": row.name,
            "type": row.type,
            "provider": row.provider,
            "kind": kind.id if kind else None,
            "status": row.status,
            "masked_value": row.masked_value,
            "fields": dict(typed.get("fields") or {}),
            "secret_fields": list(
                typed.get("secret_fields") if typed else (kind.secret_fields if kind else ["value"])
            ),
            "summary": typed.get("summary"),
            "system": row_is_system(row),
            "description": row.description,
            "base_url": row.base_url,
            "expires_at": row.expires_at,
            "usage_count": int(row.usage_count or 0),
            "last_used_at": row.last_used_at,
            "access_restrictions": row.access_restrictions or {},
            "metadata": _user_metadata(row.extra_data),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _audit_state(record: Dict[str, Any]) -> Dict[str, Any]:
        keep = (
            "name",
            "type",
            "provider",
            "kind",
            "status",
            "description",
            "expires_at",
        )
        out = {k: record.get(k) for k in keep}
        out["public_fields"] = sorted((record.get("fields") or {}).keys())
        out["secret_fields_set"] = record.get("secret_fields") or []
        return out

    async def _get(self, cred_id: uuid.UUID) -> Credentials:
        row = await self.cred_repo.get_active(cred_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Credential")
        return row

    async def _defaults(self) -> Dict[str, List[str]]:
        """credential id -> the implicit roles it plays (named settings, newest-of-kind)."""
        out: Dict[str, List[str]] = {}
        named_kinds = set()
        for setting, label, kind_id in DEFAULT_CREDENTIAL_SETTINGS:
            name = getattr(settings, setting, None)
            if not name:
                continue
            row = await self.cred_repo.get_by_name(str(name))
            if row is not None and row.status == "active":
                out.setdefault(str(row.id), []).append(label)
                if kind_id:
                    named_kinds.add(kind_id)
        for setting, label, kind_id in DEFAULT_CREDENTIAL_SETTINGS:
            if kind_id and kind_id not in named_kinds:
                row = await self.cred_repo.newest_active_by_provider(kind_id)
                if row is not None:
                    out.setdefault(str(row.id), []).append(label)
        return out

    async def with_usage(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """``used_by`` counts + ``default_for`` roles for one record (create responses)."""
        return (await self._decorate([dict(record)]))[0]

    async def _decorate(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not items:
            return items
        counts = await self.cred_repo.usage_counts([i["id"] for i in items])
        defaults = await self._defaults()
        for item in items:
            item["used_by"] = counts.get(str(item["id"])) or {"total": 0}
            item["default_for"] = defaults.get(str(item["id"]), [])
        return items

    # ---------- admin reads ----------

    async def list_credentials(
        self,
        *,
        page: int,
        page_size: int,
        type_: Optional[str] = None,
        provider: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
        sort: Optional[str] = None,
        include_system: bool = False,
    ) -> Tuple[List[Dict[str, Any]], int]:
        rows, total = await self.cred_repo.search(
            skip=(page - 1) * page_size,
            limit=page_size,
            type_=type_,
            provider=provider,
            status=status,
            search=search,
            sort=sort,
            include_system=include_system,
        )
        return await self._decorate([self.to_dict(r) for r in rows]), total

    async def get_credential(self, cred_id: uuid.UUID) -> Dict[str, Any]:
        return (await self._decorate([self.to_dict(await self._get(cred_id))]))[0]

    async def get_stats(self, include_system: bool = False) -> Dict[str, Any]:
        return await self.cred_repo.stats(include_system=include_system)

    async def get_metadata(self) -> Dict[str, Any]:
        used = await self.cred_repo.providers()
        providers = list(KNOWN_PROVIDERS) + [p for p in used if p not in KNOWN_PROVIDERS]
        pickers: Dict[str, List[str]] = {}
        for cluster_type, accepted in kinds.CLUSTER_TYPE_KINDS.items():
            pickers[f"cluster.{cluster_type}"] = accepted
        pickers["cluster.ssh_key"] = ["ssh_key"]
        for channel_type, accepted in kinds.ALERT_CHANNEL_KINDS.items():
            pickers[f"alert_channel.{channel_type}"] = accepted
        for source, accepted in kinds.MODEL_SOURCE_KINDS.items():
            pickers[f"model.{source}"] = accepted
        pickers["gpu_instance.authorized_keys"] = ["ssh_key"]
        return {
            "types": CREDENTIAL_TYPES,
            "providers": providers,
            "statuses": CREDENTIAL_STATUSES,
            "kinds": kinds.catalog(),
            "categories": CATEGORIES,
            "pickers": pickers,
            "defaults": [
                {
                    "setting": s,
                    "name": getattr(settings, s, None),
                    "used_for": label,
                    "kind": k,
                }
                for s, label, k in DEFAULT_CREDENTIAL_SETTINGS
            ],
        }

    async def usage(self, cred_id: uuid.UUID) -> Dict[str, Any]:
        row = await self._get(cred_id)
        refs = (await self.cred_repo.reference_list([row.id]))[str(row.id)]
        counts = (await self.cred_repo.usage_counts([row.id]))[str(row.id)]
        defaults = (await self._defaults()).get(str(row.id), [])
        history = await self.usage_repo.history_with_users(row.id, limit=20)
        return {
            "credential_id": row.id,
            "used_by": counts,
            "references": refs,
            "default_for": defaults,
            "history": [
                {
                    "used_at": h["used_at"],
                    "purpose": h["purpose"],
                    "used_by": h["used_by"],
                    "used_by_username": h["username"],
                }
                for h in history
            ],
        }

    # ---------- typed value preparation ----------

    def _prepare_new(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create input -> columns (type, provider, ciphertext, masked, extra_data)."""
        provider = data.get("provider") or None
        extra = _user_metadata(data.get("metadata") or {})
        kind = kinds.get_kind(provider)
        if kind is None:
            value = data.get("value")
            if not value:
                raise ValidationError("value is required")
            return {
                "type": data.get("type") or "other",
                "provider": provider,
                "plaintext": value,
                "masked": mask_value(value),
                "extra": extra,
            }
        raw = data.get("fields")
        if raw is None:
            if not data.get("value"):
                raise kinds.CredentialValidationError(f"Invalid {kind.label}: fields are required")
            raw = kinds.fields_from_value(kind, data["value"])
        fields = kinds.validate(kind, kinds.normalize(kind, raw))
        enc = kinds.encode(kind, fields)
        extra[TYPED_KEY] = self._typed_blob(kind, fields, enc)
        type_ = kind.credential_type
        if kind.id == "generic" and data.get("type") and data["type"] not in kinds.SYSTEM_TYPES:
            type_ = data["type"]
        return {
            "type": type_,
            "provider": kind.id,
            "plaintext": enc.plaintext,
            "masked": enc.masked,
            "extra": extra,
        }

    @staticmethod
    def _typed_blob(kind: kinds.CredentialKind, fields: Dict[str, Any], enc: Any) -> Dict[str, Any]:
        return {
            "version": 1,
            "fields": enc.public,
            "secret_fields": [f for f in kind.secret_fields if fields.get(f) not in (None, "")],
            "summary": enc.summary,
        }

    def _decode_row(self, row: Credentials) -> Dict[str, Any]:
        """All fields of a credential (plaintext in memory only)."""
        plaintext = self._decrypt(row)
        kind = kinds.get_kind(row.provider)
        if kind is None:
            return {"value": plaintext}
        return kinds.decode(kind, plaintext, _typed(row).get("fields"))

    # ---------- admin writes ----------

    async def create_credential(
        self,
        data: Optional[Dict[str, Any]] = None,
        *,
        created_by: Optional[uuid.UUID] = None,
        **fields: Any,
    ) -> "CredentialRecord":
        """
        Store a secret. Accepts a dict (routes) or the keyword form
        ``create_credential(name=..., type=..., value=..., created_by=..., provider=...)``.
        Typed kinds take ``fields`` (or a legacy ``value``) and are schema-validated.
        Returns the masked record (a dict that also allows ``record.id``).
        """
        data = dict(data or {}, **fields)
        if await self.cred_repo.get_by_name(data["name"]):
            raise DuplicateResourceError(resource_type="Credential", field="name")
        prepared = self._prepare_new(data)
        row = await self.cred_repo.create(
            name=data["name"],
            type=prepared["type"],
            provider=prepared["provider"],
            status="active",
            encrypted_value=encrypt_value(prepared["plaintext"]),
            masked_value=prepared["masked"],
            description=data.get("description"),
            base_url=data.get("base_url"),
            expires_at=data.get("expires_at"),
            key_version=1,
            access_restrictions=data.get("access_restrictions") or {},
            extra_data=prepared["extra"],
            created_by=created_by,
        )
        result = CredentialRecord(self.to_dict(row))
        logger.info(
            f"Credential created: id={row.id} name={row.name} type={row.type} "
            f"kind={row.provider}"
        )
        audit.record(
            "credential.created",
            resource_type="credential",
            resource_id=row.id,
            resource_name=row.name,
            after_state=self._audit_state(result),
        )
        return result

    async def update_credential(
        self, cred_id: uuid.UUID, data: Dict[str, Any], *, internal: bool = True
    ) -> Dict[str, Any]:
        """Partial update. Typed: ``fields`` merge (secret fields left out keep their value),
        ``value`` replaces the stored secret. ``internal=False`` (admin API) refuses system
        credentials."""
        row = await self._get(cred_id)
        if not internal and row_is_system(row):
            raise _system_error("changed")
        before = self.to_dict(row)
        changes: Dict[str, Any] = {}
        if data.get("name") is not None and data["name"] != row.name:
            if await self.cred_repo.get_by_name(data["name"], exclude_id=cred_id):
                raise DuplicateResourceError(resource_type="Credential", field="name")
            changes["name"] = data["name"]
        current_kind = kinds.get_kind(row.provider)
        new_provider = data["provider"] if "provider" in data else row.provider
        if current_kind is not None and new_provider not in (None, row.provider):
            raise ValidationError(
                f"A credential's type cannot change ({current_kind.label}); create a new one"
            )
        for field in ("description", "base_url", "expires_at"):
            if field in data and (data[field] is not None or field == "expires_at"):
                changes[field] = data[field]
        if data.get("access_restrictions") is not None:
            changes["access_restrictions"] = data["access_restrictions"]
        extra = dict(row.extra_data or {})
        if data.get("metadata") is not None:
            extra = {
                **_user_metadata(data["metadata"]),
                TYPED_KEY: extra.get(TYPED_KEY),
            }
            if extra[TYPED_KEY] is None:
                extra.pop(TYPED_KEY)
            changes["extra_data"] = extra
        value_changed = False
        target_kind = current_kind or kinds.get_kind(new_provider)
        converting = current_kind is None and target_kind is not None
        if target_kind is not None and (
            converting or data.get("fields") is not None or data.get("value")
        ):
            old_plain = self._decrypt(row)
            if converting:
                # Untyped (legacy) row becoming typed: the stored secret seeds the kind's
                # primary field; everything else must come with the request.
                try:
                    current: Dict[str, Any] = kinds.fields_from_value(target_kind, old_plain)
                except kinds.CredentialValidationError:
                    current = {}
            else:
                current = self._decode_row(row)
            if data.get("fields") is not None:
                incoming = data["fields"]
            elif data.get("value"):
                incoming = kinds.fields_from_value(target_kind, data["value"])
            else:
                incoming = {}
            # merge sees empties: a cleared public field is removed, an empty secret is kept.
            merged = kinds.merge(target_kind, current, incoming or {})
            fields = kinds.validate(target_kind, kinds.normalize(target_kind, merged))
            enc = kinds.encode(target_kind, fields)
            value_changed = enc.plaintext != old_plain
            extra = dict(changes.get("extra_data") or extra)
            extra[TYPED_KEY] = self._typed_blob(target_kind, fields, enc)
            changes["extra_data"] = extra
            changes["encrypted_value"] = encrypt_value(enc.plaintext)
            changes["masked_value"] = enc.masked
            changes["key_version"] = 1
            if converting:
                changes["provider"] = target_kind.id
                changes["type"] = target_kind.credential_type
        elif target_kind is None:
            if "type" in data and data["type"] is not None:
                if not internal and data["type"] in kinds.SYSTEM_TYPES:
                    raise ValidationError("ca_key / tls_key credentials are managed by GPU Deck")
                changes["type"] = data["type"]
            if "provider" in data:
                changes["provider"] = data["provider"]
            if data.get("value"):
                changes["encrypted_value"] = encrypt_value(data["value"])
                changes["masked_value"] = mask_value(data["value"])
                changes["key_version"] = 1
                value_changed = True
        if value_changed:
            changes["status"] = "active"
        expires = changes.get("expires_at", row.expires_at)
        if (
            row.status == "expired"
            and "expires_at" in changes
            and (expires is None or expires > datetime.now(timezone.utc))
        ):
            changes["status"] = "active"
        await self._cache_drop(row)
        if changes:
            await self.cred_repo.update(cred_id, **changes)
        row = await self._get(cred_id)
        await self._cache_drop(row)
        result = self.to_dict(row)
        audit.record(
            "credential.updated",
            resource_type="credential",
            resource_id=cred_id,
            resource_name=row.name,
            before_state=self._audit_state(before),
            after_state=self._audit_state(result),
            metadata={"value_changed": value_changed},
        )
        return (await self._decorate([result]))[0]

    async def delete_credential(self, cred_id: uuid.UUID, *, internal: bool = True) -> None:
        row = await self._get(cred_id)
        if not internal and row_is_system(row):
            raise _system_error("deleted")
        refs = (await self.cred_repo.reference_list([row.id]))[str(row.id)]
        if refs:
            counts = await self.cred_repo.references(cred_id)
            raise _in_use_error(counts, [f"{r['resource_type']} {r['name']}" for r in refs])
        before = self.to_dict(row)
        await self.cred_repo.soft_delete(cred_id)
        await self._cache_drop(row)
        audit.record(
            "credential.deleted",
            resource_type="credential",
            resource_id=cred_id,
            resource_name=row.name,
            before_state=self._audit_state(before),
        )
        logger.info(f"Credential deleted: id={cred_id}")

    async def reveal_credential(
        self,
        cred_id: uuid.UUID,
        *,
        requested_by: Optional[uuid.UUID],
        internal: bool = True,
    ) -> Dict[str, Any]:
        """PRIVILEGED: plaintext once; writes a usage row and an audit entry."""
        row = await self._get(cred_id)
        if not internal and row_is_system(row):
            raise _system_error("revealed")
        value = self._decrypt(row)
        fields = self._decode_row(row)
        await self.usage_repo.record_usage(
            row.id, used_by=requested_by, purpose="reveal", request_id=_request_uuid()
        )
        logger.warning(f"Credential revealed: id={row.id} by={requested_by}")
        audit.record(
            "credential.revealed",
            resource_type="credential",
            resource_id=row.id,
            resource_name=row.name,
            metadata={"masked_value": row.masked_value, "kind": row.provider},
        )
        return {"id": row.id, "value": value, "fields": fields}

    async def test_credential(
        self, cred_id: uuid.UUID, *, requested_by: Optional[uuid.UUID] = None
    ) -> Dict[str, Any]:
        """Offline check: decryptable with VAULT_KEYS, active, not expired, fields match the
        kind's schema. Cloud kinds point at /cloud/providers/{p}/check-credential for the live
        provider call. Never returns secret material."""
        row = await self._get(cred_id)
        if row_is_system(row):
            raise _system_error("tested")
        kind = kinds.get_kind(row.provider)
        checks: List[Dict[str, Any]] = []
        decoded: Optional[Dict[str, Any]] = None
        version = key_version_of(row.encrypted_value)
        if version:
            checks.append(
                {
                    "name": "decrypt",
                    "ok": True,
                    "message": "Decrypts with VAULT_KEYS"
                    + ("" if version == 1 else f" (entry {version}; rotation pending)"),
                }
            )
            decoded = self._decode_row(row)
        else:
            checks.append(
                {
                    "name": "decrypt",
                    "ok": False,
                    "message": "No VAULT_KEYS entry decrypts it",
                }
            )
        now = datetime.now(timezone.utc)
        if row.status != "active":
            checks.append({"name": "status", "ok": False, "message": f"Status is {row.status}"})
        elif row.expires_at is not None and row.expires_at <= now:
            checks.append({"name": "status", "ok": False, "message": "Past its expiry date"})
        else:
            checks.append({"name": "status", "ok": True, "message": "Active"})
        if kind is None:
            checks.append(
                {
                    "name": "schema",
                    "ok": True,
                    "message": "Untyped credential: no schema to check. Edit it to pick a type.",
                }
            )
        elif decoded is not None:
            try:
                kinds.validate(kind, decoded)
                checks.append({"name": "schema", "ok": True, "message": f"Valid {kind.label}"})
            except kinds.CredentialValidationError as exc:
                checks.append({"name": "schema", "ok": False, "message": str(exc.detail)})
        ok = all(c["ok"] for c in checks)
        await self.usage_repo.record_usage(
            row.id, used_by=requested_by, purpose="test", request_id=_request_uuid()
        )
        audit.record(
            "credential.tested",
            resource_type="credential",
            resource_id=row.id,
            resource_name=row.name,
            outcome="success" if ok else "failure",
            metadata={
                "kind": row.provider,
                "checks": {c["name"]: c["ok"] for c in checks},
            },
        )
        cloud = kind.cloud_provider if kind else None
        return {
            "id": row.id,
            "ok": ok,
            "kind": kind.id if kind else None,
            "checks": checks,
            "cloud_provider": cloud,
            "cloud_check_path": (f"/cloud/providers/{cloud}/check-credential" if cloud else None),
            "tested_at": now,
        }

    # ---------- programmatic access for other domains ----------

    def _decrypt(self, row: Credentials) -> str:
        try:
            return decrypt_value(row.encrypted_value)
        except InvalidToken as exc:
            logger.error(f"Credential {row.id} cannot be decrypted with VAULT_KEYS")
            raise ConflictError("Credential cannot be decrypted with the configured keys") from exc

    async def _record_use(self, row: Credentials, purpose: Optional[str]) -> None:
        if purpose:
            await self.usage_repo.record_usage(
                row.id, used_by=None, purpose=purpose[:255], request_id=_request_uuid()
            )

    async def _resolve(self, ref: Union[uuid.UUID, str]) -> Optional[Credentials]:
        if isinstance(ref, uuid.UUID):
            return await self.cred_repo.get_active(ref)
        try:
            return await self.cred_repo.get_active(uuid.UUID(str(ref)))
        except ValueError:
            return await self.cred_repo.get_by_name(str(ref))

    async def get_secret(
        self,
        ref: Union[uuid.UUID, str],
        *,
        purpose: Optional[str] = None,
        required: bool = True,
    ) -> Optional[str]:
        """Plaintext for a credential by id or name (active rows only), cache-first.
        Single-secret kinds return the secret itself; JSON kinds (aws, azure) a JSON object.
        """
        cache_key = (
            redis_key("cred", "by_id", str(ref))
            if isinstance(ref, uuid.UUID)
            else redis_key("cred", "by_name", str(ref).lower())
        )
        ciphertext = await self._cache_get(cache_key)
        if ciphertext:
            try:
                value = decrypt_value(ciphertext)
                if purpose:
                    row = await self._resolve(ref)
                    if row is not None:
                        await self._record_use(row, purpose)
                return value
            except InvalidToken:
                pass  # stale cache (key rotation); fall through to the row
        row = await self._resolve(ref)
        if row is None or row.status != "active":
            if required:
                raise ResourceNotFoundError(resource_type="Credential")
            return None
        value = self._decrypt(row)
        await self._cache_set(cache_key, row.encrypted_value)
        await self._record_use(row, purpose)
        return value

    async def get_fields(
        self,
        ref: Union[uuid.UUID, str],
        *,
        purpose: Optional[str] = None,
        required: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Typed view of an active credential: the kind's fields (secret + public); untyped
        rows come back as ``{"value": plaintext}``. Plaintext stays in memory."""
        row = await self._resolve(ref)
        if row is None or row.status != "active":
            if required:
                raise ResourceNotFoundError(resource_type="Credential")
            return None
        fields = self._decode_row(row)
        await self._record_use(row, purpose)
        return fields

    async def get_public_fields(self, ref: Union[uuid.UUID, str]) -> Optional[Dict[str, Any]]:
        """Non-secret fields of an active credential (no decryption, no usage row): SMTP host,
        webhook URL, SSH public key, GCP project ... ``None`` when missing / inactive.
        """
        row = await self._resolve(ref)
        if row is None or row.status != "active":
            return None
        return dict(_typed(row).get("fields") or {})

    async def get_cached_credential_value(self, cred_id: Union[uuid.UUID, str]) -> Optional[str]:
        """Compatibility alias: plaintext by id (None when missing/inactive)."""
        ref = cred_id if isinstance(cred_id, uuid.UUID) else uuid.UUID(str(cred_id))
        return await self.get_secret(ref, purpose="internal", required=False)

    async def get_secret_by_provider(
        self, provider: str, *, purpose: Optional[str] = None, required: bool = True
    ) -> Optional[str]:
        cache_key = redis_key("cred", "by_provider", provider)
        ciphertext = await self._cache_get(cache_key)
        if ciphertext:
            try:
                return decrypt_value(ciphertext)
            except InvalidToken:
                pass
        row = await self.cred_repo.get_active_by_provider(provider)
        if row is None:
            if required:
                raise ResourceNotFoundError(resource_type="Credential")
            return None
        value = self._decrypt(row)
        await self._cache_set(cache_key, row.encrypted_value)
        await self._record_use(row, purpose)
        return value

    async def upsert_secret(
        self,
        name: str,
        value: str,
        *,
        type: str = "other",
        provider: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        expires_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Create or replace a credential by name (internal callers: CA key, hub TLS, ...)."""
        existing = await self.cred_repo.get_by_name(name)
        if existing is None:
            return await self.create_credential(
                {
                    "name": name,
                    "type": type,
                    "provider": provider,
                    "value": value,
                    "description": description,
                    "metadata": metadata or {},
                    "expires_at": expires_at,
                },
                created_by=None,
            )
        update: Dict[str, Any] = {
            "value": value,
            "description": description,
            "metadata": metadata,
            "expires_at": expires_at,
        }
        if kinds.get_kind(existing.provider) is None:
            update.update({"type": type, "provider": provider})
        return await self.update_credential(existing.id, update)

    async def rotate_keys(self) -> int:
        """Re-encrypt every row not on the primary VAULT_KEYS entry; returns the count."""
        rotated = 0
        for row in await self.cred_repo.list_all_active():
            if not needs_rotation(row.encrypted_value):
                continue
            try:
                new_ct = rotate_ciphertext(row.encrypted_value)
            except InvalidToken:
                logger.error(f"Credential {row.id} is not decryptable with any VAULT_KEYS entry")
                continue
            await self.cred_repo.update(row.id, encrypted_value=new_ct, key_version=1)
            await self._cache_drop(row)
            rotated += 1
        if rotated:
            audit.record(
                "vault.keys_rotated",
                actor_type="system",
                metadata={"re_encrypted": rotated},
            )
            logger.info(f"Vault rotation re-encrypted {rotated} credential(s)")
        return rotated

    async def expire_credentials(self) -> int:
        """Mark credentials past expires_at as expired and drop their cached ciphertext."""
        now = datetime.now(timezone.utc)
        due = await self.cred_repo.list_due_for_expiry(now)
        count = await self.cred_repo.expire_expired(now)
        for row in due:
            await self._cache_drop(row)
        if count:
            audit.record(
                "credential.expired",
                actor_type="system",
                metadata={"count": count, "ids": [str(r.id) for r in due][:100]},
            )
        if count:
            logger.info(f"Vault: {count} credential(s) marked expired")
        return count


class CredentialRecord(dict):
    """Masked credential dict that also supports attribute access (``record.id``)."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _request_uuid() -> Optional[uuid.UUID]:
    rid = get_request_id()
    try:
        return uuid.UUID(rid) if rid else None
    except ValueError:
        return None


async def get_secret_value(
    ref: Union[uuid.UUID, str], *, purpose: Optional[str] = None, required: bool = True
) -> Optional[str]:
    """Convenience for workers/hooks without a session of their own."""
    async with get_session_maker()() as session:
        vault = VaultService(session)
        value = await vault.get_secret(ref, purpose=purpose, required=required)
        await session.commit()
        return value
