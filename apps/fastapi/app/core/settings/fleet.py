"""
Fleet settings: agent hub listener, PKI, join tokens, node liveness,
command timeouts and the default egress allowlist pushed to agents.

Field names match ``libs/contracts/env.md`` §7 and §8. TLS file paths for the
hub (``AGENT_HUB_TLS_CERT_FILE`` / ``_KEY_FILE`` / ``AGENT_HUB_CLIENT_CA_FILE``,
``PKI_CA_DIR``) live in ``security.py`` and are read from there.
"""

from __future__ import annotations

from typing import List, Optional

from app.core.settings.base import DomainSettings
from pydantic import Field


class FleetSettings(DomainSettings):
    """Agent hub, PKI and node lifecycle knobs."""

    # Hub listener (runs as its own process: python -m app.agent_hub_main)
    AGENT_HUB_HOST: str = Field(default="0.0.0.0", description="Bind host of the agent hub")
    AGENT_HUB_PUBLIC_URL: str = Field(
        default="https://localhost:8443",
        description="URL agents dial (embedded in join commands and hello_ack)",
    )
    AGENT_HUB_TLS_SANS: str = Field(
        default="localhost,127.0.0.1",
        description="Extra DNS names / IPs for the self-issued hub server certificate",
    )
    HUB_MAX_FRAME_BYTES: int = Field(
        default=4 * 1024 * 1024,
        description="Maximum WebSocket frame accepted from an agent",
    )
    HUB_VALIDATE_SCHEMAS: bool = Field(
        default=True,
        description="Validate every inbound frame against libs/contracts/agent-protocol JSON "
        "Schemas (close 4002 on violation)",
    )
    HUB_PING_INTERVAL_SECONDS: int = Field(default=15, description="WebSocket ping cadence")

    # PKI
    PKI_CA_CERT_PATH: Optional[str] = Field(
        None, description="Import an existing CA certificate on first boot (read once)"
    )
    PKI_CA_KEY_PATH: Optional[str] = Field(
        None, description="Import the matching CA private key on first boot (read once)"
    )
    PKI_CA_KEY_TYPE: str = Field(
        default="ecdsa_p384",
        description="Key type of a generated CA: ecdsa_p384 | ecdsa_p256",
    )
    PKI_CA_VALIDITY_DAYS: int = Field(default=3650, description="Generated CA validity")
    NODE_CERT_VALIDITY_DAYS: int = Field(default=365, description="Node agent cert validity")
    NODE_CERT_ROTATE_BEFORE_DAYS: int = Field(
        default=30, description="Auto rotate_cert this many days before not_after"
    )
    NODE_CERT_OLD_REVOKE_AFTER_SECONDS: int = Field(
        default=600,
        description="Revoke the previous cert this long after the new one connects",
    )
    SERVICE_CERT_VALIDITY_DAYS: int = Field(
        default=365, description="api_layer / control_plane / agent_hub cert validity"
    )
    CONTROL_PLANE_CLIENT_CERT_PATH: Optional[str] = Field(
        None,
        description="Client cert for control-plane -> node proxy probes (empty = vault)",
    )
    CONTROL_PLANE_CLIENT_KEY_PATH: Optional[str] = Field(
        None,
        description="Client key for control-plane -> node proxy probes (empty = vault)",
    )

    # Join
    JOIN_TOKEN_TTL_MINUTES: int = Field(default=15, description="Default join token TTL")
    JOIN_RATE_LIMIT: str = Field(default="10/minute", description="Rate limit on /agent/v1/join")

    # Liveness and intervals sent to agents
    NODE_OFFLINE_AFTER_SECONDS: int = Field(default=30, description="No heartbeat -> offline")
    HEARTBEAT_INTERVAL_SECONDS: int = Field(default=10, description="Agent heartbeat cadence")
    METRICS_INTERVAL_SECONDS: float = Field(default=2.0, description="Agent scrape cadence")
    INVENTORY_INTERVAL_SECONDS: int = Field(default=60, description="Agent inventory cadence")
    COMMAND_ACK_TIMEOUT_SECONDS: int = Field(default=10, description="Ack deadline per command")
    COMMAND_DEFAULT_TIMEOUT_SECONDS: int = Field(
        default=900, description="Default command timeout when the caller gives none"
    )
    COMMAND_MIN_AGENT_VERSIONS: str = Field(
        default="",
        description="Comma list command=semver of the minimum agent version per command "
        "(e.g. run_benchmark=1.1.0); unsupported commands are rejected before sending",
    )

    # Egress
    EGRESS_ALLOWLIST_DEFAULT: str = Field(
        default=(
            "huggingface.co:443,cdn-lfs.huggingface.co:443,cdn-lfs-us-1.huggingface.co:443,"
            "cas-bridge.xethub.hf.co:443,modelscope.cn:443,www.modelscope.cn:443"
        ),
        description="Base egress allowlist pushed to every node (plus the hub and cluster extras)",
    )

    def get_hub_sans(self) -> List[str]:
        return [s.strip() for s in self.AGENT_HUB_TLS_SANS.split(",") if s.strip()]

    def get_egress_allowlist_default(self) -> List[str]:
        return [s.strip() for s in self.EGRESS_ALLOWLIST_DEFAULT.split(",") if s.strip()]

    def get_command_min_agent_versions(self) -> dict:
        out = {}
        for item in self.COMMAND_MIN_AGENT_VERSIONS.split(","):
            if "=" in item:
                key, value = item.split("=", 1)
                out[key.strip()] = value.strip()
        return out

    @property
    def hub_ws_url(self) -> str:
        base = self.AGENT_HUB_PUBLIC_URL.rstrip("/")
        if base.startswith("https://"):
            base = "wss://" + base[len("https://") :]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://") :]
        return f"{base}/agent/v1/ws"
