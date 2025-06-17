-- /Users/radiant/Desktop/RXinDexer/create_address_balances_view.sql
-- This script creates the address_balances materialized view 
-- which is required for proper blockchain synchronization

-- Drop view if it exists
DROP MATERIALIZED VIEW IF EXISTS address_balances;

-- Create the materialized view
CREATE MATERIALIZED VIEW address_balances AS
SELECT 
    address,
    SUM(amount) as total_balance,
    COUNT(*) as utxo_count
FROM utxos
WHERE spent = FALSE
GROUP BY address
WITH DATA;

-- Create unique index
CREATE UNIQUE INDEX idx_address_balances_address
ON address_balances (address);

-- Create refresh tracking entry if table exists
INSERT INTO refresh_tracking (view_name, last_refresh, is_refreshing)
SELECT 'address_balances', NOW(), FALSE
WHERE EXISTS (
    SELECT 1 FROM information_schema.tables 
    WHERE table_name = 'refresh_tracking'
);
