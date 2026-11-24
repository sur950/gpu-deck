"""
Audit log settings.
"""

from __future__ import annotations

from app.core.settings.base import DomainSettings
from pydantic import Field


class AuditSettings(DomainSettings):
    """Retention and export limits for audit_logs."""

    AUDIT_RETENTION_DAYS: int = Field(
        default=365, description="Delete audit rows older than this (0 = keep forever)"
    )
    AUDIT_EXPORT_MAX_ROWS: int = Field(
        default=50000, description="Upper bound of rows in one CSV export"
    )
    AUDIT_QUEUE: str = Field(default="audit", description="Celery queue for audit writes")
