-- ==========================================
-- Layout unlisted policy
-- Version: 400
-- Description: libs/contracts/rules/layout.schema.json and admin.yaml carry
--   layouts.unlisted_policy (keep | unload) but 001_init has no column for
--   it. Additive, idempotent.
-- ==========================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'layout_unlisted_policy'
                   AND typnamespace = current_schema()::regnamespace) THEN
        CREATE TYPE layout_unlisted_policy AS ENUM ('keep', 'unload');
    END IF;
END $$;

ALTER TABLE layouts
    ADD COLUMN IF NOT EXISTS unlisted_policy layout_unlisted_policy NOT NULL DEFAULT 'keep';

COMMENT ON COLUMN layouts.unlisted_policy IS 'What happens to running deployments not listed in layout_items when the layout is applied: keep (default) or unload (never touches always_on models).';
