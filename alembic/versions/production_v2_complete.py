"""Production Complete Schema v2

Revision ID: production_v2_complete
Revises: None
Create Date: 2025-12-05

Complete production-ready schema with all tables, indexes, and cache tables.
Idempotent - safe to run multiple times.
"""
from alembic import op
from sqlalchemy import text

revision = 'production_v2_complete'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Create complete production schema."""
    conn = op.get_bind()
    
    # Commit any pending transaction to allow DDL
    conn.execute(text("COMMIT"))
    
    # Execute each table creation separately to avoid transaction issues
    tables = [
        # Blocks (partitioned)
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'blocks') THEN
                CREATE TABLE blocks (id SERIAL NOT NULL, hash VARCHAR(64) NOT NULL, height INTEGER NOT NULL, timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL, PRIMARY KEY (id, height)) PARTITION BY RANGE (height);
                CREATE TABLE blocks_initial PARTITION OF blocks DEFAULT;
            END IF;
        END $$;
        """,
        # Transactions (partitioned)
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'transactions') THEN
                CREATE TABLE transactions (id SERIAL NOT NULL, txid VARCHAR(64) NOT NULL, version INTEGER NOT NULL DEFAULT 1, locktime INTEGER NOT NULL DEFAULT 0, block_id INTEGER, block_height INTEGER NOT NULL, created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(), PRIMARY KEY (id, block_height)) PARTITION BY RANGE (block_height);
                CREATE TABLE transactions_initial PARTITION OF transactions DEFAULT;
            END IF;
        END $$;
        """,
        # Transaction Inputs
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'transaction_inputs') THEN
                CREATE TABLE transaction_inputs (id SERIAL PRIMARY KEY, transaction_id INTEGER NOT NULL, input_index INTEGER NOT NULL, spent_txid VARCHAR(64), spent_vout INTEGER, script_sig TEXT, sequence BIGINT NOT NULL, coinbase TEXT);
            END IF;
        END $$;
        """,
        # UTXOs (partitioned) - address is VARCHAR(128) to support NONSTANDARD:txid:vout format
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'utxos') THEN
                CREATE TABLE utxos (id SERIAL NOT NULL, txid VARCHAR(64) NOT NULL, vout INTEGER NOT NULL, address VARCHAR(128), value NUMERIC(20, 8) NOT NULL, spent BOOLEAN DEFAULT FALSE, spent_in_txid VARCHAR(64), transaction_id INTEGER, transaction_block_height INTEGER NOT NULL, script_type VARCHAR(32), script_hex TEXT, PRIMARY KEY (id, transaction_block_height)) PARTITION BY RANGE (transaction_block_height);
                CREATE TABLE utxos_initial PARTITION OF utxos DEFAULT;
            END IF;
        END $$;
        """,
        # Glyph Tokens
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'glyph_tokens') THEN
                CREATE TABLE glyph_tokens (id SERIAL PRIMARY KEY, token_id VARCHAR NOT NULL, txid VARCHAR NOT NULL, type VARCHAR, owner VARCHAR, token_metadata JSON, protocols JSON, max_supply BIGINT, current_supply BIGINT, contract_references JSON, difficulty INTEGER, container VARCHAR, author VARCHAR, ticker VARCHAR, genesis_height INTEGER, latest_height INTEGER, current_txid VARCHAR, current_vout INTEGER, created_at TIMESTAMP NOT NULL DEFAULT NOW(), updated_at TIMESTAMP DEFAULT NOW());
            END IF;
        END $$;
        """,
        # NFTs
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'nfts') THEN
                CREATE TABLE nfts (id SERIAL PRIMARY KEY, token_id VARCHAR NOT NULL, nft_metadata JSON, owner VARCHAR, collection VARCHAR, created_at TIMESTAMP);
            END IF;
        END $$;
        """,
        # User Profiles
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'user_profiles') THEN
                CREATE TABLE user_profiles (id SERIAL PRIMARY KEY, address VARCHAR NOT NULL, containers JSON, created_at TIMESTAMP);
            END IF;
        END $$;
        """,
        # Failed Blocks
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'failed_blocks') THEN
                CREATE TABLE failed_blocks (block_height BIGINT PRIMARY KEY, block_hash VARCHAR(80), fail_reason TEXT, fail_count INTEGER NOT NULL DEFAULT 1, last_failed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(), created_at TIMESTAMP);
            END IF;
        END $$;
        """,
        # Wallet Balances
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'wallet_balances') THEN
                CREATE TABLE wallet_balances (address VARCHAR PRIMARY KEY, balance NUMERIC(20, 8) NOT NULL DEFAULT 0, utxo_count INTEGER NOT NULL DEFAULT 0, last_updated TIMESTAMP DEFAULT NOW());
            END IF;
        END $$;
        """,
        # Add missing columns to glyph_tokens
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'container') THEN ALTER TABLE glyph_tokens ADD COLUMN container VARCHAR; END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'author') THEN ALTER TABLE glyph_tokens ADD COLUMN author VARCHAR; END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'ticker') THEN ALTER TABLE glyph_tokens ADD COLUMN ticker VARCHAR; END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'latest_height') THEN ALTER TABLE glyph_tokens ADD COLUMN latest_height INTEGER; END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyph_tokens' AND column_name = 'current_vout') THEN ALTER TABLE glyph_tokens ADD COLUMN current_vout INTEGER; END IF;
        END $$;
        """,
    ]
    
    for sql in tables:
        conn.execute(text(sql))
    
    # Create indexes
    indexes = [
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_blocks_hash ON blocks(hash, height)",
        "CREATE INDEX IF NOT EXISTS idx_blocks_height ON blocks(height)",
        "CREATE INDEX IF NOT EXISTS idx_blocks_height_desc ON blocks(height DESC)",
        "CREATE INDEX IF NOT EXISTS idx_transactions_txid ON transactions(txid)",
        "CREATE INDEX IF NOT EXISTS idx_transactions_block_id ON transactions(block_id)",
        "CREATE INDEX IF NOT EXISTS idx_transactions_block_height ON transactions(block_height)",
        "CREATE INDEX IF NOT EXISTS idx_transactions_created_at_desc ON transactions(created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_tx_inputs_transaction_id ON transaction_inputs(transaction_id)",
        "CREATE INDEX IF NOT EXISTS idx_tx_inputs_spent_txid ON transaction_inputs(spent_txid)",
        "CREATE INDEX IF NOT EXISTS idx_tx_inputs_spent_txid_vout ON transaction_inputs(spent_txid, spent_vout)",
        "CREATE INDEX IF NOT EXISTS idx_utxos_txid ON utxos(txid)",
        "CREATE INDEX IF NOT EXISTS idx_utxos_address ON utxos(address)",
        "CREATE INDEX IF NOT EXISTS idx_utxos_address_spent ON utxos(address, spent)",
        "CREATE INDEX IF NOT EXISTS idx_utxos_spent ON utxos(spent)",
        "CREATE INDEX IF NOT EXISTS idx_utxos_spent_in_txid ON utxos(spent_in_txid)",
        "CREATE INDEX IF NOT EXISTS idx_utxos_txid_vout ON utxos(txid, vout)",
        "CREATE INDEX IF NOT EXISTS idx_utxos_block_height ON utxos(transaction_block_height)",
        "CREATE INDEX IF NOT EXISTS idx_utxos_unspent_address ON utxos(address) WHERE spent = false",
        "CREATE INDEX IF NOT EXISTS idx_utxos_unspent_txid_vout ON utxos(txid, vout) WHERE spent = false",
        "CREATE INDEX IF NOT EXISTS idx_glyph_tokens_token_id ON glyph_tokens(token_id)",
        "CREATE INDEX IF NOT EXISTS idx_glyph_tokens_txid ON glyph_tokens(txid)",
        "CREATE INDEX IF NOT EXISTS idx_glyph_tokens_type ON glyph_tokens(type)",
        "CREATE INDEX IF NOT EXISTS idx_glyph_tokens_owner ON glyph_tokens(owner)",
        "CREATE INDEX IF NOT EXISTS idx_glyph_tokens_container ON glyph_tokens(container)",
        "CREATE INDEX IF NOT EXISTS idx_glyph_tokens_ticker ON glyph_tokens(ticker)",
        "CREATE INDEX IF NOT EXISTS idx_glyph_tokens_genesis_height ON glyph_tokens(genesis_height)",
        "CREATE INDEX IF NOT EXISTS idx_glyph_tokens_created_at_desc ON glyph_tokens(created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_glyph_tokens_updated_at_desc ON glyph_tokens(updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_glyph_tokens_type_owner ON glyph_tokens(type, owner)",
        "CREATE INDEX IF NOT EXISTS idx_nfts_token_id ON nfts(token_id)",
        "CREATE INDEX IF NOT EXISTS idx_nfts_owner ON nfts(owner)",
        "CREATE INDEX IF NOT EXISTS idx_nfts_collection ON nfts(collection)",
        "CREATE INDEX IF NOT EXISTS idx_nfts_created_at_desc ON nfts(created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_user_profiles_address ON user_profiles(address)",
        "CREATE INDEX IF NOT EXISTS idx_wallet_balances_balance_desc ON wallet_balances(balance DESC)",
    ]
    
    for idx in indexes:
        try:
            conn.execute(text(idx))
        except Exception:
            pass
    
    # GIN index for JSONB
    try:
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_glyph_tokens_metadata_gin ON glyph_tokens USING GIN ((token_metadata::jsonb) jsonb_path_ops)"))
    except Exception:
        pass


def downgrade():
    """Drop all tables."""
    conn = op.get_bind()
    tables = [
        'wallet_balances', 'failed_blocks', 'user_profiles', 'nfts',
        'glyph_tokens', 'transaction_inputs', 'utxos', 'transactions', 'blocks'
    ]
    for table in tables:
        conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
