"""Add missing foreign key constraints for referential integrity

Revision ID: 20260107_fk_constraints
Revises: 20260107_composite_idx
Create Date: 2026-01-07

Adds foreign key constraints to ensure data integrity between related tables.
Uses ON DELETE SET NULL or CASCADE as appropriate for each relationship.
"""
from alembic import op


revision = '20260107_fk_constraints'
down_revision = '20260107_composite_idx'
branch_labels = None
depends_on = None


def upgrade():
    """Add foreign key constraints."""
    
    # Note: glyphs.last_txo_id -> utxos.id cannot have FK constraint
    # because utxos is a partitioned table and PostgreSQL doesn't support
    # foreign keys referencing partitioned tables. Enforced at application level.
    
    # contracts.group_id -> contract_groups.id (CASCADE - contract deleted if group deleted)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_contracts_group'
            ) THEN
                ALTER TABLE contracts 
                ADD CONSTRAINT fk_contracts_group 
                FOREIGN KEY (group_id) REFERENCES contract_groups(id) 
                ON DELETE CASCADE;
            END IF;
        END $$;
    """)
    
    # token_price_history.swap_id -> token_swaps.id (SET NULL - history kept if swap deleted)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_price_history_swap'
            ) THEN
                ALTER TABLE token_price_history 
                ADD CONSTRAINT fk_price_history_swap 
                FOREIGN KEY (swap_id) REFERENCES token_swaps(id) 
                ON DELETE SET NULL;
            END IF;
        END $$;
    """)


def downgrade():
    """Remove foreign key constraints."""
    op.execute("ALTER TABLE contracts DROP CONSTRAINT IF EXISTS fk_contracts_group")
    op.execute("ALTER TABLE token_price_history DROP CONSTRAINT IF EXISTS fk_price_history_swap")
