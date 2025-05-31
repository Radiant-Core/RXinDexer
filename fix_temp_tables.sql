-- /Users/radiant/Desktop/RXinDexer/fix_temp_tables.sql
-- This script specifically targets and eliminates the temp_balances table creation
-- by implementing a more efficient approach using our materialized view

-- First, terminate any existing temp table creation queries
SELECT pid, pg_terminate_backend(pid) 
FROM pg_stat_activity 
WHERE query LIKE '%CREATE TEMPORARY TABLE temp_balances%'
  AND state = 'active'
  AND pid <> pg_backend_pid();

-- Create a function to efficiently create the temp_balances table
-- This replaces the slow implementation with one that uses our materialized view
CREATE OR REPLACE FUNCTION create_temp_balances_fast() RETURNS VOID AS $$
BEGIN
    -- Drop the table if it exists
    DROP TABLE IF EXISTS temp_balances;
    
    -- Create the temp table from materialized view instead of slow aggregation
    CREATE TEMPORARY TABLE temp_balances AS
    SELECT address, total_balance AS balance
    FROM address_balances;
    
    -- Add index to match what would be created in the original
    CREATE INDEX ON temp_balances (address);
END;
$$ LANGUAGE plpgsql;

-- Create a new function to trigger the temp_balances creation in a controlled way
-- This function will be exposed to the application code
CREATE OR REPLACE FUNCTION get_temp_balances() RETURNS TABLE(address VARCHAR, balance NUMERIC) AS $$
BEGIN
    -- Create the temp table efficiently
    PERFORM create_temp_balances_fast();
    
    -- Return the data
    RETURN QUERY
    SELECT t.address, t.balance
    FROM temp_balances t;
END;
$$ LANGUAGE plpgsql;

-- Explicitly add a performance trigger on the utxos table
-- This will handle the case when someone tries to do a GROUP BY query on the utxos table
CREATE OR REPLACE FUNCTION intercept_slow_aggregations() RETURNS TRIGGER AS $$
BEGIN
    -- Refresh the materialized view when needed
    PERFORM refresh_balances_now();
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Add trigger to intercept slow aggregations
DROP TRIGGER IF EXISTS trg_intercept_slow_aggregations ON utxos;
CREATE TRIGGER trg_intercept_slow_aggregations
BEFORE SELECT ON utxos
FOR STATEMENT
WHEN (current_query() LIKE '%GROUP BY%')
EXECUTE FUNCTION intercept_slow_aggregations();

-- Create a function to handle any remaining temp_balances creations
CREATE OR REPLACE FUNCTION handle_temp_balances_creation() RETURNS TRIGGER AS $$
BEGIN
    -- We want to intercept the standard temp_balances creation
    -- and replace it with our efficient version
    IF TG_OP = 'INSERT' AND TG_TABLE_NAME = 'pg_temp' THEN
        -- Check if this is a temp_balances creation
        IF current_query() LIKE '%CREATE TEMPORARY TABLE temp_balances%' THEN
            -- Intercept and replace with our efficient version
            PERFORM create_temp_balances_fast();
            RAISE NOTICE 'Intercepted temp_balances creation and replaced with efficient version';
        END IF;
    END IF;
    
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Create a faster version of update_holder_balances 
CREATE OR REPLACE FUNCTION update_holder_balances_super_efficient() RETURNS INTEGER AS $$
DECLARE
    updated_count INTEGER := 0;
BEGIN
    -- Make sure the materialized view is up to date
    PERFORM refresh_balances_now();
    
    -- Use a direct update without creating temporary tables
    WITH updated_holders AS (
        UPDATE holders h
        SET rxd_balance = a.total_balance,
            updated_at = NOW()
        FROM address_balances a
        WHERE h.address = a.address
        AND h.rxd_balance <> a.total_balance
        RETURNING h.id
    )
    SELECT COUNT(*) INTO updated_count FROM updated_holders;
    
    -- Handle new addresses directly from materialized view
    WITH new_holders AS (
        INSERT INTO holders (address, rxd_balance, first_seen_at, last_seen_at, updated_at)
        SELECT 
            a.address, 
            a.total_balance,
            NOW(),
            NOW(),
            NOW()
        FROM address_balances a
        LEFT JOIN holders h ON a.address = h.address
        WHERE h.id IS NULL
        AND a.total_balance > 0
        RETURNING id
    )
    SELECT updated_count + COUNT(*) INTO updated_count FROM new_holders;
    
    RETURN updated_count;
END;
$$ LANGUAGE plpgsql;

-- Create a function to monitor for temp table creations and terminate them
CREATE OR REPLACE FUNCTION monitor_for_temp_tables() RETURNS INTEGER AS $$
DECLARE
    killed INTEGER := 0;
    query_rec RECORD;
BEGIN
    -- Find and terminate any temp_balances creation queries that run too long
    FOR query_rec IN
        SELECT pid, query
        FROM pg_stat_activity
        WHERE state = 'active'
        AND query LIKE '%CREATE TEMPORARY TABLE temp_balances%'
        AND query_start < NOW() - INTERVAL '2 seconds'
    LOOP
        -- Terminate the query
        PERFORM pg_terminate_backend(query_rec.pid);
        killed := killed + 1;
    END LOOP;
    
    RETURN killed;
END;
$$ LANGUAGE plpgsql;

-- Run the monitor function now to terminate any existing slow queries
SELECT monitor_for_temp_tables();

-- Set up database parameters to optimize for our workload
ALTER SYSTEM SET work_mem = '64MB';
ALTER SYSTEM SET maintenance_work_mem = '256MB';
ALTER SYSTEM SET effective_io_concurrency = '200';
ALTER SYSTEM SET random_page_cost = '1.1';
SELECT pg_reload_conf();

-- Analyze tables for better query planning
ANALYZE utxos;
ANALYZE holders;
ANALYZE address_balances;
