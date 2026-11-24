import datetime
import decimal
import uuid
from typing import Optional

from sqlalchemy import (
    CHAR,
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CIDR, INET, INT4RANGE, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AdminUsers(Base):
    __tablename__ = "admin_users"
    __table_args__ = (
        CheckConstraint("failed_login_attempts >= 0", name="admin_users_failed_attempts_nonneg"),
        CheckConstraint("singleton", name="admin_users_singleton_true"),
        PrimaryKeyConstraint("id", name="admin_users_pkey"),
        UniqueConstraint("singleton", name="admin_users_single_row"),
        UniqueConstraint("username", name="admin_users_username_unique"),
        {
            "comment": "The single admin account seeded from "
            "ADMIN_USERNAME/ADMIN_PASSWORD in .env. Exactly one row."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    singleton: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="Argon2id PHC string ($argon2id$...). Never bcrypt."
    )
    password_changed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    env_secret_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="SHA-256 hex of the ADMIN_PASSWORD value last applied from .env. On boot: if sha256(env ADMIN_PASSWORD) != env_secret_hash, re-hash the env password into password_hash, update this column and env_secret_applied_at; otherwise leave a UI-set password alone. username is always synced from ADMIN_USERNAME.",
    )
    env_secret_applied_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    locked_until: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        comment="Lockout until this time after AUTH_LOCKOUT_ATTEMPTS consecutive failures (Redis counter is the hot path; this column is the durable mirror).",
    )
    last_login_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    last_login_ip: Mapped[Optional[object]] = mapped_column(INET)

    api_keys: Mapped[list["ApiKeys"]] = relationship("ApiKeys", back_populates="admin_users")
    billing_reports: Mapped[list["BillingReports"]] = relationship(
        "BillingReports", back_populates="admin_users"
    )
    credentials: Mapped[list["Credentials"]] = relationship(
        "Credentials", back_populates="admin_users"
    )
    ip_rules: Mapped[list["IpRules"]] = relationship("IpRules", back_populates="admin_users")
    layouts: Mapped[list["Layouts"]] = relationship("Layouts", back_populates="admin_users")
    models: Mapped[list["Models"]] = relationship("Models", back_populates="admin_users")
    price_plans: Mapped[list["PricePlans"]] = relationship(
        "PricePlans", back_populates="admin_users"
    )
    rules: Mapped[list["Rules"]] = relationship("Rules", back_populates="admin_users")
    sessions: Mapped[list["Sessions"]] = relationship("Sessions", back_populates="admin_user")
    alert_channels: Mapped[list["AlertChannels"]] = relationship(
        "AlertChannels", back_populates="admin_users"
    )
    clusters: Mapped[list["Clusters"]] = relationship("Clusters", back_populates="admin_users")
    credential_usage_history: Mapped[list["CredentialUsageHistory"]] = relationship(
        "CredentialUsageHistory", back_populates="admin_users"
    )
    deployments: Mapped[list["Deployments"]] = relationship(
        "Deployments", back_populates="admin_users"
    )
    benchmarks: Mapped[list["Benchmarks"]] = relationship(
        "Benchmarks", back_populates="admin_users"
    )
    join_tokens: Mapped[list["JoinTokens"]] = relationship(
        "JoinTokens", back_populates="admin_users"
    )
    benchmark_runs: Mapped[list["BenchmarkRuns"]] = relationship(
        "BenchmarkRuns", back_populates="admin_users"
    )
    cloud_instances: Mapped[list["CloudInstances"]] = relationship(
        "CloudInstances", back_populates="admin_users"
    )
    swap_operations: Mapped[list["SwapOperations"]] = relationship(
        "SwapOperations", back_populates="admin_users"
    )


class AlertEvents(Base):
    __tablename__ = "alert_events"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="alert_events_pkey"),
        Index(
            "idx_alert_events_dedupe",
            "dedupe_key",
            "last_seen_at",
            postgresql_where="(dedupe_key IS NOT NULL)",
        ),
        Index("idx_alert_events_event_type", "event_type", "last_seen_at"),
        Index("idx_alert_events_resource", "resource_type", "resource_id"),
        Index("idx_alert_events_status_seen", "status", "last_seen_at"),
        {
            "comment": "Things worth telling a human: rule actions, swap failures, node "
            "offline, device unhealthy. Repeated events with the same "
            "dedupe_key within ALERT_DEDUPE_WINDOW_SECONDS bump occurrences "
            "instead of creating rows."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    severity: Mapped[str] = mapped_column(
        Enum("info", "warning", "critical", name="alert_severity"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    payload: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        Enum("open", "acknowledged", "resolved", name="alert_event_status"),
        nullable=False,
        server_default=text("'open'::alert_event_status"),
    )
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    first_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    last_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    message: Mapped[Optional[str]] = mapped_column(Text)
    resource_type: Mapped[Optional[str]] = mapped_column(String(100))
    resource_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    resource_name: Mapped[Optional[str]] = mapped_column(String(255))
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(255))
    acknowledged_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    resolved_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    alert_deliveries: Mapped[list["AlertDeliveries"]] = relationship(
        "AlertDeliveries", back_populates="alert_event"
    )


class AuditLogs(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="audit_logs_pkey"),
        Index("idx_audit_logs_actor", "actor_type", "actor_id", "created_at"),
        Index("idx_audit_logs_created_at", "created_at"),
        Index("idx_audit_logs_event_type", "event_type", "created_at"),
        Index("idx_audit_logs_request_id", "request_id"),
        Index("idx_audit_logs_resource", "resource_type", "resource_id", "created_at"),
        {
            "comment": "Every login, key change, rule action, swap, load/unload, node "
            "join/remove, cert revoke, with time and source IP. event_type is "
            "dotted: auth.login, api_key.created, rule.fired, "
            "instance.unloaded, node.cert_revoked, ..."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_type: Mapped[str] = mapped_column(
        Enum("admin", "api_key", "system", "rule", "node_agent", name="audit_actor_type"),
        nullable=False,
    )
    outcome: Mapped[str] = mapped_column(
        Enum("success", "failure", name="audit_outcome"),
        nullable=False,
        server_default=text("'success'::audit_outcome"),
    )
    metadata_: Mapped[object] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    actor_name: Mapped[Optional[str]] = mapped_column(String(255))
    resource_type: Mapped[Optional[str]] = mapped_column(String(100))
    resource_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    resource_name: Mapped[Optional[str]] = mapped_column(String(255))
    before_state: Mapped[Optional[object]] = mapped_column(JSONB)
    after_state: Mapped[Optional[object]] = mapped_column(JSONB)
    message: Mapped[Optional[str]] = mapped_column(String(2048))
    ip_address: Mapped[Optional[object]] = mapped_column(INET)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512))
    request_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)


class Engines(Base):
    __tablename__ = "engines"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="engines_pkey"),
        Index(
            "idx_engines_name_unique", "name", postgresql_where="(deleted_at IS NULL)", unique=True
        ),
        {
            "comment": "Engine registry. One row per engine family; the custom slot "
            "(name=custom) is a container image plus start/health/metrics "
            "config on its engine_versions row."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(
        Enum(
            "llama_cpp",
            "vllm",
            "vllm_omni",
            "sglang",
            "sglang_diffusion",
            "tensorrt_llm",
            "mlx",
            "mlx_audio",
            "whisper_cpp",
            "stable_diffusion_cpp",
            "custom",
            name="engine_name",
        ),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    runtime: Mapped[str] = mapped_column(
        Enum("native", "container", name="engine_runtime"),
        nullable=False,
        server_default=text("'native'::engine_runtime"),
    )
    supported_model_types: Mapped[list[str]] = mapped_column(
        ARRAY(
            Enum(
                "chat",
                "thinking",
                "vision",
                "embedding",
                "rerank",
                "stt",
                "tts",
                "image",
                "video",
                name="model_type",
                _create_events=False,
            )
        ),
        nullable=False,
        server_default=text("'{}'::model_type[]"),
    )
    supported_vendors: Mapped[list[str]] = mapped_column(
        ARRAY(
            Enum(
                "nvidia",
                "amd",
                "hygon",
                "metax",
                "moorethreads",
                "thead",
                "apple",
                name="device_vendor",
                _create_events=False,
            )
        ),
        nullable=False,
        server_default=text("'{}'::device_vendor[]"),
    )
    supported_os: Mapped[list[str]] = mapped_column(
        ARRAY(Enum("linux", "macos", name="node_os", _create_events=False)),
        nullable=False,
        server_default=text("'{}'::node_os[]"),
    )
    supported_formats: Mapped[list[str]] = mapped_column(
        ARRAY(
            Enum(
                "gguf",
                "safetensors",
                "mlx",
                "pickle",
                "other",
                name="model_file_format",
                _create_events=False,
            )
        ),
        nullable=False,
        server_default=text("'{}'::model_file_format[]"),
    )
    capabilities: Mapped[object] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment='{"router_mode":bool,"tensor_parallel":bool,"pipeline_parallel":bool,"pd_split":bool,"speculative_decoding":bool,"kv_sharing":bool,"slices":bool,"control_vectors":bool,"native_formats":["openai","anthropic"],"streaming":bool}',
    )
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    description: Mapped[Optional[str]] = mapped_column(String(2048))
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    engine_versions: Mapped[list["EngineVersions"]] = relationship(
        "EngineVersions", back_populates="engine"
    )


class HaLeases(Base):
    __tablename__ = "ha_leases"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="ha_leases_pkey"),
        UniqueConstraint("name", name="ha_leases_name_unique"),
        {
            "comment": "Leader election leases (names: jobs-leader, rules-evaluator, "
            "metrics-rollup, agent-hub-<cluster>). Acquire with UPDATE ... "
            "WHERE expires_at < now() OR holder_id = me; version is a fencing "
            "token incremented on every acquire. TTL HA_LEASE_TTL_SECONDS, "
            "renew every HA_LEASE_RENEW_SECONDS."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    metadata_: Mapped[object] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    holder_id: Mapped[Optional[str]] = mapped_column(String(255))
    acquired_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    renewed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))


