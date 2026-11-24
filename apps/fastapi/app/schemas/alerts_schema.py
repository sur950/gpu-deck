"""
Pydantic schemas for alert channels and events (mirror of admin.yaml:
AlertChannelCreate, AlertChannelUpdate, AlertChannel, AlertTestResult,
AlertDelivery, AlertEvent).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from app.core.logger import get_logger
from pydantic import BaseModel, ConfigDict, Field

logger = get_logger(__name__)


class AlertChannelType(str, Enum):
    SLACK = "slack"
    EMAIL = "email"
    WEBHOOK = "webhook"


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


SEVERITY_RANK = {
    AlertSeverity.INFO: 0,
    AlertSeverity.WARNING: 1,
    AlertSeverity.CRITICAL: 2,
}


class AlertEventStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class AlertDeliveryStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


class AlertChannelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    type: AlertChannelType
    config: Dict[str, Any] = Field(default_factory=dict)
    credential_id: Optional[uuid.UUID] = None
    secret: Optional[str] = Field(default=None, description="Inline vault secret (write-only)")
    enabled: bool = True
    min_severity: Optional[AlertSeverity] = None
    event_filter: List[str] = Field(default_factory=list)


class AlertChannelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    config: Optional[Dict[str, Any]] = None
    credential_id: Optional[uuid.UUID] = None
    secret: Optional[str] = None
    enabled: Optional[bool] = None
    min_severity: Optional[AlertSeverity] = None
    event_filter: Optional[List[str]] = None


class AlertChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    type: AlertChannelType
    config: Dict[str, Any]
    credential_id: Optional[uuid.UUID] = None
    enabled: bool
    min_severity: AlertSeverity
    event_filter: List[str] = Field(default_factory=list)
    last_test_at: Optional[datetime] = None
    last_test_ok: Optional[bool] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AlertTestResult(BaseModel):
    ok: bool
    sent_at: datetime
    response_code: Optional[int] = None
    error: Optional[str] = None
    latency_ms: Optional[int] = None


class AlertDeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    channel_id: uuid.UUID
    channel_name: Optional[str] = None
    status: AlertDeliveryStatus
    attempts: int
    sent_at: Optional[datetime] = None
    response_code: Optional[int] = None
    error_message: Optional[str] = None


class AlertEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    severity: AlertSeverity
    event_type: str
    title: str
    message: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[uuid.UUID] = None
    resource_name: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    dedupe_key: Optional[str] = None
    status: AlertEventStatus
    occurrences: int
    first_seen_at: datetime
    last_seen_at: datetime
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    deliveries: List[AlertDeliveryOut] = Field(default_factory=list)
    created_at: Optional[datetime] = None
