"""Add unique index for mv_ft_glyph_summary concurrent refresh

Revision ID: 20260108_mv_index
Revises: 20260108_bulk_constraints
Create Date: 2026-01-08

Adds unique index on mv_ft_glyph_summary(id) required for REFRESH MATERIALIZED VIEW CONCURRENTLY.
"""

from alembic import op
from sqlalchemy import text

revision = '20260108_mv_index'
down_revision = '20260108_bulk_constraints'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(text("COMMIT"))
    
    # Add unique index for concurrent refresh support
    conn.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_ft_glyph_summary_id 
            ON mv_ft_glyph_summary (id);
            """
        )
    )
    
    print("Added unique index for mv_ft_glyph_summary concurrent refresh")


def downgrade():
    conn = op.get_bind()
    conn.execute(text("COMMIT"))
    
    conn.execute(text("DROP INDEX IF EXISTS idx_mv_ft_glyph_summary_id"))
    
    print("Removed mv_ft_glyph_summary unique index")