class LogChunks(Base):
    __tablename__ = "log_chunks"
    __table_args__ = (
        PrimaryKeyConstraint("id", "ts", name="log_chunks_pkey"),
        Index(
            "idx_log_chunks_instance_ts",
            "instance_id",
            "ts",
            postgresql_where="(instance_id IS NOT NULL)",
        ),
        Index("idx_log_chunks_node_ts", "node_id", "ts", postgresql_where="(node_id IS NOT NULL)"),
        Index(
            "idx_log_chunks_request_id", "request_id", postgresql_where="(request_id IS NOT NULL)"
        ),
        Index("idx_log_chunks_source_level", "source", "level", "ts"),
        {
            "comment": "Engine stdout/stderr and agent/control-plane/rule/swap log lines, "
            "batched into chunks of up to LOG_CHUNK_MAX_LINES lines (content "
            "is newline-separated; ts = first line, ts_end = last). level = "
            "max level in the chunk. Retention LOGS_RETENTION_DAYS."
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime(True), primary_key=True)
    source: Mapped[str] = mapped_column(
        Enum("engine", "agent", "control_plane", "rule", "swap", "api_layer", name="log_source"),
        nullable=False,
    )
    stream: Mapped[str] = mapped_column(
        Enum("stdout", "stderr", "app", name="log_stream"),
        nullable=False,
        server_default=text("'stdout'::log_stream"),
    )
    level: Mapped[str] = mapped_column(
        Enum("debug", "info", "warn", "error", name="log_level"),
        nullable=False,
        server_default=text("'info'::log_level"),
    )
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    ts_end: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    node_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    instance_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    deployment_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    logger: Mapped[Optional[str]] = mapped_column(String(255))
    request_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    attributes: Mapped[Optional[object]] = mapped_column(JSONB)


class MetricRollupsEngine1m(Base):
    __tablename__ = "metric_rollups_engine_1m"
    __table_args__ = (
        PrimaryKeyConstraint("id", "ts", name="metric_rollups_engine_1m_pkey"),
        Index("idx_metric_rollups_engine_1m_deployment_ts", "deployment_id", "ts"),
        Index("idx_metric_rollups_engine_1m_instance_ts_unique", "instance_id", "ts", unique=True),
        {
            "comment": "1-minute rollups of metric_samples_engine; *_delta columns are "
            "counter differences within the minute (requests per minute = "
            "requests_delta). Rules read these (and the raw tables for the "
            "last minute). Retention METRICS_ROLLUP_RETENTION_DAYS."
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime(True), primary_key=True)
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    instance_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    deployment_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    active_sessions_avg: Mapped[Optional[float]] = mapped_column(REAL)
    active_sessions_max: Mapped[Optional[int]] = mapped_column(Integer)
    waiting_sessions_avg: Mapped[Optional[float]] = mapped_column(REAL)
    waiting_sessions_max: Mapped[Optional[int]] = mapped_column(Integer)
    requests_delta: Mapped[Optional[int]] = mapped_column(BigInteger)
    requests_success_delta: Mapped[Optional[int]] = mapped_column(BigInteger)
    errors_delta: Mapped[Optional[int]] = mapped_column(BigInteger)
    prompt_tokens_delta: Mapped[Optional[int]] = mapped_column(BigInteger)
    generated_tokens_delta: Mapped[Optional[int]] = mapped_column(BigInteger)
    cached_tokens_delta: Mapped[Optional[int]] = mapped_column(BigInteger)
    prompt_tps_avg: Mapped[Optional[float]] = mapped_column(REAL)
    generation_tps_avg: Mapped[Optional[float]] = mapped_column(REAL)
    ttft_ms_avg: Mapped[Optional[float]] = mapped_column(REAL)
    ttft_ms_p95: Mapped[Optional[float]] = mapped_column(REAL)
    e2e_ms_avg: Mapped[Optional[float]] = mapped_column(REAL)
    e2e_ms_p95: Mapped[Optional[float]] = mapped_column(REAL)
    tpot_ms_avg: Mapped[Optional[float]] = mapped_column(REAL)
    kv_cache_usage_pct_avg: Mapped[Optional[float]] = mapped_column(REAL)
    kv_cache_usage_pct_max: Mapped[Optional[float]] = mapped_column(REAL)
    prefix_cache_hit_rate: Mapped[Optional[float]] = mapped_column(REAL)
    error_rate_pct: Mapped[Optional[float]] = mapped_column(REAL)
    healthy_seconds: Mapped[Optional[float]] = mapped_column(REAL)


class MetricRollupsGpu1m(Base):
    __tablename__ = "metric_rollups_gpu_1m"
    __table_args__ = (
        PrimaryKeyConstraint("id", "ts", name="metric_rollups_gpu_1m_pkey"),
        Index("idx_metric_rollups_gpu_1m_device_ts_unique", "device_id", "ts", unique=True),
        Index("idx_metric_rollups_gpu_1m_node_ts", "node_id", "ts"),
        {
            "comment": "1-minute rollups of metric_samples_gpu (ts = minute bucket "
            "start). Built by the metrics rollup job every minute for the "
            "previous minute. Retention METRICS_ROLLUP_RETENTION_DAYS (90 d)."
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime(True), primary_key=True)
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    utilization_pct_avg: Mapped[Optional[float]] = mapped_column(REAL)
    utilization_pct_max: Mapped[Optional[float]] = mapped_column(REAL)
    memory_used_bytes_avg: Mapped[Optional[int]] = mapped_column(BigInteger)
    memory_used_bytes_max: Mapped[Optional[int]] = mapped_column(BigInteger)
    memory_total_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    power_watts_avg: Mapped[Optional[float]] = mapped_column(REAL)
    power_watts_max: Mapped[Optional[float]] = mapped_column(REAL)
    energy_wh: Mapped[Optional[float]] = mapped_column(REAL)
    temperature_c_avg: Mapped[Optional[float]] = mapped_column(REAL)
    temperature_c_max: Mapped[Optional[float]] = mapped_column(REAL)
    sm_clock_mhz_avg: Mapped[Optional[int]] = mapped_column(Integer)
    error_count_sum: Mapped[Optional[int]] = mapped_column(Integer)
    throttled_seconds: Mapped[Optional[float]] = mapped_column(REAL)


class MetricSamplesEngine(Base):
    __tablename__ = "metric_samples_engine"
    __table_args__ = (
        PrimaryKeyConstraint("id", "ts", name="metric_samples_engine_pkey"),
        Index("idx_metric_samples_engine_deployment_ts", "deployment_id", "ts"),
        Index("idx_metric_samples_engine_instance_ts", "instance_id", "ts"),
        {
            "comment": "Raw per-instance samples every 1-2 s scraped by the node agent "
            "from engine /metrics (+ /slots) or derived from the agent proxy. "
            "*_total columns are monotonic counters since engine start; rates "
            "are computed in rollups from deltas. See "
            "libs/contracts/metrics.md."
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime(True), primary_key=True)
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    instance_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    deployment_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    active_sessions: Mapped[Optional[int]] = mapped_column(Integer)
    waiting_sessions: Mapped[Optional[int]] = mapped_column(Integer)
    slots_total: Mapped[Optional[int]] = mapped_column(Integer)
    slots_idle: Mapped[Optional[int]] = mapped_column(Integer)
    requests_total: Mapped[Optional[int]] = mapped_column(BigInteger)
    requests_success_total: Mapped[Optional[int]] = mapped_column(BigInteger)
    errors_total: Mapped[Optional[int]] = mapped_column(BigInteger)
    prompt_tokens_total: Mapped[Optional[int]] = mapped_column(BigInteger)
    generated_tokens_total: Mapped[Optional[int]] = mapped_column(BigInteger)
    cached_tokens_total: Mapped[Optional[int]] = mapped_column(BigInteger)
    prompt_tps: Mapped[Optional[float]] = mapped_column(REAL)
    generation_tps: Mapped[Optional[float]] = mapped_column(REAL)
    ttft_ms_avg: Mapped[Optional[float]] = mapped_column(REAL)
    ttft_ms_p50: Mapped[Optional[float]] = mapped_column(REAL)
    ttft_ms_p95: Mapped[Optional[float]] = mapped_column(REAL)
    e2e_ms_avg: Mapped[Optional[float]] = mapped_column(REAL)
    e2e_ms_p50: Mapped[Optional[float]] = mapped_column(REAL)
    e2e_ms_p95: Mapped[Optional[float]] = mapped_column(REAL)
    tpot_ms_avg: Mapped[Optional[float]] = mapped_column(REAL)
    queue_ms_avg: Mapped[Optional[float]] = mapped_column(REAL)
    kv_cache_used_tokens: Mapped[Optional[int]] = mapped_column(BigInteger)
    kv_cache_total_tokens: Mapped[Optional[int]] = mapped_column(BigInteger)
    kv_cache_usage_pct: Mapped[Optional[float]] = mapped_column(REAL)
    kv_cache_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    prefix_cache_hit_rate: Mapped[Optional[float]] = mapped_column(REAL)
    spec_accept_rate: Mapped[Optional[float]] = mapped_column(REAL)
    process_vram_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    process_rss_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    health: Mapped[Optional[str]] = mapped_column(
        Enum("healthy", "unhealthy", "unknown", name="instance_health")
    )
    extra: Mapped[Optional[object]] = mapped_column(JSONB)


class MetricSamplesGpu(Base):
    __tablename__ = "metric_samples_gpu"
    __table_args__ = (
        PrimaryKeyConstraint("id", "ts", name="metric_samples_gpu_pkey"),
        Index("idx_metric_samples_gpu_device_ts", "device_id", "ts"),
        Index("idx_metric_samples_gpu_node_ts", "node_id", "ts"),
        {
            "comment": "Raw per-device samples every 1-2 s from the node agent (NVML / "
            "amd-smi / vendor SMI / Apple IOKit). Retention "
            "METRICS_RAW_RETENTION_HOURS (48 h). No FKs on purpose (ingest "
            "speed; ids are validated by the hub)."
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime(True), primary_key=True)
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    utilization_pct: Mapped[Optional[float]] = mapped_column(REAL)
    memory_util_pct: Mapped[Optional[float]] = mapped_column(REAL)
    memory_used_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    memory_total_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    power_watts: Mapped[Optional[float]] = mapped_column(REAL)
    power_limit_watts: Mapped[Optional[float]] = mapped_column(REAL)
    temperature_c: Mapped[Optional[float]] = mapped_column(REAL)
    memory_temperature_c: Mapped[Optional[float]] = mapped_column(REAL)
    sm_clock_mhz: Mapped[Optional[int]] = mapped_column(Integer)
    memory_clock_mhz: Mapped[Optional[int]] = mapped_column(Integer)
    fan_pct: Mapped[Optional[float]] = mapped_column(REAL)
    encoder_pct: Mapped[Optional[float]] = mapped_column(REAL)
    decoder_pct: Mapped[Optional[float]] = mapped_column(REAL)
    pcie_tx_bytes_per_s: Mapped[Optional[int]] = mapped_column(BigInteger)
    pcie_rx_bytes_per_s: Mapped[Optional[int]] = mapped_column(BigInteger)
    ecc_errors_total: Mapped[Optional[int]] = mapped_column(BigInteger)
    error_count: Mapped[Optional[int]] = mapped_column(
        Integer, comment="Vendor error events since last sample (NVIDIA Xid, AMD RAS, ...)."
    )
    throttle_reasons: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text()))
    process_count: Mapped[Optional[int]] = mapped_column(Integer)
    extra: Mapped[Optional[object]] = mapped_column(JSONB)


class MetricSamplesSession(Base):
    __tablename__ = "metric_samples_session"
    __table_args__ = (
        PrimaryKeyConstraint("id", "ts", name="metric_samples_session_pkey"),
        Index("idx_metric_samples_session_instance_ts", "instance_id", "ts"),
        Index(
            "idx_metric_samples_session_request_id",
            "request_id",
            postgresql_where="(request_id IS NOT NULL)",
        ),
        {
            "comment": "Per-session / per-slot samples (llama.cpp /slots, vLLM/SGLang "
            "per-request where exposed, agent-proxy derived otherwise). "
            "session_key = slot id_task, request id, or proxy connection id. "
            "state: idle|processing|generating|waiting. Retention as raw "
            "metrics."
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime(True), primary_key=True)
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    instance_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    session_key: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    slot_id: Mapped[Optional[int]] = mapped_column(Integer)
    request_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    generated_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    cached_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    kv_cache_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    kv_cache_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    context_size: Mapped[Optional[int]] = mapped_column(Integer)
    ttft_ms: Mapped[Optional[float]] = mapped_column(REAL)
    elapsed_ms: Mapped[Optional[float]] = mapped_column(REAL)
    tokens_per_second: Mapped[Optional[float]] = mapped_column(REAL)
    extra: Mapped[Optional[object]] = mapped_column(JSONB)


class Traces(Base):
    __tablename__ = "traces"
    __table_args__ = (
        PrimaryKeyConstraint("id", "ts", name="traces_pkey"),
        Index("idx_traces_request_id", "request_id", postgresql_where="(request_id IS NOT NULL)"),
        Index("idx_traces_trace_id", "trace_id", "ts"),
        Index("idx_traces_ts_service", "service", "ts"),
        {
            "comment": "Span index for the UI (request -> trace lookup and span "
            "waterfall). Full OpenTelemetry spans go to "
            "OTEL_EXPORTER_OTLP_ENDPOINT; this table keeps the subset the UI "
            "needs for TRACES_RETENTION_DAYS. service: "
            "api_layer|admin_api|jobs|node_agent."
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime(True), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False)
    span_id: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    service: Mapped[str] = mapped_column(String(50), nullable=False)
    error: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    parent_span_id: Mapped[Optional[str]] = mapped_column(String(16))
    request_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    api_key_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    model_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    deployment_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    instance_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    node_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    format: Mapped[Optional[str]] = mapped_column(
        Enum("openai", "anthropic", "ollama", name="usage_format")
    )
    endpoint: Mapped[Optional[str]] = mapped_column(String(100))
    status_code: Mapped[Optional[int]] = mapped_column(Integer)
    duration_ms: Mapped[Optional[float]] = mapped_column(REAL)
    attributes: Mapped[Optional[object]] = mapped_column(JSONB)


class UsageEvents(Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        PrimaryKeyConstraint("id", "ts", name="usage_events_pkey"),
        Index("idx_usage_events_api_key_ts", "api_key_id", "ts"),
        Index("idx_usage_events_deployment_ts", "deployment_id", "ts"),
        Index("idx_usage_events_model_ts", "model_id", "ts"),
        Index("idx_usage_events_node_ts", "node_id", "ts"),
        Index("idx_usage_events_request_id", "request_id", "ts"),
        {
            "comment": "One row per serving request, written by the metering Celery task "
            "after the response (never on the request path). gpu_seconds = "
            "latency_ms/1000 * gpu_count * slice_compute_pct/100. cost_usd "
            "computed from the resolved price plan at write time (node plan, "
            "else cluster plan, else default plan, else 0). Retention "
            "USAGE_EVENTS_RETENTION_DAYS."
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime.datetime] = mapped_column(DateTime(True), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    format: Mapped[str] = mapped_column(
        Enum("openai", "anthropic", "ollama", name="usage_format"), nullable=False
    )
    endpoint: Mapped[str] = mapped_column(String(100), nullable=False)
    stream: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    reasoning_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    media_units: Mapped[decimal.Decimal] = mapped_column(
        Numeric(12, 3),
        nullable=False,
        server_default=text("0"),
        comment="Non-token work: audio seconds (stt/tts), images generated, video seconds.",
    )
    gpu_seconds: Mapped[decimal.Decimal] = mapped_column(
        Numeric(12, 3), nullable=False, server_default=text("0")
    )
    gpu_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(
        Enum("success", "error", "timeout", "cancelled", name="usage_status"), nullable=False
    )
    cost_usd: Mapped[decimal.Decimal] = mapped_column(
        Numeric(14, 8), nullable=False, server_default=text("0")
    )
    api_key_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    model_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    deployment_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    instance_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    node_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    cluster_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    ttft_ms: Mapped[Optional[int]] = mapped_column(Integer)
    http_status: Mapped[Optional[int]] = mapped_column(SmallInteger)
    error_code: Mapped[Optional[str]] = mapped_column(String(100))
    price_plan_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    ip_address: Mapped[Optional[object]] = mapped_column(INET)
    metadata_: Mapped[Optional[object]] = mapped_column("metadata", JSONB)


class UsageRollupsHourly(Base):
    __tablename__ = "usage_rollups_hourly"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="usage_rollups_hourly_pkey"),
        UniqueConstraint(
            "bucket",
            "api_key_id",
            "model_id",
            "node_id",
            "deployment_id",
            name="usage_rollups_hourly_key_unique",
        ),
        Index("idx_usage_rollups_hourly_api_key", "api_key_id", "bucket"),
        Index("idx_usage_rollups_hourly_bucket", "bucket"),
        Index("idx_usage_rollups_hourly_model", "model_id", "bucket"),
        Index("idx_usage_rollups_hourly_node", "node_id", "bucket"),
        {
            "comment": "Hourly aggregates per (api key, model, node, deployment) built "
            "from usage_events by the metering job (upsert). Billing reports "
            "and the usage screens read this; usage_events is for drill-down."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    bucket: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    request_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    success_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    error_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    cached_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    reasoning_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    media_units: Mapped[decimal.Decimal] = mapped_column(
        Numeric(14, 3), nullable=False, server_default=text("0")
    )
    gpu_seconds: Mapped[decimal.Decimal] = mapped_column(
        Numeric(14, 3), nullable=False, server_default=text("0")
    )
    latency_ms_sum: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    ttft_ms_sum: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    cost_usd: Mapped[decimal.Decimal] = mapped_column(
        Numeric(14, 6), nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    api_key_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    model_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    node_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    deployment_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)


class ApiKeys(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        ForeignKeyConstraint(
            ["created_by"], ["admin_users.id"], ondelete="SET NULL", name="api_keys_created_by_fkey"
        ),
        PrimaryKeyConstraint("id", name="api_keys_pkey"),
        UniqueConstraint("key_hash", name="api_keys_key_hash_unique"),
        UniqueConstraint("key_prefix", name="api_keys_key_prefix_unique"),
        Index("idx_api_keys_key_hash", "key_hash", postgresql_where="(deleted_at IS NULL)"),
        Index(
            "idx_api_keys_name_trgm",
            "name",
            postgresql_ops={"name": "gin_trgm_ops"},
            postgresql_using="gin",
            postgresql_where="(deleted_at IS NULL)",
        ),
        Index("idx_api_keys_name_unique", postgresql_where="(deleted_at IS NULL)", unique=True),
        Index("idx_api_keys_status", "status", postgresql_where="(deleted_at IS NULL)"),
        {
            "comment": 'API keys for the serving API layer. Key format "sk-" + 48 hex '
            "chars; key_prefix = first 12 chars; key_hash = SHA-256 hex. "
            "Limits/quotas live in your AI gateway, not here."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("active", "expired", "revoked", "suspended", name="api_key_status"),
        nullable=False,
        server_default=text("'active'::api_key_status"),
    )
    total_requests: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
        comment="Incremented by the metering job (not on the request path).",
    )
    metadata_: Mapped[object] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    description: Mapped[Optional[str]] = mapped_column(String(2048))
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    last_used_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    revoked_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    last_rotated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    admin_users: Mapped[Optional["AdminUsers"]] = relationship(
        "AdminUsers", back_populates="api_keys"
    )
    batches: Mapped[list["Batches"]] = relationship("Batches", back_populates="api_key")


class BillingReports(Base):
    __tablename__ = "billing_reports"
    __table_args__ = (
        CheckConstraint("period_end > period_start", name="billing_reports_period_valid"),
        ForeignKeyConstraint(
            ["created_by"],
            ["admin_users.id"],
            ondelete="SET NULL",
            name="billing_reports_created_by_fkey",
        ),
        PrimaryKeyConstraint("id", name="billing_reports_pkey"),
        Index(
            "idx_billing_reports_period", "period_start", postgresql_where="(deleted_at IS NULL)"
        ),
        Index("idx_billing_reports_status", "status", postgresql_where="(deleted_at IS NULL)"),
        {
            "comment": "Generated cost reports (monthly by default, any period). totals = "
            '{"cost_usd","gpu_minutes","input_tokens","output_tokens","cached_tokens","requests"}; '
            "line_items = [{group keys..., same totals}]. CSV export is "
            "rendered from line_items on demand."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    period_start: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    period_end: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    group_by: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("'{api_key,model}'::text[]")
    )
    filters: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        Enum("pending", "generating", "ready", "failed", name="billing_report_status"),
        nullable=False,
        server_default=text("'pending'::billing_report_status"),
    )
    totals: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    line_items: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    currency: Mapped[str] = mapped_column(
        CHAR(3), nullable=False, server_default=text("'USD'::bpchar")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    generated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    error_message: Mapped[Optional[str]] = mapped_column(String(2048))
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    admin_users: Mapped[Optional["AdminUsers"]] = relationship(
        "AdminUsers", back_populates="billing_reports"
    )


class Credentials(Base):
    __tablename__ = "credentials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["created_by"],
            ["admin_users.id"],
            ondelete="SET NULL",
            name="credentials_created_by_fkey",
        ),
        PrimaryKeyConstraint("id", name="credentials_pkey"),
        Index(
            "idx_credentials_extra_data",
            "extra_data",
            postgresql_using="gin",
            postgresql_where="(deleted_at IS NULL)",
        ),
        Index("idx_credentials_name_unique", postgresql_where="(deleted_at IS NULL)", unique=True),
        Index("idx_credentials_provider", "provider", postgresql_where="(deleted_at IS NULL)"),
        Index("idx_credentials_status", "status", postgresql_where="(deleted_at IS NULL)"),
        Index("idx_credentials_type", "type", postgresql_where="(deleted_at IS NULL)"),
        {
            "comment": "Encrypted secrets vault: cloud keys, HF/ModelScope tokens, "
            "SMTP/Slack/webhook secrets, internal CA key, TLS keys. Encrypted "
            "with MultiFernet(VAULT_KEYS)."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(
        Enum(
            "api_key",
            "token",
            "oauth",
            "service_account",
            "ssh_key",
            "cloud_account",
            "smtp",
            "webhook",
            "tls_key",
            "ca_key",
            "other",
            name="credential_type",
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Enum("active", "expired", "revoked", name="credential_status"),
        nullable=False,
        server_default=text("'active'::credential_status"),
    )
    encrypted_value: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Fernet ciphertext (TEXT: CA/TLS PEM keys exceed 4096 chars); never returned to clients.",
    )
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    key_version: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default=text("1"),
        comment="Index (1-based) of the VAULT_KEYS entry that encrypted this value; rotation re-encrypts rows with key_version < 1.",
    )
    access_restrictions: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    extra_data: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    provider: Mapped[Optional[str]] = mapped_column(
        String(100),
        comment="Slug of what the secret belongs to: huggingface, modelscope, aws, azure, gcp, digitalocean, slack, smtp, webhook, internal_ca, agent_hub_tls, ...",
    )
    masked_value: Mapped[Optional[str]] = mapped_column(String(50))
    description: Mapped[Optional[str]] = mapped_column(String(2048))
    base_url: Mapped[Optional[str]] = mapped_column(String(2048))
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    last_used_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    admin_users: Mapped[Optional["AdminUsers"]] = relationship(
        "AdminUsers", back_populates="credentials"
    )
    alert_channels: Mapped[list["AlertChannels"]] = relationship(
        "AlertChannels", back_populates="credential"
    )
    clusters: Mapped[list["Clusters"]] = relationship("Clusters", back_populates="credential")
    credential_usage_history: Mapped[list["CredentialUsageHistory"]] = relationship(
        "CredentialUsageHistory", back_populates="credential"
    )
    cloud_instances: Mapped[list["CloudInstances"]] = relationship(
        "CloudInstances", back_populates="credential"
    )


class EngineVersions(Base):
    __tablename__ = "engine_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["engine_id"], ["engines.id"], ondelete="CASCADE", name="engine_versions_engine_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="engine_versions_pkey"),
        Index(
            "idx_engine_versions_engine_id", "engine_id", postgresql_where="(deleted_at IS NULL)"
        ),
        Index(
            "idx_engine_versions_engine_version_unique",
            "engine_id",
            "version",
            postgresql_where="(deleted_at IS NULL)",
            unique=True,
        ),
        Index(
            "idx_engine_versions_single_default",
            "engine_id",
            postgresql_where="(is_default AND (deleted_at IS NULL))",
            unique=True,
        ),
        {
            "comment": "A pinned engine build: container image or per-platform native "
            "binary, with launch/health/metrics config the node agent uses "
            "verbatim."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    engine_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("available", "deprecated", "disabled", name="engine_version_status"),
        nullable=False,
        server_default=text("'available'::engine_version_status"),
    )
    artifacts: Mapped[object] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment='Native binaries per platform: {"linux/x86_64":{"url":"https://...","sha256":"..","kind":"tarball"},"macos/aarch64":{...}}. Empty when image is used.',
    )
    launch_template: Mapped[object] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment='{"command":["llama-server","--port","{{port}}","-m","{{model_path}}"],"env":{},"working_dir":null}; template vars listed in agent-protocol/command.json.',
    )
    default_launch_flags: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    health_config: Mapped[object] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text(
            '\'{"path": "/health", "timeout_seconds": 3, "healthy_statuses": [200], "interval_seconds": 5}\'::jsonb'
        ),
    )
    metrics_config: Mapped[object] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment='{"path":"/metrics","format":"prometheus","slots_path":"/slots","scrape_interval_seconds":2,"derive_from_proxy":false}',
    )
    port_range: Mapped[object] = mapped_column(
        INT4RANGE, nullable=False, server_default=text("'[10240,10340)'::int4range")
    )
    requirements: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    image: Mapped[Optional[str]] = mapped_column(String(512))
    min_driver_version: Mapped[Optional[str]] = mapped_column(String(50))
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    engine: Mapped["Engines"] = relationship("Engines", back_populates="engine_versions")
    deployments: Mapped[list["Deployments"]] = relationship(
        "Deployments", back_populates="engine_version"
    )
    engine_compat_checks: Mapped[list["EngineCompatChecks"]] = relationship(
        "EngineCompatChecks", back_populates="engine_version"
    )


class IpRules(Base):
    __tablename__ = "ip_rules"
    __table_args__ = (
        ForeignKeyConstraint(
            ["created_by"], ["admin_users.id"], ondelete="SET NULL", name="ip_rules_created_by_fkey"
        ),
        PrimaryKeyConstraint("id", name="ip_rules_pkey"),
        Index(
            "idx_ip_rules_scope_action_cidr_unique",
            "scope",
            "action",
            "cidr",
            postgresql_where="(deleted_at IS NULL)",
            unique=True,
        ),
        Index(
            "idx_ip_rules_scope_enabled",
            "scope",
            "enabled",
            postgresql_where="(deleted_at IS NULL)",
        ),
        {
            "comment": "IP allow/block rules. Semantics: block rules win; if any allow "
            "rule exists for a scope, only allowed CIDRs pass. Env "
            "GATEWAY_IP_ALLOWLIST is merged in as implicit api_layer allow "
            "rules. Cached in Redis ip_rules:{scope}."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    action: Mapped[str] = mapped_column(
        Enum("allow", "block", name="ip_rule_action"), nullable=False
    )
    scope: Mapped[str] = mapped_column(
        Enum("admin", "api_layer", name="ip_rule_scope"), nullable=False
    )
    cidr: Mapped[object] = mapped_column(CIDR, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    description: Mapped[Optional[str]] = mapped_column(String(512))
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    admin_users: Mapped[Optional["AdminUsers"]] = relationship(
        "AdminUsers", back_populates="ip_rules"
    )


class Layouts(Base):
    __tablename__ = "layouts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["created_by"], ["admin_users.id"], ondelete="SET NULL", name="layouts_created_by_fkey"
        ),
        PrimaryKeyConstraint("id", name="layouts_pkey"),
        Index("idx_layouts_name_unique", postgresql_where="(deleted_at IS NULL)", unique=True),
        Index(
            "idx_layouts_single_active",
            "is_active",
            postgresql_where="(is_active AND (deleted_at IS NULL))",
            unique=True,
        ),
        {
            "comment": "Named GPU layouts (Day, Night, ...). Applying a layout computes "
            "the diff against running instances and executes swap_operations. "
            "is_active marks the last applied layout."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    unlisted_policy: Mapped[str] = mapped_column(
        Enum("keep", "unload", name="layout_unlisted_policy"),
        nullable=False,
        server_default=text("'keep'::layout_unlisted_policy"),
        comment="What happens to running deployments not listed in layout_items when the layout is applied: keep (default) or unload (never touches always_on models).",
    )
    description: Mapped[Optional[str]] = mapped_column(String(2048))
    applied_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    admin_users: Mapped[Optional["AdminUsers"]] = relationship(
        "AdminUsers", back_populates="layouts"
    )
    layout_items: Mapped[list["LayoutItems"]] = relationship("LayoutItems", back_populates="layout")
    swap_operations: Mapped[list["SwapOperations"]] = relationship(
        "SwapOperations", back_populates="layout"
    )


class Models(Base):
    __tablename__ = "models"
    __table_args__ = (
        ForeignKeyConstraint(
            ["created_by"], ["admin_users.id"], ondelete="SET NULL", name="models_created_by_fkey"
        ),
        PrimaryKeyConstraint("id", name="models_pkey"),
        Index(
            "idx_models_aliases",
            "aliases",
            postgresql_using="gin",
            postgresql_where="(deleted_at IS NULL)",
        ),
        Index(
            "idx_models_name_trgm",
            "name",
            postgresql_ops={"name": "gin_trgm_ops"},
            postgresql_using="gin",
            postgresql_where="(deleted_at IS NULL)",
        ),
        Index("idx_models_name_unique", postgresql_where="(deleted_at IS NULL)", unique=True),
        Index("idx_models_priority", "priority", postgresql_where="(deleted_at IS NULL)"),
        Index("idx_models_source", "source", postgresql_where="(deleted_at IS NULL)"),
        Index("idx_models_type", "type", postgresql_where="(deleted_at IS NULL)"),
        {
            "comment": 'Catalog entry. name is the public serving name (the "model" field '
            "in OpenAI/Anthropic/Ollama requests); aliases are extra serving "
            "names."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(
        Enum("huggingface", "modelscope", "local_path", name="model_source"), nullable=False
    )
    repo_id: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        comment="HF/ModelScope repo id (org/name) or absolute local path for source=local_path.",
    )
    type: Mapped[str] = mapped_column(
        Enum(
            "chat",
            "thinking",
            "vision",
            "embedding",
            "rerank",
            "stt",
            "tts",
            "image",
            "video",
            name="model_type",
        ),
        nullable=False,
    )
    priority: Mapped[str] = mapped_column(
        Enum("always_on", "normal", "evictable", name="model_priority"),
        nullable=False,
        server_default=text("'normal'::model_priority"),
        comment="always_on: rules never unload it; normal: unload only by idle/time rules; evictable: may be evicted to make room.",
    )
    trust_remote_code: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    allow_pickle: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment="FALSE blocks versions whose only weights are pickle-based (.bin/.pt/.pth/.ckpt).",
    )
    default_launch_flags: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]")
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]")
    )
    metadata_: Mapped[object] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    family: Mapped[Optional[str]] = mapped_column(
        String(100),
        comment="Model family used for tool-parser / chat-template defaults: qwen3, llama, gemma3, mistral, deepseek, ...",
    )
    description: Mapped[Optional[str]] = mapped_column(String(4096))
    tool_parser_default: Mapped[Optional[str]] = mapped_column(String(100))
    default_context_size: Mapped[Optional[int]] = mapped_column(Integer)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    admin_users: Mapped[Optional["AdminUsers"]] = relationship(
        "AdminUsers", back_populates="models"
    )
    model_versions: Mapped[list["ModelVersions"]] = relationship(
        "ModelVersions", back_populates="model"
    )
    deployments: Mapped[list["Deployments"]] = relationship("Deployments", back_populates="model")


class PricePlans(Base):
    __tablename__ = "price_plans"
    __table_args__ = (
        CheckConstraint(
            "usd_per_gpu_minute >= 0::numeric AND usd_per_1k_input_tokens >= 0::numeric AND usd_per_1k_output_tokens >= 0::numeric AND usd_per_1k_cached_tokens >= 0::numeric",
            name="price_plans_nonneg",
        ),
        ForeignKeyConstraint(
            ["created_by"],
            ["admin_users.id"],
            ondelete="SET NULL",
            name="price_plans_created_by_fkey",
        ),
        PrimaryKeyConstraint("id", name="price_plans_pkey"),
        Index("idx_price_plans_name_unique", postgresql_where="(deleted_at IS NULL)", unique=True),
        Index(
            "idx_price_plans_single_default",
            "is_default",
            postgresql_where="(is_default AND (deleted_at IS NULL))",
            unique=True,
        ),
        {
            "comment": "USD price per GPU minute and/or per 1k tokens. Attached to a "
            "cluster (clusters.price_plan_id) and optionally overridden per "
            "node (nodes.price_plan_id). No plan = on-prem, cost 0."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(
        CHAR(3), nullable=False, server_default=text("'USD'::bpchar")
    )
    usd_per_gpu_minute: Mapped[decimal.Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, server_default=text("0")
    )
    usd_per_1k_input_tokens: Mapped[decimal.Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, server_default=text("0")
    )
    usd_per_1k_output_tokens: Mapped[decimal.Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, server_default=text("0")
    )
    usd_per_1k_cached_tokens: Mapped[decimal.Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, server_default=text("0")
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    description: Mapped[Optional[str]] = mapped_column(String(2048))
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    admin_users: Mapped[Optional["AdminUsers"]] = relationship(
        "AdminUsers", back_populates="price_plans"
    )
    clusters: Mapped[list["Clusters"]] = relationship("Clusters", back_populates="price_plan")
    nodes: Mapped[list["Nodes"]] = relationship("Nodes", back_populates="price_plan")


class Rules(Base):
    __tablename__ = "rules"
    __table_args__ = (
        CheckConstraint("cooldown_seconds >= 0", name="rules_cooldown_nonneg"),
        CheckConstraint("evaluation_interval_seconds >= 5", name="rules_interval_min"),
        ForeignKeyConstraint(
            ["created_by"], ["admin_users.id"], ondelete="SET NULL", name="rules_created_by_fkey"
        ),
        PrimaryKeyConstraint("id", name="rules_pkey"),
        Index("idx_rules_name_unique", postgresql_where="(deleted_at IS NULL)", unique=True),
        Index("idx_rules_type_enabled", "type", "enabled", postgresql_where="(deleted_at IS NULL)"),
        {
            "comment": "User-built rules. params is validated against "
            "libs/contracts/rules/<type>.schema.json; target against "
            "target-selector.schema.json. priority: lower runs first when "
            "several rules fire in one tick."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(
        Enum(
            "idle_unload",
            "time_schedule",
            "low_traffic_shrink",
            "high_load_grow",
            "make_room_by_priority",
            "auto_heal",
            name="rule_type",
        ),
        nullable=False,
    )
    params: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    target: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    cooldown_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("300")
    )
    evaluation_interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("30")
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("100"))
    fire_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    description: Mapped[Optional[str]] = mapped_column(String(2048))
    last_evaluated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    last_fired_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    admin_users: Mapped[Optional["AdminUsers"]] = relationship("AdminUsers", back_populates="rules")
    rule_runs: Mapped[list["RuleRuns"]] = relationship("RuleRuns", back_populates="rule")


class Sessions(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="sessions_expires_after_created"),
        ForeignKeyConstraint(
            ["admin_user_id"],
            ["admin_users.id"],
            ondelete="CASCADE",
            name="sessions_admin_user_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="sessions_pkey"),
        UniqueConstraint("token_hash", name="sessions_token_hash_unique"),
        Index("idx_sessions_admin_user_id", "admin_user_id", postgresql_where="is_active"),
        Index("idx_sessions_expires_at", "expires_at", postgresql_where="is_active"),
        {
            "comment": "Server-side admin sessions: Fernet token in an HttpOnly cookie, "
            "SHA-256 of it here, dict cached in Redis session:{token_hash}."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    admin_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="SHA-256 hex of the raw cookie token. CSRF uses a double-submit token derived with HMAC(SESSION_SECRET, token_hash); nothing extra is stored.",
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    ip_address: Mapped[Optional[object]] = mapped_column(INET)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512))
    last_active_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    revoked_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    admin_user: Mapped["AdminUsers"] = relationship("AdminUsers", back_populates="sessions")


class AlertChannels(Base):
    __tablename__ = "alert_channels"
    __table_args__ = (
        ForeignKeyConstraint(
            ["created_by"],
            ["admin_users.id"],
            ondelete="SET NULL",
            name="alert_channels_created_by_fkey",
        ),
        ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
            ondelete="SET NULL",
            name="alert_channels_credential_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="alert_channels_pkey"),
        Index("idx_alert_channels_enabled", "enabled", postgresql_where="(deleted_at IS NULL)"),
        Index(
            "idx_alert_channels_name_unique", postgresql_where="(deleted_at IS NULL)", unique=True
        ),
        {
            "comment": "Slack / email / webhook destinations. Non-secret settings in "
            "config "
            '({"channel":"#gpu","recipients":[...],"url":"https://..."}); the '
            "secret (webhook URL with token, SMTP password, signing secret) is "
            "a vault credential via credential_id. event_filter = list of "
            "event_type globs, empty = all."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(
        Enum("slack", "email", "webhook", name="alert_channel_type"), nullable=False
    )
    config: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    min_severity: Mapped[str] = mapped_column(
        Enum("info", "warning", "critical", name="alert_severity"),
        nullable=False,
        server_default=text("'warning'::alert_severity"),
    )
    event_filter: Mapped[list[str]] = mapped_column(
        ARRAY(Text()), nullable=False, server_default=text("'{}'::text[]")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    credential_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    last_test_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    last_test_ok: Mapped[Optional[bool]] = mapped_column(Boolean)
    last_error: Mapped[Optional[str]] = mapped_column(String(2048))
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    admin_users: Mapped[Optional["AdminUsers"]] = relationship(
        "AdminUsers", back_populates="alert_channels"
    )
    credential: Mapped[Optional["Credentials"]] = relationship(
        "Credentials", back_populates="alert_channels"
    )
    alert_deliveries: Mapped[list["AlertDeliveries"]] = relationship(
        "AlertDeliveries", back_populates="channel"
    )


class Batches(Base):
    __tablename__ = "batches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["api_key_id"], ["api_keys.id"], ondelete="SET NULL", name="batches_api_key_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="batches_pkey"),
        UniqueConstraint("external_id", name="batches_external_id_unique"),
        Index("idx_batches_api_key_id", "api_key_id", "created_at"),
        Index("idx_batches_status", "status", postgresql_where="(status <> 'ended'::batch_status)"),
        {
            "comment": "Anthropic Message Batches (POST /v1/messages/batches) run by the "
            "jobs process against the same deployments. external_id is the "
            "public id (msgbatch_<24 hex>). Results are read from batch_items "
            "and streamed as JSONL."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    format: Mapped[str] = mapped_column(
        Enum("openai", "anthropic", "ollama", name="usage_format"),
        nullable=False,
        server_default=text("'anthropic'::usage_format"),
    )
    status: Mapped[str] = mapped_column(
        Enum("in_progress", "canceling", "ended", name="batch_status"),
        nullable=False,
        server_default=text("'in_progress'::batch_status"),
    )
    request_counts: Mapped[object] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text(
            '\'{"errored": 0, "expired": 0, "canceled": 0, "succeeded": 0, "processing": 0}\'::jsonb'
        ),
    )
    total_items: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    api_key_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    ended_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    cancel_initiated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    archived_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    api_key: Mapped[Optional["ApiKeys"]] = relationship("ApiKeys", back_populates="batches")
    batch_items: Mapped[list["BatchItems"]] = relationship("BatchItems", back_populates="batch")


class Clusters(Base):
    __tablename__ = "clusters"
    __table_args__ = (
        ForeignKeyConstraint(
            ["created_by"], ["admin_users.id"], ondelete="SET NULL", name="clusters_created_by_fkey"
        ),
        ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
            ondelete="SET NULL",
            name="clusters_credential_id_fkey",
        ),
        ForeignKeyConstraint(
            ["price_plan_id"],
            ["price_plans.id"],
            ondelete="SET NULL",
            name="clusters_price_plan_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="clusters_pkey"),
        Index("idx_clusters_name_unique", postgresql_where="(deleted_at IS NULL)", unique=True),
        Index("idx_clusters_type", "type", postgresql_where="(deleted_at IS NULL)"),
        {
            "comment": "A group of GPU machines: bare metal, VMs, Kubernetes, or cloud "
            "(AWS/Azure/GCP/DO). Cloud/K8s clusters keep their account secret "
            "in credential_id and provider-specific settings (kube namespace, "
            "VPC, instance types) in config."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(
        Enum(
            "bare_metal",
            "vm",
            "kubernetes",
            "cloud_aws",
            "cloud_azure",
            "cloud_gcp",
            "cloud_do",
            name="cluster_type",
        ),
        nullable=False,
        server_default=text("'bare_metal'::cluster_type"),
    )
    status: Mapped[str] = mapped_column(
        Enum("active", "disabled", name="cluster_status"),
        nullable=False,
        server_default=text("'active'::cluster_status"),
    )
    config: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    egress_allowlist: Mapped[list[str]] = mapped_column(
        ARRAY(Text()),
        nullable=False,
        server_default=text("'{}'::text[]"),
        comment="Extra hostnames/CIDRs the node agents in this cluster may reach besides the control plane and model sources (merged with env EGRESS_ALLOWLIST_DEFAULT).",
    )
    labels: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    description: Mapped[Optional[str]] = mapped_column(String(2048))
    region: Mapped[Optional[str]] = mapped_column(String(100))
    credential_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    price_plan_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    admin_users: Mapped[Optional["AdminUsers"]] = relationship(
        "AdminUsers", back_populates="clusters"
    )
    credential: Mapped[Optional["Credentials"]] = relationship(
        "Credentials", back_populates="clusters"
    )
    price_plan: Mapped[Optional["PricePlans"]] = relationship(
        "PricePlans", back_populates="clusters"
    )
    deployments: Mapped[list["Deployments"]] = relationship("Deployments", back_populates="cluster")
    nodes: Mapped[list["Nodes"]] = relationship("Nodes", back_populates="cluster")
    gpu_instances: Mapped[list["GpuInstances"]] = relationship(
        "GpuInstances", back_populates="cluster"
    )
    join_tokens: Mapped[list["JoinTokens"]] = relationship("JoinTokens", back_populates="cluster")
    cloud_instances: Mapped[list["CloudInstances"]] = relationship(
        "CloudInstances", back_populates="cluster"
    )


class CredentialUsageHistory(Base):
    __tablename__ = "credential_usage_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
            ondelete="CASCADE",
            name="credential_usage_history_credential_id_fkey",
        ),
        ForeignKeyConstraint(
            ["used_by"],
            ["admin_users.id"],
            ondelete="SET NULL",
            name="credential_usage_history_used_by_fkey",
        ),
        PrimaryKeyConstraint("id", name="credential_usage_history_pkey"),
        Index("idx_credential_usage_history_credential_id", "credential_id"),
        Index("idx_credential_usage_history_used_at", "used_at"),
        {
            "comment": "One row per reveal/use of a vault credential (purpose: reveal, "
            "join_sign, hf_download, alert_send, ...)."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    credential_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    used_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    used_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    purpose: Mapped[Optional[str]] = mapped_column(String(255))
    request_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)

    credential: Mapped["Credentials"] = relationship(
        "Credentials", back_populates="credential_usage_history"
    )
    admin_users: Mapped[Optional["AdminUsers"]] = relationship(
        "AdminUsers", back_populates="credential_usage_history"
    )


class ModelVersions(Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["model_id"], ["models.id"], ondelete="CASCADE", name="model_versions_model_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="model_versions_pkey"),
        Index("idx_model_versions_model_id", "model_id", postgresql_where="(deleted_at IS NULL)"),
        Index(
            "idx_model_versions_model_version_unique",
            "model_id",
            "version",
            postgresql_where="(deleted_at IS NULL)",
            unique=True,
        ),
        Index(
            "idx_model_versions_single_default",
            "model_id",
            postgresql_where="(is_default AND (deleted_at IS NULL))",
            unique=True,
        ),
        {
            "comment": "A concrete set of weight files for a model: HF revision/commit, "
            "ModelScope revision, or a local directory snapshot. Exactly one "
            "is_default per model."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    model_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    version: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment='User-facing label ("main", "v2", "Q4_K_M", "2026-11-16"). revision holds the upstream commit sha when known.',
    )
    format: Mapped[str] = mapped_column(
        Enum("gguf", "safetensors", "mlx", "pickle", "other", name="model_file_format"),
        nullable=False,
        server_default=text("'other'::model_file_format"),
    )
    status: Mapped[str] = mapped_column(
        Enum("registered", "verifying", "ready", "missing", "blocked", name="model_version_status"),
        nullable=False,
        server_default=text("'registered'::model_version_status"),
        comment="blocked = pickle-only weights with models.allow_pickle=false, or hash mismatch.",
    )
    total_size_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    config: Mapped[object] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="Parsed config.json / GGUF metadata: n_layers, n_kv_heads, head_dim, max_position_embeddings, architecture (used for KV-cache size estimates).",
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    revision: Mapped[Optional[str]] = mapped_column(String(255))
    quantization: Mapped[Optional[str]] = mapped_column(String(50))
    status_message: Mapped[Optional[str]] = mapped_column(String(1024))
    parameter_count: Mapped[Optional[int]] = mapped_column(BigInteger)
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    model: Mapped["Models"] = relationship("Models", back_populates="model_versions")
    deployments: Mapped[list["Deployments"]] = relationship(
        "Deployments", back_populates="model_version"
    )
    model_files: Mapped[list["ModelFiles"]] = relationship(
        "ModelFiles", back_populates="model_version"
    )
    engine_compat_checks: Mapped[list["EngineCompatChecks"]] = relationship(
        "EngineCompatChecks", back_populates="model_version"
    )
    node_model_cache: Mapped[list["NodeModelCache"]] = relationship(
        "NodeModelCache", back_populates="model_version"
    )


class RuleRuns(Base):
    __tablename__ = "rule_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["rule_id"], ["rules.id"], ondelete="CASCADE", name="rule_runs_rule_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="rule_runs_pkey"),
        Index("idx_rule_runs_evaluated_at", "evaluated_at"),
        Index("idx_rule_runs_fired", "evaluated_at", postgresql_where="fired"),
        Index("idx_rule_runs_rule_evaluated", "rule_id", "evaluated_at"),
        {
            "comment": "History of every rule evaluation that matched, fired, dry-ran or "
            "errored (no_match rows are written at most once per "
            "RULE_RUN_NO_MATCH_LOG_INTERVAL_SECONDS to keep volume down). "
            "decision = observed values + reason; actions = [{action, "
            "target_type, target_id, swap_operation_id, status}]."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    rule_type: Mapped[str] = mapped_column(
        Enum(
            "idle_unload",
            "time_schedule",
            "low_traffic_shrink",
            "high_load_grow",
            "make_room_by_priority",
            "auto_heal",
            name="rule_type",
        ),
        nullable=False,
    )
    evaluated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    fired: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    outcome: Mapped[str] = mapped_column(
        Enum(
            "no_match", "cooldown", "fired", "dry_run", "skipped", "error", name="rule_run_outcome"
        ),
        nullable=False,
    )
    decision: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    actions: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    error_message: Mapped[Optional[str]] = mapped_column(String(2048))
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)

    rule: Mapped["Rules"] = relationship("Rules", back_populates="rule_runs")
    swap_operations: Mapped[list["SwapOperations"]] = relationship(
        "SwapOperations", back_populates="rule_run"
    )


class AlertDeliveries(Base):
    __tablename__ = "alert_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["alert_event_id"],
            ["alert_events.id"],
            ondelete="CASCADE",
            name="alert_deliveries_alert_event_id_fkey",
        ),
        ForeignKeyConstraint(
            ["channel_id"],
            ["alert_channels.id"],
            ondelete="CASCADE",
            name="alert_deliveries_channel_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="alert_deliveries_pkey"),
        Index(
            "idx_alert_deliveries_event_channel_unique", "alert_event_id", "channel_id", unique=True
        ),
        Index(
            "idx_alert_deliveries_pending",
            "status",
            "created_at",
            postgresql_where="(status = 'pending'::alert_delivery_status)",
        ),
        {
            "comment": "One row per (event, channel) send attempt chain. Written by the "
            "alerts Celery task."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    alert_event_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    channel_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("pending", "sent", "failed", "suppressed", name="alert_delivery_status"),
        nullable=False,
        server_default=text("'pending'::alert_delivery_status"),
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    sent_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    response_code: Mapped[Optional[int]] = mapped_column(Integer)
    error_message: Mapped[Optional[str]] = mapped_column(String(2048))

    alert_event: Mapped["AlertEvents"] = relationship(
        "AlertEvents", back_populates="alert_deliveries"
    )
    channel: Mapped["AlertChannels"] = relationship(
        "AlertChannels", back_populates="alert_deliveries"
    )


class BatchItems(Base):
    __tablename__ = "batch_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["batch_id"], ["batches.id"], ondelete="CASCADE", name="batch_items_batch_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="batch_items_pkey"),
        Index("idx_batch_items_batch_custom_unique", "batch_id", "custom_id", unique=True),
        Index("idx_batch_items_batch_status", "batch_id", "status"),
        {
            "comment": "One request of a message batch. request = the Anthropic "
            "MessageCreateParams; result = the Message or error object in "
            "Anthropic result shape."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    custom_id: Mapped[str] = mapped_column(String(255), nullable=False)
    request: Mapped[object] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(
            "pending",
            "processing",
            "succeeded",
            "errored",
            "canceled",
            "expired",
            name="batch_item_status",
        ),
        nullable=False,
        server_default=text("'pending'::batch_item_status"),
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    model_name: Mapped[Optional[str]] = mapped_column(String(255))
    result: Mapped[Optional[object]] = mapped_column(JSONB)
    error: Mapped[Optional[object]] = mapped_column(JSONB)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    batch: Mapped["Batches"] = relationship("Batches", back_populates="batch_items")


class Deployments(Base):
    __tablename__ = "deployments"
    __table_args__ = (
        CheckConstraint("gpu_count >= 1", name="deployments_gpu_count_pos"),
        CheckConstraint(
            "min_replicas >= 0 AND replicas >= min_replicas AND max_replicas >= replicas",
            name="deployments_replicas_range",
        ),
        CheckConstraint(
            "origin = 'external'::deployment_origin OR model_id IS NOT NULL AND model_version_id IS NOT NULL AND engine_version_id IS NOT NULL",
            name="deployments_managed_refs",
        ),
        CheckConstraint(
            "slice_compute_pct IS NULL OR slice_compute_pct >= 1 AND slice_compute_pct <= 100",
            name="deployments_slice_pct_range",
        ),
        CheckConstraint("tp_size >= 1 AND pp_size >= 1", name="deployments_parallel_pos"),
        ForeignKeyConstraint(
            ["cluster_id"], ["clusters.id"], ondelete="SET NULL", name="deployments_cluster_id_fkey"
        ),
        ForeignKeyConstraint(
            ["created_by"],
            ["admin_users.id"],
            ondelete="SET NULL",
            name="deployments_created_by_fkey",
        ),
        ForeignKeyConstraint(
            ["engine_version_id"],
            ["engine_versions.id"],
            ondelete="RESTRICT",
            name="deployments_engine_version_id_fkey",
        ),
        ForeignKeyConstraint(
            ["model_id"], ["models.id"], ondelete="RESTRICT", name="deployments_model_id_fkey"
        ),
        ForeignKeyConstraint(
            ["model_version_id"],
            ["model_versions.id"],
            ondelete="RESTRICT",
            name="deployments_model_version_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="deployments_pkey"),
        Index("idx_deployments_cluster_id", "cluster_id", postgresql_where="(deleted_at IS NULL)"),
        Index("idx_deployments_model_id", "model_id", postgresql_where="(deleted_at IS NULL)"),
        Index(
            "idx_deployments_name_trgm",
            "name",
            postgresql_ops={"name": "gin_trgm_ops"},
            postgresql_using="gin",
            postgresql_where="(deleted_at IS NULL)",
        ),
        Index("idx_deployments_name_unique", postgresql_where="(deleted_at IS NULL)", unique=True),
        Index("idx_deployments_origin", "origin", postgresql_where="(deleted_at IS NULL)"),
        Index("idx_deployments_status", "status", postgresql_where="(deleted_at IS NULL)"),
        {
            "comment": "Desired state: model version + engine version + replicas + GPU "
            "shape + launch settings. The scheduler turns it into instances."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    replicas: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    min_replicas: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_replicas: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    gpu_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
        comment="GPUs per replica (tp_size * pp_size must divide into it; 1 for llama.cpp/MLX).",
    )
    tp_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    pp_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    pd_split: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    pd_config: Mapped[object] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment='{"prefill_replicas":int,"decode_replicas":int,"transfer":"nixl|mooncake"}',
    )
    spec_decoding: Mapped[object] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment='{"enabled":bool,"draft_model_version_id":uuid|null,"num_draft_tokens":int,"method":"draft_model|ngram|eagle|mtp"}',
    )
    kv_sharing: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    kv_sharing_config: Mapped[object] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment='{"backend":"lmcache|mooncake|native","remote_url":null}',
    )
    launch_flags: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    env_vars: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    control_vectors: Mapped[object] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment='llama.cpp only: [{"path":"...gguf","scale":0.8,"layer_range":[10,20]}]',
    )
    placement_strategy: Mapped[str] = mapped_column(
        Enum("spread", "pack", name="placement_strategy"),
        nullable=False,
        server_default=text("'spread'::placement_strategy"),
    )
    node_selector: Mapped[object] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment='Label selector {"zone":"a","gpu.tier":"h100"} and/or {"node_ids":[...]}; empty = any node in cluster_id (or any cluster).',
    )
    device_vendor_filter: Mapped[list[str]] = mapped_column(
        ARRAY(
            Enum(
                "nvidia",
                "amd",
                "hygon",
                "metax",
                "moorethreads",
                "thead",
                "apple",
                name="device_vendor",
                _create_events=False,
            )
        ),
        nullable=False,
        server_default=text("'{}'::device_vendor[]"),
    )
    traffic_config: Mapped[object] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text(
            '\'{"failover": true, "strategy": "least_sessions", "max_retries": 1, "health_eject": true}\'::jsonb'
        ),
        comment='{"strategy":"round_robin|least_sessions|weighted","failover":bool,"max_retries":int,"health_eject":bool,"sticky_sessions":bool}',
    )
    status: Mapped[str] = mapped_column(
        Enum(
            "pending",
            "scheduling",
            "deploying",
            "running",
            "degraded",
            "scaling",
            "stopped",
            "failed",
            "deleting",
            name="deployment_status",
        ),
        nullable=False,
        server_default=text("'pending'::deployment_status"),
    )
    desired_running: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        comment="FALSE after an explicit stop; rules and scheduler do not start instances for it.",
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    origin: Mapped[str] = mapped_column(
        Enum("managed", "external", name="deployment_origin"),
        nullable=False,
        server_default=text("'managed'::deployment_origin"),
        comment="managed = GPU Deck runs it; external = an observed model server it did not start (read-only: never scheduled, reconciled, swapped, routed, benchmarked or sent commands).",
    )
    description: Mapped[Optional[str]] = mapped_column(String(2048))
    model_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    model_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    engine_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    cluster_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    priority_override: Mapped[Optional[str]] = mapped_column(
        Enum("always_on", "normal", "evictable", name="model_priority")
    )
    slice_vram_bytes: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        comment="When set, each replica gets a GPU slice of this VRAM (and slice_compute_pct) instead of whole devices.",
    )
    slice_compute_pct: Mapped[Optional[int]] = mapped_column(SmallInteger)
    preset: Mapped[Optional[str]] = mapped_column(
        Enum("low_latency", "high_throughput", name="deployment_preset")
    )
    tool_parser: Mapped[Optional[str]] = mapped_column(String(100))
    context_size: Mapped[Optional[int]] = mapped_column(Integer)
    status_message: Mapped[Optional[str]] = mapped_column(String(2048))
    last_request_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    external: Mapped[Optional[object]] = mapped_column(
        JSONB,
        comment='origin = external only: {"node_id","name","engine","endpoint","models":[],"version","sources":[],"pid","device_ids":[],"health","message","last_seen_at","stale_since"} from inventory.observed_engines.',
    )

    cluster: Mapped[Optional["Clusters"]] = relationship("Clusters", back_populates="deployments")
    admin_users: Mapped[Optional["AdminUsers"]] = relationship(
        "AdminUsers", back_populates="deployments"
    )
    engine_version: Mapped[Optional["EngineVersions"]] = relationship(
        "EngineVersions", back_populates="deployments"
    )
    model: Mapped[Optional["Models"]] = relationship("Models", back_populates="deployments")
    model_version: Mapped[Optional["ModelVersions"]] = relationship(
        "ModelVersions", back_populates="deployments"
    )
    benchmarks: Mapped[list["Benchmarks"]] = relationship("Benchmarks", back_populates="deployment")
    instances: Mapped[list["Instances"]] = relationship("Instances", back_populates="deployment")
    layout_items: Mapped[list["LayoutItems"]] = relationship(
        "LayoutItems", back_populates="deployment"
    )
    device_slices: Mapped[list["DeviceSlices"]] = relationship(
        "DeviceSlices", back_populates="deployment"
    )
    swap_operations: Mapped[list["SwapOperations"]] = relationship(
        "SwapOperations", back_populates="deployment"
    )


class ModelFiles(Base):
    __tablename__ = "model_files"
    __table_args__ = (
        ForeignKeyConstraint(
            ["model_version_id"],
            ["model_versions.id"],
            ondelete="CASCADE",
            name="model_files_model_version_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="model_files_pkey"),
        Index("idx_model_files_format", "format"),
        Index("idx_model_files_version_path_unique", "model_version_id", "path", unique=True),
        {
            "comment": "Files of a model version. path is relative to the repo/snapshot "
            "root. sha256 is recorded at registration (HF LFS sha256 or "
            "computed for local files) and verified on the node after "
            "download."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    model_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    format: Mapped[str] = mapped_column(
        Enum("gguf", "safetensors", "mlx", "pickle", "other", name="model_file_format"),
        nullable=False,
        server_default=text("'other'::model_file_format"),
    )
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    sha256: Mapped[Optional[str]] = mapped_column(String(64))
    shard_index: Mapped[Optional[int]] = mapped_column(Integer)
    shard_count: Mapped[Optional[int]] = mapped_column(Integer)

    model_version: Mapped["ModelVersions"] = relationship(
        "ModelVersions", back_populates="model_files"
    )
    node_model_cache: Mapped[list["NodeModelCache"]] = relationship(
        "NodeModelCache", back_populates="model_file"
    )


class Nodes(Base):
    __tablename__ = "nodes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["cluster_id"], ["clusters.id"], ondelete="RESTRICT", name="nodes_cluster_id_fkey"
        ),
        ForeignKeyConstraint(
            ["price_plan_id"],
            ["price_plans.id"],
            ondelete="SET NULL",
            name="nodes_price_plan_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="nodes_pkey"),
        Index("idx_nodes_cluster_id", "cluster_id", postgresql_where="(deleted_at IS NULL)"),
        Index(
            "idx_nodes_cluster_name_unique",
            "cluster_id",
            postgresql_where="(deleted_at IS NULL)",
            unique=True,
        ),
        Index(
            "idx_nodes_labels",
            "labels",
            postgresql_using="gin",
            postgresql_where="(deleted_at IS NULL)",
        ),
        Index(
            "idx_nodes_name_trgm",
            "name",
            postgresql_ops={"name": "gin_trgm_ops"},
            postgresql_using="gin",
            postgresql_where="(deleted_at IS NULL)",
        ),
        Index("idx_nodes_status", "status", postgresql_where="(deleted_at IS NULL)"),
        {
            "comment": "A GPU machine running the Rust node agent. Joined with a one-time "
            "token, identified by its client certificate afterwards."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    cluster_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    proxy_port: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("9443"))
    status: Mapped[str] = mapped_column(
        Enum(
            "pending",
            "online",
            "offline",
            "unhealthy",
            "draining",
            "drained",
            "removed",
            name="node_status",
        ),
        nullable=False,
        server_default=text("'pending'::node_status"),
    )
    labels: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    capabilities: Mapped[object] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment='From hello: {"engines":[{"name","versions":[...]}], "runtimes":["native","container"], "metal":true, "cuda":"12.8", ...}.',
    )
    disks: Mapped[object] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment='Inventory: [{"mount":"/var/lib/gpudeck","total_bytes":..,"free_bytes":..,"fs":"apfs"}].',
    )
    metadata_: Mapped[object] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    hostname: Mapped[Optional[str]] = mapped_column(String(255))
    ip_address: Mapped[Optional[object]] = mapped_column(INET)
    proxy_address: Mapped[Optional[str]] = mapped_column(
        String(255),
        comment="Host (IP or DNS) the API layer dials for the agent mTLS proxy; defaults to the address seen at join. Port in proxy_port.",
    )
    os: Mapped[Optional[str]] = mapped_column(Enum("linux", "macos", name="node_os"))
    os_version: Mapped[Optional[str]] = mapped_column(String(100))
    arch: Mapped[Optional[str]] = mapped_column(Enum("x86_64", "aarch64", name="node_arch"))
    kernel_version: Mapped[Optional[str]] = mapped_column(String(100))
    agent_version: Mapped[Optional[str]] = mapped_column(String(50))
    protocol_version: Mapped[Optional[int]] = mapped_column(SmallInteger)
    status_message: Mapped[Optional[str]] = mapped_column(String(1024))
    cert_fingerprint: Mapped[Optional[str]] = mapped_column(
        String(64),
        comment="SHA-256 hex of the currently active client certificate (mirror of node_certificates).",
    )
    cpu_cores: Mapped[Optional[int]] = mapped_column(Integer)
    memory_total_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    cache_dir: Mapped[Optional[str]] = mapped_column(String(1024))
    cache_limit_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    last_heartbeat_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    last_inventory_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    connected_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    disconnected_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    drain_requested_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    drain_reason: Mapped[Optional[str]] = mapped_column(String(512))
    drained_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    price_plan_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, comment="Overrides clusters.price_plan_id for billing when set."
    )
    kubernetes_node_name: Mapped[Optional[str]] = mapped_column(String(255))
    cloud_instance_id: Mapped[Optional[str]] = mapped_column(String(255))
    cloud_instance_type: Mapped[Optional[str]] = mapped_column(String(100))
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    cluster: Mapped["Clusters"] = relationship("Clusters", back_populates="nodes")
    price_plan: Mapped[Optional["PricePlans"]] = relationship("PricePlans", back_populates="nodes")
    devices: Mapped[list["Devices"]] = relationship("Devices", back_populates="node")
    engine_compat_checks: Mapped[list["EngineCompatChecks"]] = relationship(
        "EngineCompatChecks", back_populates="node"
    )
    gpu_instances: Mapped[list["GpuInstances"]] = relationship(
        "GpuInstances", back_populates="node"
    )
    instances: Mapped[list["Instances"]] = relationship("Instances", back_populates="node")
    join_tokens: Mapped[list["JoinTokens"]] = relationship(
        "JoinTokens", back_populates="used_by_node"
    )
    layout_items: Mapped[list["LayoutItems"]] = relationship("LayoutItems", back_populates="node")
    node_certificates: Mapped[list["NodeCertificates"]] = relationship(
        "NodeCertificates", back_populates="node"
    )
    node_model_cache: Mapped[list["NodeModelCache"]] = relationship(
        "NodeModelCache", back_populates="node"
    )
    benchmark_runs: Mapped[list["BenchmarkRuns"]] = relationship(
        "BenchmarkRuns", back_populates="node"
    )
    cloud_instances: Mapped[list["CloudInstances"]] = relationship(
        "CloudInstances", back_populates="node"
    )
    swap_operations: Mapped[list["SwapOperations"]] = relationship(
        "SwapOperations", back_populates="node"
    )
    agent_commands: Mapped[list["AgentCommands"]] = relationship(
        "AgentCommands", back_populates="node"
    )


class Benchmarks(Base):
    __tablename__ = "benchmarks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["created_by"],
            ["admin_users.id"],
            ondelete="SET NULL",
            name="benchmarks_created_by_fkey",
        ),
        ForeignKeyConstraint(
            ["deployment_id"],
            ["deployments.id"],
            ondelete="CASCADE",
            name="benchmarks_deployment_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="benchmarks_pkey"),
        Index(
            "idx_benchmarks_deployment_id", "deployment_id", postgresql_where="(deleted_at IS NULL)"
        ),
        Index("idx_benchmarks_name_unique", postgresql_where="(deleted_at IS NULL)", unique=True),
        {
            "comment": "Benchmark definition: deployment, target SLO "
            '{"ttft_ms_p95":500,"e2e_ms_p95":5000,"min_generation_tps":20}, '
            "concurrency sweep and workload. auto_tune asks the run to also "
            "try launch-flag variants and recommend the best."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    deployment_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    target_slo: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    concurrency_levels: Mapped[list[int]] = mapped_column(
        ARRAY(Integer()), nullable=False, server_default=text("'{1,2,4,8,16}'::integer[]")
    )
    workload: Mapped[object] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text(
            '\'{"dataset": "synthetic", "input_tokens": 512, "output_tokens": 128, "requests_per_level": 32}\'::jsonb'
        ),
    )
    duration_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("60")
    )
    auto_tune: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    description: Mapped[Optional[str]] = mapped_column(String(2048))
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    admin_users: Mapped[Optional["AdminUsers"]] = relationship(
        "AdminUsers", back_populates="benchmarks"
    )
    deployment: Mapped["Deployments"] = relationship("Deployments", back_populates="benchmarks")
    benchmark_runs: Mapped[list["BenchmarkRuns"]] = relationship(
        "BenchmarkRuns", back_populates="benchmark"
    )


class Devices(Base):
    __tablename__ = "devices"
    __table_args__ = (
        CheckConstraint("index >= 0", name="devices_index_nonneg"),
        ForeignKeyConstraint(
            ["node_id"], ["nodes.id"], ondelete="CASCADE", name="devices_node_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="devices_pkey"),
        Index("idx_devices_health", "health", postgresql_where="(deleted_at IS NULL)"),
        Index("idx_devices_node_id", "node_id", postgresql_where="(deleted_at IS NULL)"),
        Index(
            "idx_devices_node_index_unique",
            "node_id",
            "index",
            postgresql_where="(deleted_at IS NULL)",
            unique=True,
        ),
        Index("idx_devices_vendor", "vendor", postgresql_where="(deleted_at IS NULL)"),
        {
            "comment": "Accelerators reported by the node agent inventory in the common "
            "Device format (NVIDIA, AMD, Hygon, MetaX, Moore Threads, T-Head, "
            "Apple Silicon)."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    vendor: Mapped[str] = mapped_column(
        Enum(
            "nvidia",
            "amd",
            "hygon",
            "metax",
            "moorethreads",
            "thead",
            "apple",
            name="device_vendor",
        ),
        nullable=False,
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    vram_total_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    is_unified_memory: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment="TRUE for Apple Silicon: vram_total_bytes is the Metal wired limit, shared with system RAM.",
    )
    health: Mapped[str] = mapped_column(
        Enum("healthy", "degraded", "unhealthy", "unknown", name="device_health"),
        nullable=False,
        server_default=text("'unknown'::device_health"),
    )
    labels: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    extra: Mapped[object] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    uuid: Mapped[Optional[str]] = mapped_column(
        String(128),
        comment='Vendor device UUID (NVML uuid, amd-smi unique id, Apple: "apple-<chip>-<serial>"). Stable across reboots when the vendor provides one.',
    )
    driver_version: Mapped[Optional[str]] = mapped_column(String(100))
    runtime_version: Mapped[Optional[str]] = mapped_column(
        String(100), comment="CUDA / ROCm / MUSA / Metal version string."
    )
    compute_capability: Mapped[Optional[str]] = mapped_column(
        String(32), comment='NVIDIA "8.9", AMD gfx target "gfx942", Apple GPU family "metal3".'
    )
    pci_bus_id: Mapped[Optional[str]] = mapped_column(String(64))
    numa_node: Mapped[Optional[int]] = mapped_column(Integer)
    max_power_watts: Mapped[Optional[float]] = mapped_column(REAL)
    core_count: Mapped[Optional[int]] = mapped_column(Integer)
    health_reason: Mapped[Optional[str]] = mapped_column(String(1024))
    last_seen_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    node: Mapped["Nodes"] = relationship("Nodes", back_populates="devices")
    device_slices: Mapped[list["DeviceSlices"]] = relationship(
        "DeviceSlices", back_populates="device"
    )


class EngineCompatChecks(Base):
    __tablename__ = "engine_compat_checks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["engine_version_id"],
            ["engine_versions.id"],
            ondelete="CASCADE",
            name="engine_compat_checks_engine_version_id_fkey",
        ),
        ForeignKeyConstraint(
            ["model_version_id"],
            ["model_versions.id"],
            ondelete="CASCADE",
            name="engine_compat_checks_model_version_id_fkey",
        ),
        ForeignKeyConstraint(
            ["node_id"], ["nodes.id"], ondelete="CASCADE", name="engine_compat_checks_node_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="engine_compat_checks_pkey"),
        Index(
            "idx_engine_compat_checks_lookup", "model_version_id", "engine_version_id", "checked_at"
        ),
        Index("idx_engine_compat_checks_node", "node_id"),
        {
            "comment": 'Result of "can model version X run on engine version Y (on node Z '
            '/ vendor V)". score ranks candidates for auto-pick; reasons is a '
            "list of {code, message, severity}."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    model_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    engine_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    result: Mapped[str] = mapped_column(
        Enum("compatible", "incompatible", "unknown", name="compat_result"),
        nullable=False,
        server_default=text("'unknown'::compat_result"),
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    reasons: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    checked_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    node_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    device_vendor: Mapped[Optional[str]] = mapped_column(
        Enum(
            "nvidia",
            "amd",
            "hygon",
            "metax",
            "moorethreads",
            "thead",
            "apple",
            name="device_vendor",
        )
    )
    estimated_vram_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)

    engine_version: Mapped["EngineVersions"] = relationship(
        "EngineVersions", back_populates="engine_compat_checks"
    )
    model_version: Mapped["ModelVersions"] = relationship(
        "ModelVersions", back_populates="engine_compat_checks"
    )
    node: Mapped[Optional["Nodes"]] = relationship("Nodes", back_populates="engine_compat_checks")


class GpuInstances(Base):
    __tablename__ = "gpu_instances"
    __table_args__ = (
        CheckConstraint(
            "container_port >= 1 AND container_port <= 65535",
            name="gpu_instances_container_port_range",
        ),
        CheckConstraint("gpu_count >= 0 AND gpu_count <= 64", name="gpu_instances_gpu_count_range"),
        CheckConstraint(
            "idle_timeout_seconds IS NULL OR idle_timeout_seconds >= 60",
            name="gpu_instances_idle_min",
        ),
        CheckConstraint(
            "slice_compute_pct IS NULL OR slice_compute_pct >= 1 AND slice_compute_pct <= 100",
            name="gpu_instances_slice_compute_range",
        ),
        CheckConstraint(
            "slice_vram_bytes IS NULL OR slice_vram_bytes > 0", name="gpu_instances_slice_vram_pos"
        ),
        CheckConstraint("ttl_seconds IS NULL OR ttl_seconds >= 60", name="gpu_instances_ttl_min"),
        ForeignKeyConstraint(
            ["cluster_id"],
            ["clusters.id"],
            ondelete="SET NULL",
            name="gpu_instances_cluster_id_fkey",
        ),
        ForeignKeyConstraint(
            ["node_id"], ["nodes.id"], ondelete="SET NULL", name="gpu_instances_node_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="gpu_instances_pkey"),
        Index(
            "idx_gpu_instances_name_unique", postgresql_where="(deleted_at IS NULL)", unique=True
        ),
        Index("idx_gpu_instances_node_id", "node_id", postgresql_where="(deleted_at IS NULL)"),
        Index("idx_gpu_instances_state", "state", postgresql_where="(deleted_at IS NULL)"),
        {
            "comment": "SSH GPU instances on demand: a container with sshd and GPU(s) "
            "attached, run by the node agent (start_ssh_instance). Lifecycle "
            "requested -> placing -> starting -> running -> stopping -> "
            "stopped | error. Device reservations live in device_slices "
            "(gpu_instance_id). Billed as GPU minutes through usage_events "
            "(endpoint gpu_instance)."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(63), nullable=False)
    state: Mapped[str] = mapped_column(
        Enum(
            "requested",
            "placing",
            "starting",
            "running",
            "stopping",
            "stopped",
            "error",
            name="gpu_instance_state",
        ),
        nullable=False,
        server_default=text("'requested'::gpu_instance_state"),
    )
    health: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'unknown'::character varying")
    )
    image: Mapped[str] = mapped_column(String(512), nullable=False)
    gpu_count: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default=text("1"),
        comment="Whole GPUs (exclusive). 1 with slice_vram_bytes set = one GPU slice. 0 = no GPU (CPU-only container).",
    )
    node_selector: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    device_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid()), nullable=False, server_default=text("'{}'::uuid[]")
    )
    limits: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    authorized_keys: Mapped[object] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment='[{"key":"ssh-ed25519 AAAA... comment","type":"ssh-ed25519","fingerprint":"SHA256:...","comment":"..."}]',
    )
    ssh_user: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'gpudeck'::character varying")
    )
    container_port: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("22"))
    allowed_source_cidrs: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    env: Mapped[object] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    volumes: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    labels: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    host_keys: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    gpu_attached: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    warnings: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    active_connections: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    connections_total: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    state_changed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    gpu_seconds_total: Mapped[decimal.Decimal] = mapped_column(
        Numeric(14, 3), nullable=False, server_default=text("0")
    )
    metadata_: Mapped[object] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    description: Mapped[Optional[str]] = mapped_column(String(2048))
    cluster_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    node_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    state_message: Mapped[Optional[str]] = mapped_column(Text)
    slice_vram_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    slice_compute_pct: Mapped[Optional[int]] = mapped_column(SmallInteger)
    device_vendor: Mapped[Optional[str]] = mapped_column(String(32))
    requested_ssh_port: Mapped[Optional[int]] = mapped_column(Integer)
    ttl_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    idle_timeout_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    queue_until: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        comment="When set, a request that does not fit stays requested and is retried until this time.",
    )
    host_address: Mapped[Optional[str]] = mapped_column(
        String(255),
        comment="Address the agent forwarder listens on (node private address); clients connect to host_address:host_port.",
    )
    host_port: Mapped[Optional[int]] = mapped_column(Integer)
    host_key_fingerprint: Mapped[Optional[str]] = mapped_column(String(100))
    container_id: Mapped[Optional[str]] = mapped_column(String(128))
    start_command_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    stop_command_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    stop_reason: Mapped[Optional[str]] = mapped_column(String(64))
    last_connection_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    running_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    stopped_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    metered_until: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), comment="GPU time up to this instant has been written to usage_events."
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    cluster: Mapped[Optional["Clusters"]] = relationship("Clusters", back_populates="gpu_instances")
    node: Mapped[Optional["Nodes"]] = relationship("Nodes", back_populates="gpu_instances")
    device_slices: Mapped[list["DeviceSlices"]] = relationship(
        "DeviceSlices", back_populates="gpu_instance"
    )


