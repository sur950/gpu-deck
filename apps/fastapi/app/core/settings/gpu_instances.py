"""
SSH GPU instance and agent download settings.

``GPU_INSTANCE_*``: defaults and limits of SSH GPU instances
(``app/services/gpu_instances``). ``AGENT_DIST_DIR`` / ``AGENT_DOWNLOAD_BASE_URL``:
where node-agent release builds are served from (``app/services/nodes/agent_downloads.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from app.core.settings.base import DomainSettings
from pydantic import Field

# apps/node-agent/dist/agent (written by apps/node-agent/packaging/build-release.sh)
_REPO_DIST = Path(__file__).resolve().parents[4] / "node-agent" / "dist" / "agent"


class GpuInstanceSettings(DomainSettings):
    """SSH GPU instance defaults and limits."""

    GPU_INSTANCE_DEFAULT_IMAGE: str = Field(
        default="ghcr.io/sur950/gpu-deck-ssh:1",
        description=(
            "Image used when a request names none (must run sshd, protocol.md §8). Default: "
            "Ubuntu + OpenSSH (apps/node-agent/packaging/ssh-image), glibc so the NVIDIA "
            "runtime's nvidia-smi / libcuda run inside (Alpine images cannot)"
        ),
    )
    GPU_INSTANCE_DEFAULT_CONTAINER_PORT: int = Field(
        default=2222,
        description="sshd port inside the default image (gpu-deck-ssh and linuxserver: 2222)",
    )
    GPU_INSTANCE_ALLOWED_IMAGES: str = Field(
        default="",
        description="Comma list of allowed image prefixes (empty = any image)",
    )
    GPU_INSTANCE_DEFAULT_TTL_SECONDS: int = Field(
        default=8 * 3600,
        description="TTL applied when a request gives none (0 = no TTL)",
    )
    GPU_INSTANCE_MAX_TTL_SECONDS: int = Field(
        default=7 * 86400, description="Upper bound for ttl_seconds / extensions"
    )
    GPU_INSTANCE_MAX_PER_NODE: int = Field(
        default=16, description="Running SSH GPU instances allowed per node"
    )
    GPU_INSTANCE_START_TIMEOUT_SECONDS: int = Field(
        default=900,
        description="start_ssh_instance command timeout (image pull + boot)",
    )
    GPU_INSTANCE_STOP_TIMEOUT_SECONDS: int = Field(
        default=120, description="stop_ssh_instance command timeout"
    )
    GPU_INSTANCE_TICK_SECONDS: int = Field(
        default=15,
        description="Lifecycle tick (placement retries, TTL, idle, metering)",
    )
    GPU_INSTANCE_METER_INTERVAL_MINUTES: int = Field(
        default=60, description="Write a GPU-minute usage event at least this often"
    )
    GPU_INSTANCE_NOT_REPORTED_GRACE_SECONDS: int = Field(
        default=120,
        description="Running instance missing from the agent inventory this long -> error",
    )

    def agent_dist_dir(self) -> Path:
        return Path(self.AGENT_DIST_DIR).expanduser() if self.AGENT_DIST_DIR else _REPO_DIST

    def allowed_image_prefixes(self) -> List[str]:
        return [p.strip() for p in self.GPU_INSTANCE_ALLOWED_IMAGES.split(",") if p.strip()]


class AgentDownloadSettings(DomainSettings):
    """Where node-agent builds come from and which URL install scripts use."""

    AGENT_DIST_DIR: str = Field(
        default="",
        description="Local agent builds, checked first (offline / air-gapped override): "
        "<version>/<target>/ folders or flat release files "
        "gpudeck-agent-<version>-<target>.tar.gz + SHA256SUMS (empty = "
        "apps/node-agent/dist/agent in the repo)",
    )
    AGENT_RELEASE_URL: str = Field(
        default="https://github.com/sur950/gpu-deck/releases/download",
        description="Release download base: builds not found locally are fetched from "
        "<url>/v<version>/gpudeck-agent-<version>-<target>.tar.gz, verified against the "
        "release SHA256SUMS.txt and cached in AGENT_CACHE_DIR (empty = never fetch)",
    )
    AGENT_CACHE_DIR: str = Field(
        default="",
        description="Writable directory for agent builds fetched from AGENT_RELEASE_URL "
        "(empty = <tmp>/gpu-deck-agent-cache)",
    )
    AGENT_DOWNLOAD_BASE_URL: str = Field(
        default="",
        description="URL prefix GPU machines fetch install.sh and downloads/ from "
        "(empty = AGENT_HUB_PUBLIC_URL + /agent/v1, the hub; a value ending in /api/v1 "
        "means the admin API's copies)",
    )
