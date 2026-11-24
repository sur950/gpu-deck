-- =============================================================================
-- GPU Deck — 001_init / up.sql
-- -----------------------------------------------------------------------------
-- Single initial schema for all 23 product modules. SQL-first:
--   * UUID primary keys via gen_random_uuid(); TIMESTAMPTZ everywhere.
--   * created_at / updated_at / deleted_at on mutable business tables; soft delete
--     with partial indexes `WHERE deleted_at IS NULL`.
--   * Postgres enums (CREATE TYPE) for every status-like column.
--   * COMMENT ON every table; comments on non-obvious columns.
--   * update_updated_at_column() BEFORE UPDATE trigger on every table with updated_at.
--   * High-volume time series (metric samples, rollups, logs, traces, usage events)
--     are PARTITION BY RANGE (ts) per day with BIGSERIAL surrogate keys
--     (deliberate deviation from UUID PKs: btree locality + index size).
--   * create_daily_partitions() / drop_old_partitions() manage partitions.
--     The jobs process calls them daily; ingest code must call
--     create_daily_partitions() and retry once on SQLSTATE 23514 ("no partition
--     of relation ... found for row").
--
-- Retention (enforced by the jobs process with drop_old_partitions, values come
-- from env; see libs/contracts/env.md):
--   metric_samples_*        METRICS_RAW_RETENTION_HOURS      default 48 h  (2 daily partitions + today)
--   metric_rollups_*_1m     METRICS_ROLLUP_RETENTION_DAYS    default 90 d
--   log_chunks              LOGS_RETENTION_DAYS              default 14 d
--   traces                  TRACES_RETENTION_DAYS            default 7 d
--   usage_events            USAGE_EVENTS_RETENTION_DAYS      default 400 d (billing needs full months)
--   usage_rollups_hourly    never dropped automatically
--   rule_runs / audit_logs  RULE_RUNS_RETENTION_DAYS (30) / AUDIT_RETENTION_DAYS (365), row deletes
--
-- Apply with: psql -v ON_ERROR_STOP=1 -f up.sql   (alembic runs it through a raw cursor)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Extensions
-- -----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- -----------------------------------------------------------------------------
-- Generic trigger functions
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_updated_at_column() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- =============================================================================
-- ENUM TYPES
-- =============================================================================

-- Identity, keys, security
CREATE TYPE api_key_status       AS ENUM ('active', 'expired', 'revoked', 'suspended');
CREATE TYPE credential_type      AS ENUM ('api_key', 'token', 'oauth', 'service_account', 'ssh_key', 'cloud_account', 'smtp', 'webhook', 'tls_key', 'ca_key', 'other');
CREATE TYPE credential_status    AS ENUM ('active', 'expired', 'revoked');
CREATE TYPE audit_actor_type     AS ENUM ('admin', 'api_key', 'system', 'rule', 'node_agent');
CREATE TYPE audit_outcome        AS ENUM ('success', 'failure');
CREATE TYPE ip_rule_action       AS ENUM ('allow', 'block');
CREATE TYPE ip_rule_scope        AS ENUM ('admin', 'api_layer');

-- Fleet
CREATE TYPE cluster_type         AS ENUM ('bare_metal', 'vm', 'kubernetes', 'cloud_aws', 'cloud_azure', 'cloud_gcp', 'cloud_do');
CREATE TYPE cluster_status       AS ENUM ('active', 'disabled');
CREATE TYPE node_os              AS ENUM ('linux', 'macos');
CREATE TYPE node_arch            AS ENUM ('x86_64', 'aarch64');
CREATE TYPE node_status          AS ENUM ('pending', 'online', 'offline', 'unhealthy', 'draining', 'drained', 'removed');
CREATE TYPE certificate_kind     AS ENUM ('node_agent', 'api_layer', 'agent_hub', 'control_plane');
CREATE TYPE device_vendor        AS ENUM ('nvidia', 'amd', 'hygon', 'metax', 'moorethreads', 'thead', 'apple');
CREATE TYPE device_health        AS ENUM ('healthy', 'degraded', 'unhealthy', 'unknown');
CREATE TYPE device_slice_status  AS ENUM ('reserved', 'active', 'released');

-- Models and engines
CREATE TYPE model_source         AS ENUM ('huggingface', 'modelscope', 'local_path');
CREATE TYPE model_type           AS ENUM ('chat', 'thinking', 'vision', 'embedding', 'rerank', 'stt', 'tts', 'image', 'video');
CREATE TYPE model_priority       AS ENUM ('always_on', 'normal', 'evictable');
CREATE TYPE model_file_format    AS ENUM ('gguf', 'safetensors', 'mlx', 'pickle', 'other');
CREATE TYPE model_version_status AS ENUM ('registered', 'verifying', 'ready', 'missing', 'blocked');
CREATE TYPE cache_state          AS ENUM ('queued', 'downloading', 'ready', 'evicting', 'evicted', 'error');
CREATE TYPE engine_name          AS ENUM ('llama_cpp', 'vllm', 'vllm_omni', 'sglang', 'sglang_diffusion', 'tensorrt_llm', 'mlx', 'mlx_audio', 'whisper_cpp', 'stable_diffusion_cpp', 'custom');
CREATE TYPE engine_runtime       AS ENUM ('native', 'container');
CREATE TYPE engine_version_status AS ENUM ('available', 'deprecated', 'disabled');
CREATE TYPE compat_result        AS ENUM ('compatible', 'incompatible', 'unknown');

-- Deployments
CREATE TYPE deployment_preset    AS ENUM ('low_latency', 'high_throughput');
CREATE TYPE placement_strategy   AS ENUM ('spread', 'pack');
CREATE TYPE deployment_status    AS ENUM ('pending', 'scheduling', 'deploying', 'running', 'degraded', 'scaling', 'stopped', 'failed', 'deleting');
CREATE TYPE instance_state       AS ENUM ('pending', 'loading', 'ready', 'draining', 'unloading', 'stopped', 'error');
CREATE TYPE instance_health      AS ENUM ('healthy', 'unhealthy', 'unknown');

-- Automation
CREATE TYPE rule_type            AS ENUM ('idle_unload', 'time_schedule', 'low_traffic_shrink', 'high_load_grow', 'make_room_by_priority', 'auto_heal');
CREATE TYPE rule_run_outcome     AS ENUM ('no_match', 'cooldown', 'fired', 'dry_run', 'skipped', 'error');
CREATE TYPE swap_kind            AS ENUM ('load', 'unload', 'swap', 'scale_up', 'scale_down', 'restart', 'move', 'apply_layout');
CREATE TYPE swap_state           AS ENUM ('pending', 'draining', 'unloading', 'loading', 'health_check', 'completed', 'failed', 'rolling_back', 'rolled_back', 'cancelled');
CREATE TYPE swap_trigger         AS ENUM ('manual', 'rule', 'layout', 'auto_heal', 'api', 'benchmark');
CREATE TYPE agent_command_state  AS ENUM ('pending', 'sent', 'accepted', 'running', 'succeeded', 'failed', 'rejected', 'timed_out', 'cancelled');

-- Alerts
CREATE TYPE alert_channel_type   AS ENUM ('slack', 'email', 'webhook');
CREATE TYPE alert_severity       AS ENUM ('info', 'warning', 'critical');
CREATE TYPE alert_event_status   AS ENUM ('open', 'acknowledged', 'resolved');
CREATE TYPE alert_delivery_status AS ENUM ('pending', 'sent', 'failed', 'suppressed');

-- Observability
CREATE TYPE log_source           AS ENUM ('engine', 'agent', 'control_plane', 'rule', 'swap', 'api_layer');
CREATE TYPE log_level            AS ENUM ('debug', 'info', 'warn', 'error');
CREATE TYPE log_stream           AS ENUM ('stdout', 'stderr', 'app');

-- Metering and billing
CREATE TYPE usage_format         AS ENUM ('openai', 'anthropic', 'ollama');
CREATE TYPE usage_status         AS ENUM ('success', 'error', 'timeout', 'cancelled');
CREATE TYPE billing_report_status AS ENUM ('pending', 'generating', 'ready', 'failed');

-- Benchmarks and batches
CREATE TYPE benchmark_run_status AS ENUM ('pending', 'running', 'completed', 'failed', 'cancelled');
CREATE TYPE batch_status         AS ENUM ('in_progress', 'canceling', 'ended');
CREATE TYPE batch_item_status    AS ENUM ('pending', 'processing', 'succeeded', 'errored', 'canceled', 'expired');

-- =============================================================================
-- MODULE: API keys and login / Audit and security / Branding (no tables)
-- =============================================================================

-- Single seeded admin. The row count is constrained to one (singleton column).
CREATE TABLE IF NOT EXISTS admin_users (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    singleton             BOOLEAN NOT NULL DEFAULT TRUE,
    username              VARCHAR(100) NOT NULL,
    display_name          VARCHAR(255),
    password_hash         VARCHAR(255) NOT NULL,
    password_changed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    env_secret_hash       VARCHAR(64) NOT NULL,
    env_secret_applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    failed_login_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until          TIMESTAMPTZ,
    last_login_at         TIMESTAMPTZ,
    last_login_ip         INET,
    is_active             BOOLEAN NOT NULL DEFAULT TRUE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT admin_users_singleton_true CHECK (singleton),
    CONSTRAINT admin_users_single_row UNIQUE (singleton),
    CONSTRAINT admin_users_username_unique UNIQUE (username),
    CONSTRAINT admin_users_failed_attempts_nonneg CHECK (failed_login_attempts >= 0)
);
COMMENT ON TABLE  admin_users IS 'The single admin account seeded from ADMIN_USERNAME/ADMIN_PASSWORD in .env. Exactly one row.';
COMMENT ON COLUMN admin_users.password_hash IS 'Argon2id PHC string ($argon2id$...). Never bcrypt.';
COMMENT ON COLUMN admin_users.env_secret_hash IS 'SHA-256 hex of the ADMIN_PASSWORD value last applied from .env. On boot: if sha256(env ADMIN_PASSWORD) != env_secret_hash, re-hash the env password into password_hash, update this column and env_secret_applied_at; otherwise leave a UI-set password alone. username is always synced from ADMIN_USERNAME.';
COMMENT ON COLUMN admin_users.locked_until IS 'Lockout until this time after AUTH_LOCKOUT_ATTEMPTS consecutive failures (Redis counter is the hot path; this column is the durable mirror).';

-- Opaque Fernet session tokens; only the SHA-256 of the raw cookie token is stored.
CREATE TABLE IF NOT EXISTS sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_user_id   UUID NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    token_hash      VARCHAR(64) NOT NULL,
    ip_address      INET,
    user_agent      VARCHAR(512),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active_at  TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    revoked_at      TIMESTAMPTZ,
    CONSTRAINT sessions_token_hash_unique UNIQUE (token_hash),
    CONSTRAINT sessions_expires_after_created CHECK (expires_at > created_at)
);
COMMENT ON TABLE  sessions IS 'Server-side admin sessions: Fernet token in an HttpOnly cookie, SHA-256 of it here, dict cached in Redis session:{token_hash}.';
COMMENT ON COLUMN sessions.token_hash IS 'SHA-256 hex of the raw cookie token. CSRF uses a double-submit token derived with HMAC(SESSION_SECRET, token_hash); nothing extra is stored.';
CREATE INDEX IF NOT EXISTS idx_sessions_admin_user_id ON sessions(admin_user_id) WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at    ON sessions(expires_at)    WHERE is_active;

