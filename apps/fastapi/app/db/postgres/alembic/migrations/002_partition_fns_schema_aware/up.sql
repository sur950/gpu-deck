-- ==========================================
-- Schema-aware partition maintenance functions
-- Version: 002
-- Description: create_daily_partitions() and drop_old_partitions() from
--   001_init resolved tables by bare name. drop_old_partitions() matched
--   pg_class.relname without a namespace, so in a database holding several
--   schemas (scratch schemas of tests / model generation, or a second
--   install) it listed the partitions of every schema and dropped them by an
--   unqualified name. Both functions now work on current_schema() only.
--   Same signatures, idempotent (CREATE OR REPLACE).
-- ==========================================

CREATE OR REPLACE FUNCTION create_daily_partitions(p_days_ahead INTEGER DEFAULT 3, p_days_back INTEGER DEFAULT 0)
RETURNS INTEGER AS $$
DECLARE
    v_tables  TEXT[] := ARRAY['metric_samples_gpu', 'metric_samples_engine', 'metric_samples_session',
                              'metric_rollups_gpu_1m', 'metric_rollups_engine_1m',
                              'log_chunks', 'traces', 'usage_events'];
    v_schema  TEXT := current_schema();
    v_table   TEXT;
    v_day     DATE;
    v_name    TEXT;
    v_created INTEGER := 0;
    v_offset  INTEGER;
BEGIN
    FOREACH v_table IN ARRAY v_tables LOOP
        FOR v_offset IN -p_days_back..p_days_ahead LOOP
            v_day  := (now() AT TIME ZONE 'UTC')::DATE + v_offset;
            v_name := format('%s_p%s', v_table, to_char(v_day, 'YYYYMMDD'));
            IF to_regclass(format('%I.%I', v_schema, v_name)) IS NULL THEN
                EXECUTE format('CREATE TABLE IF NOT EXISTS %I.%I PARTITION OF %I.%I FOR VALUES FROM (%L) TO (%L)',
                               v_schema, v_name, v_schema, v_table,
                               to_char(v_day, 'YYYY-MM-DD') || ' 00:00:00+00',
                               to_char(v_day + 1, 'YYYY-MM-DD') || ' 00:00:00+00');
                v_created := v_created + 1;
            END IF;
        END LOOP;
    END LOOP;
    RETURN v_created;
END;
$$ LANGUAGE plpgsql;
COMMENT ON FUNCTION create_daily_partitions(INTEGER, INTEGER) IS 'Create daily partitions (UTC) for all time-series tables of the current schema. Idempotent.';

CREATE OR REPLACE FUNCTION drop_old_partitions(p_table TEXT, p_retain_days INTEGER)
RETURNS INTEGER AS $$
DECLARE
    v_schema  TEXT := current_schema();
    v_child   TEXT;
    v_day     DATE;
    v_dropped INTEGER := 0;
    v_cutoff  DATE := (now() AT TIME ZONE 'UTC')::DATE - p_retain_days;
BEGIN
    FOR v_child IN
        SELECT c.relname
        FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        JOIN pg_class p ON p.oid = i.inhparent
        WHERE p.relname = p_table
          AND p.relnamespace = v_schema::regnamespace
          AND c.relnamespace = v_schema::regnamespace
    LOOP
        BEGIN
            v_day := to_date(substring(v_child FROM '_p(\d{8})$'), 'YYYYMMDD');
        EXCEPTION WHEN OTHERS THEN
            CONTINUE;
        END;
        IF v_day IS NOT NULL AND v_day < v_cutoff THEN
            EXECUTE format('DROP TABLE IF EXISTS %I.%I', v_schema, v_child);
            v_dropped := v_dropped + 1;
        END IF;
    END LOOP;
    RETURN v_dropped;
END;
$$ LANGUAGE plpgsql;
COMMENT ON FUNCTION drop_old_partitions(TEXT, INTEGER) IS 'Drop daily partitions of one table of the current schema older than the retention window.';

-- 001_init's call resolved names through the search_path, so a schema built
-- next to an existing one could end up without its own partitions.
SELECT create_daily_partitions(3, 0);
