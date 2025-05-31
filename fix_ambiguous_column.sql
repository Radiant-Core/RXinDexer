-- /Users/radiant/Desktop/RXinDexer/fix_ambiguous_column.sql
-- This script fixes the ambiguous column reference error in our materialized view refresh trigger

-- Fix the trigger function with the ambiguous column reference
CREATE OR REPLACE FUNCTION refresh_materialized_view_on_utxo_change()
RETURNS TRIGGER AS $$
DECLARE
    last_refresh_time TIMESTAMP WITH TIME ZONE;
BEGIN
    -- Get the last refresh time with an unambiguous variable name
    SELECT refresh_tracking.last_refresh INTO last_refresh_time
    FROM refresh_tracking
    WHERE view_name = 'address_balances';
    
    -- Only refresh if it's been more than 5 minutes or NULL
    IF last_refresh_time IS NULL OR last_refresh_time < NOW() - INTERVAL '5 minutes' THEN
        -- Refresh the materialized view
        PERFORM refresh_balances_now();
        
        RAISE NOTICE 'Refreshed address_balances materialized view due to UTXO changes';
    END IF;
    
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Re-create the trigger
DROP TRIGGER IF EXISTS trg_refresh_on_utxo_change ON utxos;
CREATE TRIGGER trg_refresh_on_utxo_change
AFTER INSERT OR UPDATE OR DELETE ON utxos
FOR EACH STATEMENT
EXECUTE FUNCTION refresh_materialized_view_on_utxo_change();

-- Fix any other functions that might have the same issue
CREATE OR REPLACE FUNCTION refresh_balances_now() RETURNS VOID AS $$
DECLARE
    start_time TIMESTAMP := clock_timestamp();
    last_refresh_time TIMESTAMP WITH TIME ZONE;
BEGIN
    -- Check when the view was last refreshed with unambiguous variable name
    SELECT refresh_tracking.last_refresh INTO last_refresh_time
    FROM refresh_tracking
    WHERE view_name = 'address_balances';
    
    -- Only refresh if it's been more than 1 minute since the last refresh
    IF last_refresh_time IS NULL OR last_refresh_time < NOW() - INTERVAL '1 minute' THEN
        -- Refresh the materialized view
        REFRESH MATERIALIZED VIEW address_balances;
        
        -- Update the last refresh time
        UPDATE refresh_tracking
        SET last_refresh = NOW()
        WHERE view_name = 'address_balances';
        
        -- Insert if not exists
        IF NOT FOUND THEN
            INSERT INTO refresh_tracking (view_name, last_refresh)
            VALUES ('address_balances', NOW());
        END IF;
        
        RAISE NOTICE 'Refreshed address_balances in % ms', 
                     extract(millisecond from clock_timestamp() - start_time);
    ELSE
        RAISE NOTICE 'Skipped refresh of address_balances (last refreshed % ago)', 
                     NOW() - last_refresh_time;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Ensure the refresh_tracking table exists
CREATE TABLE IF NOT EXISTS refresh_tracking (
    view_name VARCHAR PRIMARY KEY,
    last_refresh TIMESTAMP WITH TIME ZONE NOT NULL
);

-- Add an initial record if needed
INSERT INTO refresh_tracking (view_name, last_refresh)
VALUES ('address_balances', NOW())
ON CONFLICT (view_name) DO NOTHING;

-- Update other functions that might have the same issue
CREATE OR REPLACE FUNCTION update_holder_balances_efficient() RETURNS INTEGER AS $$
DECLARE
    updated_count INTEGER := 0;
    refresh_result BOOLEAN;
BEGIN
    -- First refresh the materialized view
    BEGIN
        PERFORM refresh_balances_now();
        refresh_result := TRUE;
    EXCEPTION WHEN OTHERS THEN
        -- If refresh fails, log but continue
        RAISE NOTICE 'Warning: Failed to refresh materialized view - %', SQLERRM;
        refresh_result := FALSE;
    END;
    
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
