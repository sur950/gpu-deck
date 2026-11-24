"""
Billing schemas (admin.yaml: Billing tag — reports, estimate, CSV export).
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.schemas.metering_schema import UsageSummaryRow, UsageTotals
from pydantic import BaseModel, ConfigDict, Field, model_validator


class BillingReportStatus(str, Enum):
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class BillingGroupBy(str, Enum):
    API_KEY = "api_key"
    MODEL = "model"
    NODE = "node"
    CLUSTER = "cluster"
    DEPLOYMENT = "deployment"


class BillingReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    period_start: datetime
    period_end: datetime
    group_by: List[BillingGroupBy] = Field(
        default_factory=lambda: [BillingGroupBy.API_KEY, BillingGroupBy.MODEL]
    )
    filters: Dict[str, Any] = Field(
        default_factory=dict,
        description="{api_key_ids[], model_ids[], node_ids[], cluster_ids[]}",
    )

    @model_validator(mode="after")
    def _period(self):
        if self.period_end <= self.period_start:
            raise ValueError("period_end must be after period_start")
        allowed = {
            "api_key_ids",
            "model_ids",
            "node_ids",
            "cluster_ids",
            "deployment_ids",
        }
        unknown = set(self.filters) - allowed
        if unknown:
            raise ValueError(f"unknown filters: {sorted(unknown)}")
        return self


class CostBreakdown(BaseModel):
    gpu_time_usd: float = 0
    input_tokens_usd: float = 0
    output_tokens_usd: float = 0
    cached_tokens_usd: float = 0


class BillingLineItem(BaseModel):
    group: Dict[str, str]
    labels: Dict[str, str] = Field(default_factory=dict)
    price_plan_id: Optional[UUID] = None
    price_plan_name: Optional[str] = None
    totals: UsageTotals
    cost_breakdown: CostBreakdown = Field(default_factory=CostBreakdown)


class BillingReport(BaseModel):
    id: UUID
    name: str
    period_start: datetime
    period_end: datetime
    group_by: List[str]
    filters: Dict[str, Any] = Field(default_factory=dict)
    status: BillingReportStatus
    currency: str = "USD"
    totals: Optional[UsageTotals] = None
    line_items: List[BillingLineItem] = Field(default_factory=list)
    generated_at: Optional[datetime] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class BillingEstimate(BaseModel):
    period_start: datetime
    period_end: datetime
    as_of: datetime
    totals: UsageTotals
    projected_cost_usd: float
    by_api_key: List[UsageSummaryRow] = Field(default_factory=list)
    by_model: List[UsageSummaryRow] = Field(default_factory=list)
    by_node: List[UsageSummaryRow] = Field(default_factory=list)
