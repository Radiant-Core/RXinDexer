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
    value NUMERIC(20, 8) NOT NULL,
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
    updated_at TIMESTAMP DEFAULT NOW()
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
    max_height INTEGER DEFAULT 0,
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
-- LEGACY TABLES (Backward Compatibility)
-- ============================================================================

-- Glyph Tokens (legacy - use glyphs table for new data)
CREATE TABLE IF NOT EXISTS glyph_tokens (
    id SERIAL PRIMARY KEY,
    token_id VARCHAR NOT NULL,
    txid VARCHAR NOT NULL,
    type VARCHAR,
    owner VARCHAR,
    token_metadata JSON,
    protocols JSON,
    protocol_type INTEGER,
    name VARCHAR(255),
    description TEXT,
    token_type_name VARCHAR(100),
    immutable BOOLEAN DEFAULT TRUE,
    license VARCHAR(255),
    attrs JSON,
    location VARCHAR,
    max_supply BIGINT,
    current_supply BIGINT,
    premine BIGINT,
    circulating_supply BIGINT,
    burned_supply BIGINT DEFAULT 0,
    contract_references JSON,
    difficulty INTEGER,
    max_height INTEGER,
    reward BIGINT,
    num_contracts INTEGER,
    container VARCHAR,
    author VARCHAR,
    ticker VARCHAR(50),
    author_name VARCHAR(255),
    author_image_url VARCHAR(500),
    author_image_data TEXT,
    icon_mime_type VARCHAR(100),
    icon_url VARCHAR(500),
    icon_data TEXT,
    genesis_height INTEGER,
    latest_height INTEGER,
    current_txid VARCHAR,
    current_vout INTEGER,
    reveal_txid VARCHAR(64),
    reveal_vout INTEGER,
    spent BOOLEAN DEFAULT FALSE,
    fresh BOOLEAN DEFAULT TRUE,
    melted BOOLEAN DEFAULT FALSE,
    sealed BOOLEAN DEFAULT FALSE,
    swap_pending BOOLEAN DEFAULT FALSE,
    value BIGINT,
    deploy_method VARCHAR(20),
    holder_count INTEGER DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    supply_updated_at TIMESTAMP
);

-- NFTs table (legacy - use glyphs table for new data)
CREATE TABLE IF NOT EXISTS nfts (
    id SERIAL PRIMARY KEY,
    token_id VARCHAR NOT NULL,
    txid VARCHAR(64),
    type VARCHAR(50),
    token_type_name VARCHAR(100),
    name VARCHAR(255),
    ticker VARCHAR(50),
    description TEXT,
    nft_metadata JSON,
    attrs JSON,
    author VARCHAR,
    container VARCHAR,
    protocols JSON,
    protocol_type INTEGER,
    immutable BOOLEAN DEFAULT TRUE,
    location VARCHAR,
    owner VARCHAR,
    collection VARCHAR,
    genesis_height INTEGER,
    latest_height INTEGER,
    reveal_txid VARCHAR(64),
    reveal_vout INTEGER,
    current_txid VARCHAR(64),
    current_vout INTEGER,
    icon_mime_type VARCHAR(100),
    icon_url VARCHAR(500),
    icon_data TEXT,
    holder_count INTEGER DEFAULT 1,
    created_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT NOW()
);

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

-- Legacy indexes
CREATE INDEX IF NOT EXISTS idx_glyph_tokens_token_id ON glyph_tokens(token_id);
CREATE INDEX IF NOT EXISTS idx_glyph_tokens_type ON glyph_tokens(type);
CREATE INDEX IF NOT EXISTS idx_glyph_tokens_owner ON glyph_tokens(owner);
CREATE INDEX IF NOT EXISTS idx_glyph_tokens_container ON glyph_tokens(container);
CREATE INDEX IF NOT EXISTS idx_glyph_tokens_ticker ON glyph_tokens(ticker);
CREATE INDEX IF NOT EXISTS idx_nfts_token_id ON nfts(token_id);
CREATE INDEX IF NOT EXISTS idx_nfts_owner ON nfts(owner);
CREATE INDEX IF NOT EXISTS idx_nfts_token_type_name ON nfts(token_type_name);
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

-- Mark schema version (skip alembic migrations)
CREATE TABLE IF NOT EXISTS alembic_version (
    version_num VARCHAR(32) NOT NULL PRIMARY KEY
);
INSERT INTO alembic_version (version_num) VALUES ('20251216_schema_alignment') ON CONFLICT DO NOTHING;
