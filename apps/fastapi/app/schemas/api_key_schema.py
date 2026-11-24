"""
API key schemas (admin.yaml: API Keys tag).
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ApiKeyStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    SUSPENDED = "suspended"


class ApiKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2048)
    expires_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ApiKeyUpdate(BaseModel):
    """Partial update; every field optional."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2048)
    expires_at: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None


class ApiKey(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    key_prefix: str = Field(..., max_length=20)
    status: ApiKeyStatus
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    total_requests: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)
    revoked_at: Optional[datetime] = None
    last_rotated_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ApiKeyCreated(ApiKey):
    key: str = Field(..., description="Full plaintext key. Shown exactly once.")


class ApiKeyStats(BaseModel):
    total: int = 0
    active: int = 0
    expired: int = 0
    revoked: int = 0
    suspended: int = 0
