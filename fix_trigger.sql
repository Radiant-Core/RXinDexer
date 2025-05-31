-- Drop existing trigger and function
DROP TRIGGER IF EXISTS trg_refresh_address_balances ON utxos;
DROP FUNCTION IF EXISTS refresh_address_balances();

-- Create a table to track the last refresh time
CREATE TABLE IF NOT EXISTS refresh_tracking (
    view_name TEXT PRIMARY KEY,
    last_refresh TIMESTAMP WITH TIME ZONE
);

-- Insert initial record if not exists
INSERT INTO refresh_tracking (view_name, last_refresh)
VALUES ('address_balances', NOW() - INTERVAL '1 hour')
ON CONFLICT (view_name) DO NOTHING;

-- Create improved refresh function
CREATE OR REPLACE FUNCTION refresh_address_balances()
RETURNS TRIGGER AS $$
DECLARE
    last_refresh_time TIMESTAMP WITH TIME ZONE;
    current_time TIMESTAMP WITH TIME ZONE := NOW();
BEGIN
    -- Get the last refresh time
    SELECT last_refresh INTO last_refresh_time
    FROM refresh_tracking
    WHERE view_name = 'address_balances';
    
    -- Only refresh if more than 60 seconds have passed
    IF last_refresh_time IS NULL OR 
       EXTRACT(EPOCH FROM (current_time - last_refresh_time)) > 60 THEN
        -- Refresh the materialized view
        REFRESH MATERIALIZED VIEW address_balances;
        
        -- Update the last refresh timestamp
        UPDATE refresh_tracking
        SET last_refresh = current_time
        WHERE view_name = 'address_balances';
    END IF;
    
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Create the trigger
CREATE TRIGGER trg_refresh_address_balances
AFTER INSERT OR UPDATE OR DELETE ON utxos
FOR EACH STATEMENT
EXECUTE FUNCTION refresh_address_balances();
