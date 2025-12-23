-- Fast Backfill Script
-- 1. Disable triggers to speed up massive update
ALTER TABLE utxos_initial DISABLE TRIGGER ALL;

-- 2. Perform massive update using JOIN
-- This updates all UTXOs that are referenced by an input
-- It sets spent=true and populates the spent_in_txid in one go.
RAISE NOTICE 'Starting massive update...';
UPDATE utxos_initial u
SET spent = true,
    spent_in_txid = t.txid
FROM transaction_inputs i
JOIN transactions t ON t.id = i.transaction_id
WHERE u.txid = i.spent_txid 
  AND u.vout = i.spent_vout
  AND u.spent = false;

-- 3. Re-enable triggers
ALTER TABLE utxos_initial ENABLE TRIGGER ALL;

RAISE NOTICE 'Massive update complete.';
