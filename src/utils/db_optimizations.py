# /Users/radiant/Desktop/RXinDexer/src/utils/db_optimizations.py
# This module provides utility functions for improving database performance.
# It contains optimized database operations that use the materialized view.
# It provides helpers for efficient balance calculation and materialized view management

import logging
import time
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

def refresh_address_balances(db: Session):
    """
    Manually refresh the address_balances materialized view.
    Should be called periodically or after large batches of changes.
    
    Args:
        db: Database session
    """
    start_time = time.time()
    try:
        db.execute(text("REFRESH MATERIALIZED VIEW address_balances"))
        db.commit()
        duration = time.time() - start_time
        logger.info(f"Refreshed address_balances materialized view in {duration:.2f}s")
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to refresh address_balances view: {str(e)}")

def get_balance_efficient(db: Session, address: str) -> int:
    """
    Get the balance for an address using the materialized view for efficiency.
    Falls back to direct query if needed.
    
    Args:
        db: Database session
        address: The address to check balance for
        
    Returns:
        Balance amount as integer
    """
    try:
        # Try materialized view first (fastest)
        result = db.execute(
            text("SELECT total_balance FROM address_balances WHERE address = :address"),
            {"address": address}
        ).scalar()
        
        if result is not None:
            return result
            
        # Fall back to direct query if not in view (might be new)
        result = db.execute(
            text("""
                SELECT COALESCE(SUM(amount), 0) 
                FROM utxos 
                WHERE address = :address AND spent = FALSE
            """),
            {"address": address}
        ).scalar()
        
        return result if result is not None else 0
    except Exception as e:
        logger.error(f"Error getting balance for {address}: {str(e)}")
        return 0

def get_top_balances(db: Session, limit: int = 100, min_balance: int = 0):
    """
    Get top address balances efficiently using the materialized view.
    
    Args:
        db: Database session
        limit: Maximum number of addresses to return
        min_balance: Minimum balance to include
        
    Returns:
        List of (address, balance) tuples
    """
    try:
        query = text("""
            SELECT address, total_balance 
            FROM address_balances 
            WHERE total_balance >= :min_balance
            ORDER BY total_balance DESC 
            LIMIT :limit
        """)
        
        result = db.execute(query, {"min_balance": min_balance, "limit": limit}).all()
        return result
    except Exception as e:
        logger.error(f"Error getting top balances: {str(e)}")
        return []

def batch_update_utxos(db: Session, utxos_data: list, batch_size: int = 1000):
    """
    Efficiently update multiple UTXOs in batches to reduce transaction overhead.
    
    Args:
        db: Database session
        utxos_data: List of UTXO dictionaries with txid, vout, and spent status
        batch_size: Number of UTXOs per batch
    
    Returns:
        Number of UTXOs updated
    """
    if not utxos_data:
        return 0
        
    total_updated = 0
    
    for i in range(0, len(utxos_data), batch_size):
        batch = utxos_data[i:i+batch_size]
        try:
            # Use VALUES clause for bulk operations - much faster than individual updates
            values_str = ", ".join(
                f"('{u['txid']}', {u['vout']}, {str(u['spent']).lower()})" 
                for u in batch
            )
            
            query = text(f"""
                UPDATE utxos
                SET spent = v.spent
                FROM (VALUES {values_str}) AS v(txid, vout, spent)
                WHERE utxos.txid = v.txid AND utxos.vout = v.vout
            """)
            
            result = db.execute(query)
            db.commit()
            total_updated += result.rowcount
        except Exception as e:
            db.rollback()
            logger.error(f"Error in batch update: {str(e)}")
    
    # Refresh balance view after large changes
    if total_updated > 100:
        refresh_address_balances(db)
        
    return total_updated
