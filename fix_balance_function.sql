-- /Users/radiant/Desktop/RXinDexer/fix_balance_function.sql
-- This SQL file fixes the refresh_balances_now() function that was causing the database maintenance container to be unhealthy

DROP FUNCTION IF EXISTS refresh_balances_now();

CREATE OR REPLACE FUNCTION refresh_balances_now() 
RETURNS void AS $$
DECLARE
    refresh_start TIMESTAMP;
    processed INTEGER;
BEGIN
    -- Record start time
    refresh_start := NOW();
    
    -- Create temp table for aggregation
    CREATE TEMP TABLE IF NOT EXISTS temp_balances (
        address TEXT PRIMARY KEY,
        rxd_balance NUMERIC DEFAULT 0,
        token_balances JSONB DEFAULT '{}'::JSONB
    );
    
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
    
    -- Modified: Fix for token_ref issue by using subquery and proper aliases
    WITH token_aggregates AS (
        SELECT 
            u.address,
            u.token_ref,
            SUM(u.amount) as token_amount
        FROM 
            utxos u
        WHERE 
            u.spent = FALSE AND u.token_ref IS NOT NULL
        GROUP BY 
            u.address, u.token_ref
    )
    UPDATE temp_balances tb
    SET token_balances = COALESCE(tb.token_balances, '{}'::JSONB) || 
        ta.token_balances
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
        ta.token_balances
    FROM (
        SELECT 
            address,
            jsonb_object_agg(token_ref, token_amount::TEXT) as token_balances
        FROM (
            SELECT 
                u.address,
                u.token_ref,
                SUM(u.amount) as token_amount
            FROM 
                utxos u
            WHERE 
                u.spent = FALSE AND u.token_ref IS NOT NULL
            GROUP BY 
                u.address, u.token_ref
        ) t
        GROUP BY 
            address
    ) ta
    WHERE 
        NOT EXISTS (
            SELECT 1 FROM temp_balances tb WHERE tb.address = ta.address
        );
    
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
