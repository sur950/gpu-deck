-- ==========================================
-- llama.cpp router seed
-- Version: 600
-- Description: Engine versions seeded by an earlier release get the llama.cpp
--   metrics config pinned from llama.cpp b8798 (b8798: /metrics with the llamacpp:
--   prefix, per-slot data from /slots, no kv_cache_* gauges) and the router
--   mode facts the scheduler / swap manager rely on (models are read only at
--   router start, an unknown model means a router restart, per-model GETs
--   need ?model=). Mirrors app/services/engines/seed.py. Idempotent; only
--   touches llama_cpp versions whose metrics config is empty or unprefixed.
--  
-- ==========================================

UPDATE engine_versions ev
   SET metrics_config = COALESCE(ev.metrics_config, '{}'::JSONB) || '{"path":"/metrics","format":"prometheus","slots_path":"/slots","metric_prefix":"llamacpp:","derive_from_proxy":false}'::JSONB
  FROM engines e
 WHERE e.id = ev.engine_id
   AND e.name = 'llama_cpp'
   AND (ev.metrics_config IS NULL
        OR ev.metrics_config = '{}'::JSONB
        OR COALESCE(ev.metrics_config->>'metric_prefix', '') = ''
        OR ev.metrics_config->>'format' = 'none');

UPDATE engine_versions ev
   SET requirements = COALESCE(ev.requirements, '{}'::JSONB) || jsonb_build_object('router', '{"models_read_at_start":true,"unknown_model_load":"router_restart","per_model_query_param":"model","load_endpoint":"POST /models/load","unload_endpoint":"POST /models/unload","status_endpoint":"GET /models","kv_cache_metrics":false,"prefer_one_router_per_model_group":true}'::JSONB)
  FROM engines e
 WHERE e.id = ev.engine_id
   AND e.name = 'llama_cpp'
   AND NOT (COALESCE(ev.requirements, '{}'::JSONB) ? 'router');