class Instances(Base):
    __tablename__ = "instances"
    __table_args__ = (
        CheckConstraint(
            "pd_role IS NULL OR (pd_role::text = ANY (ARRAY['prefill'::character varying, 'decode'::character varying]::text[]))",
            name="instances_pd_role_valid",
        ),
        CheckConstraint(
            "traffic_weight >= 0 AND traffic_weight <= 1000", name="instances_traffic_weight_range"
        ),
        ForeignKeyConstraint(
            ["deployment_id"],
            ["deployments.id"],
            ondelete="CASCADE",
            name="instances_deployment_id_fkey",
        ),
        ForeignKeyConstraint(
            ["node_id"], ["nodes.id"], ondelete="CASCADE", name="instances_node_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="instances_pkey"),
        Index("idx_instances_deployment_id", "deployment_id"),
        Index("idx_instances_device_ids", "device_ids", postgresql_using="gin"),
        Index(
            "idx_instances_external_node",
            "node_id",
            postgresql_where="(origin = 'external'::deployment_origin)",
        ),
        Index("idx_instances_node_id", "node_id"),
        Index(
            "idx_instances_node_port_model_unique",
            "node_id",
            "port",
            postgresql_where="((state <> 'stopped'::instance_state) AND (port IS NOT NULL))",
            unique=True,
        ),
        Index("idx_instances_state", "state"),
        {
            "comment": "One running (or desired) replica of a deployment on one node. "
            "device_ids are devices.id (array; integrity enforced by the "
            "scheduler, GIN-indexed for reverse lookups). Stopped instances "
            "are kept for history and pruned by the jobs process."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    deployment_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    device_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid()), nullable=False, server_default=text("'{}'::uuid[]")
    )
    state: Mapped[str] = mapped_column(
        Enum(
            "pending",
            "loading",
            "ready",
            "draining",
            "unloading",
            "stopped",
            "error",
            name="instance_state",
        ),
        nullable=False,
        server_default=text("'pending'::instance_state"),
    )
    health: Mapped[str] = mapped_column(
        Enum("healthy", "unhealthy", "unknown", name="instance_health"),
        nullable=False,
        server_default=text("'unknown'::instance_health"),
    )
    traffic_weight: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default=text("100"),
        comment="0 = drained from load balancing; 100 = default; up to 1000 for weighted routing.",
    )
    resources: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    restart_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    state_changed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    origin: Mapped[str] = mapped_column(
        Enum("managed", "external", name="deployment_origin"),
        nullable=False,
        server_default=text("'managed'::deployment_origin"),
        comment="Copy of deployments.origin so instance-level queries can skip external instances without a join.",
    )
    port: Mapped[Optional[int]] = mapped_column(
        Integer,
        comment="Localhost port of the engine on the node. In llama.cpp router mode all instances of a router share the port and differ by served_model_name.",
    )
    served_model_name: Mapped[Optional[str]] = mapped_column(
        String(255),
        comment='Model id the engine itself answers to (router model name, vLLM --served-model-name). The API layer rewrites the request "model" field to this value.',
    )
    engine_process_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        comment="Groups instances that share one engine process (llama.cpp router). NULL = dedicated process.",
    )
    state_message: Mapped[Optional[str]] = mapped_column(String(2048))
    health_message: Mapped[Optional[str]] = mapped_column(String(1024))
    pd_role: Mapped[Optional[str]] = mapped_column(String(16))
    pid: Mapped[Optional[int]] = mapped_column(Integer)
    container_id: Mapped[Optional[str]] = mapped_column(String(128))
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    ready_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    stopped_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    last_health_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    last_request_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    deployment: Mapped["Deployments"] = relationship("Deployments", back_populates="instances")
    node: Mapped["Nodes"] = relationship("Nodes", back_populates="instances")
    benchmark_runs: Mapped[list["BenchmarkRuns"]] = relationship(
        "BenchmarkRuns", back_populates="instance"
    )
    device_slices: Mapped[list["DeviceSlices"]] = relationship(
        "DeviceSlices", back_populates="instance"
    )
    swap_operations_from_instance: Mapped[list["SwapOperations"]] = relationship(
        "SwapOperations",
        foreign_keys="[SwapOperations.from_instance_id]",
        back_populates="from_instance",
    )
    swap_operations_to_instance: Mapped[list["SwapOperations"]] = relationship(
        "SwapOperations",
        foreign_keys="[SwapOperations.to_instance_id]",
        back_populates="to_instance",
    )
    agent_commands: Mapped[list["AgentCommands"]] = relationship(
        "AgentCommands", back_populates="instance"
    )


