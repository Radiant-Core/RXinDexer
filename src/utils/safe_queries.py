# /Users/radiant/Desktop/RXinDexer/src/utils/safe_queries.py
# This module provides safe alternatives to problematic database queries
# It contains functions that bypass problematic JOINs and replace them with safer alternatives

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

def get_token_addresses_completely_safe(conn):
    """
    Get token addresses with a completely safe approach that avoids any JOINs.
    This is a direct replacement for the problematic query that joins utxos and glyph_tokens.
    
    Args:
        conn: Database connection
        
    Returns:
        List of (address, token_ref) tuples
    """
    logger.info("Using completely safe query to get token addresses without JOIN")
    
    try:
        # Step 1: Get all unspent UTXOs with token references
        unspent_utxos = conn.execute(text("""
            SELECT address, token_ref 
            FROM utxos 
            WHERE spent = false AND token_ref IS NOT NULL
        """)).fetchall()
        
        if not unspent_utxos:
            logger.info("No token references found in unspent UTXOs")
            return []
            
        # Step 2: Get all valid token references
        token_refs = [row[1] for row in unspent_utxos]
        
        # Step 3: Format for SQL IN clause
        placeholders = ", ".join(f"'{ref}'" for ref in token_refs)
        
        # Step 4: Check which token references actually exist in glyph_tokens
        valid_tokens = conn.execute(text(f"""
            SELECT ref FROM glyph_tokens WHERE ref IN ({placeholders})
        """)).fetchall()
        
        # Step 5: Create a set of valid token references for faster lookup
        valid_token_set = {row[0] for row in valid_tokens}
        
        # Step 6: Filter utxos to only those with valid token references
        result = [(row[0], row[1]) for row in unspent_utxos if row[1] in valid_token_set]
        
        logger.info(f"Found {len(result)} token addresses using safe query approach")
        return result
    except Exception as e:
        logger.error(f"Error in get_token_addresses_completely_safe: {str(e)}")
        return []

def refresh_materialized_views_safe(conn):
    """
    Safely refresh all materialized views.
    
    Args:
        conn: Database connection
        
    Returns:
        True if successful, False otherwise
    """
    logger.info("Refreshing materialized views with safe approach")
    
    try:
        # Directly refresh the view in an isolated operation
        conn.execute(text("REFRESH MATERIALIZED VIEW address_balances"))
        return True
    except Exception as e:
        logger.error(f"Error refreshing materialized views: {str(e)}")
        return False
