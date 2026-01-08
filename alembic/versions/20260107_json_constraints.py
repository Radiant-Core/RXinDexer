"""Add JSON field size constraints to prevent bloat

Revision ID: 20260107_json_size
Revises: 20260107_fk_constraints
Create Date: 2026-01-07

Adds CHECK constraints to limit JSON field sizes and prevent database bloat
from arbitrarily large token metadata.
"""
from alembic import op


revision = '20260107_json_size'
down_revision = '20260107_fk_constraints'
branch_labels = None
depends_on = None


def upgrade():
    """Add JSON size constraints."""
    
    # Limit glyphs.attrs to 64KB
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'check_glyphs_attrs_size'
            ) THEN
                ALTER TABLE glyphs 
                ADD CONSTRAINT check_glyphs_attrs_size 
                CHECK (attrs IS NULL OR octet_length(attrs::text) < 65536);
            END IF;
        END $$;
    """)
    
    # Limit glyphs.p (protocols array) to 4KB
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'check_glyphs_p_size'
            ) THEN
                ALTER TABLE glyphs 
                ADD CONSTRAINT check_glyphs_p_size 
                CHECK (p IS NULL OR octet_length(p::text) < 4096);
            END IF;
        END $$;
    """)
    
    # Limit glyph_tokens.token_metadata to 128KB
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'check_glyph_tokens_metadata_size'
            ) THEN
                ALTER TABLE glyph_tokens 
                ADD CONSTRAINT check_glyph_tokens_metadata_size 
                CHECK (token_metadata IS NULL OR octet_length(token_metadata::text) < 131072);
            END IF;
        END $$;
    """)
    
    # Limit nfts.nft_metadata to 128KB
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'check_nfts_metadata_size'
            ) THEN
                ALTER TABLE nfts 
                ADD CONSTRAINT check_nfts_metadata_size 
                CHECK (nft_metadata IS NULL OR octet_length(nft_metadata::text) < 131072);
            END IF;
        END $$;
    """)
    
    # Limit glyph_actions.metadata to 16KB
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'check_glyph_actions_metadata_size'
            ) THEN
                ALTER TABLE glyph_actions 
                ADD CONSTRAINT check_glyph_actions_metadata_size 
                CHECK (metadata IS NULL OR octet_length(metadata::text) < 16384);
            END IF;
        END $$;
    """)


def downgrade():
    """Remove JSON size constraints."""
    op.execute("ALTER TABLE glyphs DROP CONSTRAINT IF EXISTS check_glyphs_attrs_size")
    op.execute("ALTER TABLE glyphs DROP CONSTRAINT IF EXISTS check_glyphs_p_size")
    op.execute("ALTER TABLE glyph_tokens DROP CONSTRAINT IF EXISTS check_glyph_tokens_metadata_size")
    op.execute("ALTER TABLE nfts DROP CONSTRAINT IF EXISTS check_nfts_metadata_size")
    op.execute("ALTER TABLE glyph_actions DROP CONSTRAINT IF EXISTS check_glyph_actions_metadata_size")
