# /Users/radiant/Desktop/RXinDexer/src/db/migrations/add_enhanced_models_fixed.py
# This file creates a migration to add the enhanced models to the database.
# It includes NFT, user profile, container, and analytics tables.

from sqlalchemy import Column, String, Integer, ForeignKey, Boolean, DateTime, Index, UniqueConstraint, Table, text, inspect
from sqlalchemy.sql import func
import logging

logger = logging.getLogger(__name__)

from src.models.database import Base


def run_migration(engine):
    """
    Apply the migration to add enhanced models to the database.
    
    Args:
        engine: SQLAlchemy engine instance
    """
    inspector = inspect(engine)
    
    # List of tables to create by category
    nft_tables = ['nft_metadata', 'nft_collections', 'nft_transfers']
    user_tables = ['user_profiles', 'user_addresses', 'containers', 'container_contents', 'container_history']
    analytics_tables = ['time_series_metrics', 'rich_lists', 'token_distributions', 'market_data', 'activity_metrics']
    
    # Create only tables that don't already exist
    def create_tables_if_needed(table_list, category_name):
        tables_to_create = []
        for table_name in table_list:
            if not inspector.has_table(table_name):
                if table_name in Base.metadata.tables:
                    tables_to_create.append(Base.metadata.tables[table_name])
                    logger.info(f"Will create table {table_name}")
                else:
                    logger.warning(f"Table {table_name} defined in migration but not in models")
        
        if tables_to_create:
            logger.info(f"Creating {len(tables_to_create)} new {category_name} tables")
            Base.metadata.create_all(engine, tables=tables_to_create)
        else:
            logger.info(f"No new {category_name} tables to create")
    
    # Create tables by category
    create_tables_if_needed(nft_tables, "NFT")
    create_tables_if_needed(user_tables, "user and container")
    create_tables_if_needed(analytics_tables, "analytics")
    
    logger.info("Enhanced models migration completed successfully")
    
    # Add indexes for optimizing queries - only if using PostgreSQL
    try:
        if engine.dialect.name == 'postgresql':
            with engine.connect() as conn:
                # NFT indexes
                conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_nft_owner ON nft_metadata (owner_address);
                """))
                
                conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_nft_creator ON nft_metadata (creator_address);
                """))
                
                conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_nft_collection ON nft_metadata (collection_id);
                """))
                
                # NFT transfer indexes
                conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_nft_transfer_token ON nft_transfers (token_id);
                """))
                
                conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_nft_transfer_from ON nft_transfers (from_address);
                """))
                
                conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_nft_transfer_to ON nft_transfers (to_address);
                """))
                
                # User profile indexes
                conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_user_username ON user_profiles (username);
                """))
                
                # Container indexes
                conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_container_owner ON containers (owner_address);
                """))
                
                conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_container_type ON containers (container_type);
                """))
                
                # Analytics indexes
                conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_timeseries_type ON time_series_metrics (metric_type, timestamp);
                """))
                
                conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_richlist_token ON rich_lists (token_type, token_id, timestamp);
                """))
                
                logger.info("Created database indexes for enhanced models")
        else:
            logger.info("Skipping explicit index creation for non-PostgreSQL database")
    except Exception as e:
        logger.warning(f"Error creating indexes (non-critical): {str(e)}")


def migrate(conn):
    """
    Apply the migration using a SQLAlchemy connection.
    
    Args:
        conn: SQLAlchemy connection
    """
    # Get the engine from the connection
    engine = conn.engine
    
    # Run the migration
    run_migration(engine)
    
    return True
