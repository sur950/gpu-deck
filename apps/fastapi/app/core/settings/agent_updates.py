"""
Agent update settings (agent protocol §14): the release signing keys the control plane and the
nodes trust, the version nodes are moved to, background pre-staging and the health check.

Field names match ``libs/contracts/env.md`` §8.
"""

from __future__ import annotations

import base64
from typing import List

from app.core.settings.base import DomainSettings
from pydantic import Field


class AgentUpdateSettings(DomainSettings):
    """Node agent self-update."""

    AGENT_RELEASE_PUBLIC_KEYS: str = Field(
        default="",
        description="Comma list of release signing public keys (base64 of the 32-byte ed25519 "
        "key) agent release manifests must be signed with. Sent to every node in its signed "
        "security policy. Empty = agent updates are off (nodes are updated by re-running the "
        "installer)",
    )
    AGENT_UPDATE_TARGET_VERSION: str = Field(
        default="",
        description="Agent version nodes are moved to (empty = the newest signed release "
        "available: AGENT_DIST_DIR / AGENT_CACHE_DIR, else this control plane's own version from "
        "AGENT_RELEASE_URL)",
    )
    AGENT_UPDATE_PRESTAGE: bool = Field(
        default=True,
        description="Download the target version to every node that can update itself in the "
        "background, ahead of any update (the update itself downloads nothing)",
    )
    AGENT_UPDATE_STAGE_CONCURRENCY: int = Field(
        default=4,
        ge=1,
        le=256,
        description="Nodes downloading a staged update at the same time",
    )
    AGENT_UPDATE_HEALTH_TIMEOUT_SECONDS: int = Field(
        default=120,
        ge=30,
        le=900,
        description="The updated agent must reconnect and report healthy within this, or the "
        "node rolls back to the previous binary",
    )
    AGENT_UPDATE_TICK_SECONDS: int = Field(
        default=10,
        ge=2,
        le=300,
        description="How often the scheduler advances updates, rollouts and maintenance windows",
    )
    AGENT_UPDATE_TOKEN_TTL_SECONDS: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="Lifetime of the per-node download token sent with stage_update",
    )

    def get_release_public_keys(self) -> List[str]:
        """Valid keys only (base64 of exactly 32 bytes), in order, without duplicates."""
        out: List[str] = []
        for raw in (self.AGENT_RELEASE_PUBLIC_KEYS or "").replace("\n", ",").split(","):
            key = raw.strip()
            if not key or key in out:
                continue
            try:
                if len(base64.b64decode(key, validate=True)) == 32:
                    out.append(key)
            except ValueError:
                continue
        return out
