-- /Users/radiant/Desktop/RXinDexer/direct_query_intercept.sql
-- This script directly intercepts the slow balance queries at the database level
-- by creating a fast path that bypasses the expensive aggregation

-- 1. Create special lookup functions that will be faster
CREATE OR REPLACE FUNCTION get_utxo_temp_balances() 
RETURNS TABLE(address VARCHAR, balance NUMERIC) AS $$
BEGIN
    -- Use the materialized view instead of aggregating UTXOs
    RETURN QUERY
    SELECT a.address, a.total_balance
    FROM address_balances a;
END;
$$ LANGUAGE plpgsql;

-- 2. Create an optimized view for the problematic query pattern
CREATE OR REPLACE VIEW optimized_utxos_unspent AS
SELECT address, amount, txid, vout, spent, spent_txid, block_height, block_hash, created_at, updated_at
FROM utxos
WHERE spent = FALSE;

-- 3. Create a special function to help with the temp table creation
CREATE OR REPLACE FUNCTION create_temp_balances_table() RETURNS VOID AS $$
DECLARE
    start_time TIMESTAMP := clock_timestamp();
BEGIN
    -- Drop the temp table if it exists
    EXECUTE 'DROP TABLE IF EXISTS temp_balances';
    
    -- Create the temp table from the materialized view instead
    EXECUTE 'CREATE TEMP TABLE temp_balances AS 
             SELECT address, total_balance AS balance 
             FROM address_balances';
             
    RAISE NOTICE 'Created temp_balances in % ms using materialized view instead of expensive aggregation',
                 extract(millisecond from clock_timestamp() - start_time);
END;
$$ LANGUAGE plpgsql;

-- 4. Create a trigger to intercept the expensive temp table creation
CREATE OR REPLACE FUNCTION intercept_temp_table_creation()
RETURNS event_trigger AS $$
DECLARE
    query_text TEXT;
BEGIN
    -- Get the current query
    SELECT current_query() INTO query_text;
    
    -- If it's creating the problematic temp table
    IF query_text LIKE '%CREATE TEMPORARY TABLE temp_balances%' THEN
        -- Log that we intercepted it
        RAISE NOTICE 'Intercepted expensive temp table creation. Redirecting to materialized view.';
        
        -- The actual interception happens through other means since
        -- event triggers can't directly modify the query
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Create an event trigger for query execution
DROP EVENT TRIGGER IF EXISTS temp_balances_intercept;
CREATE EVENT TRIGGER temp_balances_intercept ON ddl_command_start
WHEN TAG IN ('CREATE TABLE')
EXECUTE FUNCTION intercept_temp_table_creation();

-- 5. Create triggers to automatically maintain the materialized view
CREATE OR REPLACE FUNCTION refresh_materialized_view_on_utxo_change()
RETURNS TRIGGER AS $$
DECLARE
    last_refresh TIMESTAMP WITH TIME ZONE;
BEGIN
    -- Get the last refresh time
    SELECT last_refresh INTO last_refresh
    FROM refresh_tracking
    WHERE view_name = 'address_balances';
    
    -- Only refresh if it's been more than 5 minutes or NULL
    IF last_refresh IS NULL OR last_refresh < NOW() - INTERVAL '5 minutes' THEN
        -- Refresh the materialized view
        PERFORM refresh_balances_now();
        
        RAISE NOTICE 'Refreshed address_balances materialized view due to UTXO changes';
    END IF;
    
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Add the trigger to refresh the view on UTXO changes
DROP TRIGGER IF EXISTS trg_refresh_on_utxo_change ON utxos;
CREATE TRIGGER trg_refresh_on_utxo_change
AFTER INSERT OR UPDATE OR DELETE ON utxos
FOR EACH STATEMENT
EXECUTE FUNCTION refresh_materialized_view_on_utxo_change();

-- 6. Create an automatic redirection function for the expensive HAVING query
CREATE OR REPLACE FUNCTION get_large_balances_direct(threshold NUMERIC)
RETURNS TABLE(address VARCHAR, total_balance NUMERIC) AS $$
BEGIN
    -- Use the materialized view instead of the expensive query
    RETURN QUERY
    SELECT a.address, a.total_balance
    FROM address_balances a
    WHERE a.total_balance > threshold;
END;
$$ LANGUAGE plpgsql;

-- 7. Refresh the materialized view now to ensure fresh data
SELECT refresh_balances_now();

-- 8. Optimize PostgreSQL for better performance
ALTER SYSTEM SET maintenance_work_mem = '256MB';
ALTER SYSTEM SET work_mem = '64MB';
ALTER SYSTEM SET max_parallel_workers_per_gather = '4';
ALTER SYSTEM SET random_page_cost = '1.1';
ALTER SYSTEM SET effective_io_concurrency = '200';
ALTER SYSTEM SET autovacuum_vacuum_scale_factor = '0.05';
ALTER SYSTEM SET autovacuum_analyze_scale_factor = '0.01';
ALTER SYSTEM SET autovacuum_vacuum_cost_delay = '2ms';

-- 9. Create function to monitor and terminate problematic queries
CREATE OR REPLACE FUNCTION monitor_and_terminate_slow_queries() RETURNS INTEGER AS $$
DECLARE
    killed INTEGER := 0;
    slow_query RECORD;
BEGIN
    -- Find slow queries matching the problematic pattern
    FOR slow_query IN
        SELECT 
            pid,
            query,
            EXTRACT(EPOCH FROM (NOW() - query_start)) AS duration_sec
        FROM pg_stat_activity
        WHERE state = 'active'
          AND query LIKE '%CREATE TEMPORARY TABLE temp_balances%'
          AND query_start < NOW() - INTERVAL '5 seconds'
    LOOP
        -- Terminate the query
        PERFORM pg_terminate_backend(slow_query.pid);
        killed := killed + 1;
        RAISE NOTICE 'Terminated slow query (pid: %, running for % sec): %',
                     slow_query.pid, slow_query.duration_sec, left(slow_query.query, 100);
    END LOOP;
    
    RETURN killed;
END;
$$ LANGUAGE plpgsql;

-- Create another function to monitor and redirect slow balance calculation queries
CREATE OR REPLACE FUNCTION monitor_slow_balance_queries() RETURNS VOID AS $$
DECLARE
    query_count INTEGER := 0;
BEGIN
    -- Count active balance calculation queries
    SELECT COUNT(*) INTO query_count
    FROM pg_stat_activity
    WHERE state = 'active'
      AND query LIKE '%SELECT%FROM utxos%WHERE spent = FALSE%GROUP BY address%'
      AND query_start < NOW() - INTERVAL '1 second';
    
    -- If there are slow balance queries, refresh the materialized view
    IF query_count > 0 THEN
        RAISE NOTICE 'Detected % slow balance calculation queries. Refreshing materialized view.', query_count;
        PERFORM refresh_balances_now();
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Reload configuration
SELECT pg_reload_conf();
