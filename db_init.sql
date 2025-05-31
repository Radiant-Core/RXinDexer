-- /Users/radiant/Desktop/RXinDexer/db_init.sql
-- This script initializes the RXinDexer database schema with optimized tables and indexes

-- Core blockchain tables
CREATE TABLE IF NOT EXISTS blocks (
    hash VARCHAR(64) PRIMARY KEY,
    height INTEGER NOT NULL,
    version INTEGER NOT NULL,
    prev_hash VARCHAR(64),
    merkle_root VARCHAR(64) NOT NULL,
    timestamp INTEGER NOT NULL,
    bits INTEGER NOT NULL,
    nonce INTEGER NOT NULL,
    chainwork VARCHAR(64),
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS transactions (
    txid VARCHAR(64) PRIMARY KEY,
    version INTEGER NOT NULL,
    block_hash VARCHAR(64) NOT NULL,
    block_height INTEGER NOT NULL,
    locktime INTEGER NOT NULL,
    size INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now(),
    FOREIGN KEY (block_hash) REFERENCES blocks(hash)
);

CREATE TABLE IF NOT EXISTS utxos (
    txid VARCHAR(64) NOT NULL,
    vout INTEGER NOT NULL,
    address VARCHAR(64) NOT NULL,
    amount NUMERIC(16,8) NOT NULL,
    token_ref VARCHAR(64),
    spent BOOLEAN DEFAULT false,
    spent_txid VARCHAR(64),
    block_height INTEGER NOT NULL,
    block_hash VARCHAR(64) NOT NULL,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now(),
    PRIMARY KEY (txid, vout)
);

CREATE TABLE IF NOT EXISTS glyph_tokens (
    ref VARCHAR(64) PRIMARY KEY,
    type VARCHAR(20) NOT NULL,
    token_metadata JSON,
    current_txid VARCHAR(64),
    current_vout INTEGER,
    genesis_txid VARCHAR(64) NOT NULL,
    genesis_block_height INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sync_state (
    id INTEGER PRIMARY KEY,
    current_height INTEGER NOT NULL,
    current_hash VARCHAR(64),
    current_chainwork VARCHAR(64),
    is_syncing INTEGER,
    last_error TEXT,
    last_updated_at DOUBLE PRECISION,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now(),
    glyph_scan_height INTEGER DEFAULT 0
);

-- Materialized view for address balances
CREATE MATERIALIZED VIEW address_balances AS
SELECT address, SUM(amount) AS total_balance
FROM utxos
WHERE spent = false
GROUP BY address;

-- Add indexes for performance
CREATE INDEX idx_blocks_height ON blocks(height);
CREATE INDEX idx_transactions_block_hash ON transactions(block_hash);
CREATE INDEX idx_transactions_block_height ON transactions(block_height);
CREATE INDEX idx_utxo_address_spent ON utxos(address, spent);
CREATE INDEX idx_utxo_token_ref_spent ON utxos(token_ref, spent);
CREATE INDEX ix_utxos_address ON utxos(address);
CREATE INDEX ix_utxos_block_height ON utxos(block_height);
CREATE INDEX ix_utxos_token_ref ON utxos(token_ref);
CREATE INDEX ix_glyph_tokens_genesis_block_height ON glyph_tokens(genesis_block_height);
CREATE INDEX ix_glyph_tokens_type ON glyph_tokens(type);
CREATE INDEX idx_address_balances_address ON address_balances(address);
CREATE INDEX idx_address_balances_balance ON address_balances(total_balance DESC);

-- Helper functions
CREATE OR REPLACE FUNCTION update_holder_balances_efficient() RETURNS VOID AS $$
BEGIN
    BEGIN
        REFRESH MATERIALIZED VIEW address_balances;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'Error refreshing address_balances: %', SQLERRM;
    END;
    RETURN;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION get_token_addresses() RETURNS TABLE(address VARCHAR, token_ref VARCHAR) AS $$
BEGIN
    RETURN QUERY
    SELECT u.address, u.token_ref
    FROM utxos u
    WHERE u.spent = false AND u.token_ref IS NOT NULL
    AND EXISTS (SELECT 1 FROM glyph_tokens g WHERE g.ref = u.token_ref);
END;
$$ LANGUAGE plpgsql;

-- Initialize sync state
INSERT INTO sync_state (id, current_height, current_hash, is_syncing, last_updated_at, created_at, updated_at, glyph_scan_height)
VALUES (1, 0, '', 0, EXTRACT(EPOCH FROM NOW()), NOW(), NOW(), 0);
