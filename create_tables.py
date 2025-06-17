#!/usr/bin/env python3
# /Users/radiant/Desktop/RXinDexer/create_tables.py
# This script directly creates all required database tables for RXinDexer using SQLAlchemy metadata.
# It ensures that all tables are properly registered and created before creating indices and materialized views.

import os
import sys
import time
import psycopg2
import traceback
from sqlalchemy import create_engine, text, inspect, Table, MetaData
from sqlalchemy.schema import CreateTable, CreateIndex
from sqlalchemy.exc import ProgrammingError, IntegrityError
from sqlalchemy.exc import SQLAlchemyError, ProgrammingError
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def wait_for_database(db_url, max_attempts=30, retry_interval=1):
    """Wait for the database to be ready"""
    print(f"Waiting for database to be ready at {db_url}...")
    attempts = 0
    while attempts < max_attempts:
        try:
            engine = create_engine(db_url)
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            print("Database is ready!")
            return engine
        except Exception as e:
            attempts += 1
            print(f"Database not ready (attempt {attempts}/{max_attempts}): {str(e)}")
            time.sleep(retry_interval)
    
    raise RuntimeError(f"Database not available after {max_attempts} attempts")


def check_table_exists(connection, table_name):
    """Check if a table exists using raw SQL"""
    try:
        result = connection.execute(text(
            f"SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = '{table_name}')"
        )).scalar()
        return result
    except Exception as e:
        print(f"Error checking if table {table_name} exists: {e}")
        return False


def reset_connection(engine):
    """Create a fresh connection that's not in a failed transaction state"""
    try:
        # Directly create a new connection
        return engine.connect()
    except Exception as e:
        print(f"Error creating new connection: {e}")
        return None


def create_utxo_table(engine):
    """Create UTXO table directly if it doesn't exist"""
    try:
        # Always get a fresh connection to avoid transaction state issues
        inspector = inspect(engine)
        if 'utxos' in inspector.get_table_names():
            print("UTXO table already exists")
            return True

        print("Creating UTXO table directly with SQL...")
        
        # Use a fresh connection in a new transaction
        with engine.begin() as conn:
            # First try to create the table
            conn.execute(text("""
            CREATE TABLE IF NOT EXISTS utxos (
                id SERIAL PRIMARY KEY,
                txid VARCHAR(64) NOT NULL,
                vout INTEGER NOT NULL,
                block_height INTEGER,
                block_hash VARCHAR(64),
                block_time TIMESTAMP,
                address VARCHAR(64),
                script_pubkey TEXT,
                amount NUMERIC(20, 8),
                spent BOOLEAN DEFAULT FALSE,
                spent_txid VARCHAR(64),
                spent_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                token_ref VARCHAR(255),
                UNIQUE(txid, vout)
            )
            """))
            
            print("Created UTXO table directly with SQL")
        
        # For indexes, use separate connections to avoid transaction abort issues
        with engine.begin() as conn:
            try:
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_utxos_address ON utxos (address)"))
                print("Created index ix_utxos_address")
            except Exception as e:
                print(f"Index creation warning (non-fatal): {e}")
        
        with engine.begin() as conn:
            try:
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_utxos_block_height ON utxos (block_height)"))
                print("Created index ix_utxos_block_height")
            except Exception as e:
                print(f"Index creation warning (non-fatal): {e}")
        
        with engine.begin() as conn:
            try:
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_utxos_spent ON utxos (spent)"))
                print("Created index ix_utxos_spent")
            except Exception as e:
                print(f"Index creation warning (non-fatal): {e}")
        
        with engine.begin() as conn:
            try:
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_utxos_token_ref ON utxos (token_ref)"))
                print("Created index ix_utxos_token_ref")
            except Exception as e:
                print(f"Index creation warning (non-fatal): {e}")
                
        # Verify the table exists now
        inspector = inspect(engine)
        if 'utxos' in inspector.get_table_names():
            print("UTXO table verification successful!")
            return True
        else:
            print("ERROR: UTXO table still not found after creation attempt!")
            return False
    except Exception as e:
        print(f"Error creating UTXO table: {e}")
        print(traceback.format_exc())
        return False


