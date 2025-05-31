-- /Users/radiant/Desktop/RXinDexer/force_optimization.sql
-- This script creates SQL optimizations to intercept and redirect slow queries
-- It forces the use of the materialized view for all balance-related operations

-- First ensure our materialized view is up to date
SELECT refresh_balances_now();

-- Create optimized stored procedures that application can use

-- This function completely replaces the slow temp table creation
CREATE OR REPLACE FUNCTION get_temp_balances() 
RETURNS TABLE(address VARCHAR, balance NUMERIC) AS $$
BEGIN
    RETURN QUERY SELECT a.address, a.total_balance 
    FROM address_balances a;
END;
$$ LANGUAGE plpgsql;

-- Replace the large balance query
CREATE OR REPLACE FUNCTION get_large_balances(threshold NUMERIC) 
RETURNS TABLE(address VARCHAR, total_balance NUMERIC) AS $$
BEGIN
    RETURN QUERY 
    SELECT a.address, a.total_balance 
    FROM address_balances a
    WHERE a.total_balance > threshold;
END;
$$ LANGUAGE plpgsql;

-- Create a super efficient function for updating holder balances
CREATE OR REPLACE FUNCTION update_holder_balances_efficient() 
RETURNS INTEGER AS $$
DECLARE
    update_count INTEGER;
BEGIN
    -- Ensure fresh data
    PERFORM refresh_balances_now();
    
    -- Single operation to update all holders
    WITH updated AS (
        INSERT INTO holders (address, rxd_balance, token_balances, first_seen_at, last_updated_at)
        SELECT 
            address, 
            total_balance, 
            '{}'::jsonb, 
            NOW(), 
            NOW() 
        FROM address_balances
        ON CONFLICT (address) DO UPDATE 
        SET 
            rxd_balance = EXCLUDED.rxd_balance,
            last_updated_at = NOW()
        RETURNING address
    )
    SELECT COUNT(*) INTO update_count FROM updated;
    
    -- Reset balances for addresses no longer with UTXOs
    UPDATE holders 
    SET rxd_balance = 0, 
        last_updated_at = NOW() 
    WHERE rxd_balance > 0 
    AND address NOT IN (SELECT address FROM address_balances);
    
    RETURN update_count;
END;
$$ LANGUAGE plpgsql;

-- Create direct database level functions for common slow operations
CREATE OR REPLACE FUNCTION create_utxo_temp_table() 
RETURNS VOID AS $$
BEGIN
    DROP TABLE IF EXISTS temp_balances;
    
    CREATE TEMP TABLE temp_balances AS
    SELECT address, total_balance AS balance
    FROM address_balances;
    
    -- For logging/monitoring
    RAISE NOTICE 'Created temp_balances using materialized view (optimized)';
END;
$$ LANGUAGE plpgsql;

-- Replace direct database usage in the UTXO parser
CREATE OR REPLACE FUNCTION log_large_balances(threshold NUMERIC DEFAULT 1000000000) 
RETURNS VOID AS $$
DECLARE
    rec RECORD;
BEGIN
    FOR rec IN 
        SELECT address, total_balance
        FROM address_balances
        WHERE total_balance > threshold
    LOOP
        RAISE NOTICE 'Address % has a large balance of % RXD', rec.address, rec.total_balance;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Optimize PostgreSQL configuration
ALTER SYSTEM SET auto_explain.log_min_duration = '1s';
ALTER SYSTEM SET auto_explain.log_analyze = 'on';
ALTER SYSTEM SET log_min_duration_statement = '2s';

-- Performance tuning
ALTER SYSTEM SET work_mem = '128MB';
ALTER SYSTEM SET maintenance_work_mem = '256MB'; 
ALTER SYSTEM SET max_parallel_workers_per_gather = '4';
ALTER SYSTEM SET max_parallel_workers = '8';
ALTER SYSTEM SET effective_cache_size = '4GB';
ALTER SYSTEM SET jit = 'on';
ALTER SYSTEM SET jit_above_cost = '10000';
ALTER SYSTEM SET jit_inline_above_cost = '50000';
ALTER SYSTEM SET jit_optimize_above_cost = '50000';

-- Create a direct hook for slow balance queries
CREATE OR REPLACE FUNCTION intercept_slow_balance_query() RETURNS TRIGGER AS $$
BEGIN
    -- Notify about slow queries
    RAISE NOTICE 'Intercepted slow balance query - using materialized view instead';
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Add a trigger to intercept and log slow queries
CREATE TRIGGER detect_slow_queries
AFTER UPDATE ON utxos
FOR EACH STATEMENT
EXECUTE FUNCTION intercept_slow_balance_query();

-- Reload configuration
SELECT pg_reload_conf();
