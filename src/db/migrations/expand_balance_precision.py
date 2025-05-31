# /Users/radiant/Desktop/RXinDexer/src/db/migrations/expand_balance_precision.py
# This file adds support for larger balances by increasing the precision of the rxd_balance field.
# This allows the database to handle balances over a billion (up to 10^30).

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

def apply_migration(conn):
    """
    Increase precision of rxd_balance field from (16,8) to (38,8) to support larger balances.
    
    Args:
        conn: Active database connection with transaction
    """
    try:
        # Alter the rxd_balance column to handle much larger values (up to 10^30)
        conn.execute(text("""
            ALTER TABLE holders 
            ALTER COLUMN rxd_balance TYPE NUMERIC(38,8);
        """))
        logger.info("Successfully expanded rxd_balance precision to (38,8)")
        return True
    except Exception as e:
        logger.error(f"Failed to expand rxd_balance precision: {str(e)}")
        return False
