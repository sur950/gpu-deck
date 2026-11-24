-- ==========================================
-- Rollback for 400_layout_unlisted_policy
-- Layout unlisted policy
-- ==========================================

ALTER TABLE layouts DROP COLUMN IF EXISTS unlisted_policy;
DROP TYPE IF EXISTS layout_unlisted_policy;