-- API keys consumed by an AI gateway (or any client). Plaintext shown once at creation.
CREATE TABLE IF NOT EXISTS api_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    description     VARCHAR(2048),
    key_prefix      VARCHAR(20) NOT NULL,
    key_hash        VARCHAR(64) NOT NULL,
    status          api_key_status NOT NULL DEFAULT 'active',
    expires_at      TIMESTAMPTZ,
    last_used_at    TIMESTAMPTZ,
    total_requests  BIGINT NOT NULL DEFAULT 0,
    metadata        JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_by      UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    revoked_at      TIMESTAMPTZ,
    last_rotated_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMPTZ,
    CONSTRAINT api_keys_key_prefix_unique UNIQUE (key_prefix),
    CONSTRAINT api_keys_key_hash_unique UNIQUE (key_hash)
);
COMMENT ON TABLE  api_keys IS 'API keys for the serving API layer. Key format "sk-" + 48 hex chars; key_prefix = first 12 chars; key_hash = SHA-256 hex. Limits/quotas live in your AI gateway, not here.';
COMMENT ON COLUMN api_keys.total_requests IS 'Incremented by the metering job (not on the request path).';
CREATE INDEX IF NOT EXISTS idx_api_keys_status   ON api_keys(status)   WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_name_unique ON api_keys(lower(name)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_api_keys_name_trgm ON api_keys USING GIN (name gin_trgm_ops) WHERE deleted_at IS NULL;

-- Vault (MultiFernet with VAULT_KEYS).
CREATE TABLE IF NOT EXISTS credentials (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                VARCHAR(255) NOT NULL,
    type                credential_type NOT NULL,
    provider            VARCHAR(100),
    status              credential_status NOT NULL DEFAULT 'active',
    encrypted_value     TEXT NOT NULL,
    masked_value        VARCHAR(50),
    description         VARCHAR(2048),
    base_url            VARCHAR(2048),
    expires_at          TIMESTAMPTZ,
    usage_count         INTEGER NOT NULL DEFAULT 0,
    last_used_at        TIMESTAMPTZ,
    key_version         SMALLINT NOT NULL DEFAULT 1,
    access_restrictions JSONB NOT NULL DEFAULT '{}'::JSONB,
    extra_data          JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_by          UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at          TIMESTAMPTZ
);
COMMENT ON TABLE  credentials IS 'Encrypted secrets vault: cloud keys, HF/ModelScope tokens, SMTP/Slack/webhook secrets, internal CA key, TLS keys. Encrypted with MultiFernet(VAULT_KEYS).';
COMMENT ON COLUMN credentials.encrypted_value IS 'Fernet ciphertext (TEXT: CA/TLS PEM keys exceed 4096 chars); never returned to clients.';
COMMENT ON COLUMN credentials.provider IS 'Slug of what the secret belongs to: huggingface, modelscope, aws, azure, gcp, digitalocean, slack, smtp, webhook, internal_ca, agent_hub_tls, ...';
COMMENT ON COLUMN credentials.key_version IS 'Index (1-based) of the VAULT_KEYS entry that encrypted this value; rotation re-encrypts rows with key_version < 1.';
CREATE INDEX IF NOT EXISTS idx_credentials_status     ON credentials(status)     WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_credentials_type       ON credentials(type)       WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_credentials_provider   ON credentials(provider)   WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_credentials_extra_data ON credentials USING GIN (extra_data) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_credentials_name_unique ON credentials(lower(name)) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS credential_usage_history (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    credential_id UUID NOT NULL REFERENCES credentials(id) ON DELETE CASCADE,
    used_by       UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    used_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    purpose       VARCHAR(255),
    request_id    UUID
);
COMMENT ON TABLE credential_usage_history IS 'One row per reveal/use of a vault credential (purpose: reveal, join_sign, hf_download, alert_send, ...).';
CREATE INDEX IF NOT EXISTS idx_credential_usage_history_credential_id ON credential_usage_history(credential_id);
CREATE INDEX IF NOT EXISTS idx_credential_usage_history_used_at       ON credential_usage_history(used_at DESC);

CREATE OR REPLACE FUNCTION update_credential_last_used() RETURNS TRIGGER AS $$
BEGIN
    UPDATE credentials SET last_used_at = NEW.used_at, usage_count = usage_count + 1 WHERE id = NEW.credential_id;
    RETURN NEW;
END;
$$ language 'plpgsql';
CREATE OR REPLACE TRIGGER update_credential_usage_on_history_insert
    AFTER INSERT ON credential_usage_history FOR EACH ROW EXECUTE FUNCTION update_credential_last_used();

-- Audit log. Immutable: no updated_at, no soft delete.
CREATE TABLE IF NOT EXISTS audit_logs (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type     VARCHAR(100) NOT NULL,
    actor_type     audit_actor_type NOT NULL,
    actor_id       UUID,
    actor_name     VARCHAR(255),
    resource_type  VARCHAR(100),
    resource_id    UUID,
    resource_name  VARCHAR(255),
    before_state   JSONB,
    after_state    JSONB,
    outcome        audit_outcome NOT NULL DEFAULT 'success',
    message        VARCHAR(2048),
    ip_address     INET,
    user_agent     VARCHAR(512),
    request_id     UUID,
    metadata       JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE  audit_logs IS 'Every login, key change, rule action, swap, load/unload, node join/remove, cert revoke, with time and source IP. event_type is dotted: auth.login, api_key.created, rule.fired, instance.unloaded, node.cert_revoked, ...';
CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at    ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_event_type    ON audit_logs(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_actor         ON audit_logs(actor_type, actor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_resource      ON audit_logs(resource_type, resource_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_request_id    ON audit_logs(request_id);

-- IP allow / block lists, per surface.
CREATE TABLE IF NOT EXISTS ip_rules (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action      ip_rule_action NOT NULL,
    scope       ip_rule_scope NOT NULL,
    cidr        CIDR NOT NULL,
    description VARCHAR(512),
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    expires_at  TIMESTAMPTZ,
    created_by  UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at  TIMESTAMPTZ
);
COMMENT ON TABLE  ip_rules IS 'IP allow/block rules. Semantics: block rules win; if any allow rule exists for a scope, only allowed CIDRs pass. Env GATEWAY_IP_ALLOWLIST is merged in as implicit api_layer allow rules. Cached in Redis ip_rules:{scope}.';
CREATE UNIQUE INDEX IF NOT EXISTS idx_ip_rules_scope_action_cidr_unique ON ip_rules(scope, action, cidr) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_ip_rules_scope_enabled ON ip_rules(scope, enabled) WHERE deleted_at IS NULL;

-- =============================================================================
-- MODULE: Billing (price plans first: referenced by clusters and nodes)
-- =============================================================================
CREATE TABLE IF NOT EXISTS price_plans (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                      VARCHAR(255) NOT NULL,
    description               VARCHAR(2048),
    currency                  CHAR(3) NOT NULL DEFAULT 'USD',
    usd_per_gpu_minute        NUMERIC(12,6) NOT NULL DEFAULT 0,
    usd_per_1k_input_tokens   NUMERIC(12,6) NOT NULL DEFAULT 0,
    usd_per_1k_output_tokens  NUMERIC(12,6) NOT NULL DEFAULT 0,
    usd_per_1k_cached_tokens  NUMERIC(12,6) NOT NULL DEFAULT 0,
    is_default                BOOLEAN NOT NULL DEFAULT FALSE,
    created_by                UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at                TIMESTAMPTZ,
    CONSTRAINT price_plans_nonneg CHECK (usd_per_gpu_minute >= 0 AND usd_per_1k_input_tokens >= 0 AND usd_per_1k_output_tokens >= 0 AND usd_per_1k_cached_tokens >= 0)
);
COMMENT ON TABLE  price_plans IS 'USD price per GPU minute and/or per 1k tokens. Attached to a cluster (clusters.price_plan_id) and optionally overridden per node (nodes.price_plan_id). No plan = on-prem, cost 0.';
CREATE UNIQUE INDEX IF NOT EXISTS idx_price_plans_name_unique ON price_plans(lower(name)) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_price_plans_single_default ON price_plans(is_default) WHERE is_default AND deleted_at IS NULL;

-- =============================================================================
-- MODULE: Clusters and nodes / GPU node agent
-- =============================================================================
CREATE TABLE IF NOT EXISTS clusters (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name             VARCHAR(255) NOT NULL,
    type             cluster_type NOT NULL DEFAULT 'bare_metal',
    description      VARCHAR(2048),
    region           VARCHAR(100),
    status           cluster_status NOT NULL DEFAULT 'active',
    credential_id    UUID REFERENCES credentials(id) ON DELETE SET NULL,
    price_plan_id    UUID REFERENCES price_plans(id) ON DELETE SET NULL,
    config           JSONB NOT NULL DEFAULT '{}'::JSONB,
    egress_allowlist TEXT[] NOT NULL DEFAULT '{}',
    labels           JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_by       UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at       TIMESTAMPTZ
);
COMMENT ON TABLE  clusters IS 'A group of GPU machines: bare metal, VMs, Kubernetes, or cloud (AWS/Azure/GCP/DO). Cloud/K8s clusters keep their account secret in credential_id and provider-specific settings (kube namespace, VPC, instance types) in config.';
COMMENT ON COLUMN clusters.egress_allowlist IS 'Extra hostnames/CIDRs the node agents in this cluster may reach besides the control plane and model sources (merged with env EGRESS_ALLOWLIST_DEFAULT).';
CREATE UNIQUE INDEX IF NOT EXISTS idx_clusters_name_unique ON clusters(lower(name)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_clusters_type ON clusters(type) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS nodes (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cluster_id           UUID NOT NULL REFERENCES clusters(id) ON DELETE RESTRICT,
    name                 VARCHAR(255) NOT NULL,
    hostname             VARCHAR(255),
    ip_address           INET,
    proxy_address        VARCHAR(255),
    proxy_port           INTEGER NOT NULL DEFAULT 9443,
    os                   node_os,
    os_version           VARCHAR(100),
    arch                 node_arch,
    kernel_version       VARCHAR(100),
    agent_version        VARCHAR(50),
    protocol_version     SMALLINT,
    status               node_status NOT NULL DEFAULT 'pending',
    status_message       VARCHAR(1024),
    labels               JSONB NOT NULL DEFAULT '{}'::JSONB,
    capabilities         JSONB NOT NULL DEFAULT '{}'::JSONB,
    cert_fingerprint     VARCHAR(64),
    cpu_cores            INTEGER,
    memory_total_bytes   BIGINT,
    disks                JSONB NOT NULL DEFAULT '[]'::JSONB,
    cache_dir            VARCHAR(1024),
    cache_limit_bytes    BIGINT,
    last_heartbeat_at    TIMESTAMPTZ,
    last_inventory_at    TIMESTAMPTZ,
    connected_at         TIMESTAMPTZ,
    disconnected_at      TIMESTAMPTZ,
    drain_requested_at   TIMESTAMPTZ,
    drain_reason         VARCHAR(512),
    drained_at           TIMESTAMPTZ,
    price_plan_id        UUID REFERENCES price_plans(id) ON DELETE SET NULL,
    kubernetes_node_name VARCHAR(255),
    cloud_instance_id    VARCHAR(255),
    cloud_instance_type  VARCHAR(100),
    metadata             JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at           TIMESTAMPTZ
);
COMMENT ON TABLE  nodes IS 'A GPU machine running the Rust node agent. Joined with a one-time token, identified by its client certificate afterwards.';
COMMENT ON COLUMN nodes.proxy_address IS 'Host (IP or DNS) the API layer dials for the agent mTLS proxy; defaults to the address seen at join. Port in proxy_port.';
COMMENT ON COLUMN nodes.capabilities IS 'From hello: {"engines":[{"name","versions":[...]}], "runtimes":["native","container"], "metal":true, "cuda":"12.8", ...}.';
COMMENT ON COLUMN nodes.cert_fingerprint IS 'SHA-256 hex of the currently active client certificate (mirror of node_certificates).';
COMMENT ON COLUMN nodes.disks IS 'Inventory: [{"mount":"/var/lib/gpudeck","total_bytes":..,"free_bytes":..,"fs":"apfs"}].';
COMMENT ON COLUMN nodes.price_plan_id IS 'Overrides clusters.price_plan_id for billing when set.';
CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_cluster_name_unique ON nodes(cluster_id, lower(name)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_nodes_cluster_id ON nodes(cluster_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_nodes_status     ON nodes(status)     WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_nodes_labels     ON nodes USING GIN (labels) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_nodes_name_trgm  ON nodes USING GIN (name gin_trgm_ops) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS join_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cluster_id      UUID NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    token_prefix    VARCHAR(12) NOT NULL,
    token_hash      VARCHAR(64) NOT NULL,
    node_name_hint  VARCHAR(255),
    labels          JSONB NOT NULL DEFAULT '{}'::JSONB,
    expires_at      TIMESTAMPTZ NOT NULL,
    used_at         TIMESTAMPTZ,
    used_by_node_id UUID REFERENCES nodes(id) ON DELETE SET NULL,
    revoked_at      TIMESTAMPTZ,
    created_by      UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT join_tokens_token_hash_unique UNIQUE (token_hash)
);
COMMENT ON TABLE  join_tokens IS 'One-time machine join tokens ("gdj_" + 40 hex), TTL JOIN_TOKEN_TTL_MINUTES. Plaintext shown once; SHA-256 stored. Consumed at POST /agent/v1/join.';
CREATE INDEX IF NOT EXISTS idx_join_tokens_cluster_id ON join_tokens(cluster_id);
CREATE INDEX IF NOT EXISTS idx_join_tokens_active     ON join_tokens(expires_at) WHERE used_at IS NULL AND revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS node_certificates (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind               certificate_kind NOT NULL DEFAULT 'node_agent',
    node_id            UUID REFERENCES nodes(id) ON DELETE CASCADE,
    serial             VARCHAR(64) NOT NULL,
    fingerprint_sha256 VARCHAR(64) NOT NULL,
    subject            VARCHAR(512) NOT NULL,
    certificate_pem    TEXT NOT NULL,
    not_before         TIMESTAMPTZ NOT NULL,
    not_after          TIMESTAMPTZ NOT NULL,
    issued_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at         TIMESTAMPTZ,
    revocation_reason  VARCHAR(255),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT node_certificates_serial_unique UNIQUE (serial),
    CONSTRAINT node_certificates_fingerprint_unique UNIQUE (fingerprint_sha256),
    CONSTRAINT node_certificates_node_required CHECK (kind <> 'node_agent' OR node_id IS NOT NULL)
);
COMMENT ON TABLE  node_certificates IS 'Certificates issued by the internal CA (key in vault, name internal-ca). node_agent certs have CN=<node_id>, O=gpudeck-nodes; service certs (api_layer, agent_hub, control_plane) have O=gpudeck-services. Revoked rows form the CRL pushed to agents (config_push.crl) and the API layer.';
CREATE INDEX IF NOT EXISTS idx_node_certificates_node_id ON node_certificates(node_id);
CREATE INDEX IF NOT EXISTS idx_node_certificates_revoked ON node_certificates(revoked_at) WHERE revoked_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_node_certificates_not_after ON node_certificates(not_after) WHERE revoked_at IS NULL;

-- =============================================================================
-- MODULE: Accelerator inventory
-- =============================================================================
CREATE TABLE IF NOT EXISTS devices (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id             UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    vendor              device_vendor NOT NULL,
    index               INTEGER NOT NULL,
    uuid                VARCHAR(128),
    name                VARCHAR(255) NOT NULL,
    vram_total_bytes    BIGINT NOT NULL DEFAULT 0,
    is_unified_memory   BOOLEAN NOT NULL DEFAULT FALSE,
    driver_version      VARCHAR(100),
    runtime_version     VARCHAR(100),
    compute_capability  VARCHAR(32),
    pci_bus_id          VARCHAR(64),
    numa_node           INTEGER,
    max_power_watts     REAL,
    core_count          INTEGER,
    health              device_health NOT NULL DEFAULT 'unknown',
    health_reason       VARCHAR(1024),
    labels              JSONB NOT NULL DEFAULT '{}'::JSONB,
    extra               JSONB NOT NULL DEFAULT '{}'::JSONB,
    last_seen_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at          TIMESTAMPTZ,
    CONSTRAINT devices_index_nonneg CHECK (index >= 0)
);
COMMENT ON TABLE  devices IS 'Accelerators reported by the node agent inventory in the common Device format (NVIDIA, AMD, Hygon, MetaX, Moore Threads, T-Head, Apple Silicon).';
COMMENT ON COLUMN devices.uuid IS 'Vendor device UUID (NVML uuid, amd-smi unique id, Apple: "apple-<chip>-<serial>"). Stable across reboots when the vendor provides one.';
COMMENT ON COLUMN devices.runtime_version IS 'CUDA / ROCm / MUSA / Metal version string.';
COMMENT ON COLUMN devices.compute_capability IS 'NVIDIA "8.9", AMD gfx target "gfx942", Apple GPU family "metal3".';
COMMENT ON COLUMN devices.is_unified_memory IS 'TRUE for Apple Silicon: vram_total_bytes is the Metal wired limit, shared with system RAM.';
CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_node_index_unique ON devices(node_id, index) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_devices_node_id ON devices(node_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_devices_vendor  ON devices(vendor)  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_devices_health  ON devices(health)  WHERE deleted_at IS NULL;

-- =============================================================================
-- MODULE: Model catalog / Model cache
-- =============================================================================
CREATE TABLE IF NOT EXISTS models (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                 VARCHAR(255) NOT NULL,
    display_name         VARCHAR(255),
    source               model_source NOT NULL,
    repo_id              VARCHAR(1024) NOT NULL,
    family               VARCHAR(100),
    type                 model_type NOT NULL,
    priority             model_priority NOT NULL DEFAULT 'normal',
    description          VARCHAR(4096),
    tool_parser_default  VARCHAR(100),
    trust_remote_code    BOOLEAN NOT NULL DEFAULT FALSE,
    allow_pickle         BOOLEAN NOT NULL DEFAULT FALSE,
    default_context_size INTEGER,
    default_launch_flags JSONB NOT NULL DEFAULT '{}'::JSONB,
    aliases              TEXT[] NOT NULL DEFAULT '{}',
    tags                 TEXT[] NOT NULL DEFAULT '{}',
    metadata             JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_by           UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at           TIMESTAMPTZ
);
COMMENT ON TABLE  models IS 'Catalog entry. name is the public serving name (the "model" field in OpenAI/Anthropic/Ollama requests); aliases are extra serving names.';
COMMENT ON COLUMN models.repo_id IS 'HF/ModelScope repo id (org/name) or absolute local path for source=local_path.';
COMMENT ON COLUMN models.family IS 'Model family used for tool-parser / chat-template defaults: qwen3, llama, gemma3, mistral, deepseek, ...';
COMMENT ON COLUMN models.priority IS 'always_on: rules never unload it; normal: unload only by idle/time rules; evictable: may be evicted to make room.';
COMMENT ON COLUMN models.allow_pickle IS 'FALSE blocks versions whose only weights are pickle-based (.bin/.pt/.pth/.ckpt).';
CREATE UNIQUE INDEX IF NOT EXISTS idx_models_name_unique ON models(lower(name)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_models_type     ON models(type)     WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_models_priority ON models(priority) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_models_source   ON models(source)   WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_models_aliases  ON models USING GIN (aliases) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_models_name_trgm ON models USING GIN (name gin_trgm_ops) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS model_versions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id         UUID NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    version          VARCHAR(255) NOT NULL,
    revision         VARCHAR(255),
    quantization     VARCHAR(50),
    format           model_file_format NOT NULL DEFAULT 'other',
    status           model_version_status NOT NULL DEFAULT 'registered',
    status_message   VARCHAR(1024),
    total_size_bytes BIGINT NOT NULL DEFAULT 0,
    parameter_count  BIGINT,
    config           JSONB NOT NULL DEFAULT '{}'::JSONB,
    is_default       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at       TIMESTAMPTZ
);
COMMENT ON TABLE  model_versions IS 'A concrete set of weight files for a model: HF revision/commit, ModelScope revision, or a local directory snapshot. Exactly one is_default per model.';
COMMENT ON COLUMN model_versions.version IS 'User-facing label ("main", "v2", "Q4_K_M", "2026-11-16"). revision holds the upstream commit sha when known.';
COMMENT ON COLUMN model_versions.config IS 'Parsed config.json / GGUF metadata: n_layers, n_kv_heads, head_dim, max_position_embeddings, architecture (used for KV-cache size estimates).';
COMMENT ON COLUMN model_versions.status IS 'blocked = pickle-only weights with models.allow_pickle=false, or hash mismatch.';
CREATE UNIQUE INDEX IF NOT EXISTS idx_model_versions_model_version_unique ON model_versions(model_id, version) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_model_versions_single_default ON model_versions(model_id) WHERE is_default AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_model_versions_model_id ON model_versions(model_id) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS model_files (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_version_id UUID NOT NULL REFERENCES model_versions(id) ON DELETE CASCADE,
    path             VARCHAR(1024) NOT NULL,
    format           model_file_format NOT NULL DEFAULT 'other',
    size_bytes       BIGINT NOT NULL DEFAULT 0,
    sha256           VARCHAR(64),
    is_required      BOOLEAN NOT NULL DEFAULT TRUE,
    shard_index      INTEGER,
    shard_count      INTEGER,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE  model_files IS 'Files of a model version. path is relative to the repo/snapshot root. sha256 is recorded at registration (HF LFS sha256 or computed for local files) and verified on the node after download.';
CREATE UNIQUE INDEX IF NOT EXISTS idx_model_files_version_path_unique ON model_files(model_version_id, path);
CREATE INDEX IF NOT EXISTS idx_model_files_format ON model_files(format);

CREATE TABLE IF NOT EXISTS node_model_cache (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id          UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    model_version_id UUID NOT NULL REFERENCES model_versions(id) ON DELETE CASCADE,
    model_file_id    UUID NOT NULL REFERENCES model_files(id) ON DELETE CASCADE,
    local_path       VARCHAR(1024) NOT NULL,
    size_bytes       BIGINT NOT NULL DEFAULT 0,
    state            cache_state NOT NULL DEFAULT 'queued',
    progress_pct     REAL NOT NULL DEFAULT 0,
    sha256_verified  BOOLEAN NOT NULL DEFAULT FALSE,
    pinned           BOOLEAN NOT NULL DEFAULT FALSE,
    pinned_reason    VARCHAR(255),
    error_message    VARCHAR(2048),
    last_used_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE  node_model_cache IS 'Warm model cache: which model files live on which node disk, their state, size and last use. pinned rows are never evicted by the LRU cleanup (set by prefetch for upcoming layouts and by always_on models).';
CREATE UNIQUE INDEX IF NOT EXISTS idx_node_model_cache_node_file_unique ON node_model_cache(node_id, model_file_id);
CREATE INDEX IF NOT EXISTS idx_node_model_cache_node_state   ON node_model_cache(node_id, state);
CREATE INDEX IF NOT EXISTS idx_node_model_cache_version      ON node_model_cache(model_version_id);
CREATE INDEX IF NOT EXISTS idx_node_model_cache_last_used_at ON node_model_cache(last_used_at) WHERE NOT pinned;

-- =============================================================================
-- MODULE: Inference engines
-- =============================================================================
CREATE TABLE IF NOT EXISTS engines (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                  engine_name NOT NULL,
    display_name          VARCHAR(255) NOT NULL,
    description           VARCHAR(2048),
    runtime               engine_runtime NOT NULL DEFAULT 'native',
    supported_model_types model_type[] NOT NULL DEFAULT '{}',
    supported_vendors     device_vendor[] NOT NULL DEFAULT '{}',
    supported_os          node_os[] NOT NULL DEFAULT '{}',
    supported_formats     model_file_format[] NOT NULL DEFAULT '{}',
    capabilities          JSONB NOT NULL DEFAULT '{}'::JSONB,
    is_builtin            BOOLEAN NOT NULL DEFAULT FALSE,
    is_enabled            BOOLEAN NOT NULL DEFAULT TRUE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at            TIMESTAMPTZ
);
COMMENT ON TABLE  engines IS 'Engine registry. One row per engine family; the custom slot (name=custom) is a container image plus start/health/metrics config on its engine_versions row.';
COMMENT ON COLUMN engines.capabilities IS '{"router_mode":bool,"tensor_parallel":bool,"pipeline_parallel":bool,"pd_split":bool,"speculative_decoding":bool,"kv_sharing":bool,"slices":bool,"control_vectors":bool,"native_formats":["openai","anthropic"],"streaming":bool}';
CREATE UNIQUE INDEX IF NOT EXISTS idx_engines_name_unique ON engines(name) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS engine_versions (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    engine_id            UUID NOT NULL REFERENCES engines(id) ON DELETE CASCADE,
    version              VARCHAR(100) NOT NULL,
    status               engine_version_status NOT NULL DEFAULT 'available',
    image                VARCHAR(512),
    artifacts            JSONB NOT NULL DEFAULT '{}'::JSONB,
    launch_template      JSONB NOT NULL DEFAULT '{}'::JSONB,
    default_launch_flags JSONB NOT NULL DEFAULT '{}'::JSONB,
    health_config        JSONB NOT NULL DEFAULT '{"path":"/health","interval_seconds":5,"timeout_seconds":3,"healthy_statuses":[200]}'::JSONB,
    metrics_config       JSONB NOT NULL DEFAULT '{}'::JSONB,
    port_range           INT4RANGE NOT NULL DEFAULT '[10240,10340)',
    min_driver_version   VARCHAR(50),
    requirements         JSONB NOT NULL DEFAULT '{}'::JSONB,
    is_default           BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at           TIMESTAMPTZ
);
COMMENT ON TABLE  engine_versions IS 'A pinned engine build: container image or per-platform native binary, with launch/health/metrics config the node agent uses verbatim.';
COMMENT ON COLUMN engine_versions.artifacts IS 'Native binaries per platform: {"linux/x86_64":{"url":"https://...","sha256":"..","kind":"tarball"},"macos/aarch64":{...}}. Empty when image is used.';
COMMENT ON COLUMN engine_versions.launch_template IS '{"command":["llama-server","--port","{{port}}","-m","{{model_path}}"],"env":{},"working_dir":null}; template vars listed in agent-protocol/command.json.';
COMMENT ON COLUMN engine_versions.metrics_config IS '{"path":"/metrics","format":"prometheus","slots_path":"/slots","scrape_interval_seconds":2,"derive_from_proxy":false}';
CREATE UNIQUE INDEX IF NOT EXISTS idx_engine_versions_engine_version_unique ON engine_versions(engine_id, version) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_engine_versions_single_default ON engine_versions(engine_id) WHERE is_default AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_engine_versions_engine_id ON engine_versions(engine_id) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS engine_compat_checks (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_version_id  UUID NOT NULL REFERENCES model_versions(id) ON DELETE CASCADE,
    engine_version_id UUID NOT NULL REFERENCES engine_versions(id) ON DELETE CASCADE,
    node_id           UUID REFERENCES nodes(id) ON DELETE CASCADE,
    device_vendor     device_vendor,
    result            compat_result NOT NULL DEFAULT 'unknown',
    score             INTEGER NOT NULL DEFAULT 0,
    reasons           JSONB NOT NULL DEFAULT '[]'::JSONB,
    estimated_vram_bytes BIGINT,
    checked_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE  engine_compat_checks IS 'Result of "can model version X run on engine version Y (on node Z / vendor V)". score ranks candidates for auto-pick; reasons is a list of {code, message, severity}.';
CREATE INDEX IF NOT EXISTS idx_engine_compat_checks_lookup ON engine_compat_checks(model_version_id, engine_version_id, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_engine_compat_checks_node ON engine_compat_checks(node_id);

-- Built-in engine rows (deterministic ids so tests and fixtures can reference them).
INSERT INTO engines (id, name, display_name, runtime, supported_model_types, supported_vendors, supported_os, supported_formats, capabilities, is_builtin) VALUES
('00000000-0000-4000-8000-000000000001', 'llama_cpp', 'llama.cpp', 'native',
 '{chat,thinking,vision,embedding,rerank}', '{nvidia,amd,hygon,metax,moorethreads,thead,apple}', '{linux,macos}', '{gguf}',
 '{"router_mode":true,"tensor_parallel":false,"pipeline_parallel":false,"pd_split":false,"speculative_decoding":true,"kv_sharing":false,"slices":true,"control_vectors":true,"native_formats":["openai"],"streaming":true}', TRUE),
('00000000-0000-4000-8000-000000000002', 'vllm', 'vLLM', 'container',
 '{chat,thinking,vision,embedding,rerank}', '{nvidia,amd,hygon,metax,moorethreads}', '{linux}', '{safetensors}',
 '{"router_mode":false,"tensor_parallel":true,"pipeline_parallel":true,"pd_split":true,"speculative_decoding":true,"kv_sharing":true,"slices":true,"control_vectors":false,"native_formats":["openai"],"streaming":true}', TRUE),
('00000000-0000-4000-8000-000000000003', 'vllm_omni', 'vLLM-Omni', 'container',
 '{tts,image,video}', '{nvidia}', '{linux}', '{safetensors}',
 '{"router_mode":false,"tensor_parallel":true,"pipeline_parallel":false,"pd_split":false,"speculative_decoding":false,"kv_sharing":false,"slices":false,"control_vectors":false,"native_formats":["openai"],"streaming":true,"async_jobs":true}', TRUE),
('00000000-0000-4000-8000-000000000004', 'sglang', 'SGLang', 'container',
 '{chat,thinking,vision,embedding}', '{nvidia,amd}', '{linux}', '{safetensors}',
 '{"router_mode":false,"tensor_parallel":true,"pipeline_parallel":true,"pd_split":true,"speculative_decoding":true,"kv_sharing":true,"slices":true,"control_vectors":false,"native_formats":["openai"],"streaming":true}', TRUE),
('00000000-0000-4000-8000-000000000005', 'sglang_diffusion', 'SGLang Diffusion', 'native',
 '{image,video}', '{nvidia,amd,apple}', '{linux,macos}', '{safetensors}',
 '{"router_mode":false,"tensor_parallel":true,"pipeline_parallel":false,"pd_split":false,"speculative_decoding":false,"kv_sharing":false,"slices":false,"control_vectors":false,"native_formats":["openai"],"streaming":false}', TRUE),
('00000000-0000-4000-8000-000000000006', 'tensorrt_llm', 'TensorRT-LLM', 'container',
 '{chat,thinking}', '{nvidia}', '{linux}', '{safetensors}',
 '{"router_mode":false,"tensor_parallel":true,"pipeline_parallel":true,"pd_split":true,"speculative_decoding":true,"kv_sharing":false,"slices":false,"control_vectors":false,"native_formats":["openai"],"streaming":true}', TRUE),
('00000000-0000-4000-8000-000000000007', 'mlx', 'MLX (mlx_lm.server)', 'native',
 '{chat,thinking,vision}', '{apple}', '{macos}', '{mlx,safetensors}',
 '{"router_mode":false,"tensor_parallel":false,"pipeline_parallel":false,"pd_split":false,"speculative_decoding":true,"kv_sharing":false,"slices":false,"control_vectors":false,"native_formats":["openai"],"streaming":true}', TRUE),
('00000000-0000-4000-8000-000000000008', 'mlx_audio', 'mlx-audio', 'native',
 '{stt,tts}', '{apple}', '{macos}', '{mlx,safetensors}',
 '{"router_mode":false,"tensor_parallel":false,"pipeline_parallel":false,"pd_split":false,"speculative_decoding":false,"kv_sharing":false,"slices":false,"control_vectors":false,"native_formats":["openai"],"streaming":true}', TRUE),
('00000000-0000-4000-8000-000000000009', 'whisper_cpp', 'whisper.cpp', 'native',
 '{stt}', '{nvidia,amd,apple}', '{linux,macos}', '{gguf,other}',
 '{"router_mode":false,"tensor_parallel":false,"pipeline_parallel":false,"pd_split":false,"speculative_decoding":false,"kv_sharing":false,"slices":false,"control_vectors":false,"native_formats":[],"streaming":false}', TRUE),
('00000000-0000-4000-8000-00000000000a', 'stable_diffusion_cpp', 'stable-diffusion.cpp', 'native',
 '{image}', '{nvidia,amd,apple}', '{linux,macos}', '{gguf,safetensors}',
 '{"router_mode":false,"tensor_parallel":false,"pipeline_parallel":false,"pd_split":false,"speculative_decoding":false,"kv_sharing":false,"slices":false,"control_vectors":false,"native_formats":[],"streaming":false}', TRUE),
('00000000-0000-4000-8000-00000000000b', 'custom', 'Custom engine (container image)', 'container',
 '{chat,thinking,vision,embedding,rerank,stt,tts,image,video}', '{nvidia,amd,hygon,metax,moorethreads,thead,apple}', '{linux,macos}', '{gguf,safetensors,mlx,pickle,other}',
 '{"router_mode":false,"tensor_parallel":false,"pipeline_parallel":false,"pd_split":false,"speculative_decoding":false,"kv_sharing":false,"slices":false,"control_vectors":false,"native_formats":["openai"],"streaming":true}', TRUE);

-- =============================================================================
-- MODULE: Deployments / Placement scheduler / High availability (traffic weights)
-- =============================================================================
CREATE TABLE IF NOT EXISTS deployments (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                   VARCHAR(255) NOT NULL,
    description            VARCHAR(2048),
    model_id               UUID NOT NULL REFERENCES models(id) ON DELETE RESTRICT,
    model_version_id       UUID NOT NULL REFERENCES model_versions(id) ON DELETE RESTRICT,
    engine_version_id      UUID NOT NULL REFERENCES engine_versions(id) ON DELETE RESTRICT,
    cluster_id             UUID REFERENCES clusters(id) ON DELETE SET NULL,
    priority_override      model_priority,
    replicas               INTEGER NOT NULL DEFAULT 1,
    min_replicas           INTEGER NOT NULL DEFAULT 0,
    max_replicas           INTEGER NOT NULL DEFAULT 1,
    gpu_count              INTEGER NOT NULL DEFAULT 1,
    slice_vram_bytes       BIGINT,
    slice_compute_pct      SMALLINT,
    tp_size                INTEGER NOT NULL DEFAULT 1,
    pp_size                INTEGER NOT NULL DEFAULT 1,
    preset                 deployment_preset,
    pd_split               BOOLEAN NOT NULL DEFAULT FALSE,
    pd_config              JSONB NOT NULL DEFAULT '{}'::JSONB,
    spec_decoding          JSONB NOT NULL DEFAULT '{}'::JSONB,
    kv_sharing             BOOLEAN NOT NULL DEFAULT FALSE,
    kv_sharing_config      JSONB NOT NULL DEFAULT '{}'::JSONB,
    launch_flags           JSONB NOT NULL DEFAULT '{}'::JSONB,
    env_vars               JSONB NOT NULL DEFAULT '{}'::JSONB,
    tool_parser            VARCHAR(100),
    context_size           INTEGER,
    control_vectors        JSONB NOT NULL DEFAULT '[]'::JSONB,
    placement_strategy     placement_strategy NOT NULL DEFAULT 'spread',
    node_selector          JSONB NOT NULL DEFAULT '{}'::JSONB,
    device_vendor_filter   device_vendor[] NOT NULL DEFAULT '{}',
    traffic_config         JSONB NOT NULL DEFAULT '{"strategy":"least_sessions","failover":true,"max_retries":1,"health_eject":true}'::JSONB,
    status                 deployment_status NOT NULL DEFAULT 'pending',
    status_message         VARCHAR(2048),
    desired_running        BOOLEAN NOT NULL DEFAULT TRUE,
    last_request_at        TIMESTAMPTZ,
    created_by             UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at             TIMESTAMPTZ,
    CONSTRAINT deployments_replicas_range CHECK (min_replicas >= 0 AND replicas >= min_replicas AND max_replicas >= replicas),
    CONSTRAINT deployments_gpu_count_pos CHECK (gpu_count >= 1),
    CONSTRAINT deployments_slice_pct_range CHECK (slice_compute_pct IS NULL OR (slice_compute_pct BETWEEN 1 AND 100)),
    CONSTRAINT deployments_parallel_pos CHECK (tp_size >= 1 AND pp_size >= 1)
);
COMMENT ON TABLE  deployments IS 'Desired state: model version + engine version + replicas + GPU shape + launch settings. The scheduler turns it into instances.';
COMMENT ON COLUMN deployments.gpu_count IS 'GPUs per replica (tp_size * pp_size must divide into it; 1 for llama.cpp/MLX).';
COMMENT ON COLUMN deployments.slice_vram_bytes IS 'When set, each replica gets a GPU slice of this VRAM (and slice_compute_pct) instead of whole devices.';
COMMENT ON COLUMN deployments.spec_decoding IS '{"enabled":bool,"draft_model_version_id":uuid|null,"num_draft_tokens":int,"method":"draft_model|ngram|eagle|mtp"}';
COMMENT ON COLUMN deployments.kv_sharing_config IS '{"backend":"lmcache|mooncake|native","remote_url":null}';
COMMENT ON COLUMN deployments.pd_config IS '{"prefill_replicas":int,"decode_replicas":int,"transfer":"nixl|mooncake"}';
COMMENT ON COLUMN deployments.control_vectors IS 'llama.cpp only: [{"path":"...gguf","scale":0.8,"layer_range":[10,20]}]';
COMMENT ON COLUMN deployments.node_selector IS 'Label selector {"zone":"a","gpu.tier":"h100"} and/or {"node_ids":[...]}; empty = any node in cluster_id (or any cluster).';
COMMENT ON COLUMN deployments.traffic_config IS '{"strategy":"round_robin|least_sessions|weighted","failover":bool,"max_retries":int,"health_eject":bool,"sticky_sessions":bool}';
COMMENT ON COLUMN deployments.desired_running IS 'FALSE after an explicit stop; rules and scheduler do not start instances for it.';
CREATE UNIQUE INDEX IF NOT EXISTS idx_deployments_name_unique ON deployments(lower(name)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_deployments_model_id   ON deployments(model_id)   WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_deployments_status     ON deployments(status)     WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_deployments_cluster_id ON deployments(cluster_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_deployments_name_trgm  ON deployments USING GIN (name gin_trgm_ops) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS instances (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deployment_id     UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    node_id           UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    device_ids        UUID[] NOT NULL DEFAULT '{}',
    port              INTEGER,
    served_model_name VARCHAR(255),
    engine_process_id UUID,
    state             instance_state NOT NULL DEFAULT 'pending',
    state_message     VARCHAR(2048),
    health            instance_health NOT NULL DEFAULT 'unknown',
    health_message    VARCHAR(1024),
    traffic_weight    SMALLINT NOT NULL DEFAULT 100,
    pd_role           VARCHAR(16),
    pid               INTEGER,
    container_id      VARCHAR(128),
    resources         JSONB NOT NULL DEFAULT '{}'::JSONB,
    restart_count     INTEGER NOT NULL DEFAULT 0,
    state_changed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at        TIMESTAMPTZ,
    ready_at          TIMESTAMPTZ,
    stopped_at        TIMESTAMPTZ,
    last_health_at    TIMESTAMPTZ,
    last_request_at   TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT instances_traffic_weight_range CHECK (traffic_weight BETWEEN 0 AND 1000),
    CONSTRAINT instances_pd_role_valid CHECK (pd_role IS NULL OR pd_role IN ('prefill', 'decode'))
);
COMMENT ON TABLE  instances IS 'One running (or desired) replica of a deployment on one node. device_ids are devices.id (array; integrity enforced by the scheduler, GIN-indexed for reverse lookups). Stopped instances are kept for history and pruned by the jobs process.';
COMMENT ON COLUMN instances.port IS 'Localhost port of the engine on the node. In llama.cpp router mode all instances of a router share the port and differ by served_model_name.';
COMMENT ON COLUMN instances.served_model_name IS 'Model id the engine itself answers to (router model name, vLLM --served-model-name). The API layer rewrites the request "model" field to this value.';
COMMENT ON COLUMN instances.engine_process_id IS 'Groups instances that share one engine process (llama.cpp router). NULL = dedicated process.';
COMMENT ON COLUMN instances.traffic_weight IS '0 = drained from load balancing; 100 = default; up to 1000 for weighted routing.';
CREATE INDEX IF NOT EXISTS idx_instances_deployment_id ON instances(deployment_id);
CREATE INDEX IF NOT EXISTS idx_instances_node_id       ON instances(node_id);
CREATE INDEX IF NOT EXISTS idx_instances_state         ON instances(state);
CREATE INDEX IF NOT EXISTS idx_instances_device_ids    ON instances USING GIN (device_ids);
CREATE UNIQUE INDEX IF NOT EXISTS idx_instances_node_port_model_unique ON instances(node_id, port, COALESCE(served_model_name, '')) WHERE state <> 'stopped' AND port IS NOT NULL;

CREATE TABLE IF NOT EXISTS device_slices (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id     UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    instance_id   UUID REFERENCES instances(id) ON DELETE SET NULL,
    deployment_id UUID REFERENCES deployments(id) ON DELETE SET NULL,
    name          VARCHAR(255),
    vram_bytes    BIGINT NOT NULL,
    compute_pct   SMALLINT NOT NULL DEFAULT 100,
    status        device_slice_status NOT NULL DEFAULT 'reserved',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    released_at   TIMESTAMPTZ,
    CONSTRAINT device_slices_vram_pos CHECK (vram_bytes > 0),
    CONSTRAINT device_slices_compute_pct_range CHECK (compute_pct BETWEEN 1 AND 100)
);
COMMENT ON TABLE  device_slices IS 'Concrete VRAM/compute reservation on one device for one instance (GPU slicing). The scheduler guarantees sum(vram_bytes) of active slices <= devices.vram_total_bytes.';
CREATE INDEX IF NOT EXISTS idx_device_slices_device_id   ON device_slices(device_id) WHERE status <> 'released';
CREATE INDEX IF NOT EXISTS idx_device_slices_instance_id ON device_slices(instance_id);

-- =============================================================================
-- MODULE: GPU layouts
-- =============================================================================
CREATE TABLE IF NOT EXISTS layouts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(255) NOT NULL,
    description VARCHAR(2048),
    is_active   BOOLEAN NOT NULL DEFAULT FALSE,
    applied_at  TIMESTAMPTZ,
    created_by  UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at  TIMESTAMPTZ
);
COMMENT ON TABLE  layouts IS 'Named GPU layouts (Day, Night, ...). Applying a layout computes the diff against running instances and executes swap_operations. is_active marks the last applied layout.';
CREATE UNIQUE INDEX IF NOT EXISTS idx_layouts_name_unique ON layouts(lower(name)) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_layouts_single_active ON layouts(is_active) WHERE is_active AND deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS layout_items (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    layout_id         UUID NOT NULL REFERENCES layouts(id) ON DELETE CASCADE,
    position          INTEGER NOT NULL DEFAULT 0,
    deployment_id     UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    node_id           UUID REFERENCES nodes(id) ON DELETE CASCADE,
    device_ids        UUID[] NOT NULL DEFAULT '{}',
    replicas          INTEGER NOT NULL DEFAULT 1,
    slice_vram_bytes  BIGINT,
    slice_compute_pct SMALLINT,
    traffic_weight    SMALLINT NOT NULL DEFAULT 100,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT layout_items_replicas_nonneg CHECK (replicas >= 0)
);
COMMENT ON TABLE  layout_items IS 'Which deployment runs where in a layout. node_id NULL + empty device_ids = let the scheduler place it. replicas 0 = deployment is unloaded in this layout. Deployments not listed are left untouched (see rules/layout.schema.json).';
CREATE INDEX IF NOT EXISTS idx_layout_items_layout_id ON layout_items(layout_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_layout_items_layout_position_unique ON layout_items(layout_id, position);

-- =============================================================================
-- MODULE: Rules engine
-- =============================================================================
CREATE TABLE IF NOT EXISTS rules (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                        VARCHAR(255) NOT NULL,
    description                 VARCHAR(2048),
    type                        rule_type NOT NULL,
    params                      JSONB NOT NULL DEFAULT '{}'::JSONB,
    target                      JSONB NOT NULL DEFAULT '{}'::JSONB,
    enabled                     BOOLEAN NOT NULL DEFAULT TRUE,
    dry_run                     BOOLEAN NOT NULL DEFAULT FALSE,
    cooldown_seconds            INTEGER NOT NULL DEFAULT 300,
    evaluation_interval_seconds INTEGER NOT NULL DEFAULT 30,
    priority                    INTEGER NOT NULL DEFAULT 100,
    last_evaluated_at           TIMESTAMPTZ,
    last_fired_at               TIMESTAMPTZ,
    fire_count                  BIGINT NOT NULL DEFAULT 0,
    created_by                  UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at                  TIMESTAMPTZ,
    CONSTRAINT rules_cooldown_nonneg CHECK (cooldown_seconds >= 0),
    CONSTRAINT rules_interval_min CHECK (evaluation_interval_seconds >= 5)
);
COMMENT ON TABLE  rules IS 'User-built rules. params is validated against libs/contracts/rules/<type>.schema.json; target against target-selector.schema.json. priority: lower runs first when several rules fire in one tick.';
CREATE UNIQUE INDEX IF NOT EXISTS idx_rules_name_unique ON rules(lower(name)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_rules_type_enabled ON rules(type, enabled) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS rule_runs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id       UUID NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
    rule_type     rule_type NOT NULL,
    evaluated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fired         BOOLEAN NOT NULL DEFAULT FALSE,
    dry_run       BOOLEAN NOT NULL DEFAULT FALSE,
    outcome       rule_run_outcome NOT NULL,
    decision      JSONB NOT NULL DEFAULT '{}'::JSONB,
    actions       JSONB NOT NULL DEFAULT '[]'::JSONB,
    error_message VARCHAR(2048),
    duration_ms   INTEGER,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE  rule_runs IS 'History of every rule evaluation that matched, fired, dry-ran or errored (no_match rows are written at most once per RULE_RUN_NO_MATCH_LOG_INTERVAL_SECONDS to keep volume down). decision = observed values + reason; actions = [{action, target_type, target_id, swap_operation_id, status}].';
CREATE INDEX IF NOT EXISTS idx_rule_runs_rule_evaluated ON rule_runs(rule_id, evaluated_at DESC);
CREATE INDEX IF NOT EXISTS idx_rule_runs_evaluated_at   ON rule_runs(evaluated_at DESC);
CREATE INDEX IF NOT EXISTS idx_rule_runs_fired          ON rule_runs(evaluated_at DESC) WHERE fired;

-- =============================================================================
-- MODULE: Swap manager / agent commands
-- =============================================================================
CREATE TABLE IF NOT EXISTS swap_operations (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind                  swap_kind NOT NULL,
    state                 swap_state NOT NULL DEFAULT 'pending',
    trigger               swap_trigger NOT NULL,
    rule_run_id           UUID REFERENCES rule_runs(id) ON DELETE SET NULL,
    layout_id             UUID REFERENCES layouts(id) ON DELETE SET NULL,
    deployment_id         UUID REFERENCES deployments(id) ON DELETE SET NULL,
    from_instance_id      UUID REFERENCES instances(id) ON DELETE SET NULL,
    to_instance_id        UUID REFERENCES instances(id) ON DELETE SET NULL,
    node_id               UUID REFERENCES nodes(id) ON DELETE SET NULL,
    plan                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    steps                 JSONB NOT NULL DEFAULT '[]'::JSONB,
    current_step          INTEGER NOT NULL DEFAULT 0,
    rollback              JSONB NOT NULL DEFAULT '{}'::JSONB,
    drain_timeout_seconds INTEGER NOT NULL DEFAULT 120,
    dry_run               BOOLEAN NOT NULL DEFAULT FALSE,
    started_at            TIMESTAMPTZ,
    finished_at           TIMESTAMPTZ,
    error_message         VARCHAR(2048),
    created_by            UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE  swap_operations IS 'State machine for every load/unload/swap/scale/move/restart. steps = [{"name":"drain","state":"completed","started_at","finished_at","error","command_id"}] in the order drain -> wait -> unload -> load -> health -> (rollback). rollback holds what to restore on failure (previous instance spec and devices).';
COMMENT ON COLUMN swap_operations.plan IS 'Immutable input: {"unload":[instance_ids],"load":[{deployment_id,node_id,device_ids,slice}],"reason":"..."}';
CREATE INDEX IF NOT EXISTS idx_swap_operations_state      ON swap_operations(state) WHERE state NOT IN ('completed', 'failed', 'rolled_back', 'cancelled');
CREATE INDEX IF NOT EXISTS idx_swap_operations_deployment ON swap_operations(deployment_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_swap_operations_created_at ON swap_operations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_swap_operations_rule_run   ON swap_operations(rule_run_id);

CREATE TABLE IF NOT EXISTS agent_commands (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id           UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    command_type      VARCHAR(50) NOT NULL,
    payload           JSONB NOT NULL DEFAULT '{}'::JSONB,
    state             agent_command_state NOT NULL DEFAULT 'pending',
    attempts          INTEGER NOT NULL DEFAULT 0,
    swap_operation_id UUID REFERENCES swap_operations(id) ON DELETE SET NULL,
    instance_id       UUID REFERENCES instances(id) ON DELETE SET NULL,
    result            JSONB,
    error_code        VARCHAR(100),
    error_message     VARCHAR(2048),
    progress_pct      REAL,
    expires_at        TIMESTAMPTZ,
    sent_at           TIMESTAMPTZ,
    accepted_at       TIMESTAMPTZ,
    completed_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE  agent_commands IS 'Every command sent to a node agent. id is the envelope id / correlation_id of the command message; command_result messages update state. Re-sent unchanged after reconnect while state in (pending, sent) and not expired; the agent is idempotent on id.';
CREATE INDEX IF NOT EXISTS idx_agent_commands_node_state ON agent_commands(node_id, state) WHERE state IN ('pending', 'sent', 'accepted', 'running');
CREATE INDEX IF NOT EXISTS idx_agent_commands_swap       ON agent_commands(swap_operation_id);
CREATE INDEX IF NOT EXISTS idx_agent_commands_created_at ON agent_commands(created_at DESC);

-- =============================================================================
-- MODULE: Alerts
-- =============================================================================
CREATE TABLE IF NOT EXISTS alert_channels (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name           VARCHAR(255) NOT NULL,
    type           alert_channel_type NOT NULL,
    config         JSONB NOT NULL DEFAULT '{}'::JSONB,
    credential_id  UUID REFERENCES credentials(id) ON DELETE SET NULL,
    enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    min_severity   alert_severity NOT NULL DEFAULT 'warning',
    event_filter   TEXT[] NOT NULL DEFAULT '{}',
    last_test_at   TIMESTAMPTZ,
    last_test_ok   BOOLEAN,
    last_error     VARCHAR(2048),
    created_by     UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at     TIMESTAMPTZ
);
COMMENT ON TABLE  alert_channels IS 'Slack / email / webhook destinations. Non-secret settings in config ({"channel":"#gpu","recipients":[...],"url":"https://..."}); the secret (webhook URL with token, SMTP password, signing secret) is a vault credential via credential_id. event_filter = list of event_type globs, empty = all.';
CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_channels_name_unique ON alert_channels(lower(name)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_alert_channels_enabled ON alert_channels(enabled) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS alert_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    severity        alert_severity NOT NULL,
    event_type      VARCHAR(100) NOT NULL,
    title           VARCHAR(512) NOT NULL,
    message         TEXT,
    resource_type   VARCHAR(100),
    resource_id     UUID,
    resource_name   VARCHAR(255),
    payload         JSONB NOT NULL DEFAULT '{}'::JSONB,
    dedupe_key      VARCHAR(255),
    status          alert_event_status NOT NULL DEFAULT 'open',
    occurrences     INTEGER NOT NULL DEFAULT 1,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acknowledged_at TIMESTAMPTZ,
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE  alert_events IS 'Things worth telling a human: rule actions, swap failures, node offline, device unhealthy. Repeated events with the same dedupe_key within ALERT_DEDUPE_WINDOW_SECONDS bump occurrences instead of creating rows.';
CREATE INDEX IF NOT EXISTS idx_alert_events_status_seen ON alert_events(status, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_events_event_type  ON alert_events(event_type, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_events_resource    ON alert_events(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_alert_events_dedupe      ON alert_events(dedupe_key, last_seen_at DESC) WHERE dedupe_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS alert_deliveries (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_event_id UUID NOT NULL REFERENCES alert_events(id) ON DELETE CASCADE,
    channel_id     UUID NOT NULL REFERENCES alert_channels(id) ON DELETE CASCADE,
    status         alert_delivery_status NOT NULL DEFAULT 'pending',
    attempts       INTEGER NOT NULL DEFAULT 0,
    sent_at        TIMESTAMPTZ,
    response_code  INTEGER,
    error_message  VARCHAR(2048),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE  alert_deliveries IS 'One row per (event, channel) send attempt chain. Written by the alerts Celery task.';
CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_deliveries_event_channel_unique ON alert_deliveries(alert_event_id, channel_id);
CREATE INDEX IF NOT EXISTS idx_alert_deliveries_pending ON alert_deliveries(status, created_at) WHERE status = 'pending';

-- =============================================================================
-- MODULE: Infra observability / Inference observability (partitioned by day)
-- =============================================================================
CREATE TABLE IF NOT EXISTS metric_samples_gpu (
    id                  BIGSERIAL,
    ts                  TIMESTAMPTZ NOT NULL,
    node_id             UUID NOT NULL,
    device_id           UUID NOT NULL,
    utilization_pct     REAL,
    memory_util_pct     REAL,
    memory_used_bytes   BIGINT,
    memory_total_bytes  BIGINT,
    power_watts         REAL,
    power_limit_watts   REAL,
    temperature_c       REAL,
    memory_temperature_c REAL,
    sm_clock_mhz        INTEGER,
    memory_clock_mhz    INTEGER,
    fan_pct             REAL,
    encoder_pct         REAL,
    decoder_pct         REAL,
    pcie_tx_bytes_per_s BIGINT,
    pcie_rx_bytes_per_s BIGINT,
    ecc_errors_total    BIGINT,
    error_count         INTEGER,
    throttle_reasons    TEXT[],
    process_count       INTEGER,
    extra               JSONB,
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);
COMMENT ON TABLE  metric_samples_gpu IS 'Raw per-device samples every 1-2 s from the node agent (NVML / amd-smi / vendor SMI / Apple IOKit). Retention METRICS_RAW_RETENTION_HOURS (48 h). No FKs on purpose (ingest speed; ids are validated by the hub).';
COMMENT ON COLUMN metric_samples_gpu.error_count IS 'Vendor error events since last sample (NVIDIA Xid, AMD RAS, ...).';
CREATE INDEX IF NOT EXISTS idx_metric_samples_gpu_device_ts ON metric_samples_gpu(device_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_metric_samples_gpu_node_ts   ON metric_samples_gpu(node_id, ts DESC);

CREATE TABLE IF NOT EXISTS metric_samples_engine (
    id                      BIGSERIAL,
    ts                      TIMESTAMPTZ NOT NULL,
    node_id                 UUID NOT NULL,
    instance_id             UUID NOT NULL,
    deployment_id           UUID NOT NULL,
    active_sessions         INTEGER,
    waiting_sessions        INTEGER,
    slots_total             INTEGER,
    slots_idle              INTEGER,
    requests_total          BIGINT,
    requests_success_total  BIGINT,
    errors_total            BIGINT,
    prompt_tokens_total     BIGINT,
    generated_tokens_total  BIGINT,
    cached_tokens_total     BIGINT,
    prompt_tps              REAL,
    generation_tps          REAL,
    ttft_ms_avg             REAL,
    ttft_ms_p50             REAL,
    ttft_ms_p95             REAL,
    e2e_ms_avg              REAL,
    e2e_ms_p50              REAL,
    e2e_ms_p95              REAL,
    tpot_ms_avg             REAL,
    queue_ms_avg            REAL,
    kv_cache_used_tokens    BIGINT,
    kv_cache_total_tokens   BIGINT,
    kv_cache_usage_pct      REAL,
    kv_cache_bytes          BIGINT,
    prefix_cache_hit_rate   REAL,
    spec_accept_rate        REAL,
    process_vram_bytes      BIGINT,
    process_rss_bytes       BIGINT,
    health                  instance_health,
    extra                   JSONB,
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);
COMMENT ON TABLE  metric_samples_engine IS 'Raw per-instance samples every 1-2 s scraped by the node agent from engine /metrics (+ /slots) or derived from the agent proxy. *_total columns are monotonic counters since engine start; rates are computed in rollups from deltas. See libs/contracts/metrics.md.';
CREATE INDEX IF NOT EXISTS idx_metric_samples_engine_instance_ts   ON metric_samples_engine(instance_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_metric_samples_engine_deployment_ts ON metric_samples_engine(deployment_id, ts DESC);

CREATE TABLE IF NOT EXISTS metric_samples_session (
    id                BIGSERIAL,
    ts                TIMESTAMPTZ NOT NULL,
    node_id           UUID NOT NULL,
    instance_id       UUID NOT NULL,
    slot_id           INTEGER,
    session_key       VARCHAR(128) NOT NULL,
    request_id        UUID,
    state             VARCHAR(16) NOT NULL,
    prompt_tokens     INTEGER,
    generated_tokens  INTEGER,
    cached_tokens     INTEGER,
    kv_cache_tokens   INTEGER,
    kv_cache_bytes    BIGINT,
    context_size      INTEGER,
    ttft_ms           REAL,
    elapsed_ms        REAL,
    tokens_per_second REAL,
    extra             JSONB,
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);
COMMENT ON TABLE  metric_samples_session IS 'Per-session / per-slot samples (llama.cpp /slots, vLLM/SGLang per-request where exposed, agent-proxy derived otherwise). session_key = slot id_task, request id, or proxy connection id. state: idle|processing|generating|waiting. Retention as raw metrics.';
CREATE INDEX IF NOT EXISTS idx_metric_samples_session_instance_ts ON metric_samples_session(instance_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_metric_samples_session_request_id  ON metric_samples_session(request_id) WHERE request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS metric_rollups_gpu_1m (
    id                     BIGSERIAL,
    ts                     TIMESTAMPTZ NOT NULL,
    node_id                UUID NOT NULL,
    device_id              UUID NOT NULL,
    sample_count           INTEGER NOT NULL,
    utilization_pct_avg    REAL,
    utilization_pct_max    REAL,
    memory_used_bytes_avg  BIGINT,
    memory_used_bytes_max  BIGINT,
    memory_total_bytes     BIGINT,
    power_watts_avg        REAL,
    power_watts_max        REAL,
    energy_wh              REAL,
    temperature_c_avg      REAL,
    temperature_c_max      REAL,
    sm_clock_mhz_avg       INTEGER,
    error_count_sum        INTEGER,
    throttled_seconds      REAL,
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);
COMMENT ON TABLE  metric_rollups_gpu_1m IS '1-minute rollups of metric_samples_gpu (ts = minute bucket start). Built by the metrics rollup job every minute for the previous minute. Retention METRICS_ROLLUP_RETENTION_DAYS (90 d).';
CREATE UNIQUE INDEX IF NOT EXISTS idx_metric_rollups_gpu_1m_device_ts_unique ON metric_rollups_gpu_1m(device_id, ts);
CREATE INDEX IF NOT EXISTS idx_metric_rollups_gpu_1m_node_ts ON metric_rollups_gpu_1m(node_id, ts DESC);

CREATE TABLE IF NOT EXISTS metric_rollups_engine_1m (
    id                      BIGSERIAL,
    ts                      TIMESTAMPTZ NOT NULL,
    node_id                 UUID NOT NULL,
    instance_id             UUID NOT NULL,
    deployment_id           UUID NOT NULL,
    sample_count            INTEGER NOT NULL,
    active_sessions_avg     REAL,
    active_sessions_max     INTEGER,
    waiting_sessions_avg    REAL,
    waiting_sessions_max    INTEGER,
    requests_delta          BIGINT,
    requests_success_delta  BIGINT,
    errors_delta            BIGINT,
    prompt_tokens_delta     BIGINT,
    generated_tokens_delta  BIGINT,
    cached_tokens_delta     BIGINT,
    prompt_tps_avg          REAL,
    generation_tps_avg      REAL,
    ttft_ms_avg             REAL,
    ttft_ms_p95             REAL,
    e2e_ms_avg              REAL,
    e2e_ms_p95              REAL,
    tpot_ms_avg             REAL,
    kv_cache_usage_pct_avg  REAL,
    kv_cache_usage_pct_max  REAL,
    prefix_cache_hit_rate   REAL,
    error_rate_pct          REAL,
    healthy_seconds         REAL,
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);
COMMENT ON TABLE  metric_rollups_engine_1m IS '1-minute rollups of metric_samples_engine; *_delta columns are counter differences within the minute (requests per minute = requests_delta). Rules read these (and the raw tables for the last minute). Retention METRICS_ROLLUP_RETENTION_DAYS.';
CREATE UNIQUE INDEX IF NOT EXISTS idx_metric_rollups_engine_1m_instance_ts_unique ON metric_rollups_engine_1m(instance_id, ts);
CREATE INDEX IF NOT EXISTS idx_metric_rollups_engine_1m_deployment_ts ON metric_rollups_engine_1m(deployment_id, ts DESC);

-- =============================================================================
-- MODULE: Logs and traces (partitioned by day)
-- =============================================================================
CREATE TABLE IF NOT EXISTS log_chunks (
    id            BIGSERIAL,
    ts            TIMESTAMPTZ NOT NULL,
    ts_end        TIMESTAMPTZ,
    node_id       UUID,
    instance_id   UUID,
    deployment_id UUID,
    source        log_source NOT NULL,
    stream        log_stream NOT NULL DEFAULT 'stdout',
    level         log_level NOT NULL DEFAULT 'info',
    logger        VARCHAR(255),
    request_id    UUID,
    line_count    INTEGER NOT NULL DEFAULT 1,
    byte_size     INTEGER NOT NULL DEFAULT 0,
    content       TEXT NOT NULL,
    attributes    JSONB,
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);
COMMENT ON TABLE  log_chunks IS 'Engine stdout/stderr and agent/control-plane/rule/swap log lines, batched into chunks of up to LOG_CHUNK_MAX_LINES lines (content is newline-separated; ts = first line, ts_end = last). level = max level in the chunk. Retention LOGS_RETENTION_DAYS.';
CREATE INDEX IF NOT EXISTS idx_log_chunks_instance_ts   ON log_chunks(instance_id, ts DESC) WHERE instance_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_log_chunks_node_ts       ON log_chunks(node_id, ts DESC) WHERE node_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_log_chunks_source_level  ON log_chunks(source, level, ts DESC);
CREATE INDEX IF NOT EXISTS idx_log_chunks_request_id    ON log_chunks(request_id) WHERE request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS traces (
    id             BIGSERIAL,
    ts             TIMESTAMPTZ NOT NULL,
    trace_id       VARCHAR(32) NOT NULL,
    span_id        VARCHAR(16) NOT NULL,
    parent_span_id VARCHAR(16),
    name           VARCHAR(255) NOT NULL,
    service        VARCHAR(50) NOT NULL,
    request_id     UUID,
    api_key_id     UUID,
    model_id       UUID,
    deployment_id  UUID,
    instance_id    UUID,
    node_id        UUID,
    format         usage_format,
    endpoint       VARCHAR(100),
    status_code    INTEGER,
    duration_ms    REAL,
    error          BOOLEAN NOT NULL DEFAULT FALSE,
    attributes     JSONB,
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);
COMMENT ON TABLE  traces IS 'Span index for the UI (request -> trace lookup and span waterfall). Full OpenTelemetry spans go to OTEL_EXPORTER_OTLP_ENDPOINT; this table keeps the subset the UI needs for TRACES_RETENTION_DAYS. service: api_layer|admin_api|jobs|node_agent.';
CREATE INDEX IF NOT EXISTS idx_traces_trace_id   ON traces(trace_id, ts);
CREATE INDEX IF NOT EXISTS idx_traces_request_id ON traces(request_id) WHERE request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_traces_ts_service ON traces(service, ts DESC);

-- =============================================================================
-- MODULE: Metering / Billing and reports
-- =============================================================================
CREATE TABLE IF NOT EXISTS usage_events (
    id               BIGSERIAL,
    ts               TIMESTAMPTZ NOT NULL,
    request_id       UUID NOT NULL,
    api_key_id       UUID,
    model_id         UUID,
    model_name       VARCHAR(255) NOT NULL,
    deployment_id    UUID,
    instance_id      UUID,
    node_id          UUID,
    cluster_id       UUID,
    format           usage_format NOT NULL,
    endpoint         VARCHAR(100) NOT NULL,
    stream           BOOLEAN NOT NULL DEFAULT FALSE,
    request_count    INTEGER NOT NULL DEFAULT 1,
    input_tokens     INTEGER NOT NULL DEFAULT 0,
    output_tokens    INTEGER NOT NULL DEFAULT 0,
    cached_tokens    INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    media_units      NUMERIC(12,3) NOT NULL DEFAULT 0,
    latency_ms       INTEGER,
    ttft_ms          INTEGER,
    gpu_seconds      NUMERIC(12,3) NOT NULL DEFAULT 0,
    gpu_count        SMALLINT NOT NULL DEFAULT 1,
    status           usage_status NOT NULL,
    http_status      SMALLINT,
    error_code       VARCHAR(100),
    price_plan_id    UUID,
    cost_usd         NUMERIC(14,8) NOT NULL DEFAULT 0,
    ip_address       INET,
    metadata         JSONB,
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);
COMMENT ON TABLE  usage_events IS 'One row per serving request, written by the metering Celery task after the response (never on the request path). gpu_seconds = latency_ms/1000 * gpu_count * slice_compute_pct/100. cost_usd computed from the resolved price plan at write time (node plan, else cluster plan, else default plan, else 0). Retention USAGE_EVENTS_RETENTION_DAYS.';
COMMENT ON COLUMN usage_events.media_units IS 'Non-token work: audio seconds (stt/tts), images generated, video seconds.';
CREATE INDEX IF NOT EXISTS idx_usage_events_api_key_ts    ON usage_events(api_key_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_usage_events_model_ts      ON usage_events(model_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_usage_events_node_ts       ON usage_events(node_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_usage_events_deployment_ts ON usage_events(deployment_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_usage_events_request_id    ON usage_events(request_id, ts);

CREATE TABLE IF NOT EXISTS usage_rollups_hourly (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bucket           TIMESTAMPTZ NOT NULL,
    api_key_id       UUID,
    model_id         UUID,
    node_id          UUID,
    deployment_id    UUID,
    request_count    BIGINT NOT NULL DEFAULT 0,
    success_count    BIGINT NOT NULL DEFAULT 0,
    error_count      BIGINT NOT NULL DEFAULT 0,
    input_tokens     BIGINT NOT NULL DEFAULT 0,
    output_tokens    BIGINT NOT NULL DEFAULT 0,
    cached_tokens    BIGINT NOT NULL DEFAULT 0,
    reasoning_tokens BIGINT NOT NULL DEFAULT 0,
    media_units      NUMERIC(14,3) NOT NULL DEFAULT 0,
    gpu_seconds      NUMERIC(14,3) NOT NULL DEFAULT 0,
    latency_ms_sum   BIGINT NOT NULL DEFAULT 0,
    ttft_ms_sum      BIGINT NOT NULL DEFAULT 0,
    cost_usd         NUMERIC(14,6) NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT usage_rollups_hourly_key_unique UNIQUE NULLS NOT DISTINCT (bucket, api_key_id, model_id, node_id, deployment_id)
);
COMMENT ON TABLE  usage_rollups_hourly IS 'Hourly aggregates per (api key, model, node, deployment) built from usage_events by the metering job (upsert). Billing reports and the usage screens read this; usage_events is for drill-down.';
CREATE INDEX IF NOT EXISTS idx_usage_rollups_hourly_bucket  ON usage_rollups_hourly(bucket DESC);
CREATE INDEX IF NOT EXISTS idx_usage_rollups_hourly_api_key ON usage_rollups_hourly(api_key_id, bucket DESC);
CREATE INDEX IF NOT EXISTS idx_usage_rollups_hourly_model   ON usage_rollups_hourly(model_id, bucket DESC);
CREATE INDEX IF NOT EXISTS idx_usage_rollups_hourly_node    ON usage_rollups_hourly(node_id, bucket DESC);

CREATE TABLE IF NOT EXISTS billing_reports (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          VARCHAR(255) NOT NULL,
    period_start  TIMESTAMPTZ NOT NULL,
    period_end    TIMESTAMPTZ NOT NULL,
    group_by      TEXT[] NOT NULL DEFAULT '{api_key,model}',
    filters       JSONB NOT NULL DEFAULT '{}'::JSONB,
    status        billing_report_status NOT NULL DEFAULT 'pending',
    totals        JSONB NOT NULL DEFAULT '{}'::JSONB,
    line_items    JSONB NOT NULL DEFAULT '[]'::JSONB,
    currency      CHAR(3) NOT NULL DEFAULT 'USD',
    generated_at  TIMESTAMPTZ,
    error_message VARCHAR(2048),
    created_by    UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at    TIMESTAMPTZ,
    CONSTRAINT billing_reports_period_valid CHECK (period_end > period_start)
);
COMMENT ON TABLE  billing_reports IS 'Generated cost reports (monthly by default, any period). totals = {"cost_usd","gpu_minutes","input_tokens","output_tokens","cached_tokens","requests"}; line_items = [{group keys..., same totals}]. CSV export is rendered from line_items on demand.';
CREATE INDEX IF NOT EXISTS idx_billing_reports_period ON billing_reports(period_start DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_billing_reports_status ON billing_reports(status) WHERE deleted_at IS NULL;

-- =============================================================================
-- MODULE: Benchmark
-- =============================================================================
CREATE TABLE IF NOT EXISTS benchmarks (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name               VARCHAR(255) NOT NULL,
    description        VARCHAR(2048),
    deployment_id      UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
    target_slo         JSONB NOT NULL DEFAULT '{}'::JSONB,
    concurrency_levels INTEGER[] NOT NULL DEFAULT '{1,2,4,8,16}',
    workload           JSONB NOT NULL DEFAULT '{"input_tokens":512,"output_tokens":128,"requests_per_level":32,"dataset":"synthetic"}'::JSONB,
    duration_seconds   INTEGER NOT NULL DEFAULT 60,
    auto_tune          BOOLEAN NOT NULL DEFAULT FALSE,
    created_by         UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at         TIMESTAMPTZ
);
COMMENT ON TABLE  benchmarks IS 'Benchmark definition: deployment, target SLO {"ttft_ms_p95":500,"e2e_ms_p95":5000,"min_generation_tps":20}, concurrency sweep and workload. auto_tune asks the run to also try launch-flag variants and recommend the best.';
CREATE UNIQUE INDEX IF NOT EXISTS idx_benchmarks_name_unique ON benchmarks(lower(name)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_benchmarks_deployment_id ON benchmarks(deployment_id) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS benchmark_runs (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    benchmark_id     UUID NOT NULL REFERENCES benchmarks(id) ON DELETE CASCADE,
    instance_id      UUID REFERENCES instances(id) ON DELETE SET NULL,
    node_id          UUID REFERENCES nodes(id) ON DELETE SET NULL,
    status           benchmark_run_status NOT NULL DEFAULT 'pending',
    progress_pct     REAL NOT NULL DEFAULT 0,
    results          JSONB NOT NULL DEFAULT '[]'::JSONB,
    best_concurrency INTEGER,
    slo_met          BOOLEAN,
    recommendation   JSONB NOT NULL DEFAULT '{}'::JSONB,
    error_message    VARCHAR(2048),
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    created_by       UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE  benchmark_runs IS 'One execution of a benchmark (run_benchmark agent command). results = [{"concurrency":n,"requests":n,"rps":x,"generation_tps":x,"ttft_ms":{"p50","p95","p99"},"e2e_ms":{...},"errors":n}]; recommendation = {"concurrency":n,"launch_flags":{...},"replicas":n,"reason":"..."}.';
CREATE INDEX IF NOT EXISTS idx_benchmark_runs_benchmark_id ON benchmark_runs(benchmark_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_benchmark_runs_status ON benchmark_runs(status) WHERE status IN ('pending', 'running');

-- =============================================================================
-- MODULE: High availability (leader election for the jobs process)
-- =============================================================================
CREATE TABLE IF NOT EXISTS ha_leases (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(100) NOT NULL,
    holder_id   VARCHAR(255),
    acquired_at TIMESTAMPTZ,
    renewed_at  TIMESTAMPTZ,
    expires_at  TIMESTAMPTZ,
    version     BIGINT NOT NULL DEFAULT 0,
    metadata    JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ha_leases_name_unique UNIQUE (name)
);
COMMENT ON TABLE  ha_leases IS 'Leader election leases (names: jobs-leader, rules-evaluator, metrics-rollup, agent-hub-<cluster>). Acquire with UPDATE ... WHERE expires_at < now() OR holder_id = me; version is a fencing token incremented on every acquire. TTL HA_LEASE_TTL_SECONDS, renew every HA_LEASE_RENEW_SECONDS.';

-- =============================================================================
-- MODULE: API formats — Anthropic message batches
-- =============================================================================
CREATE TABLE IF NOT EXISTS batches (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id         VARCHAR(64) NOT NULL,
    api_key_id          UUID REFERENCES api_keys(id) ON DELETE SET NULL,
    format              usage_format NOT NULL DEFAULT 'anthropic',
    status              batch_status NOT NULL DEFAULT 'in_progress',
    request_counts      JSONB NOT NULL DEFAULT '{"processing":0,"succeeded":0,"errored":0,"canceled":0,"expired":0}'::JSONB,
    total_items         INTEGER NOT NULL DEFAULT 0,
    ended_at            TIMESTAMPTZ,
    expires_at          TIMESTAMPTZ NOT NULL,
    cancel_initiated_at TIMESTAMPTZ,
    archived_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT batches_external_id_unique UNIQUE (external_id)
);
COMMENT ON TABLE  batches IS 'Anthropic Message Batches (POST /v1/messages/batches) run by the jobs process against the same deployments. external_id is the public id (msgbatch_<24 hex>). Results are read from batch_items and streamed as JSONL.';
CREATE INDEX IF NOT EXISTS idx_batches_api_key_id ON batches(api_key_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_batches_status     ON batches(status) WHERE status <> 'ended';

CREATE TABLE IF NOT EXISTS batch_items (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id      UUID NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    custom_id     VARCHAR(255) NOT NULL,
    model_name    VARCHAR(255),
    request       JSONB NOT NULL,
    result        JSONB,
    status        batch_item_status NOT NULL DEFAULT 'pending',
    error         JSONB,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE  batch_items IS 'One request of a message batch. request = the Anthropic MessageCreateParams; result = the Message or error object in Anthropic result shape.';
CREATE UNIQUE INDEX IF NOT EXISTS idx_batch_items_batch_custom_unique ON batch_items(batch_id, custom_id);
CREATE INDEX IF NOT EXISTS idx_batch_items_batch_status ON batch_items(batch_id, status);

-- =============================================================================
-- updated_at triggers
-- =============================================================================
CREATE OR REPLACE TRIGGER trg_admin_users_updated_at          BEFORE UPDATE ON admin_users          FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_api_keys_updated_at             BEFORE UPDATE ON api_keys             FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_credentials_updated_at          BEFORE UPDATE ON credentials          FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_ip_rules_updated_at             BEFORE UPDATE ON ip_rules             FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_price_plans_updated_at          BEFORE UPDATE ON price_plans          FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_clusters_updated_at             BEFORE UPDATE ON clusters             FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_nodes_updated_at                BEFORE UPDATE ON nodes                FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_devices_updated_at              BEFORE UPDATE ON devices              FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_models_updated_at               BEFORE UPDATE ON models               FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_model_versions_updated_at       BEFORE UPDATE ON model_versions       FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_model_files_updated_at          BEFORE UPDATE ON model_files          FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_node_model_cache_updated_at     BEFORE UPDATE ON node_model_cache     FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_engines_updated_at              BEFORE UPDATE ON engines              FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_engine_versions_updated_at      BEFORE UPDATE ON engine_versions      FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_deployments_updated_at          BEFORE UPDATE ON deployments          FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_instances_updated_at            BEFORE UPDATE ON instances            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_device_slices_updated_at        BEFORE UPDATE ON device_slices        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_layouts_updated_at              BEFORE UPDATE ON layouts              FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_layout_items_updated_at         BEFORE UPDATE ON layout_items         FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_rules_updated_at                BEFORE UPDATE ON rules                FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_swap_operations_updated_at      BEFORE UPDATE ON swap_operations      FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_agent_commands_updated_at       BEFORE UPDATE ON agent_commands       FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_alert_channels_updated_at       BEFORE UPDATE ON alert_channels       FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_alert_events_updated_at         BEFORE UPDATE ON alert_events         FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_alert_deliveries_updated_at     BEFORE UPDATE ON alert_deliveries     FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_usage_rollups_hourly_updated_at BEFORE UPDATE ON usage_rollups_hourly FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_billing_reports_updated_at      BEFORE UPDATE ON billing_reports      FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_benchmarks_updated_at           BEFORE UPDATE ON benchmarks           FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_benchmark_runs_updated_at       BEFORE UPDATE ON benchmark_runs       FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_ha_leases_updated_at            BEFORE UPDATE ON ha_leases            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_batches_updated_at              BEFORE UPDATE ON batches              FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
CREATE OR REPLACE TRIGGER trg_batch_items_updated_at          BEFORE UPDATE ON batch_items          FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- Partition management
-- =============================================================================
-- Creates one partition per day for every day-partitioned table, from
-- CURRENT_DATE - p_days_back through CURRENT_DATE + p_days_ahead (UTC days).
-- Partition names: <table>_pYYYYMMDD. Idempotent. Returns number created.
-- The jobs process runs SELECT create_daily_partitions(3) every hour; ingest
-- code calls it on SQLSTATE 23514 and retries the insert once.
CREATE OR REPLACE FUNCTION create_daily_partitions(p_days_ahead INTEGER DEFAULT 3, p_days_back INTEGER DEFAULT 0)
RETURNS INTEGER AS $$
DECLARE
    v_tables  TEXT[] := ARRAY['metric_samples_gpu', 'metric_samples_engine', 'metric_samples_session',
                              'metric_rollups_gpu_1m', 'metric_rollups_engine_1m',
                              'log_chunks', 'traces', 'usage_events'];
    v_table   TEXT;
    v_day     DATE;
    v_name    TEXT;
    v_created INTEGER := 0;
    v_offset  INTEGER;
BEGIN
    FOREACH v_table IN ARRAY v_tables LOOP
        FOR v_offset IN -p_days_back..p_days_ahead LOOP
            v_day  := (now() AT TIME ZONE 'UTC')::DATE + v_offset;
            v_name := format('%s_p%s', v_table, to_char(v_day, 'YYYYMMDD'));
            IF to_regclass(v_name) IS NULL THEN
                EXECUTE format('CREATE TABLE IF NOT EXISTS %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
                               v_name, v_table,
                               to_char(v_day, 'YYYY-MM-DD') || ' 00:00:00+00',
                               to_char(v_day + 1, 'YYYY-MM-DD') || ' 00:00:00+00');
                v_created := v_created + 1;
            END IF;
        END LOOP;
    END LOOP;
    RETURN v_created;
END;
$$ LANGUAGE plpgsql;
COMMENT ON FUNCTION create_daily_partitions(INTEGER, INTEGER) IS 'Create daily partitions (UTC) for all time-series tables. Idempotent.';

-- Drops partitions of p_table whose day is older than CURRENT_DATE - p_retain_days.
-- Returns number dropped. Called by the retention job with values from env.
CREATE OR REPLACE FUNCTION drop_old_partitions(p_table TEXT, p_retain_days INTEGER)
RETURNS INTEGER AS $$
DECLARE
    v_child   TEXT;
    v_day     DATE;
    v_dropped INTEGER := 0;
    v_cutoff  DATE := (now() AT TIME ZONE 'UTC')::DATE - p_retain_days;
BEGIN
    FOR v_child IN
        SELECT c.relname
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        JOIN pg_class p ON p.oid = i.inhparent
        WHERE p.relname = p_table
    LOOP
        BEGIN
            v_day := to_date(substring(v_child FROM '_p(\d{8})$'), 'YYYYMMDD');
        EXCEPTION WHEN OTHERS THEN
            CONTINUE;
        END;
        IF v_day IS NOT NULL AND v_day < v_cutoff THEN
            EXECUTE format('DROP TABLE IF EXISTS %I', v_child);
            v_dropped := v_dropped + 1;
        END IF;
    END LOOP;
    RETURN v_dropped;
END;
$$ LANGUAGE plpgsql;
COMMENT ON FUNCTION drop_old_partitions(TEXT, INTEGER) IS 'Drop daily partitions of one table older than the retention window.';

-- Partitions for today and the next 3 days so the stack can ingest immediately.
SELECT create_daily_partitions(3, 0);
