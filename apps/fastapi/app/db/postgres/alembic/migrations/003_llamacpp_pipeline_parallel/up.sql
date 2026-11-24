-- ==========================================
-- llama.cpp cross-node pipeline parallel
-- Version: 003
-- Description: 001_init seeded llama_cpp with pipeline_parallel = false, so
--   the scheduler never placed a llama.cpp deployment with pp_size > 1 across
--   nodes. The agent runs it with the pinned b12500 build: rank 0 is a
--   single-model llama-server with --rpc <workers> (outside the shared
--   router), ranks > 0 run ggml-rpc-server --host --port (flags checked
--   against the release's --help, agent fixtures). Idempotent.
-- ==========================================

UPDATE engines
   SET capabilities = COALESCE(capabilities, '{}'::JSONB) || '{"pipeline_parallel": true}'::JSONB
 WHERE name = 'llama_cpp'
   AND COALESCE(capabilities->>'pipeline_parallel', 'false') <> 'true';
