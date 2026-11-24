"""
Application-wide enums. Domain enums (deployment status, rule types, ...)
live next to their domain's schemas; only cross-cutting ones belong here.
"""

from enum import Enum


class HealthStatus(str, Enum):
    """Health check status."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEGRADED = "degraded"


class Environment(str, Enum):
    """Application environments."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


class ProcessName(str, Enum):
    """The processes built from this codebase (see app/core/lifecycle.py)."""

    ADMIN = "admin"
    SERVING = "serving"
    WORKER = "worker"
    SCHEDULER = "scheduler"


class ServingFormat(str, Enum):
    """API formats spoken by the serving API."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"


class TaskStatus(str, Enum):
    """Celery task status."""

    PENDING = "pending"
    STARTED = "started"
    SUCCESS = "success"
    FAILURE = "failure"
    RETRY = "retry"
    REVOKED = "revoked"
