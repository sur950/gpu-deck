-- ==========================================
-- GPU instances
-- Version: 601
-- Description: SSH GPU instances on demand (services/gpu_instances).
--   One row per requested SSH-accessible GPU container: placement (node,
--   devices or a slice), authorized keys, TTL / idle auto-stop, connection
--   details reported by the agent (start_ssh_instance result) and metering
--   progress. Device reservations are device_slices rows carrying
--   gpu_instance_id (instance_id NULL), so the deployment scheduler sees
--   them. Additive, idempotent.
-- ==========================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'gpu_instance_state'
                   AND typnamespace = current_schema()::regnamespace) THEN
        CREATE TYPE gpu_instance_state AS ENUM
            ('requested', 'placing', 'starting', 'running', 'stopping', 'stopped', 'error');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS gpu_instances (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                  VARCHAR(63) NOT NULL,
    description           VARCHAR(2048),
    cluster_id            UUID REFERENCES clusters(id) ON DELETE SET NULL,
    node_id               UUID REFERENCES nodes(id) ON DELETE SET NULL,
    state                 gpu_instance_state NOT NULL DEFAULT 'requested',
    state_message         TEXT,
    health                VARCHAR(20) NOT NULL DEFAULT 'unknown',
    image                 VARCHAR(512) NOT NULL,
    gpu_count             SMALLINT NOT NULL DEFAULT 1,
    slice_vram_bytes      BIGINT,
    slice_compute_pct     SMALLINT,
    device_vendor         VARCHAR(32),
    node_selector         JSONB NOT NULL DEFAULT '{}'::JSONB,
    device_ids            UUID[] NOT NULL DEFAULT '{}',
    limits                JSONB NOT NULL DEFAULT '{}'::JSONB,
    authorized_keys       JSONB NOT NULL DEFAULT '[]'::JSONB,
    ssh_user              VARCHAR(32) NOT NULL DEFAULT 'gpudeck',
    container_port        INTEGER NOT NULL DEFAULT 22,
    requested_ssh_port    INTEGER,
    allowed_source_cidrs  JSONB NOT NULL DEFAULT '[]'::JSONB,
    env                   JSONB NOT NULL DEFAULT '{}'::JSONB,
    volumes               JSONB NOT NULL DEFAULT '[]'::JSONB,
    labels                JSONB NOT NULL DEFAULT '{}'::JSONB,
    ttl_seconds           INTEGER,
    idle_timeout_seconds  INTEGER,
    expires_at            TIMESTAMPTZ,
    queue_until           TIMESTAMPTZ,
    host_address          VARCHAR(255),
    host_port             INTEGER,
    host_key_fingerprint  VARCHAR(100),
    host_keys             JSONB NOT NULL DEFAULT '[]'::JSONB,
    container_id          VARCHAR(128),
    gpu_attached          BOOLEAN NOT NULL DEFAULT FALSE,
    warnings              JSONB NOT NULL DEFAULT '[]'::JSONB,
    start_command_id      UUID,
    stop_command_id       UUID,
    stop_reason           VARCHAR(64),
    active_connections    INTEGER NOT NULL DEFAULT 0,
    connections_total     INTEGER NOT NULL DEFAULT 0,
    last_connection_at    TIMESTAMPTZ,
    state_changed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at            TIMESTAMPTZ,
    running_at            TIMESTAMPTZ,
    stopped_at            TIMESTAMPTZ,
    metered_until         TIMESTAMPTZ,
    gpu_seconds_total     NUMERIC(14,3) NOT NULL DEFAULT 0,
    metadata              JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_by            UUID,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at            TIMESTAMPTZ,
    CONSTRAINT gpu_instances_gpu_count_range CHECK (gpu_count BETWEEN 0 AND 64),
    CONSTRAINT gpu_instances_slice_compute_range CHECK (slice_compute_pct IS NULL OR slice_compute_pct BETWEEN 1 AND 100),
    CONSTRAINT gpu_instances_slice_vram_pos CHECK (slice_vram_bytes IS NULL OR slice_vram_bytes > 0),
    CONSTRAINT gpu_instances_ttl_min CHECK (ttl_seconds IS NULL OR ttl_seconds >= 60),
    CONSTRAINT gpu_instances_idle_min CHECK (idle_timeout_seconds IS NULL OR idle_timeout_seconds >= 60),
    CONSTRAINT gpu_instances_container_port_range CHECK (container_port BETWEEN 1 AND 65535)
);
COMMENT ON TABLE  gpu_instances IS 'SSH GPU instances on demand: a container with sshd and GPU(s) attached, run by the node agent (start_ssh_instance). Lifecycle requested -> placing -> starting -> running -> stopping -> stopped | error. Device reservations live in device_slices (gpu_instance_id). Billed as GPU minutes through usage_events (endpoint gpu_instance).';
COMMENT ON COLUMN gpu_instances.authorized_keys IS '[{"key":"ssh-ed25519 AAAA... comment","type":"ssh-ed25519","fingerprint":"SHA256:...","comment":"..."}]';
COMMENT ON COLUMN gpu_instances.gpu_count IS 'Whole GPUs (exclusive). 1 with slice_vram_bytes set = one GPU slice. 0 = no GPU (CPU-only container).';
COMMENT ON COLUMN gpu_instances.queue_until IS 'When set, a request that does not fit stays requested and is retried until this time.';
COMMENT ON COLUMN gpu_instances.metered_until IS 'GPU time up to this instant has been written to usage_events.';
COMMENT ON COLUMN gpu_instances.host_address IS 'Address the agent forwarder listens on (node private address); clients connect to host_address:host_port.';
CREATE UNIQUE INDEX IF NOT EXISTS idx_gpu_instances_name_unique ON gpu_instances(lower(name)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_gpu_instances_state   ON gpu_instances(state) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_gpu_instances_node_id ON gpu_instances(node_id) WHERE deleted_at IS NULL;
CREATE OR REPLACE TRIGGER trg_gpu_instances_updated_at BEFORE UPDATE ON gpu_instances FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

ALTER TABLE device_slices
    ADD COLUMN IF NOT EXISTS gpu_instance_id UUID REFERENCES gpu_instances(id) ON DELETE SET NULL;
COMMENT ON COLUMN device_slices.gpu_instance_id IS 'Set when the slice (or the whole-device reservation) belongs to an SSH GPU instance. A slice with neither instance_id nor gpu_instance_id is a direct reservation made through POST /devices/{id}/slices.';
CREATE INDEX IF NOT EXISTS idx_device_slices_gpu_instance_id ON device_slices(gpu_instance_id) WHERE gpu_instance_id IS NOT NULL;
