"""Token Indexer Enhancement - Comprehensive Schema

Revision ID: token_indexer_enhancement
Revises: add_token_files_containers
Create Date: 2025-12-11

Adds comprehensive token tracking tables:
- Enhanced glyph_tokens with metadata, supply, author fields
- token_holders for holder tracking
- token_swaps for swap/trade tracking
- token_burns for burn event tracking
- token_supply_history for historical supply data
- token_price_history for price tracking
- token_volume_daily for OHLCV data
- token_mint_events for DMINT tracking

Idempotent - safe to run multiple times.
"""
from alembic import op
from sqlalchemy import text

revision = 'token_indexer_enhancement'
down_revision = 'add_token_files_containers'
branch_labels = None
depends_on = None


def upgrade():
    """Add comprehensive token tracking schema."""
    conn = op.get_bind()
    
    # Commit any pending transaction to allow DDL
    conn.execute(text("COMMIT"))
    
    statements = [
        # ============================================================
        # ENHANCE GLYPH_TOKENS TABLE
        # ============================================================
        """
        DO $$ BEGIN
            -- Add protocol_type column
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'protocol_type') THEN
                ALTER TABLE glyph_tokens ADD COLUMN protocol_type INTEGER;
            END IF;
            
            -- Add core metadata fields
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'name') THEN
                ALTER TABLE glyph_tokens ADD COLUMN name VARCHAR(255);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'description') THEN
                ALTER TABLE glyph_tokens ADD COLUMN description TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'token_type_name') THEN
                ALTER TABLE glyph_tokens ADD COLUMN token_type_name VARCHAR(100);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'immutable') THEN
                ALTER TABLE glyph_tokens ADD COLUMN immutable BOOLEAN DEFAULT TRUE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'license') THEN
                ALTER TABLE glyph_tokens ADD COLUMN license VARCHAR(255);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'attrs') THEN
                ALTER TABLE glyph_tokens ADD COLUMN attrs JSON;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'location') THEN
                ALTER TABLE glyph_tokens ADD COLUMN location VARCHAR;
            END IF;
            
            -- Add supply tracking fields
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'premine') THEN
                ALTER TABLE glyph_tokens ADD COLUMN premine BIGINT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'circulating_supply') THEN
                ALTER TABLE glyph_tokens ADD COLUMN circulating_supply BIGINT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'burned_supply') THEN
                ALTER TABLE glyph_tokens ADD COLUMN burned_supply BIGINT DEFAULT 0;
            END IF;
            
            -- Add DMINT fields
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'max_height') THEN
                ALTER TABLE glyph_tokens ADD COLUMN max_height INTEGER;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'reward') THEN
                ALTER TABLE glyph_tokens ADD COLUMN reward BIGINT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'num_contracts') THEN
                ALTER TABLE glyph_tokens ADD COLUMN num_contracts INTEGER;
            END IF;
            
            -- Add author resolution cache
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'author_name') THEN
                ALTER TABLE glyph_tokens ADD COLUMN author_name VARCHAR(255);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'author_image_url') THEN
                ALTER TABLE glyph_tokens ADD COLUMN author_image_url VARCHAR(500);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'author_image_data') THEN
                ALTER TABLE glyph_tokens ADD COLUMN author_image_data TEXT;
            END IF;
            
            -- Add icon fields
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'icon_mime_type') THEN
                ALTER TABLE glyph_tokens ADD COLUMN icon_mime_type VARCHAR(100);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'icon_url') THEN
                ALTER TABLE glyph_tokens ADD COLUMN icon_url VARCHAR(500);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'icon_data') THEN
                ALTER TABLE glyph_tokens ADD COLUMN icon_data TEXT;
            END IF;
            
            -- Add reveal transaction tracking
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'reveal_txid') THEN
                ALTER TABLE glyph_tokens ADD COLUMN reveal_txid VARCHAR(64);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'reveal_vout') THEN
                ALTER TABLE glyph_tokens ADD COLUMN reveal_vout INTEGER;
            END IF;
            
            -- Add deploy method and holder count
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'deploy_method') THEN
                ALTER TABLE glyph_tokens ADD COLUMN deploy_method VARCHAR(20);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'holder_count') THEN
                ALTER TABLE glyph_tokens ADD COLUMN holder_count INTEGER DEFAULT 0;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'supply_updated_at') THEN
                ALTER TABLE glyph_tokens ADD COLUMN supply_updated_at TIMESTAMP;
            END IF;
        END $$;
        """,
        
        # Add indexes for new columns
        """
        CREATE INDEX IF NOT EXISTS ix_glyph_tokens_name ON glyph_tokens(name);
        CREATE INDEX IF NOT EXISTS ix_glyph_tokens_ticker ON glyph_tokens(ticker);
        CREATE INDEX IF NOT EXISTS ix_glyph_tokens_reveal_txid ON glyph_tokens(reveal_txid);
        CREATE INDEX IF NOT EXISTS ix_glyph_tokens_token_type_name ON glyph_tokens(token_type_name);
        """,
        
        # ============================================================
        # TOKEN HOLDERS TABLE
        # ============================================================
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'token_holders') THEN
                CREATE TABLE token_holders (
                    id SERIAL PRIMARY KEY,
                    token_id VARCHAR NOT NULL,
                    address VARCHAR NOT NULL,
                    balance BIGINT NOT NULL DEFAULT 0,
                    percentage FLOAT,
                    first_acquired_at TIMESTAMP,
                    last_updated_at TIMESTAMP DEFAULT NOW()
                );
                CREATE INDEX ix_token_holders_token_id ON token_holders(token_id);
                CREATE INDEX ix_token_holders_address ON token_holders(address);
                CREATE INDEX ix_token_holders_token_balance ON token_holders(token_id, balance);
                CREATE INDEX ix_token_holders_address_token ON token_holders(address, token_id);
                CREATE UNIQUE INDEX uq_token_holder ON token_holders(token_id, address);
            END IF;
        END $$;
        """,
        
        # ============================================================
        # TOKEN SWAPS TABLE
        # ============================================================
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'token_swaps') THEN
                CREATE TABLE token_swaps (
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
                CREATE INDEX ix_token_swaps_txid ON token_swaps(txid);
                CREATE INDEX ix_token_swaps_from_token ON token_swaps(from_token_id);
                CREATE INDEX ix_token_swaps_to_token ON token_swaps(to_token_id);
                CREATE INDEX ix_token_swaps_seller ON token_swaps(seller_address);
                CREATE INDEX ix_token_swaps_buyer ON token_swaps(buyer_address);
                CREATE INDEX ix_token_swaps_status ON token_swaps(status);
                CREATE INDEX ix_token_swaps_block_height ON token_swaps(block_height);
            END IF;
        END $$;
        """,
        
        # ============================================================
        # TOKEN BURNS TABLE
        # ============================================================
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'token_burns') THEN
                CREATE TABLE token_burns (
                    id SERIAL PRIMARY KEY,
                    token_id VARCHAR NOT NULL,
                    txid VARCHAR NOT NULL,
                    amount BIGINT NOT NULL,
                    burner_address VARCHAR,
                    block_height INTEGER,
                    burned_at TIMESTAMP DEFAULT NOW()
                );
                CREATE INDEX ix_token_burns_token_id ON token_burns(token_id);
                CREATE INDEX ix_token_burns_txid ON token_burns(txid);
                CREATE INDEX ix_token_burns_burner ON token_burns(burner_address);
                CREATE INDEX ix_token_burns_block_height ON token_burns(block_height);
            END IF;
        END $$;
        """,
        
        # ============================================================
        # TOKEN SUPPLY HISTORY TABLE
        # ============================================================
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'token_supply_history') THEN
                CREATE TABLE token_supply_history (
                    id SERIAL PRIMARY KEY,
                    token_id VARCHAR NOT NULL,
                    circulating_supply BIGINT NOT NULL,
                    burned_supply BIGINT DEFAULT 0,
                    holder_count INTEGER DEFAULT 0,
                    block_height INTEGER NOT NULL,
                    recorded_at TIMESTAMP DEFAULT NOW()
                );
                CREATE INDEX ix_token_supply_history_token_id ON token_supply_history(token_id);
                CREATE INDEX ix_token_supply_history_block_height ON token_supply_history(block_height);
                CREATE INDEX ix_token_supply_history_token_block ON token_supply_history(token_id, block_height);
            END IF;
        END $$;
        """,
        
        # ============================================================
        # TOKEN PRICE HISTORY TABLE
        # ============================================================
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'token_price_history') THEN
                CREATE TABLE token_price_history (
                    id SERIAL PRIMARY KEY,
                    token_id VARCHAR NOT NULL,
                    price_rxd FLOAT NOT NULL,
                    swap_id INTEGER REFERENCES token_swaps(id),
                    txid VARCHAR,
                    volume BIGINT NOT NULL,
                    block_height INTEGER,
                    recorded_at TIMESTAMP DEFAULT NOW()
                );
                CREATE INDEX ix_token_price_history_token_id ON token_price_history(token_id);
                CREATE INDEX ix_token_price_history_block_height ON token_price_history(block_height);
                CREATE INDEX ix_token_price_history_recorded_at ON token_price_history(recorded_at);
            END IF;
        END $$;
        """,
        
        # ============================================================
        # TOKEN VOLUME DAILY TABLE
        # ============================================================
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'token_volume_daily') THEN
                CREATE TABLE token_volume_daily (
                    id SERIAL PRIMARY KEY,
                    token_id VARCHAR NOT NULL,
                    date TIMESTAMP NOT NULL,
                    volume_tokens BIGINT DEFAULT 0,
                    volume_rxd BIGINT DEFAULT 0,
                    trade_count INTEGER DEFAULT 0,
                    open_price FLOAT,
                    high_price FLOAT,
                    low_price FLOAT,
                    close_price FLOAT
                );
                CREATE INDEX ix_token_volume_daily_token_id ON token_volume_daily(token_id);
                CREATE INDEX ix_token_volume_daily_date ON token_volume_daily(date);
                CREATE INDEX ix_token_volume_daily_token_date ON token_volume_daily(token_id, date);
                CREATE UNIQUE INDEX uq_token_volume_daily ON token_volume_daily(token_id, date);
            END IF;
        END $$;
        """,
        
        # ============================================================
        # TOKEN MINT EVENTS TABLE
        # ============================================================
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'token_mint_events') THEN
                CREATE TABLE token_mint_events (
                    id SERIAL PRIMARY KEY,
                    token_id VARCHAR NOT NULL,
                    txid VARCHAR NOT NULL,
                    minter_address VARCHAR,
                    amount BIGINT NOT NULL,
                    block_height INTEGER,
                    minted_at TIMESTAMP DEFAULT NOW()
                );
                CREATE INDEX ix_token_mint_events_token_id ON token_mint_events(token_id);
                CREATE INDEX ix_token_mint_events_txid ON token_mint_events(txid);
                CREATE INDEX ix_token_mint_events_minter ON token_mint_events(minter_address);
                CREATE INDEX ix_token_mint_events_block_height ON token_mint_events(block_height);
            END IF;
        END $$;
        """,
        
        # ============================================================
        # ADD UNIQUE CONSTRAINT ON BLOCKS HEIGHT
        # Prevents duplicate blocks from re-indexing
        # ============================================================
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_blocks_height_unique') THEN
                CREATE UNIQUE INDEX idx_blocks_height_unique ON blocks (height);
            END IF;
        END $$;
        """,
    ]
    
    for stmt in statements:
        try:
            conn.execute(text(stmt))
            conn.execute(text("COMMIT"))
        except Exception as e:
            print(f"Warning during migration: {e}")
            conn.execute(text("ROLLBACK"))


def downgrade():
    """Remove token tracking tables (use with caution)."""
    conn = op.get_bind()
    conn.execute(text("COMMIT"))
    
    # Drop new tables
    tables_to_drop = [
        'token_mint_events',
        'token_volume_daily',
        'token_price_history',
        'token_supply_history',
        'token_burns',
        'token_swaps',
        'token_holders',
    ]
    
    for table in tables_to_drop:
        try:
            conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
            conn.execute(text("COMMIT"))
        except Exception as e:
            print(f"Warning dropping {table}: {e}")
            conn.execute(text("ROLLBACK"))
    
    # Note: We don't remove columns from glyph_tokens to preserve data
    print("Note: Columns added to glyph_tokens were not removed to preserve data.")