class JoinTokens(Base):
    __tablename__ = "join_tokens"
    __table_args__ = (
        CheckConstraint("max_uses >= 1 AND max_uses <= 100000", name="join_tokens_max_uses_range"),
        CheckConstraint(
            "use_count >= 0 AND use_count <= max_uses", name="join_tokens_use_count_range"
        ),
        ForeignKeyConstraint(
            ["cluster_id"], ["clusters.id"], ondelete="CASCADE", name="join_tokens_cluster_id_fkey"
        ),
        ForeignKeyConstraint(
            ["created_by"],
            ["admin_users.id"],
            ondelete="SET NULL",
            name="join_tokens_created_by_fkey",
        ),
        ForeignKeyConstraint(
            ["used_by_node_id"],
            ["nodes.id"],
            ondelete="SET NULL",
            name="join_tokens_used_by_node_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="join_tokens_pkey"),
        UniqueConstraint("token_hash", name="join_tokens_token_hash_unique"),
        Index(
            "idx_join_tokens_active",
            "expires_at",
            postgresql_where="((used_at IS NULL) AND (revoked_at IS NULL))",
        ),
        Index("idx_join_tokens_cluster_id", "cluster_id"),
        {
            "comment": 'Machine join (enrolment) tokens ("gdj_" + 40 hex). Single use by '
            "default; max_uses > 1 enrols several machines (DaemonSets, node "
            "pools) until expiry or revocation. Plaintext shown once; SHA-256 "
            "stored. Consumed at POST /agent/v1/join."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    cluster_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    labels: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    max_uses: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
        comment="Machines this token may enrol (1 = one-time token)",
    )
    use_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
        comment="Successful joins so far; used_at is set when use_count reaches max_uses",
    )
    node_name_hint: Mapped[Optional[str]] = mapped_column(String(255))
    used_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    used_by_node_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid, comment="Node created by the most recent join"
    )
    revoked_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    last_used_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), comment="Time of the most recent join with this token"
    )

    cluster: Mapped["Clusters"] = relationship("Clusters", back_populates="join_tokens")
    admin_users: Mapped[Optional["AdminUsers"]] = relationship(
        "AdminUsers", back_populates="join_tokens"
    )
    used_by_node: Mapped[Optional["Nodes"]] = relationship("Nodes", back_populates="join_tokens")
    cloud_instances: Mapped[list["CloudInstances"]] = relationship(
        "CloudInstances", back_populates="join_token"
    )


