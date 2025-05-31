-- /Users/radiant/Desktop/RXinDexer/complete_db_optimization.sql
-- This script implements comprehensive database optimizations to:
-- 1. Fix the recurring slow queries by intercepting them at the database level
-- 2. Implement regular table maintenance
-- 3. Optimize for common query patterns
-- 4. Add monitoring tools
-- 5. Optimize bulk loading for initial sync
-- 6. Configure PostgreSQL for high-performance indexing

-- 1. INTERCEPT SLOW TEMPORARY TABLE CREATION
-- Create a more efficient function that's automatically used instead of the slow query
CREATE OR REPLACE FUNCTION create_temp_balances_efficient() RETURNS VOID AS $$
DECLARE
    start_time TIMESTAMP WITH TIME ZONE := clock_timestamp();
BEGIN
    -- Drop existing table if it exists
    DROP TABLE IF EXISTS temp_balances;
    
    -- Create temp table from materialized view instead of aggregating UTXO table
    CREATE TEMP TABLE temp_balances AS
    SELECT address, total_balance AS balance
    FROM address_balances;
    
    -- Log the performance improvement
    RAISE NOTICE 'Created temp_balances in % ms using materialized view (optimized)',
                 EXTRACT(MILLISECONDS FROM clock_timestamp() - start_time);
END;
$$ LANGUAGE plpgsql;

-- Create a rule to intercept the expensive query and replace it with our efficient function
CREATE OR REPLACE RULE intercept_temp_balances AS
    ON SELECT TO utxos
    WHERE current_query() LIKE '%CREATE TEMPORARY TABLE temp_balances%'
    DO INSTEAD
    SELECT create_temp_balances_efficient();

-- 2. IMPLEMENT REGULAR TABLE MAINTENANCE
-- Create a table to track when maintenance was last run
CREATE TABLE IF NOT EXISTS maintenance_history (
    table_name TEXT PRIMARY KEY,
    last_vacuum TIMESTAMP WITH TIME ZONE,
    last_analyze TIMESTAMP WITH TIME ZONE,
    last_reindex TIMESTAMP WITH TIME ZONE
);

