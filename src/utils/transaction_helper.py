# /Users/radiant/Desktop/RXinDexer/src/utils/transaction_helper.py
# This file provides transaction safety helpers to prevent cascading database errors
# It wraps problematic database operations with error handling and recovery logic

import logging
import time
import contextlib
from sqlalchemy import text, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError, PendingRollbackError

logger = logging.getLogger(__name__)

@contextlib.contextmanager
def safe_transaction(db: Session, operation_name="database operation"):
    """
    A context manager that provides transaction safety with comprehensive error handling.
    If a transaction fails, it will be rolled back and the operation will be allowed to continue.
    
    Args:
        db: Database session
        operation_name: Name of the operation for logging purposes
        
    Yields:
        The session for use within the with block
    """
    try:
        yield db
        db.commit()
        logger.debug(f"Successfully committed {operation_name}")
    except Exception as e:
        db.rollback()
        logger.warning(f"Transaction for {operation_name} failed and was rolled back: {str(e)}")
        # Don't re-raise - allow operation to continue

def get_token_addresses_safe(db: Session):
    """
    Safely get token addresses without using JOIN operations that might fail.
    This completely avoids any JOIN or subquery between utxos and glyph_tokens.
    
    Args:
        db: Database session
        
    Returns:
        List of (address, token_ref) tuples or empty list if query fails
    """
    try:
        # Step 1: Use a fresh connection with AUTOCOMMIT to bypass transaction issues
        with db.bind.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            # Step 2: First, get all unspent UTXOs with token references
            unspent_utxos = conn.execute(text("""
                SELECT address, token_ref 
                FROM utxos 
                WHERE spent = false AND token_ref IS NOT NULL
            """)).fetchall()
            
            if not unspent_utxos:
                return []
                
            # Step 3: Get all valid token references
            token_refs = [row[1] for row in unspent_utxos]
            
            # Step 4: Check which token references actually exist in glyph_tokens
            # Note: We use a separate query to avoid any JOINs
            placeholders = ", ".join([f"'{ref}'" for ref in token_refs])
            valid_tokens = conn.execute(text(f"""
                SELECT ref FROM glyph_tokens WHERE ref IN ({placeholders})
            """)).fetchall()
            
            # Step 5: Create a set of valid token references for faster lookup
            valid_token_set = {row[0] for row in valid_tokens}
            
            # Step 6: Filter utxos to only those with valid token references
            return [(row[0], row[1]) for row in unspent_utxos if row[1] in valid_token_set]
    except Exception as e:
        logger.warning(f"Error getting token addresses safely: {str(e)}")
        # Return empty list rather than failing
        return []

def refresh_views_safe(db: Session):
    """
    Safely refresh materialized views even if there are pending transactions.
    
    Args:
        db: Database session
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Use a fresh connection with AUTOCOMMIT to bypass transaction issues
        with db.bind.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("REFRESH MATERIALIZED VIEW address_balances"))
        return True
    except Exception as e:
        logger.warning(f"Error refreshing materialized views: {str(e)}")
        return False

def reset_failed_transactions(db: Session):
    """
    Reset any failed transactions to prevent cascading errors.
    
    Args:
        db: Database session
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Try to roll back any pending transactions
        db.rollback()
        # Get a fresh connection to reset state
        return True
    except Exception as e:
        logger.warning(f"Error resetting failed transactions: {str(e)}")
        return False