class LayoutItems(Base):
    __tablename__ = "layout_items"
    __table_args__ = (
        CheckConstraint("replicas >= 0", name="layout_items_replicas_nonneg"),
        ForeignKeyConstraint(
            ["deployment_id"],
            ["deployments.id"],
            ondelete="CASCADE",
            name="layout_items_deployment_id_fkey",
        ),
        ForeignKeyConstraint(
            ["layout_id"], ["layouts.id"], ondelete="CASCADE", name="layout_items_layout_id_fkey"
        ),
        ForeignKeyConstraint(
            ["node_id"], ["nodes.id"], ondelete="CASCADE", name="layout_items_node_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="layout_items_pkey"),
        Index("idx_layout_items_layout_id", "layout_id"),
        Index("idx_layout_items_layout_position_unique", "layout_id", "position", unique=True),
        {
            "comment": "Which deployment runs where in a layout. node_id NULL + empty "
            "device_ids = let the scheduler place it. replicas 0 = deployment "
            "is unloaded in this layout. Deployments not listed are left "
            "untouched (see rules/layout.schema.json)."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    layout_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    deployment_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    device_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid()), nullable=False, server_default=text("'{}'::uuid[]")
    )
    replicas: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    traffic_weight: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("100")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    node_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    slice_vram_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    slice_compute_pct: Mapped[Optional[int]] = mapped_column(SmallInteger)

    deployment: Mapped["Deployments"] = relationship("Deployments", back_populates="layout_items")
    layout: Mapped["Layouts"] = relationship("Layouts", back_populates="layout_items")
    node: Mapped[Optional["Nodes"]] = relationship("Nodes", back_populates="layout_items")


class NodeCertificates(Base):
    __tablename__ = "node_certificates"
    __table_args__ = (
        CheckConstraint(
            "kind <> 'node_agent'::certificate_kind OR node_id IS NOT NULL",
            name="node_certificates_node_required",
        ),
        ForeignKeyConstraint(
            ["node_id"], ["nodes.id"], ondelete="CASCADE", name="node_certificates_node_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="node_certificates_pkey"),
        UniqueConstraint("fingerprint_sha256", name="node_certificates_fingerprint_unique"),
        UniqueConstraint("serial", name="node_certificates_serial_unique"),
        Index("idx_node_certificates_node_id", "node_id"),
        Index(
            "idx_node_certificates_not_after", "not_after", postgresql_where="(revoked_at IS NULL)"
        ),
        Index(
            "idx_node_certificates_revoked",
            "revoked_at",
            postgresql_where="(revoked_at IS NOT NULL)",
        ),
        {
            "comment": "Certificates issued by the internal CA (key in vault, name "
            "internal-ca). node_agent certs have CN=<node_id>, "
            "O=gpudeck-nodes; service certs (api_layer, agent_hub, "
            "control_plane) have O=gpudeck-services. Revoked rows form the CRL "
            "pushed to agents (config_push.crl) and the API layer."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    kind: Mapped[str] = mapped_column(
        Enum("node_agent", "api_layer", "agent_hub", "control_plane", name="certificate_kind"),
        nullable=False,
        server_default=text("'node_agent'::certificate_kind"),
    )
    serial: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    certificate_pem: Mapped[str] = mapped_column(Text, nullable=False)
    not_before: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    not_after: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    issued_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    node_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    revoked_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    revocation_reason: Mapped[Optional[str]] = mapped_column(String(255))

    node: Mapped[Optional["Nodes"]] = relationship("Nodes", back_populates="node_certificates")


class NodeModelCache(Base):
    __tablename__ = "node_model_cache"
    __table_args__ = (
        ForeignKeyConstraint(
            ["model_file_id"],
            ["model_files.id"],
            ondelete="CASCADE",
            name="node_model_cache_model_file_id_fkey",
        ),
        ForeignKeyConstraint(
            ["model_version_id"],
            ["model_versions.id"],
            ondelete="CASCADE",
            name="node_model_cache_model_version_id_fkey",
        ),
        ForeignKeyConstraint(
            ["node_id"], ["nodes.id"], ondelete="CASCADE", name="node_model_cache_node_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="node_model_cache_pkey"),
        Index("idx_node_model_cache_last_used_at", "last_used_at", postgresql_where="(NOT pinned)"),
        Index("idx_node_model_cache_node_file_unique", "node_id", "model_file_id", unique=True),
        Index("idx_node_model_cache_node_state", "node_id", "state"),
        Index("idx_node_model_cache_version", "model_version_id"),
        {
            "comment": "Warm model cache: which model files live on which node disk, "
            "their state, size and last use. pinned rows are never evicted by "
            "the LRU cleanup (set by prefetch for upcoming layouts and by "
            "always_on models)."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    model_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    model_file_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    local_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    state: Mapped[str] = mapped_column(
        Enum("queued", "downloading", "ready", "evicting", "evicted", "error", name="cache_state"),
        nullable=False,
        server_default=text("'queued'::cache_state"),
    )
    progress_pct: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text("0"))
    sha256_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    pinned_reason: Mapped[Optional[str]] = mapped_column(String(255))
    error_message: Mapped[Optional[str]] = mapped_column(String(2048))
    last_used_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    model_file: Mapped["ModelFiles"] = relationship("ModelFiles", back_populates="node_model_cache")
    model_version: Mapped["ModelVersions"] = relationship(
        "ModelVersions", back_populates="node_model_cache"
    )
    node: Mapped["Nodes"] = relationship("Nodes", back_populates="node_model_cache")


class BenchmarkRuns(Base):
    __tablename__ = "benchmark_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["benchmark_id"],
            ["benchmarks.id"],
            ondelete="CASCADE",
            name="benchmark_runs_benchmark_id_fkey",
        ),
        ForeignKeyConstraint(
            ["created_by"],
            ["admin_users.id"],
            ondelete="SET NULL",
            name="benchmark_runs_created_by_fkey",
        ),
        ForeignKeyConstraint(
            ["instance_id"],
            ["instances.id"],
            ondelete="SET NULL",
            name="benchmark_runs_instance_id_fkey",
        ),
        ForeignKeyConstraint(
            ["node_id"], ["nodes.id"], ondelete="SET NULL", name="benchmark_runs_node_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="benchmark_runs_pkey"),
        Index("idx_benchmark_runs_benchmark_id", "benchmark_id", "created_at"),
        Index(
            "idx_benchmark_runs_status",
            "status",
            postgresql_where="(status = ANY (ARRAY['pending'::benchmark_run_status, 'running'::benchmark_run_status]))",
        ),
        {
            "comment": "One execution of a benchmark (run_benchmark agent command). "
            "results = "
            '[{"concurrency":n,"requests":n,"rps":x,"generation_tps":x,"ttft_ms":{"p50","p95","p99"},"e2e_ms":{...},"errors":n}]; '
            "recommendation = "
            '{"concurrency":n,"launch_flags":{...},"replicas":n,"reason":"..."}.'
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    benchmark_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("pending", "running", "completed", "failed", "cancelled", name="benchmark_run_status"),
        nullable=False,
        server_default=text("'pending'::benchmark_run_status"),
    )
    progress_pct: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text("0"))
    results: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    recommendation: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    instance_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    node_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    best_concurrency: Mapped[Optional[int]] = mapped_column(Integer)
    slo_met: Mapped[Optional[bool]] = mapped_column(Boolean)
    error_message: Mapped[Optional[str]] = mapped_column(String(2048))
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)

    benchmark: Mapped["Benchmarks"] = relationship("Benchmarks", back_populates="benchmark_runs")
    admin_users: Mapped[Optional["AdminUsers"]] = relationship(
        "AdminUsers", back_populates="benchmark_runs"
    )
    instance: Mapped[Optional["Instances"]] = relationship(
        "Instances", back_populates="benchmark_runs"
    )
    node: Mapped[Optional["Nodes"]] = relationship("Nodes", back_populates="benchmark_runs")


class CloudInstances(Base):
    __tablename__ = "cloud_instances"
    __table_args__ = (
        CheckConstraint(
            "auto_terminate_idle_minutes IS NULL OR auto_terminate_idle_minutes > 0",
            name="cloud_instances_idle_positive",
        ),
        CheckConstraint("disk_gb > 0", name="cloud_instances_disk_positive"),
        CheckConstraint("gpu_count >= 0", name="cloud_instances_gpu_count_nonneg"),
        CheckConstraint(
            "pending_action IS NULL OR (pending_action::text = ANY (ARRAY['stop'::character varying, 'terminate'::character varying]::text[]))",
            name="cloud_instances_pending_action",
        ),
        ForeignKeyConstraint(
            ["cluster_id"],
            ["clusters.id"],
            ondelete="RESTRICT",
            name="cloud_instances_cluster_id_fkey",
        ),
        ForeignKeyConstraint(
            ["created_by"],
            ["admin_users.id"],
            ondelete="SET NULL",
            name="cloud_instances_created_by_fkey",
        ),
        ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
            ondelete="SET NULL",
            name="cloud_instances_credential_id_fkey",
        ),
        ForeignKeyConstraint(
            ["join_token_id"],
            ["join_tokens.id"],
            ondelete="SET NULL",
            name="cloud_instances_join_token_id_fkey",
        ),
        ForeignKeyConstraint(
            ["node_id"], ["nodes.id"], ondelete="SET NULL", name="cloud_instances_node_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="cloud_instances_pkey"),
        Index(
            "idx_cloud_instances_cluster_id", "cluster_id", postgresql_where="(deleted_at IS NULL)"
        ),
        Index(
            "idx_cloud_instances_cluster_name_unique",
            "cluster_id",
            postgresql_where="((deleted_at IS NULL) AND (status <> 'terminated'::cloud_instance_status))",
            unique=True,
        ),
        Index(
            "idx_cloud_instances_join_token",
            "join_token_id",
            postgresql_where="(join_token_id IS NOT NULL)",
        ),
        Index("idx_cloud_instances_node_id", "node_id", postgresql_where="(node_id IS NOT NULL)"),
        Index(
            "idx_cloud_instances_provider_instance_unique",
            "provider",
            "provider_instance_id",
            postgresql_where="((provider_instance_id IS NOT NULL) AND (deleted_at IS NULL))",
            unique=True,
        ),
        Index("idx_cloud_instances_status", "status", postgresql_where="(deleted_at IS NULL)"),
        {
            "comment": "Cloud GPU VMs created by GPU Deck (AWS/Azure/GCP/DigitalOcean). "
            "Credentials come from clusters.credential_id (vault). cloud-init "
            "installs the node agent from the hub download endpoint and joins "
            "with join_token_id; node_id is set once the agent joined. "
            "Everything created on the provider is tagged gpu-deck:* (GCP "
            "labels gpu-deck-*)."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    cluster_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    provider: Mapped[str] = mapped_column(
        Enum("aws", "azure", "gcp", "digitalocean", name="cloud_provider"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    region: Mapped[str] = mapped_column(String(100), nullable=False)
    instance_type: Mapped[str] = mapped_column(String(100), nullable=False)
    disk_gb: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("200"))
    status: Mapped[str] = mapped_column(
        Enum(
            "pending",
            "provisioning",
            "booting",
            "running",
            "draining",
            "stopping",
            "stopped",
            "starting",
            "terminating",
            "terminated",
            "error",
            name="cloud_instance_status",
        ),
        nullable=False,
        server_default=text("'pending'::cloud_instance_status"),
        comment="pending (row only) -> provisioning (create call accepted) -> booting (VM up, agent not joined) -> running (node joined) -> draining (node drain before stop/terminate) -> stopping/stopped/starting -> terminating -> terminated; error keeps last_error.",
    )
    gpu_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    labels: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    tags: Mapped[object] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    options: Mapped[object] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="Provider launch options used (subnet, security groups, resource group, project, vpc, ssh keys); never secrets.",
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    node_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    credential_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    join_token_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    zone: Mapped[Optional[str]] = mapped_column(String(100))
    image: Mapped[Optional[str]] = mapped_column(String(1024))
    provider_instance_id: Mapped[Optional[str]] = mapped_column(String(255))
    provider_state: Mapped[Optional[str]] = mapped_column(String(64))
    status_message: Mapped[Optional[str]] = mapped_column(String(2048))
    pending_action: Mapped[Optional[str]] = mapped_column(
        String(32), comment="stop | terminate: what runs once the node finished draining."
    )
    public_ip: Mapped[Optional[object]] = mapped_column(INET)
    private_ip: Mapped[Optional[object]] = mapped_column(INET)
    gpu_model: Mapped[Optional[str]] = mapped_column(String(128))
    gpu_vram_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    vcpus: Mapped[Optional[int]] = mapped_column(Integer)
    memory_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    price_per_hour_usd: Mapped[Optional[decimal.Decimal]] = mapped_column(
        Numeric(12, 6),
        comment="Provider on-demand list price per hour when the provider API exposes it (AWS Pricing API, Azure Retail Prices, DigitalOcean sizes); GCP leaves it NULL.",
    )
    price_source: Mapped[Optional[str]] = mapped_column(String(255))
    auto_terminate_idle_minutes: Mapped[Optional[int]] = mapped_column(
        Integer,
        comment="Terminate (after drain) when the node ran no instance for this many minutes; NULL = never. Default from clusters.config.cloud.auto_terminate_idle_minutes.",
    )
    idle_since: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    last_error: Mapped[Optional[object]] = mapped_column(
        JSONB,
        comment='{"code","message","provider_code","at"} of the last provider/lifecycle failure (quota, capacity, auth, ...).',
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    launched_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    joined_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    drain_started_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    stopped_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    terminated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    last_polled_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    cluster: Mapped["Clusters"] = relationship("Clusters", back_populates="cloud_instances")
    admin_users: Mapped[Optional["AdminUsers"]] = relationship(
        "AdminUsers", back_populates="cloud_instances"
    )
    credential: Mapped[Optional["Credentials"]] = relationship(
        "Credentials", back_populates="cloud_instances"
    )
    join_token: Mapped[Optional["JoinTokens"]] = relationship(
        "JoinTokens", back_populates="cloud_instances"
    )
    node: Mapped[Optional["Nodes"]] = relationship("Nodes", back_populates="cloud_instances")


class DeviceSlices(Base):
    __tablename__ = "device_slices"
    __table_args__ = (
        CheckConstraint(
            "compute_pct >= 1 AND compute_pct <= 100", name="device_slices_compute_pct_range"
        ),
        CheckConstraint("vram_bytes > 0", name="device_slices_vram_pos"),
        ForeignKeyConstraint(
            ["deployment_id"],
            ["deployments.id"],
            ondelete="SET NULL",
            name="device_slices_deployment_id_fkey",
        ),
        ForeignKeyConstraint(
            ["device_id"], ["devices.id"], ondelete="CASCADE", name="device_slices_device_id_fkey"
        ),
        ForeignKeyConstraint(
            ["gpu_instance_id"],
            ["gpu_instances.id"],
            ondelete="SET NULL",
            name="device_slices_gpu_instance_id_fkey",
        ),
        ForeignKeyConstraint(
            ["instance_id"],
            ["instances.id"],
            ondelete="SET NULL",
            name="device_slices_instance_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="device_slices_pkey"),
        Index(
            "idx_device_slices_device_id",
            "device_id",
            postgresql_where="(status <> 'released'::device_slice_status)",
        ),
        Index(
            "idx_device_slices_gpu_instance_id",
            "gpu_instance_id",
            postgresql_where="(gpu_instance_id IS NOT NULL)",
        ),
        Index("idx_device_slices_instance_id", "instance_id"),
        {
            "comment": "Concrete VRAM/compute reservation on one device for one instance "
            "(GPU slicing). The scheduler guarantees sum(vram_bytes) of active "
            "slices <= devices.vram_total_bytes."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    device_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    vram_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    compute_pct: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("100")
    )
    status: Mapped[str] = mapped_column(
        Enum("reserved", "active", "released", name="device_slice_status"),
        nullable=False,
        server_default=text("'reserved'::device_slice_status"),
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    instance_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    deployment_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    name: Mapped[Optional[str]] = mapped_column(String(255))
    released_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    gpu_instance_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        comment="Set when the slice (or the whole-device reservation) belongs to an SSH GPU instance. A slice with neither instance_id nor gpu_instance_id is a direct reservation made through POST /devices/{id}/slices.",
    )

    deployment: Mapped[Optional["Deployments"]] = relationship(
        "Deployments", back_populates="device_slices"
    )
    device: Mapped["Devices"] = relationship("Devices", back_populates="device_slices")
    gpu_instance: Mapped[Optional["GpuInstances"]] = relationship(
        "GpuInstances", back_populates="device_slices"
    )
    instance: Mapped[Optional["Instances"]] = relationship(
        "Instances", back_populates="device_slices"
    )


class SwapOperations(Base):
    __tablename__ = "swap_operations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["created_by"],
            ["admin_users.id"],
            ondelete="SET NULL",
            name="swap_operations_created_by_fkey",
        ),
        ForeignKeyConstraint(
            ["deployment_id"],
            ["deployments.id"],
            ondelete="SET NULL",
            name="swap_operations_deployment_id_fkey",
        ),
        ForeignKeyConstraint(
            ["from_instance_id"],
            ["instances.id"],
            ondelete="SET NULL",
            name="swap_operations_from_instance_id_fkey",
        ),
        ForeignKeyConstraint(
            ["layout_id"],
            ["layouts.id"],
            ondelete="SET NULL",
            name="swap_operations_layout_id_fkey",
        ),
        ForeignKeyConstraint(
            ["node_id"], ["nodes.id"], ondelete="SET NULL", name="swap_operations_node_id_fkey"
        ),
        ForeignKeyConstraint(
            ["rule_run_id"],
            ["rule_runs.id"],
            ondelete="SET NULL",
            name="swap_operations_rule_run_id_fkey",
        ),
        ForeignKeyConstraint(
            ["to_instance_id"],
            ["instances.id"],
            ondelete="SET NULL",
            name="swap_operations_to_instance_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="swap_operations_pkey"),
        Index("idx_swap_operations_created_at", "created_at"),
        Index("idx_swap_operations_deployment", "deployment_id", "created_at"),
        Index("idx_swap_operations_rule_run", "rule_run_id"),
        Index(
            "idx_swap_operations_state",
            "state",
            postgresql_where="(state <> ALL (ARRAY['completed'::swap_state, 'failed'::swap_state, 'rolled_back'::swap_state, 'cancelled'::swap_state]))",
        ),
        {
            "comment": "State machine for every load/unload/swap/scale/move/restart. "
            "steps = "
            '[{"name":"drain","state":"completed","started_at","finished_at","error","command_id"}] '
            "in the order drain -> wait -> unload -> load -> health -> "
            "(rollback). rollback holds what to restore on failure (previous "
            "instance spec and devices)."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    kind: Mapped[str] = mapped_column(
        Enum(
            "load",
            "unload",
            "swap",
            "scale_up",
            "scale_down",
            "restart",
            "move",
            "apply_layout",
            name="swap_kind",
        ),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(
        Enum(
            "pending",
            "draining",
            "unloading",
            "loading",
            "health_check",
            "completed",
            "failed",
            "rolling_back",
            "rolled_back",
            "cancelled",
            name="swap_state",
        ),
        nullable=False,
        server_default=text("'pending'::swap_state"),
    )
    trigger: Mapped[str] = mapped_column(
        Enum("manual", "rule", "layout", "auto_heal", "api", "benchmark", name="swap_trigger"),
        nullable=False,
    )
    plan: Mapped[object] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment='Immutable input: {"unload":[instance_ids],"load":[{deployment_id,node_id,device_ids,slice}],"reason":"..."}',
    )
    steps: Mapped[object] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rollback: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    drain_timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("120")
    )
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    rule_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    layout_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    deployment_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    from_instance_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    to_instance_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    node_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    error_message: Mapped[Optional[str]] = mapped_column(String(2048))
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)

    admin_users: Mapped[Optional["AdminUsers"]] = relationship(
        "AdminUsers", back_populates="swap_operations"
    )
    deployment: Mapped[Optional["Deployments"]] = relationship(
        "Deployments", back_populates="swap_operations"
    )
    from_instance: Mapped[Optional["Instances"]] = relationship(
        "Instances", foreign_keys=[from_instance_id], back_populates="swap_operations_from_instance"
    )
    layout: Mapped[Optional["Layouts"]] = relationship("Layouts", back_populates="swap_operations")
    node: Mapped[Optional["Nodes"]] = relationship("Nodes", back_populates="swap_operations")
    rule_run: Mapped[Optional["RuleRuns"]] = relationship(
        "RuleRuns", back_populates="swap_operations"
    )
    to_instance: Mapped[Optional["Instances"]] = relationship(
        "Instances", foreign_keys=[to_instance_id], back_populates="swap_operations_to_instance"
    )
    agent_commands: Mapped[list["AgentCommands"]] = relationship(
        "AgentCommands", back_populates="swap_operation"
    )


class AgentCommands(Base):
    __tablename__ = "agent_commands"
    __table_args__ = (
        ForeignKeyConstraint(
            ["instance_id"],
            ["instances.id"],
            ondelete="SET NULL",
            name="agent_commands_instance_id_fkey",
        ),
        ForeignKeyConstraint(
            ["node_id"], ["nodes.id"], ondelete="CASCADE", name="agent_commands_node_id_fkey"
        ),
        ForeignKeyConstraint(
            ["swap_operation_id"],
            ["swap_operations.id"],
            ondelete="SET NULL",
            name="agent_commands_swap_operation_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="agent_commands_pkey"),
        Index("idx_agent_commands_created_at", "created_at"),
        Index(
            "idx_agent_commands_node_state",
            "node_id",
            "state",
            postgresql_where="(state = ANY (ARRAY['pending'::agent_command_state, 'sent'::agent_command_state, 'accepted'::agent_command_state, 'running'::agent_command_state]))",
        ),
        Index("idx_agent_commands_swap", "swap_operation_id"),
        {
            "comment": "Every command sent to a node agent. id is the envelope id / "
            "correlation_id of the command message; command_result messages "
            "update state. Re-sent unchanged after reconnect while state in "
            "(pending, sent) and not expired; the agent is idempotent on id."
        },
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    command_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    state: Mapped[str] = mapped_column(
        Enum(
            "pending",
            "sent",
            "accepted",
            "running",
            "succeeded",
            "failed",
            "rejected",
            "timed_out",
            "cancelled",
            name="agent_command_state",
        ),
        nullable=False,
        server_default=text("'pending'::agent_command_state"),
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    swap_operation_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    instance_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    result: Mapped[Optional[object]] = mapped_column(JSONB)
    error_code: Mapped[Optional[str]] = mapped_column(String(100))
    error_message: Mapped[Optional[str]] = mapped_column(String(2048))
    progress_pct: Mapped[Optional[float]] = mapped_column(REAL)
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    sent_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    accepted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    instance: Mapped[Optional["Instances"]] = relationship(
        "Instances", back_populates="agent_commands"
    )
    node: Mapped["Nodes"] = relationship("Nodes", back_populates="agent_commands")
    swap_operation: Mapped[Optional["SwapOperations"]] = relationship(
        "SwapOperations", back_populates="agent_commands"
    )
