"""Add unique constraints for bulk copy ON CONFLICT

Revision ID: 20260108_bulk_constraints
Revises: 20260108_glyphs_columns
Create Date: 2026-01-08

Adds unique indexes required for ON CONFLICT in bulk_copy.py:
- utxos(txid, vout, transaction_block_height)
- transaction_inputs(transaction_id, input_index)
"""

from alembic import op
from sqlalchemy import text

revision = '20260108_bulk_constraints'
down_revision = '4e2b5d8cfb56'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(text("COMMIT"))
    
    # Add unique index for utxos ON CONFLICT
    conn.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_utxos_txid_vout_height 
            ON utxos (txid, vout, transaction_block_height);
            """
        )
    )
    
    # Add unique index for transaction_inputs ON CONFLICT
    conn.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_transaction_inputs_txid_idx 
            ON transaction_inputs (transaction_id, input_index);
            """
        )
    )
    
    print("Added unique constraints for bulk copy operations")


def downgrade():
    conn = op.get_bind()
    conn.execute(text("COMMIT"))
    
    conn.execute(text("DROP INDEX IF EXISTS uq_utxos_txid_vout_height"))
    conn.execute(text("DROP INDEX IF EXISTS uq_transaction_inputs_txid_idx"))
    
    print("Removed bulk copy unique constraints")
