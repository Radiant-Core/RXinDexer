# /Users/radiant/Desktop/RXinDexer/src/utils/query_optimizer.py
# This module provides optimized versions of common slow database queries
# It replaces direct UTXO table queries with efficient materialized view alternatives

import logging
import time
from decimal import Decimal
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

# Import our transaction helper functions
from src.utils.transaction_helper import safe_transaction, get_token_addresses_safe, reset_failed_transactions

logger = logging.getLogger(__name__)

def create_optimized_temp_balances(db: Session):
    """
    Create a temporary table for address balances using the materialized view.
    This is significantly faster than aggregating the UTXO table directly.
    
    Args:
        db: Database session
    """
    start_time = time.time()
    try:
        # Use the materialized view instead of aggregating the UTXO table
        db.execute(text("""
            DROP TABLE IF EXISTS temp_balances;
            
            CREATE TEMPORARY TABLE temp_balances AS
            SELECT address, total_balance AS balance
            FROM address_balances;
        """))
        db.commit()
        
        duration = time.time() - start_time
        logger.info(f"Created optimized temp_balances in {duration:.2f}s (using materialized view)")
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating optimized temp balances: {str(e)}")
        return False

def get_large_balances(db: Session, threshold=1000000000):
    """
    Get addresses with large balances using the materialized view.
    This is much faster than querying the UTXO table directly.
    
    Args:
        db: Database session
        threshold: Minimum balance threshold
        
    Returns:
        List of tuples with (address, balance)
    """
    start_time = time.time()
    
    # First try to reset any failed transactions
    reset_failed_transactions(db)
    
    try:
        # Use a direct connection with AUTOCOMMIT to bypass transaction issues
        with db.bind.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            try:
                result = conn.execute(
                    text("""
                    SELECT address, total_balance
                    FROM address_balances
                    WHERE total_balance > :threshold
                """),
                    {"threshold": threshold}
                ).fetchall()
                
                duration = time.time() - start_time
                logger.info(f"Retrieved {len(result)} large balances in {duration:.2f}s (using materialized view)")
                return result
            except SQLAlchemyError as e:
                logger.error(f"Error querying address_balances view: {str(e)}")
                return []
    except Exception as e:
        logger.error(f"Error getting large balances: {str(e)}")
        return []

def update_holder_balances_efficient(db: Session):
    """
    Update all holder balances efficiently using the materialized view.
    
    Args:
        db: Database session
        
    Returns:
        Number of holders updated
    """
    start_time = time.time()
    
    # First try to reset any failed transactions
    reset_failed_transactions(db)
    
    try:
        # Use a direct connection with AUTOCOMMIT to bypass transaction issues
        with db.bind.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            try:
                # Refresh the address balances view
                conn.execute(text("REFRESH MATERIALIZED VIEW address_balances"))
                duration = time.time() - start_time
                logger.info(f"Updated holder balances in {duration:.2f}s (using REFRESH MATERIALIZED VIEW)")
                return 1  # Success
            except SQLAlchemyError as e:
                logger.error(f"Error refreshing address_balances view: {str(e)}")
                return 0
    except Exception as e:
        logger.error(f"Error updating holder balances efficiently: {str(e)}")
        return 0

def get_address_balance(db: Session, address: str) -> Decimal:
    """
    Get balance for an address using the materialized view.
    
    Args:
        db: Database session
        address: Address to check
        
    Returns:
        Current balance as Decimal
    """
    try:
        # Use the materialized view for faster lookup
        result = db.execute(text("""
            SELECT total_balance 
            FROM address_balances 
            WHERE address = :address
        """), {"address": address}).scalar()
        
        if result is not None:
            return Decimal(str(result))
        
        # If not in materialized view, balance is zero
        return Decimal('0')
    except Exception as e:
        logger.error(f"Error getting address balance: {str(e)}")
        # Fall back to direct UTXO query if there's an issue
        result = db.execute(text("""
            SELECT COALESCE(SUM(amount), 0)
            FROM utxos
            WHERE address = :address AND spent = FALSE
        """), {"address": address}).scalar()
        
        if result is not None:
            return Decimal(str(result))
        return Decimal('0')

def batch_update_utxos(db: Session, spent_txids, spent_vouts, spent_by_txids):
    """
    Update UTXOs as spent in an efficient batch operation.
    
    Args:
        db: Database session
        spent_txids: List of transaction IDs to mark as spent
        spent_vouts: List of vout indices
        spent_by_txids: List of spending transaction IDs
        
    Returns:
        Number of UTXOs updated
    """
    if not spent_txids or not spent_vouts or not spent_by_txids:
        return 0
        
    try:
        # Use the efficient database function
        result = db.execute(text("""
            SELECT batch_update_utxos(:txids, :vouts, :spent_by)
        """), {
            "txids": spent_txids,
            "vouts": spent_vouts,
            "spent_by": spent_by_txids
        }).scalar()
        
        db.commit()
        return result
    except Exception as e:
        db.rollback()
        logger.error(f"Error in batch update UTXOs: {str(e)}")
        return 0

def refresh_materialized_views(db: Session):
    """
    Manually refresh all materialized views.
    
    Args:
        db: Database session
    """
    start_time = time.time()
    
    # First try to reset any failed transactions
    reset_failed_transactions(db)
    
    try:
        # Use a direct connection with AUTOCOMMIT to bypass transaction issues
        with db.bind.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            try:
                # Refresh the address balances view directly
                conn.execute(text("REFRESH MATERIALIZED VIEW address_balances"))
                
                # Add any other materialized views here
                
                duration = time.time() - start_time
                logger.info(f"Refreshed materialized views in {duration:.2f}s")
                return True
            except SQLAlchemyError as e:
                logger.error(f"Error refreshing materialized views: {str(e)}")
                return False
    except Exception as e:
        logger.error(f"Error refreshing materialized views: {str(e)}")
        return False

def perform_database_maintenance(db: Session):
    """
    Perform database maintenance operations.
    
    Args:
        db: Database session
    """
    try:
        result = db.execute(text("SELECT perform_safe_maintenance()")).scalar()
        logger.info(f"Database maintenance result: {result}")
        return True
    except Exception as e:
        logger.error(f"Error performing database maintenance: {str(e)}")
        return False
