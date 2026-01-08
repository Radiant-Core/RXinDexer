-- ============================================================================
-- RXinDexer Database Schema - Reference Implementation Aligned
-- ============================================================================
-- This schema is aligned with the rxd-glyph-explorer reference implementation.
-- Features:
-- - Partitioned tables for blocks, transactions, and UTXOs
-- - Unified 'glyphs' table (primary token table)
-- - GlyphAction tracking for token history
-- - DMINT contract support
-- - Global statistics cache
-- - User engagement (likes)
-- ============================================================================

-- ============================================================================
-- CORE TABLES (Partitioned for scalability)
-- ============================================================================

-- Blocks table (partitioned by height)
CREATE TABLE IF NOT EXISTS blocks (
    id SERIAL,
    hash VARCHAR(64) NOT NULL,
    height INTEGER NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    reorg BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (id, height)
) PARTITION BY RANGE (height);

CREATE TABLE IF NOT EXISTS blocks_initial PARTITION OF blocks DEFAULT;

-- Transactions table (partitioned by block_height)
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL,
    txid VARCHAR(64) NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    locktime INTEGER NOT NULL DEFAULT 0,
    block_id INTEGER,
    block_height INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, block_height)
) PARTITION BY RANGE (block_height);

CREATE TABLE IF NOT EXISTS transactions_initial PARTITION OF transactions DEFAULT;

-- Transaction Inputs table
CREATE TABLE IF NOT EXISTS transaction_inputs (
    id SERIAL PRIMARY KEY,
    transaction_id INTEGER NOT NULL,
    input_index INTEGER NOT NULL,
    spent_txid VARCHAR(64),
    spent_vout INTEGER,
    script_sig TEXT,
    sequence BIGINT NOT NULL,
    coinbase TEXT
);

-- UTXOs table (partitioned, with glyph tracking fields)
CREATE TABLE IF NOT EXISTS utxos (
    id SERIAL,
    txid VARCHAR(64) NOT NULL,
    vout INTEGER NOT NULL,
    address VARCHAR(128),
    value BIGINT NOT NULL,  -- Satoshis (integer for precision)
    spent BOOLEAN DEFAULT FALSE,
    spent_in_txid VARCHAR(64),
    transaction_id INTEGER,
    transaction_block_height INTEGER NOT NULL,
    script_type VARCHAR(32),
    script_hex TEXT,
    -- New fields from reference
    date INTEGER,
    change BOOLEAN,
    is_glyph_reveal BOOLEAN DEFAULT FALSE,
    glyph_ref VARCHAR(72),
    contract_type VARCHAR(20),
    PRIMARY KEY (id, transaction_block_height)
) PARTITION BY RANGE (transaction_block_height);

CREATE TABLE IF NOT EXISTS utxos_initial PARTITION OF utxos DEFAULT;

-- ============================================================================
-- UNIFIED GLYPHS TABLE (Primary Token Table - Reference Aligned)
-- ============================================================================