-- Insert initial records for important tables
INSERT INTO maintenance_history (table_name, last_vacuum, last_analyze, last_reindex)
VALUES 
    ('utxos', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day'),
    ('transactions', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day'),
    ('blocks', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day'),
    ('holders', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day'),
    ('glyph_tokens', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day')
ON CONFLICT (table_name) DO NOTHING;

-- Create a function to perform maintenance on tables
CREATE OR REPLACE FUNCTION perform_table_maintenance() RETURNS VOID AS $$
DECLARE
    tables_to_maintain TEXT[] := ARRAY['utxos', 'transactions', 'blocks', 'holders', 'glyph_tokens'];
    tbl TEXT;
    last_maintenance TIMESTAMP WITH TIME ZONE;
BEGIN
    -- Process each table
    FOREACH tbl IN ARRAY tables_to_maintain LOOP
        -- Check when it was last maintained
        SELECT last_vacuum INTO last_maintenance 
        FROM maintenance_history
        WHERE table_name = tbl;
        
        -- If more than 12 hours since last maintenance or NULL
        IF last_maintenance IS NULL OR last_maintenance < NOW() - INTERVAL '12 hours' THEN
            RAISE NOTICE 'Performing maintenance on %', tbl;
            
            -- VACUUM to reclaim space and update statistics
            EXECUTE 'VACUUM (ANALYZE, VERBOSE) ' || tbl;
            
            -- Update maintenance timestamp
            UPDATE maintenance_history
            SET last_vacuum = NOW(),
                last_analyze = NOW()
            WHERE table_name = tbl;
        END IF;
    END LOOP;
    
    -- Also refresh the materialized view
    PERFORM refresh_balances_now();
    
    RAISE NOTICE 'Maintenance completed successfully';
END;
$$ LANGUAGE plpgsql;

-- 3. OPTIMIZE FOR COMMON QUERY PATTERNS
-- Create optimized indices for common queries
CREATE INDEX IF NOT EXISTS idx_utxos_address_spent ON utxos (address) WHERE spent = FALSE;
CREATE INDEX IF NOT EXISTS idx_utxos_block_height ON utxos (block_height);
CREATE INDEX IF NOT EXISTS idx_blocks_height ON blocks (height);
CREATE INDEX IF NOT EXISTS idx_transactions_block_height ON transactions (block_height);
CREATE INDEX IF NOT EXISTS idx_glyph_tokens_type ON glyph_tokens (type);

-- Create partial indices for more specific queries
CREATE INDEX IF NOT EXISTS idx_large_utxos ON utxos (address, amount) 
WHERE amount > 1000000 AND spent = FALSE;

-- Optimize index for token searches
CREATE INDEX IF NOT EXISTS idx_token_ref ON utxos (token_ref) WHERE token_ref IS NOT NULL;

-- Create specialized functions for common operations
CREATE OR REPLACE FUNCTION get_address_history(address_param TEXT, limit_param INTEGER DEFAULT 100)
RETURNS TABLE (
    txid TEXT,
    vout INTEGER,
    amount NUMERIC,
    is_spent BOOLEAN,
    block_height INTEGER,
    block_time TIMESTAMP WITH TIME ZONE
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        u.txid,
        u.vout,
        u.amount,
        u.spent,
        u.block_height,
        b.timestamp AS block_time
    FROM 
        utxos u
        JOIN blocks b ON u.block_height = b.height
    WHERE 
        u.address = address_param
    ORDER BY 
        b.timestamp DESC
    LIMIT 
        limit_param;
END;
$$ LANGUAGE plpgsql;

-- 4. ADD MONITORING TOOLS
-- Create table to store query statistics
CREATE TABLE IF NOT EXISTS query_stats (
    id SERIAL PRIMARY KEY,
    query_pattern TEXT,
    total_calls INTEGER DEFAULT 0,
    total_duration_ms BIGINT DEFAULT 0,
    avg_duration_ms NUMERIC DEFAULT 0,
    last_called TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    improvement_suggestion TEXT
);

-- Create a function to log and analyze slow queries
CREATE OR REPLACE FUNCTION log_and_analyze_query(
    query_text TEXT,
    duration_ms INTEGER
) RETURNS VOID AS $$
DECLARE
    pattern TEXT;
BEGIN
    -- Determine the query pattern
    IF query_text LIKE '%CREATE TEMPORARY TABLE%' THEN
        pattern := 'CREATE TEMPORARY TABLE';
    ELSIF query_text LIKE '%SELECT%FROM utxos%WHERE spent = FALSE%GROUP BY%' THEN
        pattern := 'BALANCE AGGREGATION';
    ELSIF query_text LIKE '%UPDATE utxos%SET spent = TRUE%' THEN
        pattern := 'MARK UTXOS SPENT';
    ELSIF query_text LIKE '%INSERT INTO utxos%' THEN
        pattern := 'INSERT UTXOS';
    ELSE
        pattern := 'OTHER QUERY';
    END IF;
    
    -- Update stats
    INSERT INTO query_stats (query_pattern, total_calls, total_duration_ms, avg_duration_ms, last_called)
    VALUES (pattern, 1, duration_ms, duration_ms, NOW())
    ON CONFLICT (query_pattern) DO UPDATE
    SET 
        total_calls = query_stats.total_calls + 1,
        total_duration_ms = query_stats.total_duration_ms + duration_ms,
        avg_duration_ms = (query_stats.total_duration_ms + duration_ms) / (query_stats.total_calls + 1),
        last_called = NOW();
        
    -- Provide improvement suggestions
    UPDATE query_stats
    SET improvement_suggestion = CASE
        WHEN pattern = 'CREATE TEMPORARY TABLE' THEN 
            'Use materialized view instead of creating temporary tables'
        WHEN pattern = 'BALANCE AGGREGATION' THEN 
            'Use address_balances materialized view'
        WHEN pattern = 'MARK UTXOS SPENT' THEN 
            'Use batch_update_utxos function for better performance'
        WHEN pattern = 'INSERT UTXOS' THEN 
            'Consider using COPY or batch inserts for better performance'
        ELSE 'Analyze query plan and consider adding indices'
    END
    WHERE query_pattern = pattern;
END;
$$ LANGUAGE plpgsql;

-- Create or replace the function to monitor and terminate long-running queries
CREATE OR REPLACE FUNCTION monitor_and_terminate_long_queries() RETURNS VOID AS $$
DECLARE
    long_running_query RECORD;
BEGIN
    -- Find queries running for more than 10 seconds
    FOR long_running_query IN 
        SELECT 
            pid, 
            query, 
            EXTRACT(EPOCH FROM (NOW() - query_start)) AS duration_sec
        FROM 
            pg_stat_activity
        WHERE 
            state = 'active'
            AND query NOT LIKE '%pg_stat_activity%'
            AND query_start < NOW() - INTERVAL '10 seconds'
    LOOP
        -- Log it
        RAISE NOTICE 'Long running query detected (pid: %, duration: % sec): %', 
            long_running_query.pid, 
            long_running_query.duration_sec,
            long_running_query.query;
            
        -- If it's one of our known problematic queries and running for > 20 seconds, terminate it
        IF (long_running_query.query LIKE '%CREATE TEMPORARY TABLE temp_balances%' OR
            long_running_query.query LIKE '%SELECT%FROM utxos%WHERE spent = FALSE%GROUP BY%') AND
           long_running_query.duration_sec > 20 THEN
           
            -- Terminate the query
            PERFORM pg_terminate_backend(long_running_query.pid);
            
            RAISE NOTICE 'Terminated slow query (pid: %)', long_running_query.pid;
        END IF;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Create trigger to capture and log slow queries
CREATE OR REPLACE FUNCTION capture_slow_query() RETURNS TRIGGER AS $$
DECLARE
    query_text TEXT;
    duration_ms INTEGER;
BEGIN
    -- Get current query
    SELECT current_query() INTO query_text;
    
    -- Estimate duration (rough approximation)
    SELECT EXTRACT(MILLISECONDS FROM (clock_timestamp() - statement_timestamp()))::INTEGER 
    INTO duration_ms;
    
    -- Only log if it's slow (> 100ms)
    IF duration_ms > 100 THEN
        PERFORM log_and_analyze_query(query_text, duration_ms);
    END IF;
    
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Add triggers for commonly used tables
DROP TRIGGER IF EXISTS capture_utxo_query_stats ON utxos;
CREATE TRIGGER capture_utxo_query_stats
AFTER INSERT OR UPDATE OR DELETE ON utxos
FOR EACH STATEMENT
EXECUTE FUNCTION capture_slow_query();

-- 5. ADDITIONAL PERFORMANCE OPTIMIZATIONS
-- Optimize PostgreSQL configuration for better performance
ALTER SYSTEM SET work_mem = '256MB';                     -- Memory for sort operations
ALTER SYSTEM SET maintenance_work_mem = '1GB';           -- For index creation
ALTER SYSTEM SET checkpoint_timeout = '30min';           -- Less frequent checkpoints during bulk loading
ALTER SYSTEM SET max_wal_size = '8GB';                  -- Larger WAL size for bulk operations
ALTER SYSTEM SET wal_buffers = '16MB';                  -- More WAL buffers
ALTER SYSTEM SET random_page_cost = '1.1';              -- Assumes SSD storage
ALTER SYSTEM SET effective_cache_size = '8GB';          -- Depends on available memory
ALTER SYSTEM SET max_parallel_workers_per_gather = '4';  -- Use parallel workers for queries
ALTER SYSTEM SET max_parallel_workers = '8';            -- Maximum workers for parallel operations
ALTER SYSTEM SET maintenance_work_mem = '256MB';         -- Memory for maintenance operations
ALTER SYSTEM SET effective_cache_size = '4GB';           -- Estimation of disk cache size
ALTER SYSTEM SET max_parallel_workers_per_gather = '4';  -- Parallel query workers
ALTER SYSTEM SET max_parallel_workers = '8';             -- Maximum parallel workers
ALTER SYSTEM SET random_page_cost = '1.1';               -- Assumes SSD storage
ALTER SYSTEM SET effective_io_concurrency = '200';       -- Concurrent I/O operations
ALTER SYSTEM SET checkpoint_completion_target = '0.9';   -- Spread checkpoints
ALTER SYSTEM SET default_statistics_target = '100';      -- Statistics detail level
ALTER SYSTEM SET autovacuum_vacuum_scale_factor = '0.1'; -- More frequent autovacuum
ALTER SYSTEM SET autovacuum_analyze_scale_factor = '0.05'; -- More frequent analyze
ALTER SYSTEM SET autovacuum_max_workers = '4';           -- Autovacuum workers
ALTER SYSTEM SET autovacuum_vacuum_cost_delay = '2ms';   -- Reduce autovacuum pause time
ALTER SYSTEM SET checkpoint_timeout = '10min';           -- Less frequent checkpoints

-- 6. SETUP MAINTENANCE SCHEDULE
-- Create function to schedule maintenance
CREATE OR REPLACE FUNCTION schedule_maintenance() RETURNS VOID AS $$
BEGIN
    -- Run maintenance on tables
    PERFORM perform_table_maintenance();
    
    -- Check for and terminate long-running queries
    PERFORM monitor_and_terminate_long_queries();
END;
$$ LANGUAGE plpgsql;

-- Function to force immediate database optimizations
CREATE OR REPLACE FUNCTION optimize_database_now() RETURNS TEXT AS $$
DECLARE
    start_time TIMESTAMP WITH TIME ZONE := clock_timestamp();
    optimization_report TEXT := '';
    duration_ms INTEGER;
BEGIN
    -- Refresh materialized views first
    PERFORM refresh_balances_now();
    optimization_report := optimization_report || 'Refreshed materialized views. ';
    
    -- Run VACUUM ANALYZE on important tables
    PERFORM perform_table_maintenance();
    optimization_report := optimization_report || 'Performed table maintenance. ';
    
    -- Terminate any long-running problematic queries
    PERFORM monitor_and_terminate_long_queries();
    optimization_report := optimization_report || 'Checked for long-running queries. ';
    
    -- Calculate duration
    duration_ms := EXTRACT(MILLISECONDS FROM (clock_timestamp() - start_time))::INTEGER;
    
    RETURN optimization_report || 'Completed in ' || duration_ms || 'ms.';
END;
$$ LANGUAGE plpgsql;

-- 7. CREATE TRANSACTION RATE CONTROL FUNCTION
-- This helps prevent database overload during high-volume sync
CREATE OR REPLACE FUNCTION control_transaction_rate(
    current_tx_count INTEGER,
    tx_threshold INTEGER DEFAULT 500
) RETURNS BOOLEAN AS $$
DECLARE
    should_pause BOOLEAN := FALSE;
    current_load RECORD;
BEGIN
    -- Get current load metrics
    SELECT 
        load_1min, 
        load_5min, 
        load_15min 
    INTO current_load
    FROM (
        SELECT 
            (SELECT extract(epoch FROM current_timestamp - stats_reset) / 60 
             FROM pg_stat_database 
             WHERE datname = current_database()) AS load_1min,
            (SELECT extract(epoch FROM current_timestamp - stats_reset) / 300 
             FROM pg_stat_database 
             WHERE datname = current_database()) AS load_5min,
            (SELECT extract(epoch FROM current_timestamp - stats_reset) / 900 
             FROM pg_stat_database 
             WHERE datname = current_database()) AS load_15min
    ) AS load_metrics;
    
    -- Determine if we should pause based on load and transaction count
    IF current_tx_count > tx_threshold OR 
       current_load.load_1min > 10 THEN
        should_pause := TRUE;
    END IF;
    
    RETURN should_pause;
END;
$$ LANGUAGE plpgsql;

-- Run initial optimization
SELECT optimize_database_now();

-- Reload configuration
SELECT pg_reload_conf();
