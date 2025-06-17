#!/usr/bin/env python
# /Users/radiant/Desktop/RXinDexer/fix_database_tables.py
# This script ensures that the database tables are properly created and indexed.

import subprocess
import logging
import time
import sys

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def create_schema_script():
    """Create a SQL script to properly initialize the database schema"""
    schema = """
-- Drop existing tables if they exist
DROP TABLE IF EXISTS transactions CASCADE;
DROP TABLE IF EXISTS blocks CASCADE;
DROP TABLE IF EXISTS utxos CASCADE;
DROP TABLE IF EXISTS holders CASCADE;
DROP TABLE IF EXISTS tokens CASCADE;
DROP TABLE IF EXISTS token_balances CASCADE;
DROP TABLE IF EXISTS sync_state CASCADE;

-- Create blocks table
CREATE TABLE blocks (
    hash VARCHAR(64) PRIMARY KEY,
    height INTEGER NOT NULL,
    version INTEGER NOT NULL,
    prev_hash VARCHAR(64),
    merkle_root VARCHAR(64) NOT NULL,
    timestamp INTEGER NOT NULL,
    bits VARCHAR(16) NOT NULL,
    nonce BIGINT NOT NULL,
    chainwork VARCHAR(64),
    size INTEGER,
    weight INTEGER,
    tx_count INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Create indexes for blocks table
CREATE INDEX idx_blocks_height ON blocks(height);
CREATE INDEX idx_blocks_prev_hash ON blocks(prev_hash);
CREATE INDEX idx_blocks_timestamp ON blocks(timestamp);

-- Create transactions table
CREATE TABLE transactions (
    tx_id VARCHAR(64) PRIMARY KEY,
    block_hash VARCHAR(64) REFERENCES blocks(hash),
    block_height INTEGER NOT NULL,
    hex_data TEXT,
    size INTEGER,
    weight INTEGER,
    version INTEGER NOT NULL,
    locktime INTEGER NOT NULL,
    timestamp INTEGER NOT NULL,
    inputs TEXT NOT NULL,
    outputs TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Create indexes for transactions table
CREATE INDEX idx_transactions_block_hash ON transactions(block_hash);
CREATE INDEX idx_transactions_block_height ON transactions(block_height);

-- Create sync_state table
CREATE TABLE sync_state (
    id INTEGER PRIMARY KEY,
    current_height INTEGER DEFAULT 0,
    current_hash TEXT,
    current_chainwork TEXT,
    is_syncing INTEGER DEFAULT 0,
    last_error TEXT,
    last_updated_at TIMESTAMP,
    glyph_scan_height INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Insert default sync state
INSERT INTO sync_state (id, current_height, is_syncing, glyph_scan_height)
VALUES (1, 0, 1, 0);

-- Create utxos table for tracking unspent transaction outputs
CREATE TABLE utxos (
    id SERIAL PRIMARY KEY,
    tx_id VARCHAR(64) NOT NULL,
    vout INTEGER NOT NULL,
    address VARCHAR(64),
    script_pubkey TEXT NOT NULL,
    amount NUMERIC(20, 8) NOT NULL,
    spent BOOLEAN DEFAULT FALSE,
    spent_by_tx VARCHAR(64),
    block_height INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT utxo_unique_constraint UNIQUE(tx_id, vout)
);

-- Create indexes for utxos table
CREATE INDEX idx_utxos_tx_id ON utxos(tx_id);
CREATE INDEX idx_utxos_address ON utxos(address);
CREATE INDEX idx_utxos_spent ON utxos(spent);
CREATE INDEX idx_utxos_spent_by_tx ON utxos(spent_by_tx);

-- Create holders table for tracking address balances
CREATE TABLE holders (
    address VARCHAR(64) PRIMARY KEY,
    balance NUMERIC(20, 8) NOT NULL DEFAULT 0,
    tx_count INTEGER NOT NULL DEFAULT 0,
    first_seen_height INTEGER,
    last_seen_height INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
"""
    return schema

