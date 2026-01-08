"""Add missing columns to glyphs table

Revision ID: 20260108_glyphs_columns
Revises: 20260108_data_retention
Create Date: 2026-01-08

Adds columns that were in models.py and init.sql but missing from the original
glyphs table creation migration:
- owner, max_supply, current_supply, circulating_supply
- genesis_height, current_txid, current_vout
- holder_count, deploy_method
- difficulty, max_height, reward, num_contracts
- author_name, author_ref_type
"""

from alembic import op
from sqlalchemy import text

revision = '20260108_glyphs_columns'
down_revision = '6db246955ba2'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(text("COMMIT"))
    
    # Add missing columns to glyphs table (idempotent)
    conn.execute(
        text(
            """
            DO $$ BEGIN
                -- Owner and supply tracking
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyphs' AND column_name = 'owner') THEN
                    ALTER TABLE glyphs ADD COLUMN owner VARCHAR;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyphs' AND column_name = 'max_supply') THEN
                    ALTER TABLE glyphs ADD COLUMN max_supply BIGINT;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyphs' AND column_name = 'current_supply') THEN
                    ALTER TABLE glyphs ADD COLUMN current_supply BIGINT;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyphs' AND column_name = 'circulating_supply') THEN
                    ALTER TABLE glyphs ADD COLUMN circulating_supply BIGINT;
                END IF;
                
                -- Genesis and current location
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyphs' AND column_name = 'genesis_height') THEN
                    ALTER TABLE glyphs ADD COLUMN genesis_height INTEGER;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyphs' AND column_name = 'current_txid') THEN
                    ALTER TABLE glyphs ADD COLUMN current_txid VARCHAR(64);
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyphs' AND column_name = 'current_vout') THEN
                    ALTER TABLE glyphs ADD COLUMN current_vout INTEGER;
                END IF;
                
                -- Holder count and deploy method
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyphs' AND column_name = 'holder_count') THEN
                    ALTER TABLE glyphs ADD COLUMN holder_count INTEGER DEFAULT 0;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyphs' AND column_name = 'deploy_method') THEN
                    ALTER TABLE glyphs ADD COLUMN deploy_method VARCHAR(20);
                END IF;
                
                -- DMINT contract fields
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyphs' AND column_name = 'difficulty') THEN
                    ALTER TABLE glyphs ADD COLUMN difficulty INTEGER;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyphs' AND column_name = 'max_height') THEN
                    ALTER TABLE glyphs ADD COLUMN max_height BIGINT;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyphs' AND column_name = 'reward') THEN
                    ALTER TABLE glyphs ADD COLUMN reward BIGINT;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyphs' AND column_name = 'num_contracts') THEN
                    ALTER TABLE glyphs ADD COLUMN num_contracts INTEGER;
                END IF;
                
                -- Resolved author info
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyphs' AND column_name = 'author_name') THEN
                    ALTER TABLE glyphs ADD COLUMN author_name VARCHAR(255);
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'glyphs' AND column_name = 'author_ref_type') THEN
                    ALTER TABLE glyphs ADD COLUMN author_ref_type VARCHAR(20);
                END IF;
            END $$;
            """
        )
    )
    
    # Add indexes for new columns
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_glyphs_owner ON glyphs(owner);
            CREATE INDEX IF NOT EXISTS ix_glyphs_genesis_height ON glyphs(genesis_height);
            CREATE INDEX IF NOT EXISTS ix_glyphs_holder_count ON glyphs(holder_count DESC);
            """
        )
    )
    
    print("Added missing columns to glyphs table")


def downgrade():
    conn = op.get_bind()
    conn.execute(text("COMMIT"))
    
    # Drop indexes
    conn.execute(text("DROP INDEX IF EXISTS ix_glyphs_owner"))
    conn.execute(text("DROP INDEX IF EXISTS ix_glyphs_genesis_height"))
    conn.execute(text("DROP INDEX IF EXISTS ix_glyphs_holder_count"))
    
    # Drop columns
    columns = [
        'owner', 'max_supply', 'current_supply', 'circulating_supply',
        'genesis_height', 'current_txid', 'current_vout',
        'holder_count', 'deploy_method',
        'difficulty', 'max_height', 'reward', 'num_contracts',
        'author_name', 'author_ref_type'
    ]
    for col in columns:
        conn.execute(text(f"ALTER TABLE glyphs DROP COLUMN IF EXISTS {col}"))
    
    print("Removed glyphs table columns")