CREATE TABLE IF NOT EXISTS glyphs (
    id SERIAL PRIMARY KEY,
    ref VARCHAR(72) NOT NULL UNIQUE,
    token_type VARCHAR(20) NOT NULL,
    p JSON,
    name VARCHAR(255) NOT NULL,
    ticker VARCHAR(50),
    type VARCHAR(100) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    immutable BOOLEAN,
    attrs JSON DEFAULT '{}',
    author VARCHAR NOT NULL DEFAULT '',
    container VARCHAR NOT NULL DEFAULT '',
    is_container BOOLEAN DEFAULT FALSE,
    container_items JSON,
    spent BOOLEAN NOT NULL DEFAULT FALSE,
    fresh BOOLEAN NOT NULL DEFAULT TRUE,
    melted BOOLEAN DEFAULT FALSE,
    sealed BOOLEAN DEFAULT FALSE,
    swap_pending BOOLEAN DEFAULT FALSE,
    value BIGINT,
    burned_supply BIGINT DEFAULT 0,
    location VARCHAR,
    reveal_outpoint VARCHAR,
    last_txo_id INTEGER,
    height INTEGER,
    timestamp INTEGER,
    embed_type VARCHAR(100),
    embed_data TEXT,
    remote_type VARCHAR(100),
    remote_url VARCHAR(500),
    remote_hash VARCHAR,
    remote_hash_sig VARCHAR,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    -- Additional fields for full compatibility (previously in legacy tables)
    owner VARCHAR,                      -- Current owner address
    max_supply BIGINT,                  -- Maximum token supply
    current_supply BIGINT,              -- Current minted supply
    circulating_supply BIGINT,          -- Circulating supply (minted - burned)
    genesis_height INTEGER,             -- Block height of token creation
    current_txid VARCHAR(64),           -- Current UTXO txid
    current_vout INTEGER,               -- Current UTXO vout
    holder_count INTEGER DEFAULT 0,     -- Cached holder count
    deploy_method VARCHAR(20),          -- Deployment method (direct, dmint, etc)
    -- DMINT contract fields
    difficulty INTEGER,
    max_height BIGINT,
    reward BIGINT,
    num_contracts INTEGER,
    -- Resolved author info (cached)
    author_name VARCHAR(255),
    author_ref_type VARCHAR(20)         -- Type of author ref (user, container, etc)
);

-- ============================================================================
-- GLYPH ACTIONS TABLE (Action Tracking)
-- ============================================================================

CREATE TABLE IF NOT EXISTS glyph_actions (
    id SERIAL PRIMARY KEY,
    ref VARCHAR(72) NOT NULL,
    type VARCHAR(30) NOT NULL,
    txid VARCHAR NOT NULL,
    height INTEGER NOT NULL,
    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    metadata JSON
);

