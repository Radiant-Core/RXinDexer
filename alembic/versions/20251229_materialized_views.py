"""Add materialized views for expensive aggregations

Revision ID: 20251229_materialized_views
Revises: 20251229_performance_indexes
Create Date: 2025-12-29

"""

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision = '20251229_materialized_views'
down_revision = '20251229_performance_indexes'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(text("COMMIT"))

    # Materialized view for token holder statistics
    conn.execute(
        text(
            """
            DROP MATERIALIZED VIEW IF EXISTS mv_token_holder_stats CASCADE;
            CREATE MATERIALIZED VIEW mv_token_holder_stats AS
            SELECT 
                th.token_id,
                COUNT(DISTINCT th.address) as holder_count,
                SUM(th.balance) as circulating_supply,
                COUNT(*) as holder_entries
            FROM token_holders th
            WHERE th.balance > 0 
                AND th.address IS NOT NULL 
                AND length(trim(th.address)) > 0
            GROUP BY th.token_id
            WITH DATA;
            
            CREATE UNIQUE INDEX idx_mv_token_holder_stats_token_id 
                ON mv_token_holder_stats(token_id);
            CREATE INDEX idx_mv_token_holder_stats_holder_count 
                ON mv_token_holder_stats(holder_count DESC, token_id);
            """
        )
    )

    # Materialized view for token burn statistics
    conn.execute(
        text(
            """
            DROP MATERIALIZED VIEW IF EXISTS mv_token_burn_stats CASCADE;
            CREATE MATERIALIZED VIEW mv_token_burn_stats AS
            SELECT 
                token_id,
                SUM(amount) as burned_supply,
                COUNT(*) as burn_count
            FROM token_burns
            GROUP BY token_id
            WITH DATA;
            
            CREATE UNIQUE INDEX idx_mv_token_burn_stats_token_id 
                ON mv_token_burn_stats(token_id);
            """
        )
    )

    # Materialized view for glyph token legacy data
    conn.execute(
        text(
            """
            DROP MATERIALIZED VIEW IF EXISTS mv_glyph_token_stats CASCADE;
            CREATE MATERIALIZED VIEW mv_glyph_token_stats AS
            SELECT 
                token_id,
                MAX(max_supply) as max_supply,
                MAX(difficulty) as difficulty,
                MAX(premine) as premine,
                COUNT(*) as token_entries
            FROM glyph_tokens
            GROUP BY token_id
            WITH DATA;
            
            CREATE UNIQUE INDEX idx_mv_glyph_token_stats_token_id 
                ON mv_glyph_token_stats(token_id);
            """
        )
    )

    # Materialized view for FT glyph summary
    conn.execute(
        text(
            """
            DROP MATERIALIZED VIEW IF EXISTS mv_ft_glyph_summary CASCADE;
            CREATE MATERIALIZED VIEW mv_ft_glyph_summary AS
            SELECT 
                g.id,
                g.ref,
                g.token_type,
                COALESCE(NULLIF(trim(g.name), ''), NULLIF(trim(g.ticker), ''), g.ref) as display_name,
                NULLIF(trim(g.ticker), '') as display_ticker,
                g.height,
                g.created_at,
                g.updated_at,
                (g.embed_data IS NOT NULL OR g.remote_url IS NOT NULL) as has_image,
                COALESCE(ths.holder_count, 0) as holder_count,
                COALESCE(ths.circulating_supply, 0) as circulating_supply,
                gts.max_supply,
                COALESCE(tbs.burned_supply, 0) as burned_supply,
                gts.difficulty,
                gts.premine,
                ROW_NUMBER() OVER (
                    PARTITION BY (COALESCE(NULLIF(trim(g.name), ''), NULLIF(trim(g.ticker), ''), g.ref), NULLIF(trim(g.ticker), ''))
                    ORDER BY COALESCE(ths.holder_count, 0) DESC, COALESCE(g.height, 2147483647) ASC, g.id ASC
                ) as canonical_rank
            FROM glyphs g
            LEFT JOIN mv_token_holder_stats ths ON ths.token_id = g.ref
            LEFT JOIN mv_token_burn_stats tbs ON tbs.token_id = g.ref
            LEFT JOIN mv_glyph_token_stats gts ON gts.token_id = g.ref
            WHERE g.token_type = 'FT';
            
            CREATE INDEX idx_mv_ft_glyph_summary_rank 
                ON mv_ft_glyph_summary(canonical_rank, holder_count DESC);
            CREATE INDEX idx_mv_ft_glyph_summary_has_image 
                ON mv_ft_glyph_summary(has_image, holder_count DESC) WHERE has_image = true;
            """
        )
    )

    # Function to refresh materialized views
    conn.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION refresh_materialized_views()
            RETURNS void AS $$
            BEGIN
                REFRESH MATERIALIZED VIEW CONCURRENTLY mv_token_holder_stats;
                REFRESH MATERIALIZED VIEW CONCURRENTLY mv_token_burn_stats;
                REFRESH MATERIALIZED VIEW CONCURRENTLY mv_glyph_token_stats;
                REFRESH MATERIALIZED VIEW CONCURRENTLY mv_ft_glyph_summary;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )

    print("Materialized views created successfully")


def downgrade():
    conn = op.get_bind()
    conn.execute(text("COMMIT"))

    # Drop materialized views and function
    conn.execute(text("DROP FUNCTION IF EXISTS refresh_materialized_views()"))
    conn.execute(text("DROP MATERIALIZED VIEW IF EXISTS mv_ft_glyph_summary CASCADE"))
    conn.execute(text("DROP MATERIALIZED VIEW IF EXISTS mv_glyph_token_stats CASCADE"))
    conn.execute(text("DROP MATERIALIZED VIEW IF EXISTS mv_token_burn_stats CASCADE"))
    conn.execute(text("DROP MATERIALIZED VIEW IF EXISTS mv_token_holder_stats CASCADE"))

    print("Materialized views dropped successfully")
