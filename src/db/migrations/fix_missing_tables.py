# /Users/radiant/Desktop/RXinDexer/src/db/migrations/fix_missing_tables.py
# This script creates any missing tables in the database that are defined in the models
# but don't currently exist in the database schema.
# It supports both incremental fixes and clean rebuilds of the database schema.

import os
import logging
import sys
from pathlib import Path
from sqlalchemy import inspect, text, exc as sa_exc
import time
import importlib
import traceback

# Add parent directory to path for imports
parent_dir = Path(__file__).resolve().parent.parent.parent.parent
sys.path.append(str(parent_dir))

from src.models.database import Base, engine, get_db_context
from src.models import (
    UTXO, GlyphToken, Holder, SyncState, Block, Transaction,
    NFTMetadata, NFTCollection, NFTTransfer,
    UserProfile, Container, ContainerHistory,
    TimeSeriesMetric, RichList, TokenDistribution,
    MarketData, ActivityMetric
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def get_model_tables():
    """Get all table names defined in the models"""
    return {table.name: table for table in Base.metadata.tables.values()}

def get_db_tables():
    """Get all existing table names from the database"""
    inspector = inspect(engine)
    return set(inspector.get_table_names())

def fix_missing_tables(force_recreate=False):
    """
    Create any tables that are in the models but not in the database.
    
    Args:
        force_recreate: If True, will drop and recreate all tables for a clean rebuild
    """
    model_tables = get_model_tables()
    db_tables = get_db_tables()
    
    logger.info(f"Found {len(model_tables)} tables in models")
    logger.info(f"Found {len(db_tables)} tables in database")
    
    if force_recreate:
        # Drop all tables for a clean rebuild
        logger.warning("FORCE RECREATE mode active - dropping all existing tables")
        try:
            # Check database dialect to use appropriate commands
            dialect = engine.dialect.name
            
            # Disable foreign key checks based on the database type
            with engine.begin() as conn:
                if dialect == 'postgresql':
                    conn.execute(text("SET CONSTRAINTS ALL DEFERRED"))
                elif dialect == 'sqlite':
                    conn.execute(text("PRAGMA foreign_keys = OFF"))
                else:
                    logger.warning(f"Unknown dialect {dialect}, foreign key constraints may prevent table drops")
            
            # Drop tables in reverse dependency order
            Base.metadata.drop_all(bind=engine)
            logger.info("All tables dropped successfully")
            
            # Make sure we update the db_tables list after dropping
            db_tables = set()
        except Exception as e:
            logger.error(f"Error dropping tables: {str(e)}")
            logger.error(traceback.format_exc())
    
    # Find missing tables
    missing_tables = []
    for table_name, table_obj in model_tables.items():
        if table_name not in db_tables:
            missing_tables.append((table_name, table_obj))
    
    logger.info(f"Found {len(missing_tables)} missing tables: {[t[0] for t in missing_tables]}")
    
    # Create missing tables
    if force_recreate or missing_tables:
        logger.info("Creating tables...")
        try:
            # Create all tables at once
            Base.metadata.create_all(bind=engine)
            logger.info("All tables created successfully")
        except Exception as e:
            logger.error(f"Error creating all tables at once: {str(e)}")
            logger.error(traceback.format_exc())
            
            # Fall back to creating tables one by one
            logger.info("Falling back to creating tables one by one")
            for table_name, table_obj in model_tables.items():
                try:
                    logger.info(f"Creating table: {table_name}")
                    if not inspect(engine).has_table(table_name):
                        table_obj.create(engine)
                        logger.info(f"Successfully created table: {table_name}")
                except Exception as e:
                    logger.error(f"Error creating table {table_name}: {str(e)}")
                    logger.error(traceback.format_exc())

def verify_table_columns():
    """Verify that all table columns match between models and database"""
    model_tables = get_model_tables()
    inspector = inspect(engine)
    
    column_issues = []
    
    for table_name, table_obj in model_tables.items():
        # Skip if table doesn't exist
        if table_name not in inspector.get_table_names():
            continue
        
        # Get columns from model
        model_columns = {col.name: col for col in table_obj.columns}
        
        # Get columns from database
        db_columns = {col['name']: col for col in inspector.get_columns(table_name)}
        
        # Find missing columns
        for col_name in model_columns:
            if col_name not in db_columns:
                column_issues.append(f"Column '{col_name}' missing from table '{table_name}' in database")
    
    if column_issues:
        logger.warning("Found column inconsistencies:")
        for issue in column_issues:
            logger.warning(issue)
    else:
        logger.info("All table columns are consistent between models and database")
    
    return column_issues

def fix_missing_views():
    """Recreate any missing materialized views"""
    try:
        # Get database dialect to handle database-specific functionality
        dialect = engine.dialect.name
        
        if dialect == 'postgresql':
            # PostgreSQL supports materialized views
            with engine.connect() as conn:
                # Check if the view exists
                result = conn.execute(text("SELECT to_regclass('public.address_balances')")).scalar()
                
                if not result:
                    logger.info("Materialized view address_balances is missing, recreating...")
                    conn.execute(text("""
                    CREATE MATERIALIZED VIEW address_balances AS
                    SELECT address, SUM(amount) AS total_balance
                    FROM utxos
                    WHERE spent = false
                    GROUP BY address;
                    
                    CREATE INDEX idx_address_balances_address ON address_balances(address);
                    CREATE INDEX idx_address_balances_balance ON address_balances(total_balance DESC);
                    """))
                    logger.info("Successfully recreated materialized view address_balances")
        elif dialect == 'sqlite':
            # SQLite doesn't support materialized views, create a regular view instead
            with engine.connect() as conn:
                # Check if the view exists
                result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='view' AND name='address_balances'")).scalar()
                
                if not result:
                    logger.info("Creating regular view address_balances for SQLite...")
                    conn.execute(text("""
                    CREATE VIEW address_balances AS
                    SELECT address, SUM(amount) AS total_balance
                    FROM utxos
                    WHERE spent = 0
                    GROUP BY address;
                    """))
                    logger.info("Successfully created view address_balances")
        else:
            logger.warning(f"Unsupported dialect {dialect} for materialized views")
    except Exception as e:
        logger.error(f"Error fixing materialized views: {str(e)}")
        logger.error(traceback.format_exc())

def initialize_sync_state():
    """Initialize sync state if it doesn't exist"""
    from sqlalchemy.sql import func
    from datetime import datetime
    from src.models.sync_state import SyncState
    
    try:
        with get_db_context() as db:
            count = db.query(SyncState).count()
            if count == 0:
                logger.info("Creating initial sync state record")
                # Use datetime object instead of timestamp for better database compatibility
                sync_state = SyncState(
                    id=1,
                    current_height=0,
                    current_hash='',
                    is_syncing=0,
                    last_updated_at=datetime.now(),  # Use datetime object instead of timestamp
                    glyph_scan_height=0
                )
                db.add(sync_state)
                db.commit()
                logger.info("Initial sync state created")
    except Exception as e:
        logger.error(f"Error initializing sync state: {str(e)}")
        logger.error(traceback.format_exc())

def main(clean_rebuild=False):
    """
    Run the database schema fixes
    
    Args:
        clean_rebuild: If True, will drop and recreate all tables for a clean rebuild
    """
    logger.info("Starting database schema fix")
    logger.info(f"Mode: {'CLEAN REBUILD' if clean_rebuild else 'INCREMENTAL FIX'}")
    
    # Create missing tables (or recreate all if clean_rebuild=True)
    fix_missing_tables(force_recreate=clean_rebuild)
    
    # Verify column consistency
    column_issues = verify_table_columns()
    
    # Fix materialized views
    fix_missing_views()
    
    # Initialize sync state if needed
    initialize_sync_state()
    
    if column_issues and not clean_rebuild:
        logger.warning("Column inconsistencies found. Consider running with --clean-rebuild.")
    else:
        logger.info("Database schema fix completed successfully")
    
    return not bool(column_issues)

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Fix database schema inconsistencies")
    parser.add_argument("--clean-rebuild", action="store_true", help="Drop and recreate all tables for a clean rebuild")
    args = parser.parse_args()
    
    main(clean_rebuild=args.clean_rebuild)
