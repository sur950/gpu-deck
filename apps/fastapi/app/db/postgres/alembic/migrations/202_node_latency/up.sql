-- ==========================================
-- Node latency history
-- Version: 202
-- Description: per-minute round-trip times between the processes and every node.
--   source 'hub'       : agent hub -> agent (WebSocket ping / pong), api_layer_id = ''.
--   source 'api_layer' : one API layer -> node over the serving path it uses (tunnel or
--                        direct proxy), probed every SERVING_RTT_PROBE_SECONDS.
--   The jobs process copies the live values (Redis gpu-deck:latency:*) here once a minute;
--   retention LATENCY_RETENTION_DAYS. No FKs on purpose (metric table, ids validated by the
--   writers). Additive, idempotent.
-- ==========================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'latency_source'
                   AND typnamespace = current_schema()::regnamespace) THEN
        CREATE TYPE latency_source AS ENUM ('hub', 'api_layer');
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS node_latency_samples (
    id            BIGSERIAL PRIMARY KEY,
    ts            TIMESTAMPTZ NOT NULL,
    node_id       UUID NOT NULL,
    source        latency_source NOT NULL,
    api_layer_id  VARCHAR(120) NOT NULL DEFAULT '',
    region        VARCHAR(64),
    mode          VARCHAR(16),
    rtt_ms        REAL NOT NULL,
    jitter_ms     REAL,
    min_ms        REAL,
    max_ms        REAL,
    p95_ms        REAL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  node_latency_samples IS 'Per-minute round-trip time from the agent hub (source hub) and from each API layer (source api_layer) to a node. Written by the jobs process from the live Redis view gpu-deck:latency:*; retention LATENCY_RETENTION_DAYS. No FKs on purpose.';
COMMENT ON COLUMN node_latency_samples.ts IS 'Minute the value was taken (UTC, truncated to the minute).';
COMMENT ON COLUMN node_latency_samples.api_layer_id IS 'API_LAYER_ID of the measuring API layer; empty for the hub.';
COMMENT ON COLUMN node_latency_samples.region IS 'API_LAYER_REGION of the measuring API layer; null for the hub.';
COMMENT ON COLUMN node_latency_samples.mode IS 'Serving path the API layer used: tunnel (agent dials out) or direct (mTLS proxy).';
COMMENT ON COLUMN node_latency_samples.rtt_ms IS 'Smoothed round trip (EWMA of the probes) in milliseconds.';
COMMENT ON COLUMN node_latency_samples.jitter_ms IS 'Smoothed absolute difference between consecutive probes (RFC 3550 style).';
COMMENT ON COLUMN node_latency_samples.p95_ms IS '95th percentile of the most recent probes (up to 60).';

CREATE UNIQUE INDEX IF NOT EXISTS idx_node_latency_samples_unique
    ON node_latency_samples(node_id, source, api_layer_id, ts);
CREATE INDEX IF NOT EXISTS idx_node_latency_samples_node_ts
    ON node_latency_samples(node_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_node_latency_samples_ts
    ON node_latency_samples(ts);
