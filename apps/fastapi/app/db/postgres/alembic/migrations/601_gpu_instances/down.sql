-- ==========================================
-- Rollback for 601_gpu_instances
-- ==========================================

DROP INDEX IF EXISTS idx_device_slices_gpu_instance_id;
ALTER TABLE device_slices DROP COLUMN IF EXISTS gpu_instance_id;
DROP TABLE IF EXISTS gpu_instances;
DROP TYPE IF EXISTS gpu_instance_state;
