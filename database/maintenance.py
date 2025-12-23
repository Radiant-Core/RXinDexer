import logging
from sqlalchemy import text
from database.session import engine

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def restore_heavy_indices():
    """
    Restores heavy indices (address, spent) that were disabled for initial sync.
    Uses CREATE INDEX CONCURRENTLY IF NOT EXISTS to avoid locking the table
    and to be safe to run multiple times.
    
    NOTE: This function uses AUTOCOMMIT isolation level because CREATE INDEX CONCURRENTLY
    cannot run inside a transaction block.
    """
    logger.info("[MAINTENANCE] Checking if heavy indices need to be restored...")
    
    # We must use a connection with AUTOCOMMIT for CREATE INDEX CONCURRENTLY
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        try:
            # Index 1: utxos(address)
            logger.info("[MAINTENANCE] Creating index 'ix_utxos_address' (if not exists)...")
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_utxos_address ON utxos (address)"))
            
            # Index 2: utxos(spent)
            logger.info("[MAINTENANCE] Creating index 'ix_utxos_spent' (if not exists)...")
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_utxos_spent ON utxos (spent)"))
            
            logger.info("[MAINTENANCE] Heavy indices check/restoration complete.")
            return True
        except Exception as e:
            logger.error(f"[MAINTENANCE] Failed to restore indices: {e}")
            return False
