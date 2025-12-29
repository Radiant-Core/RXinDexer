"""Add burned_supply to unified glyphs table

Revision ID: 20251228_glyphs_burned
Revises: 20251227_address_clusters
Create Date: 2025-12-28

"""

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision = '20251228_glyphs_burned'
down_revision = '20251227_address_clusters'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(text("COMMIT"))

    conn.execute(
        text(
            """
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'glyphs'
                      AND column_name = 'burned_supply'
                ) THEN
                    ALTER TABLE glyphs ADD COLUMN burned_supply BIGINT DEFAULT 0;
                END IF;
            END $$;
            """
        )
    )

    conn.execute(
        text(
            """
            UPDATE glyphs g
            SET burned_supply = b.burned_supply
            FROM (
                SELECT token_id, SUM(amount) AS burned_supply
                FROM token_burns
                GROUP BY token_id
            ) b
            WHERE g.ref = b.token_id;
            """
        )
    )


def downgrade():
    # No safe downgrade (would drop derived burn accounting)
    pass
