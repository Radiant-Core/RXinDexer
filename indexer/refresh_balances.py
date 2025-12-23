#!/usr/bin/env python3
"""
Refresh wallet_balances table from UTXOs.
Run periodically (e.g., every 5 minutes) to keep balances current.

NOTE: This should only run when the indexer is caught up (sync lag < threshold)
because during bulk sync, spent checks are skipped and balances would be incorrect.
"""
import sys
import os
import time
import logging

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database.session import get_session

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Sync lag threshold - don't refresh balances if lag exceeds this
SYNC_LAG_THRESHOLD = int(os.getenv("BALANCE_REFRESH_LAG_THRESHOLD", "1000"))


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


def refresh_wallet_balances(batch_size: int = 100000):
    """
    Refresh wallet_balances table from UTXOs using batched processing.
    This is designed to run without locking the table for too long.
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
            db.commit()
            
            duration = time.time() - start_time
            logger.info(f"Wallet balance refresh completed in {duration:.2f}s - {count:,} wallets updated")
            
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
    args = parser.parse_args()
    
    if args.daemon:
        logger.info(f"Starting balance refresh daemon (interval: {args.interval} minutes, lag threshold: {SYNC_LAG_THRESHOLD})")
        while True:
            try:
                # Only refresh if sync is caught up
                if args.force or (is_sync_caught_up() and is_spent_backfill_complete()):
                    refresh_wallet_balances()
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
        refresh_wallet_balances()
        stats = get_refresh_stats()
        print(f"Stats: {stats}")
