"""
Vault (credential) schemas (admin.yaml: Vault tag).

Typed credentials: ``provider`` is the kind (aws, azure, gcp, digitalocean,
huggingface, modelscope, slack, smtp, webhook, ssh_key, container_registry,
kubernetes, generic; catalog at ``GET /vault/metadata``) and ``fields`` carries
its inputs. Secret fields and the legacy ``value`` are accepted on
create/update only and never returned; ``fields`` in responses holds the
public (non-secret) fields only.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CredentialType(str, Enum):
    API_KEY = "api_key"
    TOKEN = "token"
    OAUTH = "oauth"
    SERVICE_ACCOUNT = "service_account"
    SSH_KEY = "ssh_key"
    CLOUD_ACCOUNT = "cloud_account"
    SMTP = "smtp"
    WEBHOOK = "webhook"
    TLS_KEY = "tls_key"
    CA_KEY = "ca_key"
    OTHER = "other"


class CredentialStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class CredentialCreate(BaseModel):
    """Typed: ``provider`` + ``fields``. Legacy: ``value`` (+ ``type``); without a provider
    the credential is stored as a ``generic`` secret."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    provider: Optional[str] = Field(None, max_length=100, description="Credential kind")
    fields: Optional[Dict[str, Any]] = Field(
        None, description="Kind fields (secret ones are write-only)"
    )
    type: Optional[CredentialType] = None
    value: Optional[str] = Field(None, min_length=1, description="Plaintext secret; never returned")
    description: Optional[str] = Field(None, max_length=2048)
    base_url: Optional[str] = Field(None, max_length=2048)
    expires_at: Optional[datetime] = None
    access_restrictions: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _value_or_fields(self) -> "CredentialCreate":
        if self.fields is None and not self.value:
            raise ValueError("fields (or value) is required")
        return self


class CredentialUpdate(BaseModel):
    """Partial update; every field optional. ``fields`` merges: secret fields left out keep
    their stored value, public fields present replace (empty clears)."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    provider: Optional[str] = Field(None, max_length=100)
    fields: Optional[Dict[str, Any]] = None
    type: Optional[CredentialType] = None
    value: Optional[str] = Field(None, min_length=1)
    description: Optional[str] = Field(None, max_length=2048)
    base_url: Optional[str] = Field(None, max_length=2048)
    expires_at: Optional[datetime] = None
    access_restrictions: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


class CredentialUsageCounts(BaseModel):
    clusters: int = 0
    alert_channels: int = 0
    models: int = 0
    cloud_instances: int = 0
    total: int = 0


class Credential(BaseModel):
    id: UUID
    name: str
    type: CredentialType
    provider: Optional[str] = None
    kind: Optional[str] = None
    status: CredentialStatus
    masked_value: Optional[str] = None
    fields: Dict[str, Any] = Field(default_factory=dict)
    secret_fields: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    system: bool = False
    used_by: CredentialUsageCounts = Field(default_factory=CredentialUsageCounts)
    default_for: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    base_url: Optional[str] = None
    expires_at: Optional[datetime] = None
    usage_count: int = 0
    last_used_at: Optional[datetime] = None
    access_restrictions: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class CredentialStats(BaseModel):
    total: int = 0
    active: int = 0
    expiring_soon: int = 0
    expired: int = 0
    by_kind: Dict[str, int] = Field(default_factory=dict)


class CredentialRevealed(BaseModel):
    id: UUID
    value: str
    fields: Dict[str, Any] = Field(default_factory=dict)


class CredentialCheck(BaseModel):
    name: str
    ok: bool
    message: str


class CredentialTestResult(BaseModel):
    id: UUID
    ok: bool
    kind: Optional[str] = None
    checks: List[CredentialCheck] = Field(default_factory=list)
    cloud_provider: Optional[str] = None
    cloud_check_path: Optional[str] = None
    tested_at: datetime


class CredentialReference(BaseModel):
    resource_type: str
    resource_id: UUID
    name: str
    field: str
    detail: Optional[str] = None


class CredentialUsageEntry(BaseModel):
    used_at: datetime
    purpose: Optional[str] = None
    used_by: Optional[UUID] = None
    used_by_username: Optional[str] = None


class CredentialUsage(BaseModel):
    credential_id: UUID
    used_by: CredentialUsageCounts
    references: List[CredentialReference] = Field(default_factory=list)
    default_for: List[str] = Field(default_factory=list)
    history: List[CredentialUsageEntry] = Field(default_factory=list)


class CredentialKindField(BaseModel):
    name: str
    label: str
    input: str
    secret: bool = False
    required: bool = False
    help: Optional[str] = None
    placeholder: Optional[str] = None
    default: Any = None
    pattern: Optional[str] = None
    pattern_hint: Optional[str] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    upload_accept: Optional[str] = None


class CredentialKindInfo(BaseModel):
    id: str
    label: str
    category: str
    credential_type: CredentialType
    description: str
    fields: List[CredentialKindField]
    secret_fields: List[str] = Field(default_factory=list)
    derived_fields: List[str] = Field(default_factory=list)
    json_schema: Dict[str, Any]
    docs_url: Optional[str] = None
    used_for: List[str] = Field(default_factory=list)
    consumers: List[str] = Field(default_factory=list)
    cloud_provider: Optional[str] = None
    test: str = "format"


class CredentialCategory(BaseModel):
    id: str
    label: str


class CredentialDefault(BaseModel):
    setting: str
    name: Optional[str] = None
    used_for: str
    kind: Optional[str] = None


class VaultMetadata(BaseModel):
    types: List[str] = Field(default_factory=list)
    providers: List[str] = Field(default_factory=list)
    statuses: List[str] = Field(default_factory=list)
    kinds: List[CredentialKindInfo] = Field(default_factory=list)
    categories: List[CredentialCategory] = Field(default_factory=list)
    pickers: Dict[str, List[str]] = Field(default_factory=dict)
    defaults: List[CredentialDefault] = Field(default_factory=list)
