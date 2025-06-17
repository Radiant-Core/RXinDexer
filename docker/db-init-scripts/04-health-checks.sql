-- /docker/db-init-scripts/04-health-checks.sql
-- Database health check functions for monitoring

-- Function to perform a basic health check
CREATE OR REPLACE FUNCTION perform_health_check()
RETURNS JSONB AS $$
DECLARE
    result JSONB;
    db_version text;
    db_name text;
    db_size text;
    active_connections int;
    max_connections int;
    cache_hit_ratio numeric;
    replication_status jsonb;
    replication_delay interval;
    last_vacuum timestamp with time zone;
    last_analyze timestamp with time zone;
    dead_tuples bigint;
    live_tuples bigint;
    dead_tuples_pct numeric;
    long_running_queries int;
    blocking_queries int;
    
    -- Thresholds
    max_cache_hit_ratio numeric := 0.99;  -- 99% cache hit ratio is good
    max_replication_delay interval := '5 min';
    max_vacuum_age interval := '7 days';
    max_analyze_age interval := '7 days';
    max_dead_tuples_pct numeric := 10.0;  -- 10% dead tuples is a threshold for concern
    max_long_running_query_age interval := '5 min';
    
    -- Status tracking
    is_healthy boolean := true;
    messages text[] := '{}';
    warnings text[] := '{}';
    errors text[] := '{}';
    
BEGIN
    -- Get basic database info
    SELECT 
        version(),
        current_database(),
        pg_size_pretty(pg_database_size(current_database()))
    INTO db_version, db_name, db_size;
    
    -- Get connection info
    SELECT 
        count(*),
        current_setting('max_connections')::int
    INTO active_connections, max_connections
    FROM pg_stat_activity
    WHERE pid <> pg_backend_pid();
    
    -- Calculate cache hit ratio
    SELECT 
        sum(heap_blks_hit) / nullif(sum(heap_blks_hit) + sum(heap_blks_read), 0)::numeric
    INTO cache_hit_ratio
    FROM pg_statio_user_tables;
    
    -- Check for long running queries
    SELECT count(*)
    INTO long_running_queries
    FROM pg_stat_activity
    WHERE state != 'idle'
    AND query_start < (now() - max_long_running_query_age);
    
    -- Check for blocking queries
    SELECT count(DISTINCT blocked_locks.pid)
    INTO blocking_queries
    FROM pg_catalog.pg_locks blocked_locks
    JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
    JOIN pg_catalog.pg_locks blocking_locks 
        ON blocking_locks.locktype = blocked_locks.locktype
        AND blocking_locks.DATABASE IS NOT DISTINCT FROM blocked_locks.DATABASE
        AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
        AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
        AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
        AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
        AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
        AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
        AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
        AND blocking_locks.objsubid = blocked_locks.objsubid
        AND blocking_locks.pid != blocked_locks.pid
    JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
    WHERE NOT blocked_locks.GRANTED;
    
    -- Get replication status (if in recovery)
    IF pg_is_in_recovery() THEN
        SELECT 
            jsonb_build_object(
                'in_recovery', true,
                'replay_lag', pg_last_wal_replay_lag(),
                'replay_lag_bytes', pg_last_wal_replay_lag()::text::interval * 16 * 1024 * 1024, -- Approximate bytes
                'replay_lsn', pg_last_wal_replay_lsn()::text,
                'replay_time', pg_last_xact_replay_timestamp()
            )
        INTO replication_status;
        
        -- Check for replication delay
        SELECT pg_last_wal_replay_lag() INTO replication_delay;
        IF replication_delay > max_replication_delay THEN
            is_healthy := false;
            errors := array_append(errors, format('Replication delay is high: %s', replication_delay));
        END IF;
    ELSE
        SELECT 
            jsonb_build_object(
                'in_recovery', false,
                'current_wal_lsn', pg_current_wal_lsn()::text,
                'last_wal_receive_lsn', pg_last_wal_receive_lsn()::text,
                'last_wal_replay_lsn', pg_last_wal_replay_lsn()::text,
                'is_wal_replay_paused', pg_is_wal_replay_paused()
            )
        INTO replication_status;
    END IF;
    
    -- Get last vacuum and analyze times
    SELECT 
        max(last_vacuum),
        max(last_analyze)
    INTO last_vacuum, last_analyze
    FROM pg_stat_user_tables
    WHERE schemaname NOT IN ('pg_catalog', 'information_schema');
    
    -- Check for old vacuums/analyzes
    IF last_vacuum < (now() - max_vacuum_age) THEN
        warnings := array_append(warnings, format('Last vacuum was more than %s ago', max_vacuum_age));
    END IF;
    
    IF last_analyze < (now() - max_analyze_age) THEN
        warnings := array_append(warnings, format('Last analyze was more than %s ago', max_analyze_age));
    END IF;
    
    -- Check for dead tuples
    SELECT 
        sum(n_dead_tup),
        sum(n_live_tup),
        round((sum(n_dead_tup)::numeric / nullif(sum(n_live_tup + n_dead_tup), 0) * 100)::numeric, 2)
    INTO dead_tuples, live_tuples, dead_tuples_pct
    FROM pg_stat_user_tables
    WHERE schemaname NOT IN ('pg_catalog', 'information_schema');
    
    IF dead_tuples_pct > max_dead_tuples_pct THEN
        is_healthy := false;
        errors := array_append(errors, format('High percentage of dead tuples: %s%%', dead_tuples_pct));
    END IF;
    
    -- Check for long running queries
    IF long_running_queries > 0 THEN
        warnings := array_append(warnings, format('%s long running queries detected', long_running_queries));
    END IF;
    
    -- Check for blocking queries
    IF blocking_queries > 0 THEN
        is_healthy := false;
        errors := array_append(errors, format('%s blocking queries detected', blocking_queries));
    END IF;
    
    -- Check cache hit ratio
    IF cache_hit_ratio < max_cache_hit_ratio THEN
        warnings := array_append(warnings, format('Cache hit ratio is low: %s%% (should be > %s%%)', 
            round(cache_hit_ratio * 100, 2), 
            max_cache_hit_ratio * 100));
    END IF;
    
    -- Check connection usage
    IF (active_connections::float / max_connections) > 0.8 THEN
        is_healthy := false;
        errors := array_append(errors, format('High connection usage: %s/%s (%s%%)', 
            active_connections, max_connections,
            round((active_connections::float / max_connections) * 100, 1)));
    END IF;
    
    -- Build the result
    result := jsonb_build_object(
        'status', CASE WHEN is_healthy THEN 'healthy' ELSE 'unhealthy' END,
        'timestamp', now(),
        'database', jsonb_build_object(
            'name', db_name,
            'version', db_version,
            'size', db_size,
            'uptime', pg_postmaster_start_time() as start_time,
            'in_recovery', pg_is_in_recovery()
        ),
        'connections', jsonb_build_object(
            'active', active_connections,
            'max', max_connections,
            'usage_pct', round((active_connections::float / max_connections) * 100, 1)
        ),
        'performance', jsonb_build_object(
            'cache_hit_ratio', round(coalesce(cache_hit_ratio, 0) * 100, 2),
            'dead_tuples', dead_tuples,
            'live_tuples', live_tuples,
            'dead_tuples_pct', dead_tuples_pct,
            'last_vacuum', last_vacuum,
            'last_analyze', last_analyze
        ),
        'replication', replication_status,
        'issues', jsonb_build_object(
            'errors', errors,
            'warnings', warnings,
            'messages', messages
        )
    );
    
    RETURN result;
