"""NFTs Table Enhancement - Dedicated Columns

Revision ID: nfts_table_enhancement
Revises: token_indexer_enhancement
Create Date: 2025-12-12

Adds dedicated columns to nfts table for efficient querying without JSON parsing.
Mirrors glyph_tokens structure for consistency.

Fields added:
- type: nft/mutable_nft/delegate (script-derived)
- token_type_name: user/container/object (payload.type from reveal)
- name, description: core metadata
- author, container: refs from payload.by/payload.in
- protocols, protocol_type: protocol info from reveal
- immutable: whether NFT is mutable
- genesis_height, latest_height: block tracking
- reveal_txid, reveal_vout: reveal transaction
- txid: genesis transaction
- icon_mime_type, icon_url, icon_data: image fields
- holder_count: cached holder count

Idempotent - safe to run multiple times.
"""
from alembic import op
from sqlalchemy import text

revision = 'nfts_table_enhancement'
down_revision = 'token_indexer_enhancement'
branch_labels = None
depends_on = None


def upgrade():
    """Add dedicated columns to nfts table."""
    conn = op.get_bind()
    
    # Commit any pending transaction to allow DDL
    conn.execute(text("COMMIT"))
    
    statements = [
        # ============================================================
        # ENHANCE NFTS TABLE WITH DEDICATED COLUMNS
        # ============================================================
        """
        DO $$ BEGIN
            -- Script-derived type (nft, mutable_nft, delegate)
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'type') THEN
                ALTER TABLE nfts ADD COLUMN type VARCHAR(50);
            END IF;
            
            -- Payload type from reveal (user, container, object/null)
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'token_type_name') THEN
                ALTER TABLE nfts ADD COLUMN token_type_name VARCHAR(100);
            END IF;
            
            -- Core metadata
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'name') THEN
                ALTER TABLE nfts ADD COLUMN name VARCHAR(255);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'description') THEN
                ALTER TABLE nfts ADD COLUMN description TEXT;
            END IF;
            
            -- Author and container refs (from payload.by and payload.in)
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'author') THEN
                ALTER TABLE nfts ADD COLUMN author VARCHAR;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'container') THEN
                ALTER TABLE nfts ADD COLUMN container VARCHAR;
            END IF;
            
            -- Protocol information
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'protocols') THEN
                ALTER TABLE nfts ADD COLUMN protocols JSON;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'protocol_type') THEN
                ALTER TABLE nfts ADD COLUMN protocol_type INTEGER;
            END IF;
            
            -- Mutability flag
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'immutable') THEN
                ALTER TABLE nfts ADD COLUMN immutable BOOLEAN DEFAULT TRUE;
            END IF;
            
            -- Genesis transaction
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'txid') THEN
                ALTER TABLE nfts ADD COLUMN txid VARCHAR(64);
            END IF;
            
            -- Block height tracking
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'genesis_height') THEN
                ALTER TABLE nfts ADD COLUMN genesis_height INTEGER;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'latest_height') THEN
                ALTER TABLE nfts ADD COLUMN latest_height INTEGER;
            END IF;
            
            -- Reveal transaction tracking
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'reveal_txid') THEN
                ALTER TABLE nfts ADD COLUMN reveal_txid VARCHAR(64);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'reveal_vout') THEN
                ALTER TABLE nfts ADD COLUMN reveal_vout INTEGER;
            END IF;
            
            -- Current location
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'current_txid') THEN
                ALTER TABLE nfts ADD COLUMN current_txid VARCHAR(64);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'current_vout') THEN
                ALTER TABLE nfts ADD COLUMN current_vout INTEGER;
            END IF;
            
            -- Ticker
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'ticker') THEN
                ALTER TABLE nfts ADD COLUMN ticker VARCHAR(50);
            END IF;
            
            -- Custom attributes from payload.attrs
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'attrs') THEN
                ALTER TABLE nfts ADD COLUMN attrs JSON;
            END IF;
            
            -- Linked payload location (when payload.loc points to another ref)
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'location') THEN
                ALTER TABLE nfts ADD COLUMN location VARCHAR;
            END IF;
            
            -- Icon/image fields
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'icon_mime_type') THEN
                ALTER TABLE nfts ADD COLUMN icon_mime_type VARCHAR(100);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'icon_url') THEN
                ALTER TABLE nfts ADD COLUMN icon_url VARCHAR(500);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'icon_data') THEN
                ALTER TABLE nfts ADD COLUMN icon_data TEXT;
            END IF;
            
            -- Holder count (cached)
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'holder_count') THEN
                ALTER TABLE nfts ADD COLUMN holder_count INTEGER DEFAULT 1;
            END IF;
            
            -- Updated timestamp
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'nfts' AND column_name = 'updated_at') THEN
                ALTER TABLE nfts ADD COLUMN updated_at TIMESTAMP DEFAULT NOW();
            END IF;
        END $$;
        """,
        
        # ============================================================
        # ADD INDEXES FOR EFFICIENT QUERYING
        # ============================================================
        """
        CREATE INDEX IF NOT EXISTS ix_nfts_type ON nfts(type);
        CREATE INDEX IF NOT EXISTS ix_nfts_token_type_name ON nfts(token_type_name);
        CREATE INDEX IF NOT EXISTS ix_nfts_name ON nfts(name);
        CREATE INDEX IF NOT EXISTS ix_nfts_ticker ON nfts(ticker);
        CREATE INDEX IF NOT EXISTS ix_nfts_author ON nfts(author);
        CREATE INDEX IF NOT EXISTS ix_nfts_container ON nfts(container);
        CREATE INDEX IF NOT EXISTS ix_nfts_genesis_height ON nfts(genesis_height);
        CREATE INDEX IF NOT EXISTS ix_nfts_latest_height ON nfts(latest_height);
        CREATE INDEX IF NOT EXISTS ix_nfts_reveal_txid ON nfts(reveal_txid);
        CREATE INDEX IF NOT EXISTS ix_nfts_txid ON nfts(txid);
        CREATE INDEX IF NOT EXISTS ix_nfts_immutable ON nfts(immutable);
        CREATE INDEX IF NOT EXISTS ix_nfts_holder_count ON nfts(holder_count);
        CREATE INDEX IF NOT EXISTS ix_nfts_updated_at ON nfts(updated_at DESC);
        """,
        
        # ============================================================
        # COMPOSITE INDEXES FOR COMMON QUERY PATTERNS
        # ============================================================
        """
        CREATE INDEX IF NOT EXISTS ix_nfts_type_created ON nfts(type, created_at DESC);
        CREATE INDEX IF NOT EXISTS ix_nfts_token_type_created ON nfts(token_type_name, created_at DESC);
        CREATE INDEX IF NOT EXISTS ix_nfts_author_created ON nfts(author, created_at DESC);
        CREATE INDEX IF NOT EXISTS ix_nfts_container_created ON nfts(container, created_at DESC);
        """,
        
        # ============================================================
        # GIN INDEX FOR JSON METADATA (for any remaining JSON queries)
        # ============================================================
        """
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_nfts_metadata_gin') THEN
                CREATE INDEX ix_nfts_metadata_gin ON nfts USING GIN ((nft_metadata::jsonb) jsonb_path_ops);
            END IF;
        EXCEPTION WHEN others THEN
            NULL;
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
    """Remove added columns from nfts table (use with caution)."""
    conn = op.get_bind()
    conn.execute(text("COMMIT"))
    
    # Drop indexes first
    indexes_to_drop = [
        'ix_nfts_type',
        'ix_nfts_token_type_name',
        'ix_nfts_name',
        'ix_nfts_author',
        'ix_nfts_container',
        'ix_nfts_genesis_height',
        'ix_nfts_latest_height',
        'ix_nfts_reveal_txid',
        'ix_nfts_txid',
        'ix_nfts_immutable',
        'ix_nfts_holder_count',
        'ix_nfts_updated_at',
        'ix_nfts_type_created',
        'ix_nfts_token_type_created',
        'ix_nfts_author_created',
        'ix_nfts_container_created',
        'ix_nfts_metadata_gin',
    ]
    
    for idx in indexes_to_drop:
        try:
            conn.execute(text(f"DROP INDEX IF EXISTS {idx}"))
            conn.execute(text("COMMIT"))
        except Exception as e:
            print(f"Warning dropping index {idx}: {e}")
            conn.execute(text("ROLLBACK"))
    
    # Note: We don't remove columns to preserve data
    print("Note: Columns added to nfts were not removed to preserve data.")
