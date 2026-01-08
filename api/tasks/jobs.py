"""
Background task job definitions.

These are async functions that run in the ARQ worker process.
"""

import logging
import httpx
from typing import Optional, Dict, Any
from datetime import datetime
from sqlalchemy import text, select, func

logger = logging.getLogger("rxindexer.tasks.jobs")


async def update_holder_counts(ctx: dict, token_ref: Optional[str] = None):
    """
    Update holder count statistics for tokens.
    
    Args:
        ctx: ARQ context with db_session_factory
        token_ref: Optional specific token ref to update (updates all if None)
    """
    logger.info(f"Starting holder count update: token_ref={token_ref}")
    
    db_factory = ctx.get("db_session_factory")
    if not db_factory:
        logger.error("No database session factory in context")
        return {"status": "error", "message": "No database connection"}
    
    async with db_factory() as db:
        try:
            if token_ref:
                # Update specific token
                result = await db.execute(text("""
                    UPDATE glyphs 
                    SET attrs = jsonb_set(
                        COALESCE(attrs, '{}')::jsonb,
                        '{holder_count}',
                        (
                            SELECT COUNT(DISTINCT address)::text::jsonb
                            FROM utxos 
                            WHERE glyph_ref = :ref AND NOT spent AND address IS NOT NULL
                        )
                    )
                    WHERE ref = :ref
                """), {"ref": token_ref})
                await db.commit()
                logger.info(f"Updated holder count for {token_ref}")
                return {"status": "success", "updated": 1}
            else:
                # Batch update all tokens with UTXOs
                result = await db.execute(text("""
                    WITH holder_counts AS (
                        SELECT glyph_ref, COUNT(DISTINCT address) as holder_count
                        FROM utxos
                        WHERE glyph_ref IS NOT NULL AND NOT spent AND address IS NOT NULL
                        GROUP BY glyph_ref
                    )
                    UPDATE glyphs g
                    SET attrs = jsonb_set(
                        COALESCE(g.attrs, '{}')::jsonb,
                        '{holder_count}',
                        hc.holder_count::text::jsonb
                    )
                    FROM holder_counts hc
                    WHERE g.ref = hc.glyph_ref
                """))
                count = result.rowcount
                await db.commit()
                logger.info(f"Updated holder counts for {count} tokens")
                return {"status": "success", "updated": count}
        except Exception as e:
            logger.error(f"Error updating holder counts: {e}")
            await db.rollback()
            return {"status": "error", "message": str(e)}


async def refresh_token_metadata(ctx: dict, token_ref: str):
    """
    Refresh metadata for a specific token.
    
    Args:
        ctx: ARQ context
        token_ref: Token reference to refresh
    """
    logger.info(f"Refreshing metadata for token: {token_ref}")
    
    db_factory = ctx.get("db_session_factory")
    if not db_factory:
        return {"status": "error", "message": "No database connection"}
    
    async with db_factory() as db:
        try:
            # Get token info
            from database.models import Glyph
            result = await db.execute(select(Glyph).where(Glyph.ref == token_ref))
            glyph = result.scalar_one_or_none()
            
            if not glyph:
                return {"status": "error", "message": "Token not found"}
            
            # If token has remote_url, try to fetch and validate
            if glyph.remote_url:
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        response = await client.head(glyph.remote_url)
                        # Update remote metadata
                        await db.execute(text("""
                            UPDATE glyphs 
                            SET attrs = jsonb_set(
                                COALESCE(attrs, '{}')::jsonb,
                                '{remote_status}',
                                :status::jsonb
                            ),
                            updated_at = NOW()
                            WHERE ref = :ref
                        """), {
                            "ref": token_ref,
                            "status": f'"{response.status_code}"'
                        })
                        await db.commit()
                except Exception as e:
                    logger.debug(f"Could not fetch remote URL: {e}")
            
            return {"status": "success", "token_ref": token_ref}
        except Exception as e:
            logger.error(f"Error refreshing token metadata: {e}")
            return {"status": "error", "message": str(e)}


