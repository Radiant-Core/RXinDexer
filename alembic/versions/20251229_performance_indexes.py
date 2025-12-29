"""Add performance indexes for slow queries

Revision ID: 20251229_performance_indexes
Revises: 20251228_glyphs_burned
Create Date: 2025-12-29

"""

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision = '20251229_performance_indexes'
down_revision = '20251228_glyphs_burned'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(text("COMMIT"))

    # Index for UTXO address queries (slow address lookup)
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_utxos_address_spent_txid 
            ON utxos(address, spent, txid) 
            WHERE address IS NOT NULL;
            """
        )
    )

    # Index for token holder queries (complex aggregation)
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_token_holders_token_balance_positive 
            ON token_holders(token_id, balance DESC) 
            WHERE balance > 0;
            """
        )
    )

    # Index for token holder address queries
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_token_holders_address_balance_positive 
            ON token_holders(address, token_id, balance DESC) 
            WHERE balance > 0 AND address IS NOT NULL;
            """
        )
    )

    # Composite index for glyph table queries (FT token listing)
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_glyphs_ft_type_height 
            ON glyphs(token_type, height DESC, id) 
            WHERE token_type = 'FT';
            """
        )
    )

    # Index for glyph name/ticker searches
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_glyphs_name_ticker_ft 
            ON glyphs(token_type, name, ticker, id) 
            WHERE token_type = 'FT';
            """
        )
    )

    # Index for glyph image presence
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_glyphs_ft_has_image 
            ON glyphs(token_type, id) 
            WHERE token_type = 'FT' AND (embed_data IS NOT NULL OR remote_url IS NOT NULL);
            """
        )
    )

    # Index for token burns aggregation
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_token_burns_token_amount 
            ON token_burns(token_id, amount DESC);
            """
        )
    )

    # Index for glyph tokens legacy table
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_glyph_tokens_token_supply 
            ON glyph_tokens(token_id, max_supply, difficulty, premine);
            """
        )
    )

    # Address clusters performance index
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_address_clusters_cluster_address 
            ON address_clusters(cluster_id, address);
            """
        )
    )

    # Partial index for transactions block height queries
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_transactions_block_height_created 
            ON transactions(block_height DESC, created_at DESC);
            """
        )
    )

    print("Performance indexes created successfully")


def downgrade():
    conn = op.get_bind()
    conn.execute(text("COMMIT"))

    # Drop performance indexes
    indexes = [
        'ix_utxos_address_spent_txid',
        'ix_token_holders_token_balance_positive',
        'ix_token_holders_address_balance_positive',
        'ix_glyphs_ft_type_height',
        'ix_glyphs_name_ticker_ft',
        'ix_glyphs_ft_has_image',
        'ix_token_burns_token_amount',
        'ix_glyph_tokens_token_supply',
        'ix_address_clusters_cluster_address',
        'ix_transactions_block_height_created'
    ]

    for index in indexes:
        conn.execute(text(f"DROP INDEX IF EXISTS {index}"))

    print("Performance indexes dropped successfully")
