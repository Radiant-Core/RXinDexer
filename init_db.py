#!/usr/bin/env python
# Updated database initialization script
# This creates a temporary fix to ensure all models are loaded

import os
import sys
import logging
from sqlalchemy import text

# Add app directory to path
sys.path.append('/app')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import required modules
from src.models.database import engine, get_db_context, Base
try:
    from src.models.block import Block
    logger.info("Block model imported")
except ImportError as e:
    logger.error(f"Failed to import Block model: {e}")

try:    
    from src.models.transaction import Transaction
    logger.info("Transaction model imported")
except ImportError as e:
    logger.error(f"Failed to import Transaction model: {e}")

try:
    from src.models.utxo import UTXO
    logger.info("UTXO model imported")
except ImportError as e:
    logger.error(f"Failed to import UTXO model: {e}")

try:
    from src.models.sync_state import SyncState
    logger.info("SyncState model imported")
except ImportError as e:
    logger.error(f"Failed to import SyncState model: {e}")

try:
    from src.models.holder import Holder
    logger.info("Holder model imported")
except ImportError as e:
    logger.error(f"Failed to import Holder model: {e}")

def initialize_db():
    try:
        logger.info("Initializing database connection")
        
        # Test connection
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database connection successful")
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            return False
            
        # Create all models
        logger.info("Creating database schema")
        Base.metadata.create_all(bind=engine)
        logger.info("Schema created successfully")
        
        # Check if sync state exists
        with get_db_context() as db:
            # Try to access the sync_state table directly
            try:
                result = db.execute(text("SELECT * FROM sync_state WHERE id = 1")).fetchone()
                if not result:
                    logger.info("Creating initial sync state via SQL")
                    db.execute(text(
                        "INSERT INTO sync_state (id, current_height, is_syncing, glyph_scan_height) "
                        "VALUES (1, 0, 1, 0)"
                    ))
                    db.commit()
            except Exception as e:
                logger.warning(f"Error checking sync_state: {e}")
        
        logger.info("Database initialization complete")
        return True
        
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        return False

if __name__ == "__main__":
    success = initialize_db()
    if success:
        print("Database schema initialized successfully")
    else:
        print("Failed to initialize database schema")
        sys.exit(1)
