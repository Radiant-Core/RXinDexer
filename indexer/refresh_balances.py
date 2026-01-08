#!/usr/bin/env python3
"""
Refresh wallet_balances table from UTXOs.
Run periodically (e.g., every 5 minutes) to keep balances current.

Supports two modes:
1. Full refresh - Complete recalculation of all balances (used initially or for recovery)
2. Incremental refresh - Only update balances for addresses with changed UTXOs (faster)

NOTE: This should only run when the indexer is caught up (sync lag < threshold)
because during bulk sync, spent checks are skipped and balances would be incorrect.
"""
import sys
import os
import time
import logging
from datetime import datetime, timedelta

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database.session import get_session

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Sync lag threshold - don't refresh balances if lag exceeds this
SYNC_LAG_THRESHOLD = int(os.getenv("BALANCE_REFRESH_LAG_THRESHOLD", "1000"))
# Use incremental updates when possible
INCREMENTAL_ENABLED = os.getenv("BALANCE_INCREMENTAL_ENABLED", "1").lower() in ("1", "true", "yes")
# Force full refresh every N cycles (to catch any drift)
FULL_REFRESH_INTERVAL = int(os.getenv("BALANCE_FULL_REFRESH_INTERVAL", "12"))  # Every 12 cycles (~1 hour)


def get_sync_lag() -> int:
    """Get current sync lag between node and indexed height."""
    try:
        from indexer.sync import rpc_call, get_last_synced_height
        with get_session() as db:
            node_height = rpc_call("getblockcount")
            db_height = get_last_synced_height(db)
            return node_height - db_height
    except Exception as e:
        logger.warning(f"Could not determine sync lag: {e}")
        return 999999  # Assume not synced if we can't check


def is_sync_caught_up() -> bool:
    """Check if indexer is caught up enough for accurate balance refresh."""
    lag = get_sync_lag()
    caught_up = lag <= SYNC_LAG_THRESHOLD
    if not caught_up:
        logger.info(f"Sync lag is {lag} (threshold: {SYNC_LAG_THRESHOLD}). Skipping balance refresh.")
    return caught_up


def is_spent_backfill_complete() -> bool:
    """Balances are only reliable once spent backfill has marked historical spends."""
    try:
        from database.models import BackfillStatus
        with get_session() as db:
            status = db.query(BackfillStatus).filter(BackfillStatus.backfill_type == 'spent').first()
            if not (status and status.is_complete):
                return False

            # Guard against "complete" being stale: require last_processed_id caught up
            # to the latest transaction_inputs id.
            try:
                max_input_id = db.execute(text("SELECT COALESCE(MAX(id), 0) FROM transaction_inputs")).scalar() or 0
                last_processed_id = int(status.last_processed_id or 0)
                return last_processed_id >= int(max_input_id)
            except Exception:
                return False
    except Exception as e:
        logger.warning(f"Could not determine spent backfill status: {e}")
        return False


def get_last_refresh_block() -> int:
    """Get the block height of the last balance refresh."""
    try:
        with get_session() as db:
            result = db.execute(text("""
                SELECT COALESCE(
                    (SELECT value::int FROM system_state WHERE key = 'last_balance_refresh_block'),
                    0
                )
            """))
            return result.scalar() or 0
    except Exception:
        return 0


def set_last_refresh_block(block_height: int):
    """Record the block height of the last balance refresh."""
    try:
        with get_session() as db:
            db.execute(text("""
                INSERT INTO system_state (key, value, updated_at)
                VALUES ('last_balance_refresh_block', :height, NOW())
                ON CONFLICT (key) DO UPDATE SET value = :height, updated_at = NOW()
            """), {"height": str(block_height)})
            db.commit()
    except Exception as e:
        logger.warning(f"Could not record last refresh block: {e}")


