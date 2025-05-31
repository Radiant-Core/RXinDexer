-- /Users/radiant/Desktop/RXinDexer/db_maintenance.sql
-- This script provides database maintenance and fixes issues with high resource usage
-- It's designed to be run directly rather than from functions to avoid restrictions

-- First, let's terminate any long-running problematic queries
SELECT pg_terminate_backend(pid) 
FROM pg_stat_activity 
WHERE query LIKE '%CREATE TEMPORARY TABLE temp_balances%'
   OR query LIKE '%SELECT%FROM utxos%WHERE spent = FALSE%GROUP BY%'
   AND query_start < NOW() - INTERVAL '10 seconds';

-- Run ANALYZE on the UTXO table to update statistics
ANALYZE VERBOSE utxos;

-- Create a more efficient function for the temp_balances creation that's causing problems
CREATE OR REPLACE FUNCTION create_temp_balances_efficient() RETURNS VOID AS $$
BEGIN
    -- Drop existing table if it exists
    DROP TABLE IF EXISTS temp_balances;
    
    -- Create temp table from materialized view instead of aggregating UTXO table
    CREATE TEMPORARY TABLE temp_balances AS
    SELECT address, total_balance AS balance
    FROM address_balances;
    
    -- Log that we used the efficient version
    RAISE NOTICE 'Created temp_balances using materialized view (optimized)';
END;
$$ LANGUAGE plpgsql;

-- Refresh the materialized view to ensure fresh data
SELECT refresh_balances_now();

-- Create improved indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_utxos_address_spent ON utxos (address) WHERE spent = FALSE;
CREATE INDEX IF NOT EXISTS idx_utxos_block_height ON utxos (block_height);
CREATE INDEX IF NOT EXISTS idx_utxos_large_balances ON utxos (address, amount) WHERE amount > 1000000 AND spent = FALSE;
CREATE INDEX IF NOT EXISTS idx_utxos_token_ref ON utxos (token_ref) WHERE token_ref IS NOT NULL;

-- Add more indexes for JOIN performance
CREATE INDEX IF NOT EXISTS idx_holders_address ON holders (address);
CREATE INDEX IF NOT EXISTS idx_holders_balance ON holders (rxd_balance DESC);

-- Create a tracking table for query stats
CREATE TABLE IF NOT EXISTS query_stats (
    id SERIAL PRIMARY KEY,
    query_pattern TEXT UNIQUE,
    total_calls INTEGER DEFAULT 0,
    total_duration_ms BIGINT DEFAULT 0,
    avg_duration_ms NUMERIC DEFAULT 0,
    last_called TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    improvement_suggestion TEXT
);

-- Create function to log and analyze slow queries
CREATE OR REPLACE FUNCTION log_slow_query(
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

-- Create a trigger to capture slow queries
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
        PERFORM log_slow_query(query_text, duration_ms);
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

-- Optimize for high-volume inserts with a more efficient function
CREATE OR REPLACE FUNCTION batch_insert_utxos(
    txids TEXT[],
    vouts INTEGER[],
    addresses TEXT[],
    amounts NUMERIC[],
    spent_values BOOLEAN[],
    block_heights INTEGER[],
    block_hashes TEXT[]
) RETURNS INTEGER AS $$
DECLARE
    insert_count INTEGER := 0;
BEGIN
    -- Perform the batch insert
    INSERT INTO utxos (txid, vout, address, amount, spent, block_height, block_hash, created_at, updated_at)
    SELECT 
        txids[i],
        vouts[i],
        addresses[i],
        amounts[i],
        spent_values[i],
        block_heights[i],
        block_hashes[i],
        NOW(),
        NOW()
    FROM generate_series(1, array_length(txids, 1)) AS i
    ON CONFLICT (txid, vout) 
    DO UPDATE SET 
        address = EXCLUDED.address,
        amount = EXCLUDED.amount,
        block_height = EXCLUDED.block_height,
        block_hash = EXCLUDED.block_hash,
        updated_at = NOW();
    
    GET DIAGNOSTICS insert_count = ROW_COUNT;
    
    -- Auto refresh view if many records were inserted
    IF insert_count > 1000 THEN
        PERFORM refresh_balances_now();
    END IF;
    
    RETURN insert_count;
END;
$$ LANGUAGE plpgsql;

-- Optimize database settings for better performance
ALTER SYSTEM SET work_mem = '64MB';
ALTER SYSTEM SET maintenance_work_mem = '256MB';
ALTER SYSTEM SET max_parallel_workers_per_gather = '4';
ALTER SYSTEM SET max_parallel_workers = '8';
ALTER SYSTEM SET autovacuum_vacuum_scale_factor = '0.1';
ALTER SYSTEM SET autovacuum_analyze_scale_factor = '0.05';
ALTER SYSTEM SET autovacuum_max_workers = '4';
ALTER SYSTEM SET autovacuum_vacuum_cost_delay = '2ms';
ALTER SYSTEM SET checkpoint_timeout = '10min';
ALTER SYSTEM SET effective_cache_size = '4GB';
ALTER SYSTEM SET random_page_cost = '1.1';
ALTER SYSTEM SET effective_io_concurrency = '200';
ALTER SYSTEM SET log_min_duration_statement = '1000';

-- Create a script to handle the CREATE TEMPORARY TABLE problem by overriding it
-- with a temporary view that uses the materialized view instead
CREATE OR REPLACE FUNCTION override_temp_balance_creation() RETURNS VOID AS $$
BEGIN
    -- Create a view with the same structure that uses the materialized view
    CREATE OR REPLACE VIEW temp_balance_view AS
    SELECT address, total_balance AS balance
    FROM address_balances;
    
    RAISE NOTICE 'Created temp_balance_view that redirects to the materialized view';
END;
$$ LANGUAGE plpgsql;

-- Execute the function to create the override view
SELECT override_temp_balance_creation();

-- Reload configuration
SELECT pg_reload_conf();
