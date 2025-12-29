"""Background task to refresh materialized views periodically."""

import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import text
from api.dependencies import get_db

logger = logging.getLogger(__name__)

async def refresh_materialized_views():
    """Refresh materialized views in the background."""
    try:
        # Get a database session
        from database import SessionLocal
        db = SessionLocal()
        try:
            # Refresh materialized views concurrently
            db.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_token_holder_stats"))
            db.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_token_burn_stats"))
            db.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_glyph_token_stats"))
            db.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_ft_glyph_summary"))
            db.commit()
            logger.info("Materialized views refreshed successfully")
        except Exception as e:
            logger.error(f"Error refreshing materialized views: {e}")
            db.rollback()
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Error in refresh task: {e}")

async def periodic_refresh():
    """Periodically refresh materialized views every 5 minutes."""
    while True:
        try:
            await refresh_materialized_views()
            await asyncio.sleep(300)  # 5 minutes
        except Exception as e:
            logger.error(f"Error in periodic refresh: {e}")
            await asyncio.sleep(60)  # Retry after 1 minute on error