def create_sync_state_table(engine):
    """Create sync_state table directly if it doesn't exist"""
    try:
        # Check if table exists with fresh inspector
        inspector = inspect(engine)
        if 'sync_state' in inspector.get_table_names():
            print("sync_state table already exists")
            return True

        print("Creating sync_state table directly with SQL...")
        # Create table in a separate transaction
        with engine.begin() as conn:
            conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sync_state (
                id SERIAL PRIMARY KEY,
                current_height INTEGER,
                target_height INTEGER,
                sync_start_time TIMESTAMP,
                sync_end_time TIMESTAMP,
                status VARCHAR(32),
                progress FLOAT,
                continuous_mode BOOLEAN DEFAULT FALSE,
                is_syncing BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """))
            
            print("Created sync_state table directly with SQL")
        
        # Insert default row in a separate transaction
        try:
            with engine.begin() as conn:
                # First check if we already have a row
                result = conn.execute(text("SELECT COUNT(*) FROM sync_state")).scalar()
                if result == 0:
                    conn.execute(text("""
                    INSERT INTO sync_state (current_height, target_height, status, progress, continuous_mode, is_syncing)
                    VALUES (0, 0, 'idle', 0.0, FALSE, FALSE)
                    """))
                    print("Inserted default sync_state row")
        except Exception as e:
            print(f"Warning: Could not insert default sync_state row: {e}")
        
        return True
    except Exception as e:
        print(f"Error creating sync_state table: {e}")
        print(traceback.format_exc())
        return False


def safe_create_table(engine, table):
    """Create a single table safely with explicit DDL control"""
    table_name = table.name
    try:
        # Check if table exists
        inspector = inspect(engine)
        exists = table_name in inspector.get_table_names()
        
        # If table exists, just return success
        if exists:
            print(f"Table {table_name} already exists")
            return True
            
        # Create table without indexes first
        with engine.begin() as conn:
            # Create the table DDL statement
            create_table_stmt = CreateTable(table)
            # Execute create table
            print(f"Creating table {table_name}...")
            conn.execute(create_table_stmt)
            print(f"Table {table_name} created successfully")
            
            # Create indexes one by one with error handling
            for index in table.indexes:
                try:
                    # Create the index DDL statement
                    create_index_stmt = CreateIndex(index)
                    print(f"Creating index {index.name} on {table_name}...")
                    conn.execute(create_index_stmt)
                    print(f"Index {index.name} created successfully")
                except ProgrammingError as e:
                    # If index already exists, just continue
                    if 'already exists' in str(e):
                        print(f"Index {index.name} already exists, skipping")
                        continue
                    else:
                        print(f"Error creating index {index.name}: {e}")
                        # Continue with other indexes even if one fails
        return True
    except ProgrammingError as e:
        if 'already exists' in str(e):
            print(f"Table {table_name} was created by another process, continuing")
            return True
        else:
            print(f"Error creating table {table_name}: {e}")
            return False
    except Exception as e:
        print(f"Error creating table {table_name}: {e}")
        print(traceback.format_exc())
        return False


def create_all_tables(engine):
    """Create all tables directly using SQLAlchemy metadata"""
    print("Creating all tables using SQLAlchemy metadata...")
    
    # Import all models to ensure they're registered with Base.metadata
    from src.models.database import Base
    from src.models.sync_state import SyncState
    from src.models.block import Block
    from src.models.transaction import Transaction
    from src.models.utxo import UTXO
    from src.models.glyph_token import GlyphToken
    
    # First, make sure all models are imported and registered
    all_tables = Base.metadata.sorted_tables
    print(f"Found {len(all_tables)} tables in metadata")
    
    # Use inspect to really check what tables exist
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    print(f"Found {len(existing_tables)} existing tables in database")
    
    # Option to drop all tables to ensure a fresh state (only in development)
    if os.environ.get('ENVIRONMENT') != 'production' and os.environ.get('RESET_DB') == 'true':
        print("WARNING: Dropping all existing tables as RESET_DB is true")
        try:
            with engine.begin() as conn:
                # Drop in reverse order to handle dependencies
                conn.execute(text("DROP SCHEMA public CASCADE"))
                conn.execute(text("CREATE SCHEMA public"))
            print("All tables dropped by recreating schema")
        except Exception as e:
            print(f"Error dropping schema: {e}")
            print("Falling back to dropping individual tables...")
            try:
                Base.metadata.drop_all(engine)
                print("All tables dropped using SQLAlchemy")
            except Exception as e:
                print(f"Error dropping tables: {e}")
        
        # Refresh our view of existing tables
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
    
    # Create tables one by one in dependency order
    tables_created = []
    
    # Track if we succeeded in creating the utxos table
    utxos_table_created = False
    
    # First handle core tables that other tables might depend on
    critical_tables = ['blocks', 'transactions', 'sync_state', 'utxos']
    remaining_tables = []
    
    for table in all_tables:
        if table.name in critical_tables:
            success = safe_create_table(engine, table)
            if success:
                tables_created.append(table.name)
                if table.name == 'utxos':
                    utxos_table_created = True
        else:
            remaining_tables.append(table)
    
    # Now create the remaining tables
    for table in remaining_tables:
        success = safe_create_table(engine, table)
        if success:
            tables_created.append(table.name)
    
    # Verify all necessary tables were created
    print(f"Created {len(tables_created)} tables: {', '.join(tables_created) if tables_created else 'none'}")
    
    # Special handling for critical tables
    inspector = inspect(engine)
    
    # Check and create the utxos table if it's missing
    if 'utxos' not in inspector.get_table_names():
        print("WARNING: UTXO table was not created successfully. Using direct creation...")
        utxos_table_created = create_utxo_table(engine)
    else:
        print("UTXO table exists")
        utxos_table_created = True
    
    # Check and create the sync_state table if it's missing
    if 'sync_state' not in inspector.get_table_names():
        print("WARNING: sync_state table was not created. Using direct creation...")
        create_sync_state_table(engine)
    
    # After tables are created, create materialized view in its own transaction
    try:
        # Check if utxos table exists now with a fresh inspector
        inspector = inspect(engine)
        if 'utxos' in inspector.get_table_names():
            # First drop if it exists to refresh - in its own transaction
            with engine.begin() as conn:
                try:
                    conn.execute(text("DROP MATERIALIZED VIEW IF EXISTS address_balances CASCADE"))
                    print("Dropped existing address_balances materialized view")
                except Exception as e:
                    print(f"Warning when dropping view (non-fatal): {e}")
            
            # Create the view - in its own transaction
            with engine.begin() as conn:
                try:
                    conn.execute(text("""
                        CREATE MATERIALIZED VIEW address_balances AS
                        SELECT address, SUM(amount) AS total_balance
                        FROM utxos
                        WHERE spent = FALSE
                        GROUP BY address
                        ORDER BY total_balance DESC
                    """))
                    print("Created address_balances materialized view")
                except Exception as e:
                    print(f"Error creating materialized view: {e}")
                    print(traceback.format_exc())
            
            # Create index on the view - in its own transaction
            with engine.begin() as conn:
                try:
                    conn.execute(text("""
                        CREATE UNIQUE INDEX IF NOT EXISTS ix_address_balances_address ON address_balances (address)
                    """))
                    print("Created index on address_balances materialized view")
                    print("Successfully completed address_balances materialized view creation")
                except Exception as e:
                    print(f"Warning when creating index (non-fatal): {e}")
        else:
            print("Skipping materialized view creation as 'utxos' table does not exist")
    except Exception as e:
        print(f"Error in materialized view creation process: {e}")
        print(traceback.format_exc())

def main():
    db_url = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/rxindexer')
    
    try:
        engine = wait_for_database(db_url)
        create_all_tables(engine)
        print("Database initialization complete!")
    except Exception as e:
        print(f"Fatal error during database initialization: {e}")
        print(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main()
