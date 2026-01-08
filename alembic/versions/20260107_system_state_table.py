"""Add system_state table for tracking incremental balance refresh

Revision ID: 20260107_system_state
Revises: 
Create Date: 2026-01-07

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260107_system_state'
down_revision = '20251229_materialized_views'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create system_state table for storing key-value pairs
    op.execute("""
        CREATE TABLE IF NOT EXISTS system_state (
            key VARCHAR(255) PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        
        -- Create index for faster lookups
        CREATE INDEX IF NOT EXISTS idx_system_state_key ON system_state(key);
        
        -- Add comment
        COMMENT ON TABLE system_state IS 'Key-value store for system state tracking (e.g., last balance refresh block)';
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS system_state;
    """)
