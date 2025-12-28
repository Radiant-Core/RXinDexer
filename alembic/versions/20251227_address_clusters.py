"""Add address_clusters table for wallet clustering

Revision ID: 20251227_address_clusters
Revises: 20251226_fix_max_height_bigint
Create Date: 2025-12-27

"""

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision = '20251227_address_clusters'
down_revision = '20251226_fix_max_height_bigint'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(text("COMMIT"))

    conn.execute(
        text(
            """
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = 'address_cluster_id_seq') THEN
                    CREATE SEQUENCE address_cluster_id_seq;
                END IF;

                IF NOT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_name = 'address_clusters'
                ) THEN
                    CREATE TABLE address_clusters (
                        address TEXT PRIMARY KEY,
                        cluster_id BIGINT NOT NULL DEFAULT nextval('address_cluster_id_seq'),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    );
                END IF;

                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_indexes
                    WHERE indexname = 'ix_address_clusters_cluster_id'
                ) THEN
                    CREATE INDEX ix_address_clusters_cluster_id ON address_clusters (cluster_id);
                END IF;

                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_indexes
                    WHERE indexname = 'ix_address_clusters_address'
                ) THEN
                    CREATE INDEX ix_address_clusters_address ON address_clusters (address);
                END IF;
            END $$;
            """
        )
    )


def downgrade():
    # No safe downgrade (would drop derived clustering data)
    pass
