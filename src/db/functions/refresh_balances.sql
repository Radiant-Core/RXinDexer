-- /Users/radiant/Desktop/RXinDexer/src/db/functions/refresh_balances.sql
-- This file implements the refresh_balances_now() function for the RXinDexer application
-- Purpose: Recalculates and updates wallet holder balances from UTXOs in a performance-optimized way
-- This function does NOT handle token metadata or other enriched data processing

CREATE OR REPLACE FUNCTION refresh_balances_now() 
RETURNS VOID AS $$
DECLARE
    refresh_start TIMESTAMP;
    processed INT;
BEGIN
    refresh_start := NOW();
    
    -- Create temporary table for aggregating balances
    CREATE TEMP TABLE IF NOT EXISTS temp_balances (
        address TEXT PRIMARY KEY,
        rxd_balance DECIMAL(20,8) DEFAULT 0,
        token_balances JSONB DEFAULT '{}'::JSONB
    ) ON COMMIT DROP;
    
    -- Clear temp table if it exists
    TRUNCATE temp_balances;
    
    -- Aggregate RXD balances from unspent UTXOs
    INSERT INTO temp_balances (address, rxd_balance)
    SELECT 
        address, 
        SUM(amount) as rxd_balance
    FROM 
        utxos
    WHERE 
        spent = FALSE AND token_ref IS NULL
    GROUP BY 
        address
    ON CONFLICT (address) DO UPDATE
    SET rxd_balance = EXCLUDED.rxd_balance;
    
    -- Get processed count
    GET DIAGNOSTICS processed = ROW_COUNT;
    
    -- Aggregate token balances from unspent UTXOs with token references
    WITH token_aggregates AS (
        SELECT 
            address,
            token_ref,
            SUM(amount) as token_amount
        FROM 
            utxos
        WHERE 
            spent = FALSE AND token_ref IS NOT NULL
        GROUP BY 
            address, token_ref
    )
    UPDATE temp_balances tb
    SET token_balances = COALESCE(tb.token_balances, '{}'::JSONB) || 
        jsonb_object_agg(ta.token_ref, ta.token_amount::TEXT)::JSONB
    FROM (
        SELECT 
            address,
            jsonb_object_agg(token_ref, token_amount::TEXT) as token_balances
        FROM 
            token_aggregates
        GROUP BY 
            address
    ) ta
    WHERE tb.address = ta.address;
    
    -- Insert addresses that only have token balances
    INSERT INTO temp_balances (address, token_balances)
    SELECT 
        ta.address,
        jsonb_object_agg(ta.token_ref, ta.token_amount::TEXT)::JSONB
    FROM 
        token_aggregates ta
    WHERE 
        NOT EXISTS (
            SELECT 1 FROM temp_balances tb WHERE tb.address = ta.address
        )
    GROUP BY 
        ta.address;
    
    -- Update holders table with fresh balances
    -- First, update existing holders
    UPDATE holders h
    SET 
        rxd_balance = COALESCE(tb.rxd_balance, 0),
        token_balances = COALESCE(tb.token_balances, '{}'::JSONB),
        last_updated_at = NOW()
    FROM 
        temp_balances tb
    WHERE 
        h.address = tb.address;
    
    -- Then, insert new holders
    INSERT INTO holders (address, rxd_balance, token_balances, last_updated_at)
    SELECT 
        tb.address,
        COALESCE(tb.rxd_balance, 0),
        COALESCE(tb.token_balances, '{}'::JSONB),
        NOW()
    FROM 
        temp_balances tb
    WHERE 
        NOT EXISTS (
            SELECT 1 FROM holders h WHERE h.address = tb.address
        );
    
    -- Log completion
    RAISE NOTICE 'Balance refresh completed in % seconds, % addresses processed',
        EXTRACT(EPOCH FROM (NOW() - refresh_start)),
        processed;
        
    -- Clean up
    DROP TABLE IF EXISTS temp_balances;
END;
$$ LANGUAGE plpgsql;

-- Create an index to speed up the function if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 
        FROM pg_indexes 
        WHERE tablename = 'utxos' AND indexname = 'idx_utxos_address_spent_token'
    ) THEN
        CREATE INDEX idx_utxos_address_spent_token ON utxos(address, spent, token_ref);
    END IF;
END
$$;
