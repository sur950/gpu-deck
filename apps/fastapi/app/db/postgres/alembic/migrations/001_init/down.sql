-- =============================================================================
-- GPU Deck — 001_init / down.sql
-- Drops everything created by up.sql in reverse dependency order.
-- Dropping a partitioned parent drops all of its daily partitions.
-- =============================================================================

-- Partition management functions
DROP FUNCTION IF EXISTS drop_old_partitions(TEXT, INTEGER);
DROP FUNCTION IF EXISTS create_daily_partitions(INTEGER, INTEGER);

-- Batches
DROP TABLE IF EXISTS batch_items CASCADE;
DROP TABLE IF EXISTS batches CASCADE;

-- HA
DROP TABLE IF EXISTS ha_leases CASCADE;

-- Benchmark
DROP TABLE IF EXISTS benchmark_runs CASCADE;
DROP TABLE IF EXISTS benchmarks CASCADE;

-- Metering / billing
DROP TABLE IF EXISTS billing_reports CASCADE;
DROP TABLE IF EXISTS usage_rollups_hourly CASCADE;
DROP TABLE IF EXISTS usage_events CASCADE;

-- Logs and traces
DROP TABLE IF EXISTS traces CASCADE;
DROP TABLE IF EXISTS log_chunks CASCADE;

-- Metrics
DROP TABLE IF EXISTS metric_rollups_engine_1m CASCADE;
DROP TABLE IF EXISTS metric_rollups_gpu_1m CASCADE;
DROP TABLE IF EXISTS metric_samples_session CASCADE;
DROP TABLE IF EXISTS metric_samples_engine CASCADE;
DROP TABLE IF EXISTS metric_samples_gpu CASCADE;

-- Alerts
DROP TABLE IF EXISTS alert_deliveries CASCADE;
DROP TABLE IF EXISTS alert_events CASCADE;
DROP TABLE IF EXISTS alert_channels CASCADE;

-- Swap manager / agent commands
DROP TABLE IF EXISTS agent_commands CASCADE;
DROP TABLE IF EXISTS swap_operations CASCADE;

-- Rules
DROP TABLE IF EXISTS rule_runs CASCADE;
DROP TABLE IF EXISTS rules CASCADE;

-- Layouts
DROP TABLE IF EXISTS layout_items CASCADE;
DROP TABLE IF EXISTS layouts CASCADE;

-- Deployments
DROP TABLE IF EXISTS device_slices CASCADE;
DROP TABLE IF EXISTS instances CASCADE;
DROP TABLE IF EXISTS deployments CASCADE;

-- Engines
DROP TABLE IF EXISTS engine_compat_checks CASCADE;
DROP TABLE IF EXISTS engine_versions CASCADE;
DROP TABLE IF EXISTS engines CASCADE;

-- Models / cache
DROP TABLE IF EXISTS node_model_cache CASCADE;
DROP TABLE IF EXISTS model_files CASCADE;
DROP TABLE IF EXISTS model_versions CASCADE;
DROP TABLE IF EXISTS models CASCADE;

-- Inventory
DROP TABLE IF EXISTS devices CASCADE;

-- Fleet
DROP TABLE IF EXISTS node_certificates CASCADE;
DROP TABLE IF EXISTS join_tokens CASCADE;
DROP TABLE IF EXISTS nodes CASCADE;
DROP TABLE IF EXISTS clusters CASCADE;
DROP TABLE IF EXISTS price_plans CASCADE;

-- Security / identity
DROP TABLE IF EXISTS ip_rules CASCADE;
DROP TABLE IF EXISTS audit_logs CASCADE;
DROP TABLE IF EXISTS credential_usage_history CASCADE;
DROP TABLE IF EXISTS credentials CASCADE;
DROP TABLE IF EXISTS api_keys CASCADE;
DROP TABLE IF EXISTS sessions CASCADE;
DROP TABLE IF EXISTS admin_users CASCADE;

-- Trigger functions
DROP FUNCTION IF EXISTS update_credential_last_used();
DROP FUNCTION IF EXISTS update_updated_at_column();

-- Enum types (reverse of creation)
DROP TYPE IF EXISTS batch_item_status;
DROP TYPE IF EXISTS batch_status;
DROP TYPE IF EXISTS benchmark_run_status;
DROP TYPE IF EXISTS billing_report_status;
DROP TYPE IF EXISTS usage_status;
DROP TYPE IF EXISTS usage_format;
DROP TYPE IF EXISTS log_stream;
DROP TYPE IF EXISTS log_level;
DROP TYPE IF EXISTS log_source;
DROP TYPE IF EXISTS alert_delivery_status;
DROP TYPE IF EXISTS alert_event_status;
DROP TYPE IF EXISTS alert_severity;
DROP TYPE IF EXISTS alert_channel_type;
DROP TYPE IF EXISTS agent_command_state;
DROP TYPE IF EXISTS swap_trigger;
DROP TYPE IF EXISTS swap_state;
DROP TYPE IF EXISTS swap_kind;
DROP TYPE IF EXISTS rule_run_outcome;
DROP TYPE IF EXISTS rule_type;
DROP TYPE IF EXISTS instance_health;
DROP TYPE IF EXISTS instance_state;
DROP TYPE IF EXISTS deployment_status;
DROP TYPE IF EXISTS placement_strategy;
DROP TYPE IF EXISTS deployment_preset;
DROP TYPE IF EXISTS compat_result;
DROP TYPE IF EXISTS engine_version_status;
DROP TYPE IF EXISTS engine_runtime;
DROP TYPE IF EXISTS engine_name;
DROP TYPE IF EXISTS cache_state;
DROP TYPE IF EXISTS model_version_status;
DROP TYPE IF EXISTS model_file_format;
DROP TYPE IF EXISTS model_priority;
DROP TYPE IF EXISTS model_type;
DROP TYPE IF EXISTS model_source;
DROP TYPE IF EXISTS device_slice_status;
DROP TYPE IF EXISTS device_health;
DROP TYPE IF EXISTS device_vendor;
DROP TYPE IF EXISTS certificate_kind;
DROP TYPE IF EXISTS node_status;
DROP TYPE IF EXISTS node_arch;
DROP TYPE IF EXISTS node_os;
DROP TYPE IF EXISTS cluster_status;
DROP TYPE IF EXISTS cluster_type;
DROP TYPE IF EXISTS ip_rule_scope;
DROP TYPE IF EXISTS ip_rule_action;
DROP TYPE IF EXISTS audit_outcome;
DROP TYPE IF EXISTS audit_actor_type;
DROP TYPE IF EXISTS credential_status;
DROP TYPE IF EXISTS credential_type;
DROP TYPE IF EXISTS api_key_status;

-- Extensions are shared with other schemas on the same database; left in place on purpose.
