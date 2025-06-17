-- Create refresh tracking table
CREATE TABLE IF NOT EXISTS refresh_tracking (
    view_name TEXT PRIMARY KEY,
    last_refresh TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    refresh_interval INTERVAL DEFAULT '1 hour'
);

-- Insert default entry for address_balances if it doesn't exist
INSERT INTO refresh_tracking (view_name, last_refresh, refresh_interval)
VALUES ('address_balances', NOW(), '1 hour')
ON CONFLICT (view_name) DO NOTHING;

-- Create or replace safe refresh function
CREATE OR REPLACE FUNCTION safe_refresh_address_balances()
RETURNS BOOLEAN AS $$
DECLARE
    last_time TIMESTAMP WITH TIME ZONE;
    refresh_needed BOOLEAN;
    refresh_interval INTERVAL;
BEGIN
    -- Get last refresh time and interval
    SELECT 
        last_refresh, 
        NOW() - last_refresh > refresh_interval,
        refresh_interval
    INTO last_time, refresh_needed, refresh_interval
    FROM refresh_tracking
    WHERE view_name = 'address_balances';
    
    -- Skip refresh if not needed
    IF NOT refresh_needed THEN
        RETURN FALSE;
    END IF;
    
    -- Perform the refresh
    REFRESH MATERIALIZED VIEW address_balances;
    
    -- Update the last refresh time
    UPDATE refresh_tracking
    SET last_refresh = NOW()
    WHERE view_name = 'address_balances';
    
    RETURN TRUE;
EXCEPTION
    WHEN OTHERS THEN
        -- Handle errors gracefully
        RETURN FALSE;
END;
$$ LANGUAGE plpgsql;

-- Create address_balances materialized view
DROP MATERIALIZED VIEW IF EXISTS address_balances;

CREATE MATERIALIZED VIEW address_balances AS
SELECT 
    address,
    SUM(amount) as total_balance,
    COUNT(*) as utxo_count
FROM utxos
WHERE spent = FALSE
GROUP BY address
WITH DATA;

CREATE UNIQUE INDEX idx_address_balances_address
ON address_balances (address);

-- Schedule periodic refresh
CREATE OR REPLACE FUNCTION schedule_balance_refreshes()
RETURNS VOID AS $$
BEGIN
    PERFORM safe_refresh_address_balances();
END;
$$ LANGUAGE plpgsql;

-- Add a utility function to manually refresh when needed
CREATE OR REPLACE FUNCTION refresh_balances_now()
RETURNS VOID AS $$
BEGIN
    UPDATE refresh_tracking
    SET last_refresh = NOW() - INTERVAL '1 hour'
    WHERE view_name = 'address_balances';
    
    PERFORM safe_refresh_address_balances();
END;
$$ LANGUAGE plpgsql;

-- Do initial entry for address_balances
INSERT INTO refresh_tracking (view_name, last_refresh, refresh_interval)
VALUES ('address_balances', NOW() - INTERVAL '2 hour', '1 hour')
ON CONFLICT (view_name) DO UPDATE
SET last_refresh = NOW() - INTERVAL '2 hour';

-- Do initial refresh
SELECT refresh_balances_now();