-- ============================================================================
-- DMINT CONTRACT TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS contract_groups (
    id SERIAL PRIMARY KEY,
    first_ref VARCHAR(72) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    ticker VARCHAR(50) DEFAULT '',
    token_type VARCHAR(20) DEFAULT 'FT',
    description TEXT DEFAULT '',
    num_contracts INTEGER DEFAULT 0,
    total_supply BIGINT DEFAULT 0,
    minted_supply BIGINT DEFAULT 0,
    glyph_data JSON,
    files JSON,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS contracts (
    id SERIAL PRIMARY KEY,
    contract_ref VARCHAR(72) NOT NULL UNIQUE,
    token_ref VARCHAR(72) NOT NULL,
    location VARCHAR NOT NULL,
    output_index INTEGER NOT NULL,
    height INTEGER DEFAULT 0,
    max_height BIGINT DEFAULT 0,
    reward BIGINT DEFAULT 0,
    target BIGINT DEFAULT 0,
    script TEXT DEFAULT '',
    message VARCHAR(255) DEFAULT '',
    group_id INTEGER REFERENCES contract_groups(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS contract_list (
    id SERIAL PRIMARY KEY,
    base_ref VARCHAR(72) NOT NULL,
    count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================================
-- GLOBAL STATISTICS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS stats (
    id SERIAL PRIMARY KEY,
    glyphs_total INTEGER DEFAULT 0,
    glyphs_nft INTEGER DEFAULT 0,
    glyphs_ft INTEGER DEFAULT 0,
    glyphs_dat INTEGER DEFAULT 0,
    glyphs_containers INTEGER DEFAULT 0,
    glyphs_contained_items INTEGER DEFAULT 0,
    glyphs_users INTEGER DEFAULT 0,
    txos_total INTEGER DEFAULT 0,
    txos_rxd INTEGER DEFAULT 0,
    txos_nft INTEGER DEFAULT 0,
    txos_ft INTEGER DEFAULT 0,
    blocks_count INTEGER DEFAULT 0,
    latest_block_hash VARCHAR,
    latest_block_height INTEGER,
    latest_block_timestamp TIMESTAMP,
    last_updated TIMESTAMP DEFAULT NOW(),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================================
-- USER ENGAGEMENT - LIKES
-- ============================================================================

CREATE TABLE IF NOT EXISTS glyph_likes (
    id SERIAL PRIMARY KEY,
    glyph_ref VARCHAR(72) NOT NULL,
    user_address VARCHAR NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(glyph_ref, user_address)
);

-- ============================================================================
-- IMPORT STATE TRACKING
-- ============================================================================

CREATE TABLE IF NOT EXISTS import_state (
    id SERIAL PRIMARY KEY,
    last_block_height INTEGER NOT NULL DEFAULT 0,
    last_block_hash VARCHAR NOT NULL DEFAULT '',
    last_updated TIMESTAMP DEFAULT NOW(),
    is_importing BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ============================================================================
-- LEGACY TABLES (kept for materialized view compatibility)
-- ============================================================================
-- glyph_tokens and nfts tables have been consolidated into the unified 'glyphs' table.
-- All token data is now stored in 'glyphs' with the following token_type values:
--   - NFT: Non-fungible tokens
--   - FT: Fungible tokens
--   - DAT: Data tokens
--   - CONTAINER: Container/collection tokens
--   - USER: User identity tokens

-- Legacy glyph_tokens table (kept for materialized views)
CREATE TABLE IF NOT EXISTS glyph_tokens (
    id SERIAL PRIMARY KEY,
    token_id VARCHAR(72) NOT NULL,
    txid VARCHAR(64),
    type VARCHAR(20),
    ticker VARCHAR(50),
    max_supply BIGINT,
    difficulty INTEGER,
    premine BIGINT,
    genesis_height INTEGER,
    latest_height INTEGER,
    icon_mime_type VARCHAR(100),
    icon_data TEXT,
    icon_url VARCHAR(500),
    container VARCHAR,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_glyph_tokens_token_id ON glyph_tokens(token_id);

-- Address Clusters (for wallet clustering analysis)
CREATE SEQUENCE IF NOT EXISTS address_cluster_id_seq;
CREATE TABLE IF NOT EXISTS address_clusters (
    address TEXT PRIMARY KEY,
    cluster_id BIGINT NOT NULL DEFAULT nextval('address_cluster_id_seq'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_address_clusters_cluster_id ON address_clusters (cluster_id);

-- System State (key-value store for tracking)
CREATE TABLE IF NOT EXISTS system_state (
    key VARCHAR(255) PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_system_state_key ON system_state(key);

-- User Profiles
CREATE TABLE IF NOT EXISTS user_profiles (
    id SERIAL PRIMARY KEY,
    address VARCHAR NOT NULL,
    containers JSON,
    created_at TIMESTAMP
);

-- Failed Blocks
CREATE TABLE IF NOT EXISTS failed_blocks (
    block_height BIGINT PRIMARY KEY,
    block_hash VARCHAR(80),
    fail_reason TEXT,
    fail_count INTEGER NOT NULL DEFAULT 1,
    last_failed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP
);

-- Token Files
CREATE TABLE IF NOT EXISTS token_files (
    id SERIAL PRIMARY KEY,
    token_id VARCHAR NOT NULL,
    token_type VARCHAR NOT NULL,
    file_key VARCHAR,
    mime_type VARCHAR,
    file_data TEXT,
    remote_url VARCHAR,
    file_hash VARCHAR,
    file_size INTEGER,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Containers
CREATE TABLE IF NOT EXISTS containers (
    id SERIAL PRIMARY KEY,
    container_id VARCHAR NOT NULL UNIQUE,
    name VARCHAR,
    description TEXT,
    owner VARCHAR,
    token_count INTEGER DEFAULT 0,
    container_metadata JSON,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Backfill Status
CREATE TABLE IF NOT EXISTS backfill_status (
    id SERIAL PRIMARY KEY,
    backfill_type VARCHAR NOT NULL UNIQUE,
    is_complete BOOLEAN DEFAULT FALSE,
    last_processed_id BIGINT,
    total_processed BIGINT DEFAULT 0,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Token Holders
CREATE TABLE IF NOT EXISTS token_holders (
    id SERIAL PRIMARY KEY,
    token_id VARCHAR NOT NULL,
    address VARCHAR NOT NULL,
    balance BIGINT NOT NULL DEFAULT 0,
    percentage FLOAT,
    first_acquired_at TIMESTAMP,
    last_updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(token_id, address)
);

-- Token Swaps
CREATE TABLE IF NOT EXISTS token_swaps (
    id SERIAL PRIMARY KEY,
    txid VARCHAR NOT NULL,
    psrt_hex TEXT,
    from_token_id VARCHAR,
    from_amount BIGINT NOT NULL,
    from_is_rxd BOOLEAN DEFAULT FALSE,
    to_token_id VARCHAR,
    to_amount BIGINT NOT NULL,
    to_is_rxd BOOLEAN DEFAULT FALSE,
    seller_address VARCHAR,
    buyer_address VARCHAR,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    price_per_token FLOAT,
    created_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    block_height INTEGER
);

-- Token Burns
CREATE TABLE IF NOT EXISTS token_burns (
    id SERIAL PRIMARY KEY,
    token_id VARCHAR NOT NULL,
    txid VARCHAR NOT NULL,
    amount BIGINT NOT NULL,
    burner_address VARCHAR,
    block_height INTEGER,
    burned_at TIMESTAMP DEFAULT NOW()
);

-- Token Supply History
CREATE TABLE IF NOT EXISTS token_supply_history (
    id SERIAL PRIMARY KEY,
    token_id VARCHAR NOT NULL,
    circulating_supply BIGINT NOT NULL,
    burned_supply BIGINT DEFAULT 0,
    holder_count INTEGER DEFAULT 0,
    block_height INTEGER NOT NULL,
    recorded_at TIMESTAMP DEFAULT NOW()
);

-- Token Price History
CREATE TABLE IF NOT EXISTS token_price_history (
    id SERIAL PRIMARY KEY,
    token_id VARCHAR NOT NULL,
    price_rxd FLOAT NOT NULL,
    swap_id INTEGER REFERENCES token_swaps(id),
    txid VARCHAR,
    volume BIGINT NOT NULL,
    block_height INTEGER,
    recorded_at TIMESTAMP DEFAULT NOW()
);

-- Token Volume Daily
CREATE TABLE IF NOT EXISTS token_volume_daily (
    id SERIAL PRIMARY KEY,
    token_id VARCHAR NOT NULL,
    date TIMESTAMP NOT NULL,
    volume_tokens BIGINT DEFAULT 0,
    volume_rxd BIGINT DEFAULT 0,
    trade_count INTEGER DEFAULT 0,
    open_price FLOAT,
    high_price FLOAT,
    low_price FLOAT,
    close_price FLOAT,
    UNIQUE(token_id, date)
);

-- Token Mint Events
CREATE TABLE IF NOT EXISTS token_mint_events (
    id SERIAL PRIMARY KEY,
    token_id VARCHAR NOT NULL,
    txid VARCHAR NOT NULL,
    minter_address VARCHAR,
    amount BIGINT NOT NULL,
    block_height INTEGER,
    minted_at TIMESTAMP DEFAULT NOW()
);

-- Wallet Balances Cache
CREATE TABLE IF NOT EXISTS wallet_balances (
    address VARCHAR PRIMARY KEY,
    balance NUMERIC(20, 8) NOT NULL DEFAULT 0,
    utxo_count INTEGER NOT NULL DEFAULT 0,
    last_updated TIMESTAMP DEFAULT NOW()
);

-- ============================================================================
-- FOREIGN KEY CONSTRAINTS
-- ============================================================================

-- Note: FK to partitioned table (utxos) not supported in PostgreSQL.
-- glyphs.last_txo_id is a logical reference, enforced at application level.

-- ============================================================================
-- JSON SIZE CONSTRAINTS (Prevent bloat)
-- ============================================================================

ALTER TABLE glyphs ADD CONSTRAINT check_glyphs_attrs_size 
    CHECK (attrs IS NULL OR octet_length(attrs::text) < 65536);
ALTER TABLE glyphs ADD CONSTRAINT check_glyphs_p_size 
    CHECK (p IS NULL OR octet_length(p::text) < 4096);
ALTER TABLE glyph_actions ADD CONSTRAINT check_glyph_actions_metadata_size 
    CHECK (metadata IS NULL OR octet_length(metadata::text) < 16384);

-- ============================================================================
-- INDEXES
-- ============================================================================

-- Blocks
CREATE UNIQUE INDEX IF NOT EXISTS idx_blocks_hash ON blocks(hash, height);
CREATE INDEX IF NOT EXISTS idx_blocks_height ON blocks(height);
-- Note: Can't create unique index on height alone for partitioned table (must include partition key)

-- Transactions
CREATE INDEX IF NOT EXISTS idx_transactions_txid ON transactions(txid);
CREATE INDEX IF NOT EXISTS idx_transactions_block_height ON transactions(block_height);

-- Transaction Inputs
CREATE INDEX IF NOT EXISTS idx_tx_inputs_transaction_id ON transaction_inputs(transaction_id);
CREATE INDEX IF NOT EXISTS idx_tx_inputs_spent_txid ON transaction_inputs(spent_txid);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tx_inputs_txid_input_unique ON transaction_inputs(transaction_id, input_index);

-- UTXOs
CREATE INDEX IF NOT EXISTS idx_utxos_txid ON utxos(txid);
CREATE INDEX IF NOT EXISTS idx_utxos_address ON utxos(address);
CREATE INDEX IF NOT EXISTS idx_utxos_spent ON utxos(spent);
CREATE INDEX IF NOT EXISTS idx_utxos_glyph_ref ON utxos(glyph_ref);
CREATE INDEX IF NOT EXISTS idx_utxos_contract_type ON utxos(contract_type);
CREATE INDEX IF NOT EXISTS idx_utxos_unspent_address ON utxos(address) WHERE spent = false;
CREATE UNIQUE INDEX IF NOT EXISTS idx_utxos_txid_vout_unique ON utxos(txid, vout, transaction_block_height);

-- Glyphs (new unified table)
CREATE INDEX IF NOT EXISTS idx_glyphs_ref ON glyphs(ref);
CREATE INDEX IF NOT EXISTS idx_glyphs_token_type ON glyphs(token_type);
CREATE INDEX IF NOT EXISTS idx_glyphs_name ON glyphs(name);
CREATE INDEX IF NOT EXISTS idx_glyphs_ticker ON glyphs(ticker);
CREATE INDEX IF NOT EXISTS idx_glyphs_author ON glyphs(author);
CREATE INDEX IF NOT EXISTS idx_glyphs_container ON glyphs(container);
CREATE INDEX IF NOT EXISTS idx_glyphs_is_container ON glyphs(is_container);
CREATE INDEX IF NOT EXISTS idx_glyphs_spent ON glyphs(spent);
CREATE INDEX IF NOT EXISTS idx_glyphs_height ON glyphs(height);
CREATE INDEX IF NOT EXISTS idx_glyphs_spent_fresh ON glyphs(spent, fresh);
CREATE INDEX IF NOT EXISTS idx_glyphs_spent_token_type ON glyphs(spent, token_type);

-- Composite indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_glyphs_spent_type_created ON glyphs(spent, token_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_utxos_address_spent ON utxos(address, spent);

-- Partial indexes for performance
CREATE INDEX IF NOT EXISTS idx_glyphs_unspent_nfts ON glyphs(token_type, id) WHERE spent = false AND token_type = 'NFT';
CREATE INDEX IF NOT EXISTS idx_utxos_unspent_by_address ON utxos(address, txid, vout) WHERE spent = false;
CREATE INDEX IF NOT EXISTS idx_token_holders_token_balance_desc ON token_holders(token_id, balance DESC);

-- Glyph Actions
CREATE INDEX IF NOT EXISTS idx_glyph_actions_ref ON glyph_actions(ref);
CREATE INDEX IF NOT EXISTS idx_glyph_actions_type ON glyph_actions(type);
CREATE INDEX IF NOT EXISTS idx_glyph_actions_txid ON glyph_actions(txid);
CREATE INDEX IF NOT EXISTS idx_glyph_actions_height ON glyph_actions(height);

-- Contracts
CREATE INDEX IF NOT EXISTS idx_contracts_token_ref ON contracts(token_ref);
CREATE INDEX IF NOT EXISTS idx_contract_groups_first_ref ON contract_groups(first_ref);
CREATE INDEX IF NOT EXISTS idx_contract_list_base_ref ON contract_list(base_ref);

-- Glyph Likes
CREATE INDEX IF NOT EXISTS idx_glyph_likes_glyph_ref ON glyph_likes(glyph_ref);
CREATE INDEX IF NOT EXISTS idx_glyph_likes_user_address ON glyph_likes(user_address);

-- Additional glyphs indexes for owner and genesis_height
CREATE INDEX IF NOT EXISTS idx_glyphs_owner ON glyphs(owner);
CREATE INDEX IF NOT EXISTS idx_glyphs_genesis_height ON glyphs(genesis_height);
CREATE INDEX IF NOT EXISTS idx_token_files_token_id ON token_files(token_id);
CREATE INDEX IF NOT EXISTS idx_containers_container_id ON containers(container_id);
CREATE INDEX IF NOT EXISTS idx_token_holders_token_id ON token_holders(token_id);
CREATE INDEX IF NOT EXISTS idx_token_holders_address ON token_holders(address);

-- ============================================================================
-- INITIAL DATA
-- ============================================================================

-- Initialize stats
INSERT INTO stats (id, glyphs_total, blocks_count) VALUES (1, 0, 0) ON CONFLICT DO NOTHING;

-- Initialize import state
INSERT INTO import_state (id, last_block_height, last_block_hash, is_importing) VALUES (1, 0, '', false) ON CONFLICT DO NOTHING;

-- ============================================================================
-- MATERIALIZED VIEWS (Expensive Aggregations)
-- ============================================================================

-- Token holder statistics
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_token_holder_stats AS
SELECT 
    th.token_id,
    COUNT(DISTINCT th.address) as holder_count,
    SUM(th.balance) as circulating_supply,
    COUNT(*) as holder_entries
FROM token_holders th
WHERE th.balance > 0 
    AND th.address IS NOT NULL 
    AND length(trim(th.address)) > 0
GROUP BY th.token_id
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_token_holder_stats_token_id 
    ON mv_token_holder_stats(token_id);

-- Token burn statistics
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_token_burn_stats AS
SELECT 
    token_id,
    SUM(amount) as burned_supply,
    COUNT(*) as burn_count
FROM token_burns
GROUP BY token_id
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_token_burn_stats_token_id 
    ON mv_token_burn_stats(token_id);

-- Glyph token legacy stats (for compatibility)
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_glyph_token_stats AS
SELECT 
    token_id,
    MAX(max_supply) as max_supply,
    MAX(difficulty) as difficulty,
    MAX(premine) as premine,
    COUNT(*) as token_entries
FROM glyph_tokens
GROUP BY token_id
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_glyph_token_stats_token_id 
    ON mv_glyph_token_stats(token_id);

-- FT glyph summary view
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_ft_glyph_summary AS
SELECT 
    g.id,
    g.ref,
    g.token_type,
    COALESCE(NULLIF(trim(g.name), ''), NULLIF(trim(g.ticker), ''), g.ref) as display_name,
    NULLIF(trim(g.ticker), '') as display_ticker,
    g.height,
    g.created_at,
    g.updated_at,
    (g.embed_data IS NOT NULL OR g.remote_url IS NOT NULL) as has_image,
    COALESCE(ths.holder_count, 0) as holder_count,
    COALESCE(ths.circulating_supply, 0) as circulating_supply,
    gts.max_supply,
    COALESCE(tbs.burned_supply, 0) as burned_supply,
    gts.difficulty,
    gts.premine
FROM glyphs g
LEFT JOIN mv_token_holder_stats ths ON ths.token_id = g.ref
LEFT JOIN mv_token_burn_stats tbs ON tbs.token_id = g.ref
LEFT JOIN mv_glyph_token_stats gts ON gts.token_id = g.ref
WHERE g.token_type = 'FT'
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_ft_glyph_summary_id
    ON mv_ft_glyph_summary(id);
CREATE INDEX IF NOT EXISTS idx_mv_ft_glyph_summary_ref 
    ON mv_ft_glyph_summary(ref);
CREATE INDEX IF NOT EXISTS idx_mv_ft_glyph_summary_holder_count 
    ON mv_ft_glyph_summary(holder_count DESC, ref);

-- Function to refresh all materialized views
CREATE OR REPLACE FUNCTION refresh_materialized_views()
RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_token_holder_stats;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_token_burn_stats;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_glyph_token_stats;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_ft_glyph_summary;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- PARTITION MANAGEMENT (Data Retention Policies)
-- ============================================================================

-- Partition configuration table for automated partition management
CREATE TABLE IF NOT EXISTS partition_config (
    id SERIAL PRIMARY KEY,
    table_name VARCHAR(100) NOT NULL UNIQUE,
    partition_type VARCHAR(20) NOT NULL DEFAULT 'monthly',
    retention_months INTEGER DEFAULT 48,
    auto_create BOOLEAN DEFAULT true,
    last_partition_created TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Insert default partition configuration
INSERT INTO partition_config (table_name, partition_type, retention_months)
VALUES 
    ('glyph_actions', 'monthly', 48),
    ('token_price_history', 'monthly', 48), 
    ('token_volume_daily', 'monthly', 48)
ON CONFLICT (table_name) DO NOTHING;

-- Function to create new partitions automatically
CREATE OR REPLACE FUNCTION create_monthly_partitions()
RETURNS void AS $$
DECLARE
    v_table_name TEXT;
    v_partition_name TEXT;
    v_start_date DATE;
    v_end_date DATE;
    v_month_ahead INTERVAL := '1 month';
    v_months_to_create INTEGER := 3;
BEGIN
    FOR i IN 0..v_months_to_create-1 LOOP
        v_start_date := date_trunc('month', CURRENT_DATE + (v_month_ahead * i));
        v_end_date := v_start_date + v_month_ahead;
        
        -- Note: For production, partitioned tables are created via migration
        -- This function is a placeholder for future partition maintenance
        RAISE NOTICE 'Would create partition for % to %', v_start_date, v_end_date;
    END LOOP;
    
    RAISE NOTICE 'Monthly partitions check complete';
END;
$$ LANGUAGE plpgsql;

-- Mark schema version (skip alembic migrations for fresh installs)
-- This schema includes all production-ready constraints from migrations through 20260108
CREATE TABLE IF NOT EXISTS alembic_version (
    version_num VARCHAR(32) NOT NULL PRIMARY KEY
);
INSERT INTO alembic_version (version_num) VALUES ('20260108_mv_index') ON CONFLICT DO NOTHING;
