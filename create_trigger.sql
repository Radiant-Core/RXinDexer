-- Create refresh function for the materialized view
CREATE OR REPLACE FUNCTION refresh_address_balances()
RETURNS TRIGGER AS $$
BEGIN
    -- Only refresh periodically to avoid constant updates
    IF (SELECT extract(epoch from now()) - COALESCE(
        (SELECT extract(epoch from last_refresh) FROM pg_stat_user_tables 
            WHERE relname = 'address_balances'), 0)) > 60 THEN
        REFRESH MATERIALIZED VIEW address_balances;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Drop existing trigger if it exists
DROP TRIGGER IF EXISTS trg_refresh_address_balances ON utxos;

-- Create the new trigger
CREATE TRIGGER trg_refresh_address_balances
AFTER INSERT OR UPDATE OR DELETE ON utxos
FOR EACH STATEMENT
EXECUTE FUNCTION refresh_address_balances();
