-- Reverse 300_external_deployments: external rows go away with the columns.

DELETE FROM instances WHERE origin = 'external';
DELETE FROM deployments WHERE origin = 'external';

DROP INDEX IF EXISTS idx_instances_external_node;
DROP INDEX IF EXISTS idx_deployments_origin;

ALTER TABLE instances DROP COLUMN IF EXISTS origin;

ALTER TABLE deployments DROP CONSTRAINT IF EXISTS deployments_managed_refs;
ALTER TABLE deployments ALTER COLUMN model_id          SET NOT NULL;
ALTER TABLE deployments ALTER COLUMN model_version_id  SET NOT NULL;
ALTER TABLE deployments ALTER COLUMN engine_version_id SET NOT NULL;
ALTER TABLE deployments DROP COLUMN IF EXISTS external;
ALTER TABLE deployments DROP COLUMN IF EXISTS origin;

DROP TYPE IF EXISTS deployment_origin;
