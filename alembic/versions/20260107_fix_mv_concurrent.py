"""Fix materialized view for concurrent refresh

Revision ID: 20260107_fix_mv_concurrent
Revises: 20251229_materialized_views
Create Date: 2026-01-07

"""

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision = '20260107_fix_mv_concurrent'
down_revision = '20251229_materialized_views'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(text("COMMIT"))

    # Drop the existing complex materialized view
    conn.execute(text("DROP MATERIALIZED VIEW IF EXISTS mv_ft_glyph_summary CASCADE"))
    
    # Recreate it with a simpler structure that supports CONCURRENT refresh
    # Move the complex ranking logic to the application layer
    conn.execute(
        text(
            """
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
                gts.premine
            FROM glyphs g
            LEFT JOIN mv_token_holder_stats ths ON ths.token_id = g.ref
            LEFT JOIN mv_token_burn_stats tbs ON tbs.token_id = g.ref
            LEFT JOIN mv_glyph_token_stats gts ON gts.token_id = g.ref
            WHERE g.token_type = 'FT';
            
            CREATE INDEX idx_mv_ft_glyph_summary_ref 
                ON mv_ft_glyph_summary(ref);
            CREATE INDEX idx_mv_ft_glyph_summary_holder_count 
                ON mv_ft_glyph_summary(holder_count DESC, ref);
            CREATE INDEX idx_mv_ft_glyph_summary_has_image 
                ON mv_ft_glyph_summary(has_image, holder_count DESC) WHERE has_image = true;
            """
        )
    )
    
    # Update the refresh function to use CONCURRENT for all views
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

    print("Materialized views updated for concurrent refresh")


def downgrade():
    conn = op.get_bind()
    conn.execute(text("COMMIT"))

    # Revert to the original complex view
    conn.execute(text("DROP MATERIALIZED VIEW IF EXISTS mv_ft_glyph_summary CASCADE"))
    
    conn.execute(
        text(
            """
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
    
    # Revert the refresh function
    conn.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION refresh_materialized_views()
            RETURNS void AS $$
            BEGIN
                REFRESH MATERIALIZED VIEW CONCURRENTLY mv_token_holder_stats;
                REFRESH MATERIALIZED VIEW CONCURRENTLY mv_token_burn_stats;
                REFRESH MATERIALIZED VIEW CONCURRENTLY mv_glyph_token_stats;
                REFRESH MATERIALIZED VIEW mv_ft_glyph_summary;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )

    print("Reverted materialized views to original structure")
