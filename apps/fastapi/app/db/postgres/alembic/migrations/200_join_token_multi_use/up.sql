-- ==========================================
-- Multi-use enrolment tokens
-- Version: 200
-- Description: A join token can enrol more than one machine (Kubernetes
--   DaemonSets, autoscaling node pools, cloud images): max_uses machines
--   until it expires or is revoked. use_count counts successful joins;
--   used_at is set when the token is used up (the last allowed join), so
--   "used_at IS NULL" still means "can join" and the active index stays
--   valid. used_by_node_id is the most recent node. Single-use tokens
--   (max_uses = 1, the default) behave exactly as before.
-- ==========================================

ALTER TABLE join_tokens
    ADD COLUMN IF NOT EXISTS max_uses INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS use_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ;

UPDATE join_tokens SET use_count = 1, last_used_at = used_at
 WHERE used_at IS NOT NULL AND use_count = 0;

ALTER TABLE join_tokens DROP CONSTRAINT IF EXISTS join_tokens_max_uses_range;
ALTER TABLE join_tokens ADD CONSTRAINT join_tokens_max_uses_range
    CHECK (max_uses BETWEEN 1 AND 100000);
ALTER TABLE join_tokens DROP CONSTRAINT IF EXISTS join_tokens_use_count_range;
ALTER TABLE join_tokens ADD CONSTRAINT join_tokens_use_count_range
    CHECK (use_count >= 0 AND use_count <= max_uses);

COMMENT ON TABLE join_tokens IS 'Machine join (enrolment) tokens ("gdj_" + 40 hex). Single use by default; max_uses > 1 enrols several machines (DaemonSets, node pools) until expiry or revocation. Plaintext shown once; SHA-256 stored. Consumed at POST /agent/v1/join.';
COMMENT ON COLUMN join_tokens.max_uses IS 'Machines this token may enrol (1 = one-time token)';
COMMENT ON COLUMN join_tokens.use_count IS 'Successful joins so far; used_at is set when use_count reaches max_uses';
COMMENT ON COLUMN join_tokens.last_used_at IS 'Time of the most recent join with this token';
COMMENT ON COLUMN join_tokens.used_by_node_id IS 'Node created by the most recent join';