def refresh_wallet_balances_incremental() -> int:
    """
    Incrementally update wallet balances for addresses with changed UTXOs.
    Only processes addresses that have UTXOs created or spent since last refresh.
    Returns number of addresses updated.
    """
    logger.info("Starting incremental wallet balance refresh...")
    start_time = time.time()
    
    with get_session() as db:
        try:
            last_block = get_last_refresh_block()
            
            # Get current max block height
            current_block = db.execute(text("SELECT COALESCE(MAX(height), 0) FROM blocks")).scalar() or 0
            
            if current_block <= last_block:
                logger.info(f"No new blocks since last refresh (block {last_block}). Skipping.")
                return 0
            
            logger.info(f"Processing changes from block {last_block + 1} to {current_block}")
            
            # Find addresses with changed UTXOs in the block range
            # This includes: new UTXOs created, UTXOs spent
            sql = f"""
                WITH changed_addresses AS (
                    SELECT DISTINCT address FROM utxos_initial
                    WHERE transaction_block_height > {last_block}
                      AND address IS NOT NULL
                      AND address NOT LIKE 'NONSTANDARD:%'
                    UNION
                    SELECT DISTINCT u.address FROM utxos_initial u
                    JOIN transactions t ON u.spent_in_txid = t.txid
                    WHERE t.block_height > {last_block}
                      AND u.address IS NOT NULL
                      AND u.address NOT LIKE 'NONSTANDARD:%'
                ),
                new_balances AS (
                    SELECT 
                        ca.address,
                        COALESCE(SUM(CASE WHEN u.spent = false THEN u.value ELSE 0 END), 0) as balance,
                        COUNT(CASE WHEN u.spent = false THEN 1 END) as utxo_count
                    FROM changed_addresses ca
                    LEFT JOIN utxos_initial u ON u.address = ca.address AND u.spent = false
                    GROUP BY ca.address
                )
                INSERT INTO wallet_balances (address, balance, utxo_count, last_updated)
                SELECT address, balance, utxo_count, NOW()
                FROM new_balances
                ON CONFLICT (address) DO UPDATE SET
                    balance = EXCLUDED.balance,
                    utxo_count = EXCLUDED.utxo_count,
                    last_updated = NOW()
                RETURNING address
            """
            result = db.execute(text(sql))
            
            updated_count = len(result.fetchall())
            
            # Remove addresses with zero balance
            db.execute(text("DELETE FROM wallet_balances WHERE balance = 0"))
            
            db.commit()
            
            # Record the block height
            set_last_refresh_block(current_block)
            
            duration = time.time() - start_time
            logger.info(f"Incremental balance refresh completed in {duration:.2f}s - {updated_count:,} addresses updated (blocks {last_block + 1}-{current_block})")
            
            return updated_count
            
        except Exception as e:
            logger.error(f"Error in incremental balance refresh: {e}")
            db.rollback()
            raise


def refresh_wallet_balances(batch_size: int = 100000, force_full: bool = False):
    """
    Refresh wallet_balances table from UTXOs using batched processing.
    This is designed to run without locking the table for too long.
    
    Args:
        batch_size: Batch size for processing (unused in current implementation)
        force_full: Force a full refresh instead of incremental
    """
    logger.info("Starting wallet balance refresh...")
    start_time = time.time()
    
    with get_session() as db:
        try:
            # Use a temp table approach for atomic swap
            logger.info("Creating temporary balance aggregation...")
            
            # Step 1: Create temp table with aggregated balances
            # Note: Using parameter binding to avoid SQL injection and quote issues
            db.execute(text("""
                CREATE TEMP TABLE temp_balances AS
                SELECT 
                    address,
                    SUM(value) as balance,
                    COUNT(*) as utxo_count
                FROM utxos_initial
                WHERE spent = false 
                  AND address IS NOT NULL
                  AND address NOT LIKE :nonstandard_prefix
                GROUP BY address
            """), {"nonstandard_prefix": "NONSTANDARD:%"})
            
            # Step 2: Get count
            result = db.execute(text("SELECT COUNT(*) FROM temp_balances"))
            count = result.scalar()
            logger.info(f"Aggregated {count:,} wallet addresses")
            
            # Step 3: Truncate and insert (faster than UPSERT for full refresh)
            logger.info("Updating wallet_balances table...")
            db.execute(text("TRUNCATE wallet_balances"))
            db.execute(text("""
                INSERT INTO wallet_balances (address, balance, utxo_count, last_updated)
                SELECT address, balance, utxo_count, NOW()
                FROM temp_balances
            """))
            
            # Step 4: Cleanup
            db.execute(text("DROP TABLE IF EXISTS temp_balances"))
            
            # Record the current block height
            current_block = db.execute(text("SELECT COALESCE(MAX(height), 0) FROM blocks")).scalar() or 0
            db.commit()
            
            set_last_refresh_block(current_block)
            
            duration = time.time() - start_time
            logger.info(f"Full wallet balance refresh completed in {duration:.2f}s - {count:,} wallets updated")
            
            return count
            
        except Exception as e:
            logger.error(f"Error refreshing wallet balances: {e}")
            db.rollback()
            raise