def fix_database_tables():
    """Fix the database tables to ensure proper schema"""
    try:
        # Stop the indexer service first
        subprocess.run(["docker", "stop", "rxindexer-indexer"], check=True)
        logger.info("Stopped indexer container")
        
        # Create a temporary file for the schema script
        with open("/tmp/schema.sql", "w") as f:
            f.write(create_schema_script())
        
        # Copy schema script to the database container
        subprocess.run(["docker", "cp", "/tmp/schema.sql", "rxindexer-db:/tmp/schema.sql"], check=True)
        logger.info("Schema script created and copied to database container")
        
        # Execute schema script
        subprocess.run(["docker", "exec", "rxindexer-db", "psql", "-U", "postgres", "-d", "rxindexer", "-f", "/tmp/schema.sql"], check=True)
        logger.info("Database schema created successfully")
        
        # Fix the database initialization script inside the indexer container
        model_initialization = """#!/usr/bin/env python
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
from src.models.block import Block
from src.models.transaction import Transaction
from src.models.utxo import UTXO
from src.models.sync_state import SyncState
from src.models.holder import Holder

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
            sync_state = db.query(SyncState).first()
            if not sync_state:
                logger.info("Creating initial sync state")
                sync_state = SyncState(
                    id=1,
                    current_height=0,
                    is_syncing=1,
                    glyph_scan_height=0
                )
                db.add(sync_state)
                db.commit()
        
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
"""
        
        # Create temporary initialization script
        with open("/tmp/init_db.py", "w") as f:
            f.write(model_initialization)
        
        # Copy initialization script to the indexer container
        subprocess.run(["docker", "cp", "/tmp/init_db.py", "rxindexer-indexer:/app/init_db.py"], check=True)
        logger.info("Initialization script created and copied to indexer container")
        
        # Make the script executable
        subprocess.run(["docker", "exec", "rxindexer-indexer", "chmod", "+x", "/app/init_db.py"], check=True)
        
        # Execute the script to create models
        subprocess.run(["docker", "exec", "rxindexer-indexer", "python", "/app/init_db.py"], check=True)
        logger.info("Database models initialized successfully")
        
        # Start the indexer
        subprocess.run(["docker", "start", "rxindexer-indexer"], check=True)
        logger.info("Started indexer container")
        
        return True
    except Exception as e:
        logger.error(f"Failed to fix database tables: {e}")
        return False

def monitor_sync_progress():
    """Monitor the sync progress to verify blocks are being processed"""
    try:
        time.sleep(30)  # Wait for indexer to initialize
        
        # Check for recent log entries
        cmd = "docker exec rxindexer-indexer tail -n 40 /app/logs/indexer.log"
        logs = subprocess.check_output(cmd, shell=True).decode().strip()
        logger.info(f"Recent logs:\n{logs}")
        
        # Check if blocks are being inserted
        cmd = "docker exec rxindexer-db psql -U postgres -d rxindexer -c \"SELECT COUNT(*) FROM blocks\""
        try:
            block_count = subprocess.check_output(cmd, shell=True).decode().strip()
            logger.info(f"Current block count: {block_count}")
        except Exception:
            logger.warning("Could not check block count - table may not exist yet")
        
        # Check sync state
        cmd = "docker exec rxindexer-db psql -U postgres -d rxindexer -c \"SELECT * FROM sync_state\""
        try:
            sync_state = subprocess.check_output(cmd, shell=True).decode().strip()
            logger.info(f"Current sync state: {sync_state}")
        except Exception:
            logger.warning("Could not check sync state - table may not exist yet")
        
        return True
    except Exception as e:
        logger.error(f"Error monitoring sync progress: {e}")
        return False

if __name__ == "__main__":
    print("Starting to fix database tables...")
    
    # Fix database tables
    success = fix_database_tables()
    if success:
        print("Database tables fixed successfully")
    else:
        print("Failed to fix database tables")
        sys.exit(1)
    
    # Wait for services to restart
    print("Waiting 10 seconds for services to start...")
    time.sleep(10)
    
    # Monitor sync progress
    print("Monitoring sync progress (this will take about 30 seconds)...")
    monitor_sync_progress()
    
    print("\nDatabase table fix completed.")
    print("\nNext steps:")
    print("1. Monitor indexer logs: docker exec rxindexer-indexer tail -f /app/logs/indexer.log")
    print("2. Check database for blocks: docker exec rxindexer-db psql -U postgres -d rxindexer -c \"SELECT COUNT(*) FROM blocks\"")
    print("3. Once blocks are being indexed, test API endpoints: curl http://localhost:8000/api/v1/blocks/latest")
