"""
Common schemas used across the application.
"""

from datetime import datetime
from typing import Any, Dict, Generic, List, Optional, TypeVar

from app.utils.enums import HealthStatus
from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    """Detailed error information."""

    code: str
    message: str
    details: Optional[Any] = None
    field: Optional[str] = None


class PaginationParams(BaseModel):
    """Pagination parameters."""

    page: int = Field(default=1, ge=1, description="Page number")
    page_size: int = Field(default=20, ge=1, le=1000, description="Items per page")


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response."""

    items: List[T]
    total: int
    page: int
    page_size: int
    has_more: bool

    @classmethod
    def create(cls, items: List[T], total: int, page: int, page_size: int):
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            has_more=(page * page_size) < total,
        )


class ServiceHealth(BaseModel):
    """Individual service health."""

    status: HealthStatus
    response_time_ms: Optional[float] = None
    error: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class HealthCheckResponse(BaseModel):
    """Health check response."""

    status: HealthStatus
    version: str
    process: str
    timestamp: datetime
    checks: Dict[str, Any]


class TaskResponse(BaseModel):
    """Background task response."""

    task_id: str
    status: str
    message: str
