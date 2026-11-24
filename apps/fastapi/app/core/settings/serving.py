"""
Serving API (data plane) settings — libs/contracts/openapi/serving.md §9 and
libs/contracts/env.md §11.

Mount prefixes (``SERVING_*_PREFIX``) are declared by the skeleton in
``app.py``; ``app.services.serving.formats`` normalises them to the explicit
roots of serving.md §1 (``/openai``, ``/anthropic``, ``/ollama``) and the bare
compat paths are mounted by ``app/api_layer_main.py``.
"""

from __future__ import annotations

from typing import List, Optional

from app.core.settings.base import DomainSettings
from pydantic import Field


class ServingSettings(DomainSettings):
    """Routing, limits, upstream mTLS and local-dev knobs of the API layer."""

    SERVING_ENABLE_BARE_PATHS: bool = Field(
        default=True, description="Also serve /v1/* and /api/* without a format prefix"
    )
    SERVING_LIST_UNLOADED_MODELS: bool = Field(
        default=False, description="List models with a deployment but no ready instance"
    )
    SERVING_ON_DEMAND_LOAD: bool = Field(
        default=False,
        description="Ask the control plane to start a stopped deployment when a request "
        "arrives for it and wait SERVING_NO_INSTANCE_WAIT_SECONDS",
    )
    SERVING_NO_INSTANCE_WAIT_SECONDS: int = Field(
        default=30, description="Wait for a loading instance before 503 model_not_ready"
    )
    SERVING_EJECT_SECONDS: int = Field(
        default=10,
        description="Skip an instance after a connect/TLS failure for this long",
    )
    SERVING_REWRITE_RESPONSE_MODEL: bool = Field(
        default=True,
        description="Rewrite the response model field back to the requested name",
    )
    SERVING_EXPOSE_ROUTING_HEADERS: bool = Field(
        default=True, description="Add x-gpudeck-model/deployment/instance/node headers"
    )
    SERVING_MAX_JSON_BODY_BYTES: int = Field(
        default=16 * 1024 * 1024, description="Cap for JSON request bodies (413 beyond)"
    )
    SERVING_MAX_UPLOAD_BYTES: int = Field(
        default=256 * 1024 * 1024, description="Cap for multipart uploads (413 beyond)"
    )
    SERVING_MAX_INPUT_ITEMS: int = Field(
        default=2048, description="Max embeddings input items / rerank documents"
    )
    SERVING_REQUEST_TIMEOUT_SECONDS: int = Field(
        default=600, description="Total time limit per request (non-video)"
    )
    SERVING_STREAM_IDLE_TIMEOUT_SECONDS: int = Field(
        default=120, description="Max gap between upstream stream frames"
    )
    SERVING_CONNECT_TIMEOUT_SECONDS: float = Field(
        default=5.0, description="TCP/TLS connect timeout to the node-agent proxy"
    )
    SERVING_VIDEO_JOB_TIMEOUT_SECONDS: int = Field(
        default=3600, description="Timeout for async video generation jobs"
    )
    SERVING_MAX_CONCURRENT_REQUESTS: int = Field(
        default=1024,
        description="In-flight cap per API-layer process (503 instance_busy)",
    )
    SERVING_BATCH_MAX_ITEMS: int = Field(default=10000, description="Max requests per batch")
    SERVING_BATCH_MAX_BYTES: int = Field(
        default=256 * 1024 * 1024, description="Max batch request body size"
    )
    SERVING_BATCH_EXPIRES_HOURS: int = Field(default=24, description="Batch lifetime")
    SERVING_BATCH_RESULTS_RETENTION_DAYS: int = Field(
        default=29, description="Days batch results stay retrievable"
    )
    SERVING_BATCH_CONCURRENCY: int = Field(
        default=4, description="Parallel items per batch in the jobs process"
    )
    SERVING_ANTHROPIC_DEFAULT_VERSION: str = Field(
        default="2023-06-01",
        description="anthropic-version echoed when the client sends none",
    )
    SERVING_COMPACTION_MODEL_OVERRIDE: Optional[str] = Field(
        None,
        description="Model used for API-layer compaction instead of the request's model",
    )
    SERVING_COMPACTION_MAX_TOKENS: int = Field(
        default=2048,
        description="max_tokens of the summarisation call the API layer runs",
    )
    ROUTING_TABLE_POLL_SECONDS: int = Field(
        default=5,
        description="Fallback poll of the Redis routing table when pub/sub is silent",
    )
    SERVING_INFLIGHT_FLUSH_MS: int = Field(
        default=250,
        description="How often per-instance in-flight counts are flushed to Redis",
    )
    SERVING_HEALTH_REQUIRE_NODES: bool = Field(
        default=False,
        description="/health answers 503 unless the routing table lists >= 1 online node "
        "(serving.md); off by default so an empty dev stack is healthy",
    )

    # mTLS client identity towards the node-agent proxies (:9443)
    API_LAYER_CLIENT_CERT_PATH: Optional[str] = Field(
        None, description="Client certificate (PEM) presented to node-agent proxies"
    )
    API_LAYER_CLIENT_KEY_PATH: Optional[str] = Field(
        None, description="Private key (PEM) of the client certificate"
    )
    API_LAYER_CA_BUNDLE_PATH: Optional[str] = Field(
        None,
        description="CA bundle override (otherwise the routing table's ca_bundle_pem)",
    )
    SERVING_UPSTREAM_VERIFY_TLS: bool = Field(
        default=True,
        description="Verify node-agent proxy certificates (dev only: false)",
    )
    SERVING_UPSTREAM_KEEPALIVE_SECONDS: float = Field(
        default=300.0,
        description="Idle time a pooled connection to a node-agent proxy is kept for reuse "
        "(a new one costs TCP + TLS round trips)",
    )
    SERVING_UPSTREAM_HTTP2: bool = Field(
        default=True,
        description="Offer HTTP/2 (ALPN h2) to node-agent proxies; one connection multiplexes "
        "concurrent requests. HTTP/1.1 is used when a proxy does not negotiate h2",
    )
    SERVING_UPSTREAM_MAX_CONNECTIONS_PER_NODE: int = Field(
        default=512, description="Connection cap of the pool towards one node-agent proxy"
    )
    SERVING_UPSTREAM_MAX_KEEPALIVE_PER_NODE: int = Field(
        default=128, description="Idle connections kept per node-agent proxy"
    )
    SERVING_UPSTREAM_PREWARM: bool = Field(
        default=True,
        description="Open connections to a node proxy as soon as it serves an instance and "
        "keep idle pools warm",
    )
    SERVING_UPSTREAM_WARM_CONNECTIONS: int = Field(
        default=2, description="Connections opened per node proxy when it is first routed"
    )
    SERVING_UPSTREAM_KEEPWARM_SECONDS: int = Field(
        default=20,
        description="Interval of the GET /_health ping that keeps each routed node's pool "
        "warm (0 = off; keep it below the agent's 30 s HTTP/2 keep-alive ping)",
    )

    # Local-dev direct engine mode (no node agent): route to an engine on localhost.
    SERVING_DEV_ENGINE_URL: Optional[str] = Field(
        None,
        description="Direct engine base URL for local testing (e.g. http://127.0.0.1:11434); "
        "requests go to {url}/{engine_path} without a node-agent proxy",
    )
    SERVING_DEV_ENGINE_MODELS: str = Field(
        default="*",
        description="Comma-separated serving names the dev engine answers to ('*' = any)",
    )
    SERVING_DEV_ENGINE_FORMATS: str = Field(
        default="openai",
        description="Formats the dev engine speaks natively (openai,ollama)",
    )
    SERVING_DEV_ENGINE_FAMILY: Optional[str] = Field(
        None, description="Model family hint for the dev engine (thinking tag handling)"
    )
    SERVING_DEV_ENGINE_TYPE: str = Field(
        default="chat", description="Model type of the dev engine model(s)"
    )

    def dev_engine_models(self) -> List[str]:
        return [m.strip() for m in self.SERVING_DEV_ENGINE_MODELS.split(",") if m.strip()]

    def dev_engine_formats(self) -> List[str]:
        return [f.strip() for f in self.SERVING_DEV_ENGINE_FORMATS.split(",") if f.strip()]
