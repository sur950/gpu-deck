-- ==========================================
-- Node latency history (reverse of 202 up.sql)
-- ==========================================

DROP INDEX IF EXISTS idx_node_latency_samples_ts;
DROP INDEX IF EXISTS idx_node_latency_samples_node_ts;
DROP INDEX IF EXISTS idx_node_latency_samples_unique;
DROP TABLE IF EXISTS node_latency_samples;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'latency_source'
               AND typnamespace = current_schema()::regnamespace) THEN
        DROP TYPE latency_source;
    END IF;
END $$;
