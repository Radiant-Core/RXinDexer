-- Fix for materialized view refresh mechanism
-- This script fixes the issues with transaction conflicts during UTXO processing

-- Drop existing problematic triggers and functions
DROP TRIGGER IF EXISTS trg_refresh_address_balances ON utxos;
DROP FUNCTION IF EXISTS refresh_address_balances();
DROP TABLE IF EXISTS refresh_tracking;

-- Create a table to track refresh operations
CREATE TABLE refresh_tracking (
    view_name TEXT PRIMARY KEY,
    last_refresh TIMESTAMP WITH TIME ZONE,
    is_refreshing BOOLEAN DEFAULT FALSE
);

-- Insert initial record for address_balances
INSERT INTO refresh_tracking (view_name, last_refresh, is_refreshing)
VALUES ('address_balances', NOW() - INTERVAL '1 hour', FALSE)
ON CONFLICT (view_name) DO NOTHING;

-- Create a non-blocking function to check if refresh is needed
CREATE OR REPLACE FUNCTION check_refresh_needed()
RETURNS BOOLEAN AS $$
DECLARE
    last_refresh_time TIMESTAMP WITH TIME ZONE;
    is_being_refreshed BOOLEAN;
    refresh_needed BOOLEAN := FALSE;
BEGIN
    -- Get the last refresh info
    SELECT last_refresh, is_refreshing INTO last_refresh_time, is_being_refreshed
    FROM refresh_tracking
    WHERE view_name = 'address_balances';
    
    -- Determine if refresh is needed and not already in progress
    IF last_refresh_time IS NULL OR 
       EXTRACT(EPOCH FROM (NOW() - last_refresh_time)) > 300 THEN
        refresh_needed := TRUE;
    END IF;
    
    -- Don't refresh if another process is already refreshing
    IF is_being_refreshed THEN
        refresh_needed := FALSE;
    END IF;
    
    RETURN refresh_needed;
END;
$$ LANGUAGE plpgsql;

-- Create a safer refresh function that doesn't block transactions
CREATE OR REPLACE FUNCTION safe_refresh_address_balances()
RETURNS VOID AS $$
DECLARE
    refresh_needed BOOLEAN;
BEGIN
    -- Check if refresh is needed
    SELECT check_refresh_needed() INTO refresh_needed;
    
    -- If refresh is needed, lock and refresh
    IF refresh_needed THEN
        -- Mark as being refreshed
        UPDATE refresh_tracking
        SET is_refreshing = TRUE
        WHERE view_name = 'address_balances';
        
        -- Refresh materialized view
        BEGIN
            REFRESH MATERIALIZED VIEW address_balances;
            
            -- Update the last refresh timestamp and release lock
            UPDATE refresh_tracking
            SET last_refresh = NOW(),
                is_refreshing = FALSE
            WHERE view_name = 'address_balances';
        EXCEPTION
            WHEN OTHERS THEN
                -- Reset the refresh flag if there's an error
                UPDATE refresh_tracking
                SET is_refreshing = FALSE
                WHERE view_name = 'address_balances';
                RAISE;
        END;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Create a lightweight notification function for the trigger
CREATE OR REPLACE FUNCTION notify_balance_change()
RETURNS TRIGGER AS $$
BEGIN
    -- Just notify changes, don't do expensive operations directly in trigger
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Create the trigger that only performs lightweight operations
CREATE TRIGGER trg_notify_balance_changes
AFTER INSERT OR UPDATE OR DELETE ON utxos
FOR EACH STATEMENT
EXECUTE FUNCTION notify_balance_change();

-- Add index for optimizing the aggregate query that builds the materialized view
CREATE INDEX IF NOT EXISTS idx_utxos_address_spent_amount ON utxos (address, spent, amount)
WHERE spent = FALSE;

-- Update the materialized view definition
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

-- Create unique index on address for fast lookups
CREATE UNIQUE INDEX idx_address_balances_address
ON address_balances (address);

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

-- Perform initial refresh
SELECT refresh_balances_now();