async def refresh_balances(ctx: dict, address: Optional[str] = None):
    """
    Refresh wallet balances.
    
    Args:
        ctx: ARQ context
        address: Optional specific address (refreshes active addresses if None)
    """
    logger.info(f"Refreshing balances: address={address}")
    
    db_factory = ctx.get("db_session_factory")
    if not db_factory:
        return {"status": "error", "message": "No database connection"}
    
    async with db_factory() as db:
        try:
            if address:
                # Refresh specific address
                result = await db.execute(text("""
                    INSERT INTO wallet_balances (address, confirmed_balance, pending_balance, utxo_count, last_updated)
                    SELECT 
                        address,
                        COALESCE(SUM(CASE WHEN NOT spent THEN value ELSE 0 END), 0) as confirmed,
                        0 as pending,
                        COUNT(CASE WHEN NOT spent THEN 1 END) as utxo_count,
                        NOW()
                    FROM utxos
                    WHERE address = :address
                    GROUP BY address
                    ON CONFLICT (address) DO UPDATE SET
                        confirmed_balance = EXCLUDED.confirmed_balance,
                        pending_balance = EXCLUDED.pending_balance,
                        utxo_count = EXCLUDED.utxo_count,
                        last_updated = NOW()
                """), {"address": address})
                await db.commit()
                return {"status": "success", "updated": 1}
            else:
                # Refresh all addresses with recent activity
                result = await db.execute(text("""
                    INSERT INTO wallet_balances (address, confirmed_balance, pending_balance, utxo_count, last_updated)
                    SELECT 
                        address,
                        COALESCE(SUM(CASE WHEN NOT spent THEN value ELSE 0 END), 0) as confirmed,
                        0 as pending,
                        COUNT(CASE WHEN NOT spent THEN 1 END) as utxo_count,
                        NOW()
                    FROM utxos
                    WHERE address IS NOT NULL
                    GROUP BY address
                    ON CONFLICT (address) DO UPDATE SET
                        confirmed_balance = EXCLUDED.confirmed_balance,
                        pending_balance = EXCLUDED.pending_balance,
                        utxo_count = EXCLUDED.utxo_count,
                        last_updated = NOW()
                """))
                count = result.rowcount
                await db.commit()
                logger.info(f"Refreshed {count} wallet balances")
                return {"status": "success", "updated": count}
        except Exception as e:
            logger.error(f"Error refreshing balances: {e}")
            await db.rollback()
            return {"status": "error", "message": str(e)}


async def send_webhook(ctx: dict, url: str, payload: Dict[str, Any], event_type: str = "generic"):
    """
    Send a webhook notification.
    
    Args:
        ctx: ARQ context
        url: Webhook URL to send to
        payload: JSON payload to send
        event_type: Type of event (block, transaction, token)
    """
    logger.info(f"Sending webhook to {url}: event_type={event_type}")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                json={
                    "event_type": event_type,
                    "timestamp": datetime.utcnow().isoformat(),
                    "data": payload
                },
                headers={
                    "Content-Type": "application/json",
                    "X-RXinDexer-Event": event_type,
                }
            )
            
            success = 200 <= response.status_code < 300
            
            if success:
                logger.info(f"Webhook sent successfully to {url}: status={response.status_code}")
            else:
                logger.warning(f"Webhook failed: {url} returned {response.status_code}")
            
            return {
                "status": "success" if success else "failed",
                "url": url,
                "status_code": response.status_code
            }
    except Exception as e:
        logger.error(f"Error sending webhook to {url}: {e}")
        return {"status": "error", "url": url, "message": str(e)}


async def cleanup_old_data(ctx: dict, days: int = 30):
    """
    Clean up old data from the database.
    
    Args:
        ctx: ARQ context
        days: Number of days to retain data
    """
    logger.info(f"Starting cleanup of data older than {days} days")
    
    db_factory = ctx.get("db_session_factory")
    if not db_factory:
        return {"status": "error", "message": "No database connection"}
    
    async with db_factory() as db:
        try:
            # Clean up old backfill status entries
            result = await db.execute(text("""
                DELETE FROM backfill_status 
                WHERE is_complete = true 
                AND updated_at < NOW() - INTERVAL ':days days'
            """.replace(":days", str(days))))
            
            deleted = result.rowcount
            await db.commit()
            
            logger.info(f"Cleanup completed: deleted {deleted} old records")
            return {"status": "success", "deleted": deleted}
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            await db.rollback()
            return {"status": "error", "message": str(e)}
