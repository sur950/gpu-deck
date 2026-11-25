-- ==========================================
-- Rollback for 201_agent_updates
-- ==========================================

DROP TABLE IF EXISTS agent_update_runs;
DROP TABLE IF EXISTS agent_update_rollouts;
DROP TYPE IF EXISTS agent_rollout_state;
DROP TYPE IF EXISTS agent_update_state;