def get_refresh_stats():
    """Get stats about the wallet_balances table."""
    with get_session() as db:
        result = db.execute(text("""
            SELECT 
                COUNT(*) as total_wallets,
                SUM(balance) as total_balance,
                MAX(last_updated) as last_refresh
            FROM wallet_balances
        """))
        row = result.fetchone()
        return {
            "total_wallets": row[0],
            "total_balance": float(row[1]) if row[1] else 0,
            "last_refresh": row[2]
        }


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Refresh wallet balances")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon, refreshing every N minutes")
    parser.add_argument("--interval", type=int, default=5, help="Refresh interval in minutes (default: 5)")
    parser.add_argument("--force", action="store_true", help="Force refresh even if sync/spent backfill checks fail")
    parser.add_argument("--full", action="store_true", help="Force full refresh instead of incremental")
    args = parser.parse_args()
    
    if args.daemon:
        logger.info(f"Starting balance refresh daemon (interval: {args.interval} minutes, lag threshold: {SYNC_LAG_THRESHOLD}, incremental: {INCREMENTAL_ENABLED})")
        cycle_count = 0
        
        while True:
            try:
                # Only refresh if sync is caught up
                if args.force or (is_sync_caught_up() and is_spent_backfill_complete()):
                    cycle_count += 1
                    
                    # Decide between incremental and full refresh
                    use_incremental = INCREMENTAL_ENABLED and not args.full
                    force_full = (cycle_count % FULL_REFRESH_INTERVAL == 0)
                    
                    if use_incremental and not force_full:
                        # Try incremental first
                        try:
                            updated = refresh_wallet_balances_incremental()
                            if updated == 0:
                                logger.info("No changes detected, skipping stats update")
                            else:
                                stats = get_refresh_stats()
                                logger.info(f"Stats: {stats['total_wallets']:,} wallets, {stats['total_balance']:,.2f} RXD total")
                        except Exception as e:
                            logger.warning(f"Incremental refresh failed ({e}), falling back to full refresh")
                            refresh_wallet_balances(force_full=True)
                            stats = get_refresh_stats()
                            logger.info(f"Stats: {stats['total_wallets']:,} wallets, {stats['total_balance']:,.2f} RXD total")
                    else:
                        # Full refresh
                        if force_full:
                            logger.info(f"Periodic full refresh (cycle {cycle_count})")
                        refresh_wallet_balances(force_full=True)
                        stats = get_refresh_stats()
                        logger.info(f"Stats: {stats['total_wallets']:,} wallets, {stats['total_balance']:,.2f} RXD total")
                else:
                    logger.info("Waiting for sync/spent backfill to complete before refreshing balances...")
            except Exception as e:
                logger.error(f"Refresh failed: {e}")
            
            time.sleep(args.interval * 60)
    else:
        if not args.force:
            if not is_sync_caught_up():
                raise SystemExit("Refusing to refresh wallet_balances: indexer is not caught up (sync lag above threshold). Use --force to override.")
            if not is_spent_backfill_complete():
                raise SystemExit("Refusing to refresh wallet_balances: spent backfill is not complete. Use --force to override.")
        
        if args.full or not INCREMENTAL_ENABLED:
            refresh_wallet_balances(force_full=True)
        else:
            refresh_wallet_balances_incremental()
        
        stats = get_refresh_stats()
        print(f"Stats: {stats}")
