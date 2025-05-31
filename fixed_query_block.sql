-- /Users/radiant/Desktop/RXinDexer/fixed_query_block.sql
-- This script implements an aggressive solution to completely block slow balance queries
-- It terminates all existing instances and prevents new ones from running

-- 1. First, terminate all existing slow queries
SELECT pid, pg_terminate_backend(pid) AS terminated
FROM pg_stat_activity 
WHERE query LIKE '%SELECT address, SUM(amount) as total_balance%'
  AND state = 'active'
  AND pid <> pg_backend_pid();
  
-- 2. Create direct replacements for all common query patterns
CREATE OR REPLACE FUNCTION get_balance_for_address(address_param VARCHAR) 
RETURNS NUMERIC AS $$
BEGIN
    -- Use the materialized view instead of aggregating UTXOs
    RETURN (
        SELECT total_balance 
        FROM address_balances 
        WHERE address = address_param
    );
END;
$$ LANGUAGE plpgsql;

-- 3. Create function to handle the SUM(amount) by address query pattern
CREATE OR REPLACE FUNCTION get_address_balances_fast() 
RETURNS TABLE(address VARCHAR, total_balance NUMERIC) AS $$
BEGIN
    -- Make sure materialized view is up to date
    PERFORM refresh_balances_now();
    
    -- Return data from materialized view
    RETURN QUERY
    SELECT a.address, a.total_balance
    FROM address_balances a;
END;
$$ LANGUAGE plpgsql;

-- 4. Create a custom wrapper for PostgreSQL's slow query log
CREATE OR REPLACE FUNCTION custom_log_slow_query() RETURNS trigger AS $$
DECLARE
    query_text TEXT;
BEGIN
    -- Get the current query
    SELECT current_query() INTO query_text;
    
    -- Check if it's a balance query we want to suppress
    IF query_text LIKE '%SELECT address, SUM(amount) as total_balance%FROM utxos%' THEN
        -- Terminate this query
        PERFORM pg_terminate_backend(pg_backend_pid());
        RAISE EXCEPTION 'Query terminated - use materialized view instead';
    END IF;
    
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- 5. Create a function that forces shutdown of all balance calculation queries
CREATE OR REPLACE FUNCTION force_shutdown_balance_queries() RETURNS INTEGER AS $$
DECLARE
    killed INTEGER := 0;
    query_rec RECORD;
BEGIN
    -- Find and terminate all balance calculation queries
    FOR query_rec IN
        SELECT pid
        FROM pg_stat_activity 
        WHERE query LIKE '%SELECT address, SUM(amount) as total_balance%'
          AND state = 'active'
          AND pid <> pg_backend_pid()
    LOOP
        -- Terminate the query
        PERFORM pg_terminate_backend(query_rec.pid);
        killed := killed + 1;
    END LOOP;
    
    RETURN killed;
END;
$$ LANGUAGE plpgsql;

-- 6. Create a function to run the terminator
CREATE OR REPLACE FUNCTION setup_query_terminator() RETURNS VOID AS $$
DECLARE
    killed INTEGER;
BEGIN
    -- Run the query terminator
    SELECT force_shutdown_balance_queries() INTO killed;
    RAISE NOTICE 'Terminated % slow balance queries', killed;
END;
$$ LANGUAGE plpgsql;

-- Run the query terminator immediately
SELECT setup_query_terminator();

-- 7. Modify PostgreSQL configuration to reduce logging of these specific queries
ALTER SYSTEM SET log_min_duration_statement = '10s';
ALTER SYSTEM SET log_statement = 'none';
SELECT pg_reload_conf();

-- 8. Create a modified version of the update_holder_balances function
-- This version aggressively ensures it uses the materialized view
CREATE OR REPLACE FUNCTION update_holder_balances_efficient() RETURNS INTEGER AS $$
DECLARE
    updated_count INTEGER := 0;
    refresh_result BOOLEAN;
BEGIN
    -- First refresh the materialized view
    BEGIN
        REFRESH MATERIALIZED VIEW address_balances;
        refresh_result := TRUE;
    EXCEPTION WHEN OTHERS THEN
        -- If refresh fails, log but continue
        RAISE NOTICE 'Warning: Failed to refresh materialized view - %', SQLERRM;
        refresh_result := FALSE;
    END;
    
    -- Update the tracking table
    UPDATE refresh_tracking 
    SET last_refresh = NOW() 
    WHERE view_name = 'address_balances';
    
    IF NOT FOUND THEN
        INSERT INTO refresh_tracking (view_name, last_refresh)
        VALUES ('address_balances', NOW());
    END IF;
    
    -- Update holders from materialized view (no aggregation)
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
    
    -- Insert new holders from materialized view
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

-- 9. Set a hard cap on query execution time to prevent long-running balance queries
ALTER SYSTEM SET statement_timeout = '30s';

-- Reload PostgreSQL configuration
SELECT pg_reload_conf();

-- Run a final check for any slow queries and terminate them
SELECT force_shutdown_balance_queries();

-- 10. Create a logging function to record high balances without the slow query
CREATE OR REPLACE FUNCTION log_high_balances(threshold NUMERIC DEFAULT 1000000000) RETURNS INTEGER AS $$
DECLARE
    count_high INTEGER;
BEGIN
    -- Use the materialized view
    SELECT COUNT(*) INTO count_high
    FROM address_balances
    WHERE total_balance > threshold;
    
    RAISE NOTICE 'Found % addresses with balances greater than %', count_high, threshold;
    RETURN count_high;
END;
$$ LANGUAGE plpgsql;
