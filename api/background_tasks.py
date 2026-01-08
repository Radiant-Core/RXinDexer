"""Background tasks for API including materialized view refresh and WebSocket broadcasts."""

import asyncio
import logging
from typing import Optional, Set
from sqlalchemy import text

from database.session import SessionLocal, AsyncSessionLocal

logger = logging.getLogger(__name__)

# Track running background tasks
_background_tasks: Set[asyncio.Task] = set()
_shutdown_event: Optional[asyncio.Event] = None


async def refresh_materialized_views():
    """Refresh materialized views in the background."""
    try:
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
            logger.debug(f"Error refreshing materialized views (may not exist yet): {e}")
            db.rollback()
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Error in refresh task: {e}")


async def periodic_refresh():
    """Periodically refresh materialized views every 5 minutes."""
    global _shutdown_event
    while _shutdown_event is None or not _shutdown_event.is_set():
        try:
            await refresh_materialized_views()
            # Wait with interruptible sleep
            for _ in range(60):  # 5 minutes in 5-second chunks
                if _shutdown_event and _shutdown_event.is_set():
                    break
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic refresh: {e}")
            await asyncio.sleep(60)


async def block_height_monitor():
    """
    Monitor for new blocks and broadcast to WebSocket clients.
    
    Polls the database for new blocks and broadcasts them to connected clients.
    """
    global _shutdown_event
    from api.websocket import broadcast_new_block
    
    last_height = 0
    
    # Get initial height
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(text("SELECT COALESCE(MAX(height), 0) FROM blocks"))
            last_height = result.scalar() or 0
            logger.info(f"Block monitor starting at height {last_height}")
    except Exception as e:
        logger.warning(f"Could not get initial block height: {e}")
    
    while _shutdown_event is None or not _shutdown_event.is_set():
        try:
            async with AsyncSessionLocal() as db:
                # Check for new blocks
                result = await db.execute(text("""
                    SELECT height, hash, 
                           EXTRACT(EPOCH FROM timestamp)::bigint as timestamp,
                           (SELECT COUNT(*) FROM transactions WHERE block_id = blocks.id) as tx_count
                    FROM blocks 
                    WHERE height > :last_height 
                    ORDER BY height ASC 
                    LIMIT 10
                """), {"last_height": last_height})
                
                new_blocks = result.fetchall()
                
                for block in new_blocks:
                    block_data = {
                        "height": block[0],
                        "hash": block[1],
                        "timestamp": block[2],
                        "tx_count": block[3] or 0,
                    }
                    await broadcast_new_block(block_data)
                    last_height = block[0]
                    logger.debug(f"Broadcast new block {last_height}")
            
            # Poll every 2 seconds
            await asyncio.sleep(2)
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"Block monitor error: {e}")
            await asyncio.sleep(5)


async def start_background_tasks():
    """Start all background tasks."""
    global _background_tasks, _shutdown_event
    
    _shutdown_event = asyncio.Event()
    
    # Start materialized view refresh
    task1 = asyncio.create_task(periodic_refresh())
    _background_tasks.add(task1)
    task1.add_done_callback(_background_tasks.discard)
    
    # Start block monitor for WebSocket broadcasts
    task2 = asyncio.create_task(block_height_monitor())
    _background_tasks.add(task2)
    task2.add_done_callback(_background_tasks.discard)
    
    logger.info("Background tasks started (materialized view refresh, block monitor)")


async def stop_background_tasks():
    """Stop all background tasks gracefully."""
    global _background_tasks, _shutdown_event
    
    if _shutdown_event:
        _shutdown_event.set()
    
    # Cancel all tasks
    for task in _background_tasks:
        task.cancel()
    
    # Wait for tasks to complete
    if _background_tasks:
        await asyncio.gather(*_background_tasks, return_exceptions=True)
    
    _background_tasks.clear()
    logger.info("Background tasks stopped")
