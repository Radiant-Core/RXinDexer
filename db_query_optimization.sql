-- /Users/radiant/Desktop/RXinDexer/db_query_optimization.sql
-- This file implements query rewrite rules to automatically optimize slow balance queries
-- It intercepts common slow query patterns and redirects them to use the materialized view

-- First, refresh the materialized view to ensure it has the latest data
SELECT refresh_balances_now();

-- Create the optimized balance query view to intercept common query patterns
CREATE OR REPLACE VIEW optimized_balance_query AS
SELECT 
    address,
    total_balance AS balance,
    total_balance AS amount -- For compatibility with existing code
FROM address_balances;

-- Create a rule to redirect temporary table creation for balance aggregation
CREATE OR REPLACE RULE redirect_temp_balance_creation AS
    ON SELECT TO utxos
    WHERE current_query() LIKE '%CREATE TEMPORARY TABLE temp_balances%'
    DO INSTEAD
    SELECT 
        address, 
        total_balance as balance
    FROM address_balances;

-- Create a rule to optimize large balance queries
CREATE OR REPLACE RULE optimize_large_balance_query AS
    ON SELECT TO utxos
    WHERE current_query() LIKE '%HAVING SUM(amount) > 1000000000%'
    DO INSTEAD
    SELECT 
        address, 
        total_balance
    FROM address_balances
    WHERE total_balance > 1000000000;

-- Create an optimized function for address balance lookup
CREATE OR REPLACE FUNCTION get_address_balance(address_param VARCHAR)
RETURNS NUMERIC AS $$
DECLARE
    balance_result NUMERIC;
BEGIN
    -- Get the balance from the materialized view
    SELECT total_balance INTO balance_result
    FROM address_balances
    WHERE address = address_param;
    
    -- Return 0 if no balance found
    RETURN COALESCE(balance_result, 0);
END;
$$ LANGUAGE plpgsql;

-- Create an optimized function for all balances
CREATE OR REPLACE FUNCTION get_all_balances()
RETURNS TABLE(address VARCHAR, balance NUMERIC) AS $$
BEGIN
    RETURN QUERY
    SELECT a.address, a.total_balance
    FROM address_balances a;
END;
$$ LANGUAGE plpgsql;

-- Create an index advisor function to help identify slow queries
CREATE OR REPLACE FUNCTION analyze_slow_queries()
RETURNS TABLE(query TEXT, duration NUMERIC, calls INTEGER, suggested_index TEXT) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        query,
        total_time/calls as avg_duration,
        calls,
        'Consider index on ' || 
        CASE 
            WHEN query LIKE '%WHERE%address%' THEN 'address'
            WHEN query LIKE '%WHERE%spent%' THEN 'spent'
            WHEN query LIKE '%GROUP BY%' THEN 'columns in GROUP BY'
            ELSE 'relevant columns'
        END as suggestion
    FROM pg_stat_statements
    WHERE total_time > 1000 -- queries taking more than 1 second
    AND query NOT LIKE '%pg_stat_statements%'
    ORDER BY avg_duration DESC
    LIMIT 10;
END;
$$ LANGUAGE plpgsql;

-- Configure PostgreSQL for better query performance
ALTER SYSTEM SET work_mem = '128MB';
ALTER SYSTEM SET maintenance_work_mem = '256MB';
ALTER SYSTEM SET effective_cache_size = '4GB'; 
ALTER SYSTEM SET random_page_cost = 1.1;
ALTER SYSTEM SET effective_io_concurrency = 200;

-- Create cron job to regularly refresh the materialized view
CREATE EXTENSION IF NOT EXISTS pg_cron;

SELECT cron.schedule('*/5 * * * *', 'SELECT refresh_balances_now()');

-- Add an automatic query optimization function
CREATE OR REPLACE FUNCTION auto_optimize_query() RETURNS event_trigger AS $$
BEGIN
    -- Log slow query patterns
    IF current_query() LIKE '%SELECT%FROM utxos%WHERE spent = FALSE%GROUP BY address%' THEN
        RAISE NOTICE 'Slow balance query detected - consider using address_balances materialized view';
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE EVENT TRIGGER slow_query_detector ON sql_drop
    EXECUTE PROCEDURE auto_optimize_query();

-- Reload configuration to apply settings
SELECT pg_reload_conf();
