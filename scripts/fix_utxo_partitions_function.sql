-- scripts/fix_utxo_partitions_function.sql
-- Drop the existing function if it exists
DROP FUNCTION IF EXISTS create_utxo_partitions() CASCADE;

-- Create a function that can be called directly
CREATE OR REPLACE FUNCTION create_utxo_partitions()
RETURNS VOID AS $$
BEGIN
    PERFORM maintain_utxo_partitions();
    RETURN;
END;
$$ LANGUAGE plpgsql;
