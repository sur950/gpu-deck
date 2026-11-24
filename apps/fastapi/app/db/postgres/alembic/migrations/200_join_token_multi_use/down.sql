-- ==========================================
-- Rollback: multi-use enrolment tokens (200)
-- Partly used multi-use tokens become used (they cannot be represented).
-- ==========================================

UPDATE join_tokens SET used_at = COALESCE(last_used_at, now())
 WHERE used_at IS NULL AND use_count > 0;

ALTER TABLE join_tokens DROP CONSTRAINT IF EXISTS join_tokens_use_count_range;
ALTER TABLE join_tokens DROP CONSTRAINT IF EXISTS join_tokens_max_uses_range;
ALTER TABLE join_tokens
    DROP COLUMN IF EXISTS last_used_at,
    DROP COLUMN IF EXISTS use_count,
    DROP COLUMN IF EXISTS max_uses;

COMMENT ON TABLE join_tokens IS 'One-time machine join tokens ("gdj_" + 40 hex), TTL JOIN_TOKEN_TTL_MINUTES. Plaintext shown once; SHA-256 stored. Consumed at POST /agent/v1/join.';
COMMENT ON COLUMN join_tokens.used_by_node_id IS NULL;
