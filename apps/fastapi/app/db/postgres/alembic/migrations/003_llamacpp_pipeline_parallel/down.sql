-- ==========================================
-- Rollback of 003_llamacpp_pipeline_parallel
-- ==========================================

UPDATE engines
   SET capabilities = COALESCE(capabilities, '{}'::JSONB) || '{"pipeline_parallel": false}'::JSONB
 WHERE name = 'llama_cpp';
