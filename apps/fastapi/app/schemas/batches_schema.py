"""
Admin view of Anthropic message batches (admin.yaml: /batches, Metering tag).
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from uuid import UUID

from app.schemas.metering_schema import UsageFormat
from pydantic import BaseModel, ConfigDict


class BatchStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    CANCELING = "canceling"
    ENDED = "ended"


class Batch(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    external_id: str
    api_key_id: Optional[UUID] = None
    format: UsageFormat = UsageFormat.ANTHROPIC
    status: BatchStatus
    request_counts: Dict[str, Any]
    total_items: int
    ended_at: Optional[datetime] = None
    expires_at: datetime
    cancel_initiated_at: Optional[datetime] = None
    created_at: datetime
