"""
Benchmark settings: where the load generator points and how it authenticates.
"""

from __future__ import annotations

from typing import Optional

from app.core.settings.base import DomainSettings
from pydantic import Field


class BenchmarkSettings(DomainSettings):
    """Load-test runner configuration (runs in the jobs process)."""

    BENCHMARK_TARGET_URL: Optional[str] = Field(
        None,
        description="Base URL of the serving API the runner calls; empty = PUBLIC_SERVING_URL",
    )
    BENCHMARK_API_KEY_CREDENTIAL: str = Field(
        default="benchmark-api-key",
        description="Vault credential name holding the API key the runner sends",
    )
    BENCHMARK_API_KEY: Optional[str] = Field(
        None,
        description="Plain API key for the runner (dev only; the vault credential wins)",
    )
    BENCHMARK_REQUEST_TIMEOUT_SECONDS: float = Field(
        default=300.0, description="Per-request timeout during a load test"
    )
    BENCHMARK_MAX_CONCURRENCY: int = Field(
        default=512, description="Upper bound for any concurrency level"
    )
    BENCHMARK_VERIFY_TLS: bool = Field(
        default=True,
        description="Verify the serving API certificate (off for self-signed dev certs)",
    )
    BENCHMARK_STALE_RUN_MINUTES: int = Field(
        default=180,
        description="Running benchmark runs without progress this long are marked failed",
    )
    BENCHMARK_READY_WAIT_SECONDS: int = Field(
        default=900,
        description="Auto-tune waits this long for a restarted instance to be ready",
    )
