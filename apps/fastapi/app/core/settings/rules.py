"""
Rules engine settings: evaluator tick, default timezone,
swap concurrency guard, rule_runs history volume and retention.

Env names follow libs/contracts/env.md section 9.
"""

from __future__ import annotations

from app.core.settings.base import DomainSettings
from pydantic import Field


class RulesSettings(DomainSettings):
    """Knobs for the rules evaluator loop in the jobs process."""

    RULES_TICK_SECONDS: int = Field(
        default=10, description="Evaluator tick: how often enabled rules are considered"
    )
    RULES_DEFAULT_TIMEZONE: str = Field(
        default="UTC",
        description="Default IANA zone for time_schedule rules and time windows",
    )
    RULES_MAX_CONCURRENT_SWAPS: int = Field(
        default=2,
        description="Cluster-wide cap on swap operations in a non-terminal state; rules that "
        "would exceed it record skipped(reason=swap_capacity)",
    )
    RULE_RUN_NO_MATCH_LOG_INTERVAL_SECONDS: int = Field(
        default=3600,
        description="A no_match rule_runs row is written at most this often per rule",
    )
    RULE_RUNS_RETENTION_DAYS: int = Field(
        default=30,
        description="rule_runs rows older than this are deleted by the retention job",
    )
    RULES_SCHEDULE_LOOKBACK_HOURS: int = Field(
        default=48,
        description="How far back a time_schedule rule looks for a missed run on start-up "
        "(catch_up); longer gaps are ignored",
    )
