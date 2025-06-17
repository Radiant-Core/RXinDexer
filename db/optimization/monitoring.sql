-- /Users/radiant/Desktop/RXinDexer/db/optimization/monitoring.sql
-- This file contains monitoring and logging configurations for RXinDexer

-- ============================================
-- 1. SLOW QUERY LOGGING
-- ============================================

-- Create a table to log slow queries
CREATE TABLE IF NOT EXISTS query_logs (
    id BIGSERIAL PRIMARY KEY,
    query_text TEXT NOT NULL,
    execution_time DOUBLE PRECISION NOT NULL,
    rows_returned INTEGER,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    application_name TEXT,
    parameters JSONB
);

-- Create a function to log slow queries
CREATE OR REPLACE FUNCTION log_slow_query() 
RETURNS event_trigger AS $$
DECLARE
    r RECORD;
    query_text TEXT;
    params JSONB;
BEGIN
    -- Get the query text and parameters
    SELECT current_query() INTO query_text;
    
    -- Get execution time from pg_stat_statements
    SELECT 
        total_time,
        calls,
        rows
    INTO r
    FROM pg_stat_statements 
    WHERE query = query_text
    LIMIT 1;
    
    -- If the query took more than 1000ms, log it
    IF r.total_time > 1000 THEN
        INSERT INTO query_logs (
            query_text, 
            execution_time, 
            rows_returned,
            application_name,
            parameters
        ) VALUES (
            substring(query_text from 1 for 1000), -- Truncate very long queries
            r.total_time / r.calls, -- Average time per call
            r.rows / NULLIF(r.calls, 0), -- Average rows per call
            current_setting('application_name', true),
            NULL -- Could be extended to log parameters
        );
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Create event trigger for slow queries
-- Note: This requires the 'pg_stat_statements' extension to be loaded in postgresql.conf
-- and shared_preload_libraries = 'pg_stat_statements' to be set

-- ============================================
-- 2. DATABASE STATISTICS
-- ============================================

-- View for table statistics
CREATE OR REPLACE VIEW table_statistics AS
SELECT 
    schemaname AS schema_name,
    relname AS table_name,
    n_live_tup AS row_estimate,
    pg_size_pretty(pg_table_size(C.oid)) AS table_size,
    pg_size_pretty(pg_indexes_size(C.oid)) AS index_size,
    pg_size_pretty(pg_total_relation_size(C.oid)) AS total_size,
    last_vacuum,
    last_autovacuum,
    last_analyze,
    last_autoanalyze
FROM 
    pg_stat_user_tables
    JOIN pg_class C ON pg_stat_user_tables.relid = C.oid
ORDER BY 
    pg_total_relation_size(C.oid) DESC;

-- View for index statistics
CREATE OR REPLACE VIEW index_statistics AS
SELECT
    schemaname AS schema_name,
    relname AS table_name,
    indexrelname AS index_name,
    pg_size_pretty(pg_relation_size(indexrelid)) AS index_size,
    idx_scan AS index_scans,
    idx_tup_read AS index_tuples_read,
    idx_tup_fetch AS index_tuples_fetched
FROM 
    pg_stat_user_indexes
ORDER BY 
    pg_relation_size(indexrelid) DESC;

-- ============================================
-- 3. PERFORMANCE MONITORING FUNCTIONS
-- ============================================

-- Function to get query statistics
CREATE OR REPLACE FUNCTION get_query_stats(min_exec_time_ms FLOAT DEFAULT 10.0)
RETURNS TABLE (
    query TEXT,
    calls BIGINT,
    total_time_ms DOUBLE PRECISION,
    avg_time_ms DOUBLE PRECISION,
    rows_per_call DOUBLE PRECISION,
    shared_blks_hit BIGINT,
    shared_blks_read BIGINT,
    shared_blks_dirtied BIGINT,
    shared_blks_written BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        query,
        calls,
        total_time,
        mean_time,
        rows / NULLIF(calls, 0) AS rows_per_call,
        shared_blks_hit,
        shared_blks_read,
        shared_blks_dirtied,
        shared_blks_written
    FROM 
        pg_stat_statements
    WHERE 
        mean_time >= min_exec_time_ms
    ORDER BY 
        (total_time / NULLIF(calls, 0)) DESC;
END;
$$ LANGUAGE plpgsql;
