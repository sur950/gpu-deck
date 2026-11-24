-- ==========================================
-- Rollback for 600_llamacpp_router_seed
-- Data-only migration: the router facts are removed; the metrics config is
-- left as is (it is what the engine actually exposes).
-- ==========================================

UPDATE engine_versions ev
   SET requirements = ev.requirements - 'router'
  FROM engines e
 WHERE e.id = ev.engine_id AND e.name = 'llama_cpp';
