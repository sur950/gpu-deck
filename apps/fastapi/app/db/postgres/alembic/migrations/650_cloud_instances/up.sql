-- ==========================================
-- Cloud GPU VMs provisioned by the control plane
-- Version: 650
-- Description: one row per VM GPU Deck creates on AWS / Azure / GCP /
--   DigitalOcean. The VM installs the node agent from cloud-init and
--   joins with a one-time join token minted for it; once joined the row
--   links to its nodes row. Additive, idempotent.
-- ==========================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'cloud_provider' AND typnamespace = current_schema()::regnamespace) THEN
        CREATE TYPE cloud_provider AS ENUM ('aws', 'azure', 'gcp', 'digitalocean');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'cloud_instance_status' AND typnamespace = current_schema()::regnamespace) THEN
        CREATE TYPE cloud_instance_status AS ENUM (
            'pending', 'provisioning', 'booting', 'running', 'draining', 'stopping',
            'stopped', 'starting', 'terminating', 'terminated', 'error'
        );
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS cloud_instances (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cluster_id                  UUID NOT NULL REFERENCES clusters(id) ON DELETE RESTRICT,
    node_id                     UUID REFERENCES nodes(id) ON DELETE SET NULL,
    credential_id               UUID REFERENCES credentials(id) ON DELETE SET NULL,
    join_token_id               UUID REFERENCES join_tokens(id) ON DELETE SET NULL,
    provider                    cloud_provider NOT NULL,
    name                        VARCHAR(255) NOT NULL,
    region                      VARCHAR(100) NOT NULL,
    zone                        VARCHAR(100),
    instance_type               VARCHAR(100) NOT NULL,
    image                       VARCHAR(1024),
    disk_gb                     INTEGER NOT NULL DEFAULT 200,
    provider_instance_id        VARCHAR(255),
    provider_state              VARCHAR(64),
    status                      cloud_instance_status NOT NULL DEFAULT 'pending',
    status_message              VARCHAR(2048),
    pending_action              VARCHAR(32),
    public_ip                   INET,
    private_ip                  INET,
    gpu_model                   VARCHAR(128),
    gpu_count                   INTEGER NOT NULL DEFAULT 0,
    gpu_vram_bytes              BIGINT,
    vcpus                       INTEGER,
    memory_bytes                BIGINT,
    price_per_hour_usd          NUMERIC(12,6),
    price_source                VARCHAR(255),
    auto_terminate_idle_minutes INTEGER,
    idle_since                  TIMESTAMPTZ,
    labels                      JSONB NOT NULL DEFAULT '{}'::JSONB,
    tags                        JSONB NOT NULL DEFAULT '{}'::JSONB,
    options                     JSONB NOT NULL DEFAULT '{}'::JSONB,
    last_error                  JSONB,
    created_by                  UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    launched_at                 TIMESTAMPTZ,
    joined_at                   TIMESTAMPTZ,
    drain_started_at            TIMESTAMPTZ,
    stopped_at                  TIMESTAMPTZ,
    terminated_at               TIMESTAMPTZ,
    last_polled_at              TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at                  TIMESTAMPTZ,
    CONSTRAINT cloud_instances_disk_positive CHECK (disk_gb > 0),
    CONSTRAINT cloud_instances_gpu_count_nonneg CHECK (gpu_count >= 0),
    CONSTRAINT cloud_instances_idle_positive CHECK (
        auto_terminate_idle_minutes IS NULL OR auto_terminate_idle_minutes > 0
    ),
    CONSTRAINT cloud_instances_pending_action CHECK (
        pending_action IS NULL OR pending_action IN ('stop', 'terminate')
    )
);
COMMENT ON TABLE  cloud_instances IS 'Cloud GPU VMs created by GPU Deck (AWS/Azure/GCP/DigitalOcean). Credentials come from clusters.credential_id (vault). cloud-init installs the node agent from the hub download endpoint and joins with join_token_id; node_id is set once the agent joined. Everything created on the provider is tagged gpu-deck:* (GCP labels gpu-deck-*).';
COMMENT ON COLUMN cloud_instances.status IS 'pending (row only) -> provisioning (create call accepted) -> booting (VM up, agent not joined) -> running (node joined) -> draining (node drain before stop/terminate) -> stopping/stopped/starting -> terminating -> terminated; error keeps last_error.';
COMMENT ON COLUMN cloud_instances.pending_action IS 'stop | terminate: what runs once the node finished draining.';
COMMENT ON COLUMN cloud_instances.price_per_hour_usd IS 'Provider on-demand list price per hour when the provider API exposes it (AWS Pricing API, Azure Retail Prices, DigitalOcean sizes); GCP leaves it NULL.';
COMMENT ON COLUMN cloud_instances.auto_terminate_idle_minutes IS 'Terminate (after drain) when the node ran no instance for this many minutes; NULL = never. Default from clusters.config.cloud.auto_terminate_idle_minutes.';
COMMENT ON COLUMN cloud_instances.options IS 'Provider launch options used (subnet, security groups, resource group, project, vpc, ssh keys); never secrets.';
COMMENT ON COLUMN cloud_instances.last_error IS '{"code","message","provider_code","at"} of the last provider/lifecycle failure (quota, capacity, auth, ...).';

CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_instances_cluster_name_unique
    ON cloud_instances(cluster_id, lower(name))
    WHERE deleted_at IS NULL AND status <> 'terminated';
CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_instances_provider_instance_unique
    ON cloud_instances(provider, provider_instance_id)
    WHERE provider_instance_id IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_cloud_instances_cluster_id ON cloud_instances(cluster_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_cloud_instances_status     ON cloud_instances(status)     WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_cloud_instances_node_id    ON cloud_instances(node_id)    WHERE node_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cloud_instances_join_token ON cloud_instances(join_token_id) WHERE join_token_id IS NOT NULL;

CREATE OR REPLACE TRIGGER trg_cloud_instances_updated_at BEFORE UPDATE ON cloud_instances FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
