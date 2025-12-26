"""Fix max_height column types (BIGINT)

Revision ID: 20251226_fix_max_height_bigint
Revises: 20251216_schema_alignment
Create Date: 2025-12-26

"""

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = '20251226_fix_max_height_bigint'
down_revision = '20251216_schema_alignment'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(text("COMMIT"))

    conn.execute(
        text(
            """
            DO $$ BEGIN
                -- glyph_tokens.max_height
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'glyph_tokens'
                      AND column_name = 'max_height'
                      AND data_type = 'integer'
                ) THEN
                    ALTER TABLE glyph_tokens ALTER COLUMN max_height TYPE BIGINT USING max_height::bigint;
                END IF;

                -- contracts.max_height
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'contracts'
                      AND column_name = 'max_height'
                      AND data_type = 'integer'
                ) THEN
                    ALTER TABLE contracts ALTER COLUMN max_height TYPE BIGINT USING max_height::bigint;
                END IF;
            END $$;
            """
        )
    )


def downgrade():
    # No safe downgrade (potential data loss if values exceed 32-bit integer range)
    pass
