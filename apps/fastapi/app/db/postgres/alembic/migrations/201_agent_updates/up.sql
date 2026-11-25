-- ==========================================
-- Agent updates
-- Version: 201
-- Description: node agent self-update (agent protocol §14).
--   agent_update_runs: one update of one node: the pre-staged, signature-verified binary is
--   swapped in, the agent restarts, re-adopts its running engines and must reconnect and report
--   healthy, otherwise it rolls back to the previous binary. Steps keep the phase timestamps.
--   agent_update_rollouts: a rolling update of a cluster, one node at a time, stopping at the
--   first failure; started by an admin (trigger manual) or inside the cluster's maintenance
--   window (trigger auto, clusters.config.agent_updates).
--   Additive, idempotent.
-- ==========================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'agent_update_state'
                   AND typnamespace = current_schema()::regnamespace) THEN
        CREATE TYPE agent_update_state AS ENUM (
            'pending', 'staging', 'staged', 'swapping', 'restarting', 'readopting',
            'health_check', 'succeeded', 'rolled_back', 'failed', 'cancelled'
        );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'agent_rollout_state'
                   AND typnamespace = current_schema()::regnamespace) THEN
        CREATE TYPE agent_rollout_state AS ENUM ('running', 'succeeded', 'failed', 'cancelled');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS agent_update_rollouts (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cluster_id       UUID NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    target_version   VARCHAR(64) NOT NULL,
    trigger          VARCHAR(16) NOT NULL DEFAULT 'manual',
    state            agent_rollout_state NOT NULL DEFAULT 'running',
    node_ids         UUID[] NOT NULL DEFAULT '{}',
    current_node_id  UUID REFERENCES nodes(id) ON DELETE SET NULL,
    completed_count  INTEGER NOT NULL DEFAULT 0,
    skipped          JSONB NOT NULL DEFAULT '[]'::JSONB,
    failed_node_id   UUID REFERENCES nodes(id) ON DELETE SET NULL,
    message          VARCHAR(1024),
    created_by       UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at       TIMESTAMPTZ,
    CONSTRAINT agent_update_rollouts_trigger_check CHECK (trigger IN ('manual', 'auto'))
);
COMMENT ON TABLE  agent_update_rollouts IS 'Rolling agent update of a cluster: node_ids in order, one node at a time, stops at the first failed or rolled-back node. At most one running rollout per cluster.';
COMMENT ON COLUMN agent_update_rollouts.trigger IS 'manual = an admin started it; auto = the cluster policy (clusters.config.agent_updates.mode = auto) started it inside the maintenance window.';
COMMENT ON COLUMN agent_update_rollouts.skipped IS '[{"node_id","reason"}]: nodes left out (offline, managed by Kubernetes, agent too old to update itself, already on the version).';
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_update_rollouts_running
    ON agent_update_rollouts(cluster_id) WHERE state = 'running' AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_agent_update_rollouts_cluster
    ON agent_update_rollouts(cluster_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE OR REPLACE TRIGGER trg_agent_update_rollouts_updated_at BEFORE UPDATE ON agent_update_rollouts FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS agent_update_runs (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id                UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    cluster_id             UUID REFERENCES clusters(id) ON DELETE SET NULL,
    rollout_id             UUID REFERENCES agent_update_rollouts(id) ON DELETE SET NULL,
    from_version           VARCHAR(64),
    to_version             VARCHAR(64) NOT NULL,
    state                  agent_update_state NOT NULL DEFAULT 'pending',
    trigger                VARCHAR(16) NOT NULL DEFAULT 'manual',
    steps                  JSONB NOT NULL DEFAULT '[]'::JSONB,
    stage_command_id       UUID,
    apply_command_id       UUID,
    error_code             VARCHAR(64),
    error_message          VARCHAR(2048),
    health_timeout_seconds INTEGER NOT NULL DEFAULT 120,
    deadline_at            TIMESTAMPTZ,
    created_by             UUID REFERENCES admin_users(id) ON DELETE SET NULL,
    started_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at            TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at             TIMESTAMPTZ,
    CONSTRAINT agent_update_runs_trigger_check CHECK (trigger IN ('manual', 'auto', 'rollout'))
);
COMMENT ON TABLE  agent_update_runs IS 'One agent update of one node: staged -> swapping -> restarting -> readopting (engines) -> health_check -> succeeded, or rolled_back / failed. At most one active run per node.';
COMMENT ON COLUMN agent_update_runs.steps IS '[{"phase","at","message"}] in order, from the agent''s agent_update events and the hub (disconnect = restarting).';
COMMENT ON COLUMN agent_update_runs.deadline_at IS 'Past this the control plane stops waiting (the node rolls back on its own and reports when it reconnects).';
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_update_runs_active
    ON agent_update_runs(node_id)
    WHERE state IN ('pending', 'staging', 'staged', 'swapping', 'restarting', 'readopting', 'health_check')
      AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_agent_update_runs_node
    ON agent_update_runs(node_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_agent_update_runs_rollout
    ON agent_update_runs(rollout_id) WHERE rollout_id IS NOT NULL;
CREATE OR REPLACE TRIGGER trg_agent_update_runs_updated_at BEFORE UPDATE ON agent_update_runs FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
