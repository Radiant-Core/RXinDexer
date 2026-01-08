"""Fix UTXO value column from Float to BigInteger for precision

Revision ID: 20260107_utxo_bigint
Revises: 20260107_system_state
Create Date: 2026-01-07

This migration fixes a critical bug where UTXO.value was stored as Float,
causing potential precision loss with satoshi values. BigInteger ensures
exact integer storage without floating-point rounding errors.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = '20260107_utxo_bigint'
down_revision = '20260107_system_state'
branch_labels = None
depends_on = None


def upgrade():
    """Convert UTXO value column from Float to BigInteger."""
    # PostgreSQL allows direct type conversion with USING clause
    # ROUND() ensures any existing float values are properly converted
    op.execute("""
        ALTER TABLE utxos 
        ALTER COLUMN value TYPE BIGINT 
        USING ROUND(value)::BIGINT
    """)


def downgrade():
    """Revert UTXO value column back to Float (not recommended)."""
    op.execute("""
        ALTER TABLE utxos 
        ALTER COLUMN value TYPE DOUBLE PRECISION 
        USING value::DOUBLE PRECISION
    """)
