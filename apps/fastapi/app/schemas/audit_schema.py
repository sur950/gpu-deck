"""
Audit log schemas (admin.yaml: Audit tag).
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class AuditActorType(str, Enum):
    ADMIN = "admin"
    API_KEY = "api_key"
    SYSTEM = "system"
    RULE = "rule"
    NODE_AGENT = "node_agent"


class AuditOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"


class AuditLog(BaseModel):
    id: UUID
    event_type: str = Field(..., max_length=100)
    actor_type: AuditActorType
    actor_id: Optional[UUID] = None
    actor_name: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[UUID] = None
    resource_name: Optional[str] = None
    before_state: Optional[Dict[str, Any]] = None
    after_state: Optional[Dict[str, Any]] = None
    outcome: AuditOutcome
    message: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    request_id: Optional[UUID] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
