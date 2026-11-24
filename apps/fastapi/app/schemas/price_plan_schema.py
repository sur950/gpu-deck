"""
Price plan schemas (admin.yaml: Price Plans tag).
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PricePlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2048)
    usd_per_gpu_minute: float = Field(0, ge=0)
    usd_per_1k_input_tokens: float = Field(0, ge=0)
    usd_per_1k_output_tokens: float = Field(0, ge=0)
    usd_per_1k_cached_tokens: float = Field(0, ge=0)
    is_default: bool = False


class PricePlanUpdate(BaseModel):
    """Partial update; every field optional (affects future usage only)."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2048)
    usd_per_gpu_minute: Optional[float] = Field(None, ge=0)
    usd_per_1k_input_tokens: Optional[float] = Field(None, ge=0)
    usd_per_1k_output_tokens: Optional[float] = Field(None, ge=0)
    usd_per_1k_cached_tokens: Optional[float] = Field(None, ge=0)
    is_default: Optional[bool] = None


class PricePlan(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: Optional[str] = None
    currency: str = Field(..., max_length=3)
    usd_per_gpu_minute: float
    usd_per_1k_input_tokens: float
    usd_per_1k_output_tokens: float
    usd_per_1k_cached_tokens: float
    is_default: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
