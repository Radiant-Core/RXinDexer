-- /Users/radiant/Desktop/RXinDexer/safe_optimization.sql
-- This script applies database optimizations safely without terminating important connections
-- It focuses on fixing the core issues causing high resource usage

-- 1. REFRESH THE MATERIALIZED VIEW FOR LATEST DATA
SELECT refresh_balances_now();

-- 2. CREATE EFFICIENT FUNCTIONS TO REPLACE SLOW QUERIES
-- Create an efficient function for temporary balance table creation
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

-- Create a direct function to get large balances efficiently
CREATE OR REPLACE FUNCTION get_large_balances(threshold NUMERIC DEFAULT 1000000000)
RETURNS TABLE(address VARCHAR, total_balance NUMERIC) AS $$
BEGIN
    RETURN QUERY
    SELECT ab.address, ab.total_balance
    FROM address_balances ab
    WHERE ab.total_balance > threshold;
END;
$$ LANGUAGE plpgsql;

-- 3. IMPROVE INDEXING FOR BETTER PERFORMANCE
-- Create targeted indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_utxos_address_spent ON utxos (address) WHERE spent = FALSE;
CREATE INDEX IF NOT EXISTS idx_utxos_block_height ON utxos (block_height);
CREATE INDEX IF NOT EXISTS idx_utxos_large_balances ON utxos (address, amount) 
    WHERE amount > 1000000 AND spent = FALSE;
CREATE INDEX IF NOT EXISTS idx_utxos_token_ref ON utxos (token_ref) 
    WHERE token_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_holders_address ON holders (address);
CREATE INDEX IF NOT EXISTS idx_holders_balance ON holders (rxd_balance DESC);

-- 4. OPTIMIZE POSTGRESQL CONFIGURATION
-- Tune PostgreSQL settings for better performance with balance calculation queries
ALTER SYSTEM SET work_mem = '64MB';                    -- For sorting and hash operations
ALTER SYSTEM SET maintenance_work_mem = '256MB';       -- For maintenance tasks
ALTER SYSTEM SET max_parallel_workers_per_gather = '2'; -- Parallel query execution
ALTER SYSTEM SET max_parallel_workers = '4';           -- Total parallel workers
ALTER SYSTEM SET effective_cache_size = '2GB';         -- Cache size estimation
ALTER SYSTEM SET random_page_cost = '1.1';             -- SSD optimization
ALTER SYSTEM SET log_min_duration_statement = '1000';  -- Log slow queries
ALTER SYSTEM SET default_statistics_target = '100';    -- Statistics collection detail
ALTER SYSTEM SET autovacuum_vacuum_scale_factor = '0.1'; -- More frequent vacuuming
ALTER SYSTEM SET autovacuum_analyze_scale_factor = '0.05'; -- More frequent analysis

-- 5. CREATE OPTIMIZED VERSION OF THE SLOW QUERY
-- This view provides the same data as the slow query but uses the materialized view
CREATE OR REPLACE VIEW optimized_balance_view AS
SELECT address, total_balance AS balance, utxo_count
FROM address_balances;

-- 6. CREATE EFFICIENT BATCH UPDATE FUNCTION FOR UTXOS
-- This reduces transaction overhead during bulk operations
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

-- 7. CREATE AUTOMATIC DATABASE MAINTENANCE FUNCTION
-- This should be run periodically from a scheduled job
CREATE OR REPLACE FUNCTION perform_safe_maintenance() RETURNS TEXT AS $$
DECLARE
    maintenance_log TEXT := '';
BEGIN
    -- Refresh materialized views
    PERFORM refresh_balances_now();
    maintenance_log := maintenance_log || 'Refreshed materialized views. ';
    
    -- Update table statistics
    ANALYZE utxos;
    maintenance_log := maintenance_log || 'Updated UTXO table statistics. ';
    
    ANALYZE holders;
    maintenance_log := maintenance_log || 'Updated holder table statistics. ';
    
    -- Return maintenance report
    RETURN maintenance_log || 'Maintenance completed successfully.';
END;
$$ LANGUAGE plpgsql;

-- Run the maintenance function to update statistics
SELECT perform_safe_maintenance();

-- Reload the PostgreSQL configuration
SELECT pg_reload_conf();
