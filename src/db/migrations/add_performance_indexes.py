# /Users/radiant/Desktop/RXinDexer/src/db/migrations/add_performance_indexes.py
# This file adds additional indexes to improve query performance for high-volume blocks.
# It targets the most frequently accessed fields to speed up UTXO and balance lookups.

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

def apply_migration(conn):
    """
    Add performance-optimized indexes to UTXO and holders tables.
    
    Args:
        conn: Active database connection with transaction
    """
    try:
        # Add index on address for faster UTXO lookups by address
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_utxos_address ON utxos (address);
        """))
        logger.info("Created index on utxos.address")
        
        # Add index on spent status for faster unspent UTXO queries
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_utxos_spent ON utxos (spent) WHERE spent = FALSE;
        """))
        logger.info("Created index on utxos.spent")
        
        # Add index on address+spent for the most common query pattern
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_utxos_address_spent ON utxos (address, spent);
        """))
        logger.info("Created index on utxos.address+spent")
        
        # Add index on txid for faster lookups when spending UTXOs
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_utxos_txid ON utxos (txid);
        """))
        logger.info("Created index on utxos.txid")
        
        # Add partial index for unspent UTXOs to optimize the most common query
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_utxos_unspent ON utxos (address, amount) WHERE spent = FALSE;
        """))
        logger.info("Created partial index for unspent UTXOs")
        
        return True
    except Exception as e:
        logger.error(f"Failed to create performance indexes: {str(e)}")
        return False
