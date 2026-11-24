-- ==========================================
-- External (observed) deployments
-- Version: 300
-- Description: model servers GPU Deck did not start and only observes
--   (agent.toml [[observe]], agent protocol §12). The agent reports them in
--   inventory.observed_engines; the hub keeps one deployment + one instance per
--   observed server with origin = 'external'. They have no catalog model,
--   model version or engine version, so those columns become nullable for
--   external rows only (CHECK). The scheduler, reconciler, rules, swaps,
--   layouts, routing table, benchmarks and command dispatch skip them.
--   Additive, idempotent.
-- ==========================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'deployment_origin'
                   AND typnamespace = current_schema()::regnamespace) THEN
        CREATE TYPE deployment_origin AS ENUM ('managed', 'external');
    END IF;
END $$;

ALTER TABLE deployments
    ADD COLUMN IF NOT EXISTS origin   deployment_origin NOT NULL DEFAULT 'managed',
    ADD COLUMN IF NOT EXISTS external JSONB;

ALTER TABLE deployments ALTER COLUMN model_id          DROP NOT NULL;
ALTER TABLE deployments ALTER COLUMN model_version_id  DROP NOT NULL;
ALTER TABLE deployments ALTER COLUMN engine_version_id DROP NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint c
                   JOIN pg_class t ON t.oid = c.conrelid
                   WHERE c.conname = 'deployments_managed_refs'
                     AND t.relnamespace = current_schema()::regnamespace) THEN
        ALTER TABLE deployments ADD CONSTRAINT deployments_managed_refs CHECK (
            origin = 'external'
            OR (model_id IS NOT NULL AND model_version_id IS NOT NULL AND engine_version_id IS NOT NULL)
        );
    END IF;
END $$;

ALTER TABLE instances
    ADD COLUMN IF NOT EXISTS origin deployment_origin NOT NULL DEFAULT 'managed';

CREATE INDEX IF NOT EXISTS idx_deployments_origin ON deployments(origin) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_instances_external_node ON instances(node_id) WHERE origin = 'external';

COMMENT ON COLUMN deployments.origin IS 'managed = GPU Deck runs it; external = an observed model server it did not start (read-only: never scheduled, reconciled, swapped, routed, benchmarked or sent commands).';
COMMENT ON COLUMN deployments.external IS 'origin = external only: {"node_id","name","engine","endpoint","models":[],"version","sources":[],"pid","device_ids":[],"health","message","last_seen_at","stale_since"} from inventory.observed_engines.';
COMMENT ON COLUMN instances.origin IS 'Copy of deployments.origin so instance-level queries can skip external instances without a join.';