EXCEPTION WHEN OTHERS THEN
    RETURN jsonb_build_object(
        'status', 'error',
        'timestamp', now(),
        'error', SQLERRM,
        'context', SQLSTATE
    );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Function to check replication status
CREATE OR REPLACE FUNCTION check_replication_status()
RETURNS TABLE (
    role text,
    in_recovery boolean,
    wal_position text,
    replay_lag interval,
    replay_lag_bytes bigint,
    replay_lsn text,
    replay_time timestamp with time zone,
    is_wal_replay_paused boolean,
    last_wal_receive_lsn text,
    last_wal_receive_time timestamp with time zone,
    last_wal_replay_lsn text,
    last_wal_replay_time timestamp with time zone,
    sent_lsn text,
    write_lsn text,
    flush_lsn text,
    replay_lsn text,
    write_lag interval,
    flush_lag interval,
    replay_lag_interval interval,
    sync_priority integer,
    sync_state text,
    reply_time timestamp with time zone
) AS $$
BEGIN
    IF pg_is_in_recovery() THEN
        -- This is a replica
        RETURN QUERY
        SELECT 
            'replica'::text as role,
            true as in_recovery,
            pg_last_wal_replay_lsn()::text as wal_position,
            pg_last_wal_replay_lag() as replay_lag,
            (extract(epoch from pg_last_wal_replay_lag()) * 16 * 1024 * 1024)::bigint as replay_lag_bytes,
            pg_last_wal_replay_lsn()::text as replay_lsn,
            pg_last_xact_replay_timestamp() as replay_time,
            pg_is_wal_replay_paused() as is_wal_replay_paused,
            pg_last_wal_receive_lsn()::text as last_wal_receive_lsn,
            pg_last_wal_receive_timestamp() as last_wal_receive_time,
            pg_last_wal_replay_lsn()::text as last_wal_replay_lsn,
            pg_last_wal_replay_timestamp() as last_wal_replay_time,
            NULL::text as sent_lsn,
            NULL::text as write_lsn,
            NULL::text as flush_lsn,
            NULL::text as replay_lsn,
            NULL::interval as write_lag,
            NULL::interval as flush_lag,
            NULL::interval as replay_lag_interval,
            NULL::integer as sync_priority,
            NULL::text as sync_state,
            NULL::timestamp with time zone as reply_time;
    ELSE
        -- This is a primary
        RETURN QUERY
        SELECT 
            'primary'::text as role,
            false as in_recovery,
            pg_current_wal_lsn()::text as wal_position,
            NULL::interval as replay_lag,
            NULL::bigint as replay_lag_bytes,
            NULL::text as replay_lsn,
            NULL::timestamp with time zone as replay_time,
            NULL::boolean as is_wal_replay_paused,
            pg_last_wal_receive_lsn()::text as last_wal_receive_lsn,
            pg_last_wal_receive_timestamp() as last_wal_receive_time,
            pg_last_wal_replay_lsn()::text as last_wal_replay_lsn,
            pg_last_wal_replay_timestamp() as last_wal_replay_time,
            sent_lsn::text,
            write_lsn::text,
            flush_lsn::text,
            replay_lsn::text,
            write_lag,
            flush_lag,
            replay_lag as replay_lag_interval,
            sync_priority,
            sync_state,
            reply_time
        FROM pg_stat_replication;
    END IF;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Grant execute on health check functions to monitor user
GRANT EXECUTE ON FUNCTION perform_health_check() TO monitor;
GRANT EXECUTE ON FUNCTION check_replication_status() TO monitor;
