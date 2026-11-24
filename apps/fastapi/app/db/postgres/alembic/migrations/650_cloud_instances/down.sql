-- ==========================================
-- Rollback for 650_cloud_instances
-- ==========================================

DROP TRIGGER IF EXISTS trg_cloud_instances_updated_at ON cloud_instances;
DROP TABLE IF EXISTS cloud_instances;
DROP TYPE IF EXISTS cloud_instance_status;
DROP TYPE IF EXISTS cloud_provider;
