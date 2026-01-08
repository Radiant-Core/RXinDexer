"""Add missing composite indexes for common query patterns

Revision ID: 20260107_composite_idx
Revises: 20260107_utxo_bigint
Create Date: 2026-01-07

Adds composite and partial indexes for:
- Glyphs: (spent, token_type, created_at) for listing queries
- UTXOs: (address, spent) for wallet balance queries
- Token holders: (token_id, balance) for top holder queries
- Partial indexes for unspent records
"""
from alembic import op


revision = '20260107_composite_idx'
down_revision = '20260107_utxo_bigint'
branch_labels = None
depends_on = None


def upgrade():
    """Add composite and partial indexes for performance."""
    
    # Composite index on glyphs for common listing queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_glyphs_spent_type_created 
        ON glyphs(spent, token_type, created_at DESC)
    """)
    
    # Composite index on UTXOs for wallet balance queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_utxos_address_spent 
        ON utxos(address, spent)
    """)
    
    # Partial index for unspent NFTs (very common query)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_glyphs_unspent_nfts 
        ON glyphs(token_type, id) 
        WHERE spent = false AND token_type = 'NFT'
    """)
    
    # Partial index for unspent UTXOs by address (wallet lookups)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_utxos_unspent_by_address 
        ON utxos(address, txid, vout) 
        WHERE spent = false
    """)
    
    # Composite index on token_holders for top holder queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_token_holders_token_balance_desc 
        ON token_holders(token_id, balance DESC)
    """)


def downgrade():
    """Remove added indexes."""
    op.execute("DROP INDEX IF EXISTS ix_glyphs_spent_type_created")
    op.execute("DROP INDEX IF EXISTS ix_utxos_address_spent")
    op.execute("DROP INDEX IF EXISTS ix_glyphs_unspent_nfts")
    op.execute("DROP INDEX IF EXISTS ix_utxos_unspent_by_address")
    op.execute("DROP INDEX IF EXISTS ix_token_holders_token_balance_desc")
