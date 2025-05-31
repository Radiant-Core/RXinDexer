# /Users/radiant/Desktop/RXinDexer/src/db/migrations/add_last_updated_at.py
# This file adds the last_updated_at column to the sync_state table

import os
import logging
import sys
from pathlib import Path

# Add parent directory to path for imports
parent_dir = Path(__file__).resolve().parent.parent.parent.parent
sys.path.append(str(parent_dir))

from sqlalchemy import text
from src.models.database import engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def run_migration():
    """Add the last_updated_at column to the sync_state table if it doesn't exist."""
    logger.info("Running migration to add last_updated_at column...")
    
    try:
        # Check if the column already exists
        with engine.connect() as conn:
            # Try to add the column if it doesn't exist
            conn.execute(text("""
                DO $$
                BEGIN
                    BEGIN
                        ALTER TABLE sync_state ADD COLUMN last_updated_at FLOAT;
                    EXCEPTION
                        WHEN duplicate_column THEN
                            RAISE NOTICE 'Column last_updated_at already exists in sync_state';
                    END;
                END $$;
            """))
            
            # Reset the is_syncing flag to ensure we're not stuck
            conn.execute(text("""
                UPDATE sync_state SET is_syncing = 0 WHERE id = 1;
            """))
            
            conn.commit()
            
        logger.info("Migration completed successfully!")
        return True
        
    except Exception as e:
        logger.error(f"Error running migration: {str(e)}")
        return False

if __name__ == "__main__":
    run_migration()
