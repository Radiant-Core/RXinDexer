-- /Users/radiant/Desktop/RXinDexer/db/optimization/performance.sql
-- This file contains performance-related database optimizations for RXinDexer
-- It should be applied after the initial database schema is created

-- ============================================
-- 1. CORE PERFORMANCE OPTIMIZATIONS
-- ============================================

-- Create optimized indices for common query patterns
CREATE INDEX IF NOT EXISTS idx_utxos_address_spent ON utxos (address) WHERE spent = FALSE;
CREATE INDEX IF NOT EXISTS idx_utxos_block_height ON utxos (block_height);
CREATE INDEX IF NOT EXISTS idx_blocks_height ON blocks (height);
CREATE INDEX IF NOT EXISTS idx_transactions_block_height ON transactions (block_height);
CREATE INDEX IF NOT EXISTS idx_glyph_tokens_type ON glyph_tokens (type);

-- Create partial indices for more specific queries
CREATE INDEX IF NOT EXISTS idx_large_utxos ON utxos (address, amount) 
WHERE amount > 1000;

-- Optimize the materialized view refresh function
CREATE OR REPLACE FUNCTION refresh_balances_now() RETURNS TIMESTAMP AS $$
DECLARE
    start_time TIMESTAMP WITH TIME ZONE := clock_timestamp();
BEGIN
    -- Drop and recreate the materialized view with updated data
    DROP MATERIALIZED VIEW IF EXISTS address_balances CASCADE;
    
    -- Create materialized view with optimized query
    CREATE MATERIALIZED VIEW address_balances AS
    SELECT 
        address,
        COALESCE(SUM(amount), 0) AS total_balance,
        COUNT(*) AS utxo_count
    FROM 
        utxos
    WHERE 
        spent = FALSE
    GROUP BY 
        address;
    
    -- Create index on the materialized view
    CREATE UNIQUE INDEX idx_address_balances_address ON address_balances (address);
    
    -- Log the refresh
    RAISE NOTICE 'Refreshed address_balances in % ms', 
                 EXTRACT(MILLISECONDS FROM clock_timestamp() - start_time);
    
    RETURN clock_timestamp();
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- 2. QUERY OPTIMIZATION HELPERS
-- ============================================

-- Optimized function to get address balance
CREATE OR REPLACE FUNCTION get_address_balance(p_address VARCHAR(64)) 
RETURNS TABLE (
    address VARCHAR(64),
    balance NUMERIC(20, 8),
    utxo_count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        ab.address,
        ab.total_balance AS balance,
        ab.utxo_count
    FROM 
        address_balances ab
    WHERE 
        ab.address = p_address;
    
    -- If not found in materialized view (shouldn't happen), fall back to direct query
    IF NOT FOUND THEN
        RETURN QUERY
        SELECT 
            u.address,
            COALESCE(SUM(u.amount), 0) AS balance,
            COUNT(*) AS utxo_count
        FROM 
            utxos u
        WHERE 
            u.address = p_address
            AND u.spent = FALSE
        GROUP BY 
            u.address;
    END IF;
END;
$$ LANGUAGE plpgsql STABLE;
