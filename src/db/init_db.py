# /Users/radiant/Desktop/RXinDexer/src/db/init_db_fixed.py
# This file initializes the database schema by creating all tables and indexes required by the RXinDexer application.
# It provides a clean, database-agnostic way to set up the database for development, testing, or production.

import os
import logging
import sys
from pathlib import Path

# Add parent directory to path for imports
parent_dir = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(parent_dir))

from sqlalchemy import text, inspect
import time
from src.models.database import Base, engine, init_db
from src.models import UTXO, GlyphToken, Holder, SyncState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def create_tables():
    """Create all database tables defined in the models."""
    logger.info("Creating database tables...")
    
    try:
        # Create tables
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully!")
        
        # Initialize sync state if it doesn't exist
        init_db()
        
        # Run schema migrations for existing tables
        apply_migrations()
        
        return True
    except Exception as e:
        logger.error(f"Error creating database tables: {str(e)}")
        return False

def apply_migrations():
    """Apply schema migrations to existing tables with robust error handling."""
    logger.info("Applying schema migrations...")
    try:
        # Get database inspector
        inspector = inspect(engine)
        dialect = engine.dialect.name
        
        # Apply DB functions 
        apply_db_functions()
        
        # Check if sync_state table exists
        if not inspector.has_table('sync_state'):
            logger.info("sync_state table doesn't exist yet, will be created later")
            return True
        
        # Use a connection for applying migrations
        with engine.connect() as conn:
            # Get existing columns in sync_state table
            existing_columns = set(column['name'] for column in inspector.get_columns('sync_state'))
            
            # Define columns to add if they don't exist
            columns_to_add = {
                'last_updated_at': 'FLOAT',
                'last_error': 'TEXT', 
                'current_chainwork': 'VARCHAR(64)',
                'glyph_scan_height': 'INTEGER DEFAULT 0'
            }
            
            # Try to add columns in a database-agnostic way
            for column_name, column_type in columns_to_add.items():
                if column_name not in existing_columns:
                    try:
                        # SQLite doesn't support IF NOT EXISTS for columns
                        alter_statement = f"ALTER TABLE sync_state ADD COLUMN {column_name} {column_type}"
                        logger.info(f"Adding column {column_name} to sync_state table")
                        conn.execute(text(alter_statement))
                        conn.commit()
                    except Exception as e:
                        # If column already exists, this will catch the error
                        if 'duplicate column' in str(e).lower():
                            logger.info(f"Column {column_name} already exists in sync_state")
                        else:
                            logger.error(f"Error adding column {column_name}: {str(e)}")
                else:
                    logger.info(f"Column {column_name} already exists in sync_state")
            
            # Check for holders table
            if inspector.has_table('holders'):
                logger.info("Holders table exists, checking column types")
                
                # Reset the sync state to ensure we're not stuck
                current_time = time.time()
                try:
                    # Use NOW() function for proper timestamp conversion directly in SQL
                    reset_sql = """
                    UPDATE sync_state 
                    SET is_syncing = 0, 
                        last_updated_at = NOW(),
                        last_error = 'Reset during migration' 
                    WHERE is_syncing = 1
                    """
                    conn.execute(text(reset_sql))
                    conn.commit()
                    logger.info("Reset any stuck sync processes")
                except Exception as e:
                    logger.error(f"Error resetting sync state: {str(e)}")
            
            # Add enhanced models for NFTs, user profiles, containers, and analytics
            try:
                # Import migration module
                from src.db.migrations import add_enhanced_models
                
                # Run the migration
                logger.info("Applying enhanced models migration...")
                add_enhanced_models.migrate(conn)
                conn.commit()
                
                logger.info("Enhanced models migration applied successfully!")
            except Exception as e:
                logger.error(f"Error applying enhanced models migration: {str(e)}")
            
            # Log success
            logger.info("Database schema migrations completed successfully")
        
        return True
    except Exception as e:
        logger.error(f"Error applying schema migrations: {str(e)}")
        return False


def apply_db_functions():
    """Apply or update database functions for improved performance."""
    try:
        logger.info("Applying database functions...")
        db_functions_path = Path(__file__).resolve().parent / 'functions'
        
        # Check if functions directory exists
        if not db_functions_path.exists():
            logger.warning(f"Functions directory not found at {db_functions_path}")
            return False
        
        # Apply each SQL function file
        for sql_file in db_functions_path.glob('*.sql'):
            logger.info(f"Applying database function from {sql_file.name}")
            
            # Read the SQL file
            with open(sql_file, 'r') as f:
                sql = f.read()
            
            # Execute the SQL - we do this in a transaction
            with engine.begin() as conn:
                try:
                    conn.execute(text(sql))
                    logger.info(f"Successfully applied {sql_file.name}")
                except Exception as e:
                    logger.error(f"Error applying {sql_file.name}: {str(e)}")
        
        logger.info("Finished applying database functions")
        return True
    except Exception as e:
        logger.error(f"Error in apply_db_functions: {str(e)}")
        return False


if __name__ == "__main__":
    create_tables()
