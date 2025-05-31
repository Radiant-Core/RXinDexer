# /Users/radiant/Desktop/RXinDexer/improved_db_optimization.sql
# This file implements improved database optimizations for better query performance
# It creates views and functions to replace slow balance queries with optimized versions

-- Refresh the materialized view to ensure fresh data
SELECT refresh_balances_now();

-- Create a view that other queries can use for balance lookups
CREATE OR REPLACE VIEW address_balance_lookup AS
SELECT 
    address,
    total_balance AS balance,
    total_balance AS amount, 
    utxo_count
FROM address_balances;

-- Create a function to return all balances above a threshold
CREATE OR REPLACE FUNCTION get_large_balances(threshold NUMERIC DEFAULT 1000000000)
RETURNS TABLE(address VARCHAR, total_balance NUMERIC) AS $$
BEGIN
    RETURN QUERY
    SELECT ab.address, ab.total_balance
    FROM address_balances ab
    WHERE ab.total_balance > threshold;
END;
$$ LANGUAGE plpgsql;

-- Create a function to create a temp balances table using the materialized view
CREATE OR REPLACE FUNCTION create_temp_balances()
RETURNS VOID AS $$
BEGIN
    DROP TABLE IF EXISTS temp_balances;
    
    CREATE TEMP TABLE temp_balances AS
    SELECT address, total_balance AS balance
    FROM address_balances;
END;
$$ LANGUAGE plpgsql;

-- Create an optimized function for querying address balance
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

-- Create optimized indexes for the tables we query frequently
CREATE INDEX IF NOT EXISTS idx_utxos_address_amount ON utxos(address, amount) WHERE spent = FALSE;
CREATE INDEX IF NOT EXISTS idx_utxos_spent_txid ON utxos(spent_txid) WHERE spent = TRUE;
CREATE INDEX IF NOT EXISTS idx_holders_balance ON holders(rxd_balance DESC);

-- Set up automatic refresh of the materialized view
CREATE OR REPLACE FUNCTION schedule_materialized_view_refresh()
RETURNS VOID AS $$
BEGIN
    -- Refresh the materialized view if it's more than 5 minutes old
    IF (SELECT extract(epoch from now()) - COALESCE(
        (SELECT extract(epoch from last_refresh) FROM refresh_tracking 
            WHERE view_name = 'address_balances'), 0)) > 300 THEN
        PERFORM refresh_balances_now();
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Create a function to check if we need to do a regular balance refresh
CREATE OR REPLACE FUNCTION should_refresh_balance_view()
RETURNS BOOLEAN AS $$
DECLARE
    last_refresh_time TIMESTAMP WITH TIME ZONE;
    current_time TIMESTAMP WITH TIME ZONE := NOW();
    txn_count INTEGER;
BEGIN
    -- Get the last refresh time
    SELECT last_refresh INTO last_refresh_time
    FROM refresh_tracking
    WHERE view_name = 'address_balances';
    
    -- Get the transaction count since last refresh
    SELECT COUNT(*) INTO txn_count
    FROM utxos
    WHERE updated_at > last_refresh_time;
    
    -- Return true if more than 5 minutes passed or more than 1000 transactions
    RETURN (last_refresh_time IS NULL OR 
           EXTRACT(EPOCH FROM (current_time - last_refresh_time)) > 300 OR
           txn_count > 1000);
END;
$$ LANGUAGE plpgsql;

-- Configure PostgreSQL for better query performance
ALTER SYSTEM SET work_mem = '128MB';
ALTER SYSTEM SET maintenance_work_mem = '256MB';
ALTER SYSTEM SET effective_cache_size = '4GB'; 
ALTER SYSTEM SET random_page_cost = 1.1;
ALTER SYSTEM SET effective_io_concurrency = 200;
ALTER SYSTEM SET jit = on;

-- Add monitoring for slow queries
CREATE OR REPLACE FUNCTION log_slow_query() 
RETURNS TRIGGER AS $$
BEGIN
    IF TG_TABLE_NAME = 'utxos' AND 
       (current_query() LIKE '%SELECT%FROM utxos%WHERE spent = FALSE%GROUP BY address%' OR
        current_query() LIKE '%CREATE TEMPORARY TABLE temp_balances%') THEN
        
        RAISE NOTICE 'Slow query pattern detected - consider using address_balances view';
        
        -- Log to PostgreSQL log
        RAISE LOG 'Slow query pattern: %', current_query();
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Add a trigger to intercept slow queries
CREATE TRIGGER detect_slow_queries
AFTER INSERT OR UPDATE ON utxos
FOR EACH STATEMENT
EXECUTE FUNCTION log_slow_query();

-- Add utility function to refresh view on demand
CREATE OR REPLACE FUNCTION manual_refresh()
RETURNS VOID AS $$
BEGIN
    PERFORM refresh_balances_now();
    RAISE NOTICE 'Materialized view refreshed successfully';
END;
$$ LANGUAGE plpgsql;

-- Create an efficient batch update function for UTXO changes
CREATE OR REPLACE FUNCTION batch_update_utxos(
    spent_txids TEXT[],
    spent_vouts INTEGER[],
    spent_by_txids TEXT[]
) RETURNS INTEGER AS $$
DECLARE
    updated_count INTEGER;
BEGIN
    UPDATE utxos u SET 
        spent = TRUE, 
        spent_txid = v.spent_txid,
        updated_at = NOW()
    FROM (
        SELECT 
            unnest(spent_txids) as txid,
            unnest(spent_vouts) as vout,
            unnest(spent_by_txids) as spent_txid
    ) as v
    WHERE u.txid = v.txid AND u.vout = v.vout;
    
    GET DIAGNOSTICS updated_count = ROW_COUNT;
    
    -- Auto-refresh if many UTXOs were changed
    IF updated_count > 1000 THEN
        PERFORM refresh_balances_now();
    END IF;
    
    RETURN updated_count;
END;
$$ LANGUAGE plpgsql;

-- Reload configuration to apply settings
SELECT pg_reload_conf();
