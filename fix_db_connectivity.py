#!/usr/bin/env python3
# /Users/radiant/Desktop/RXinDexer/fix_db_connectivity.py
# This script fixes database connectivity issues by ensuring blocks are properly persisted
# and fixing any inconsistencies between sync_state and actual block data.

import os
import sys
import logging
import time
from pathlib import Path
from datetime import datetime

# Add the project root to the path
root_dir = Path(__file__).parent.absolute()
sys.path.append(str(root_dir))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Force PostgreSQL connection for consistent behavior with Docker environment
os.environ["DATABASE_URL"] = "postgresql://postgres:postgres@localhost:5432/rxindexer"
os.environ["DB_TYPE"] = "postgresql"

# Import project modules after setting environment variables
from sqlalchemy import text, inspect
from src.models.database import engine, get_db
from src.sync.rpc_client import RadiantRPC
from src.parser.block_parser import BlockParser

def check_docker_environment():
    """Check if we're running inside Docker and adjust database connection accordingly"""
    if os.path.exists("/.dockerenv"):
        logger.info("Running inside Docker container")
        # Inside Docker, use the container name as hostname
        os.environ["DATABASE_URL"] = "postgresql://postgres:postgres@db:5432/rxindexer"
    else:
        logger.info("Running outside Docker container")
        # Local execution connects to localhost
        os.environ["DATABASE_URL"] = "postgresql://postgres:postgres@localhost:5432/rxindexer"
    
    logger.info(f"Using database URL: {os.environ['DATABASE_URL']}")

def check_database_connectivity():
    """Check if we can connect to the database"""
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1")).scalar()
            if result == 1:
                logger.info("Database connection successful")
                return True
            else:
                logger.error("Database connection test failed")
                return False
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        return False

def check_table_status():
    """Check if required tables exist"""
    try:
        inspector = inspect(engine)
        required_tables = ["blocks", "transactions", "sync_state", "utxos"]
        missing_tables = []
        
        for table in required_tables:
            if not inspector.has_table(table):
                missing_tables.append(table)
        
        if missing_tables:
            logger.warning(f"Missing tables: {', '.join(missing_tables)}")
            return False
        
        logger.info("All required tables exist")
        return True
    except Exception as e:
        logger.error(f"Error checking tables: {e}")
        return False

def check_sync_state():
    """Check the current sync state"""
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT * FROM sync_state LIMIT 1")).fetchone()
            if not result:
                logger.warning("No sync_state record found")
                return None
            
            logger.info(f"Current sync state: height={result.current_height}, "
                        f"is_syncing={result.is_syncing}")
            return result
    except Exception as e:
        logger.error(f"Error checking sync state: {e}")
        return None

def check_block_count():
    """Check how many blocks are stored in the database"""
    try:
        with engine.connect() as conn:
            block_count = conn.execute(text("SELECT COUNT(*) FROM blocks")).scalar()
            transaction_count = conn.execute(text("SELECT COUNT(*) FROM transactions")).scalar()
            
            logger.info(f"Found {block_count} blocks and {transaction_count} transactions in database")
            
            if block_count > 0:
                # Get highest block
                highest = conn.execute(
                    text("SELECT height, hash FROM blocks ORDER BY height DESC LIMIT 1")
                ).fetchone()
                logger.info(f"Highest block in database: height={highest.height}, hash={highest.hash}")
            
            return block_count
    except Exception as e:
        logger.error(f"Error checking block count: {e}")
        return 0

def fix_sync_state_mismatch(block_count, sync_state):
    """Fix mismatch between sync_state and actual blocks"""
    if not sync_state:
        logger.error("No sync_state to fix")
        return False
    
    try:
        # If we have blocks but sync_state shows higher, we need to reset sync_state
        if block_count == 0 and sync_state.current_height > 0:
            logger.warning(f"Sync state shows height {sync_state.current_height} but no blocks in database")
            logger.info("Resetting sync_state to match actual database state")
            
            with engine.begin() as conn:
                conn.execute(
                    text("""
                    UPDATE sync_state 
                    SET current_height = 0, 
                        current_hash = '', 
                        is_syncing = 0, 
                        last_updated_at = NOW() 
                    WHERE id = 1
                    """)
                )
            logger.info("Sync state reset to height 0")
            return True
        
        # If we have blocks but sync_state doesn't match highest block
        elif block_count > 0:
            with engine.connect() as conn:
                highest = conn.execute(
                    text("SELECT height, hash FROM blocks ORDER BY height DESC LIMIT 1")
                ).fetchone()
                
                if highest.height != sync_state.current_height:
                    logger.warning(
                        f"Mismatch: sync_state.height={sync_state.current_height}, "
                        f"highest block.height={highest.height}"
                    )
                    
                    with engine.begin() as conn:
                        conn.execute(
                            text("""
                            UPDATE sync_state 
                            SET current_height = :height, 
                                current_hash = :hash, 
                                last_updated_at = NOW() 
                            WHERE id = 1
                            """),
                            {"height": highest.height, "hash": highest.hash}
                        )
                    logger.info(f"Sync state updated to match highest block: height={highest.height}")
                    return True
        
        return False
    except Exception as e:
        logger.error(f"Error fixing sync state mismatch: {e}")
        return False

def test_block_insertion():
    """Test inserting a block to verify database writes work"""
    try:
        # Create test block data
        test_hash = f"test_block_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        test_block = {
            "hash": test_hash,
            "height": 999999,
            "prev_hash": "test_prev_hash",
            "merkle_root": "test_merkle_root",
            "timestamp": int(datetime.now().timestamp()),
            "nonce": 12345,
            "bits": "1d00ffff",
            "version": 1,
            "size": 1000,
            "weight": 4000,
            "tx_count": 1
        }
        
        logger.info("Testing block insertion...")
        
        # Try inserting using direct SQL
        with engine.begin() as conn:
            conn.execute(
                text("""
                INSERT INTO blocks (
                    hash, height, prev_hash, merkle_root, timestamp, nonce,
                    bits, version, size, weight, tx_count, created_at, updated_at
                ) VALUES (
                    :hash, :height, :prev_hash, :merkle_root, :timestamp, :nonce,
                    :bits, :version, :size, :weight, :tx_count, NOW(), NOW()
                )
                ON CONFLICT (hash) DO NOTHING
                """),
                test_block
            )
        
        # Verify block was inserted
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT * FROM blocks WHERE hash = :hash"),
                {"hash": test_hash}
            ).fetchone()
            
            if result:
                logger.info("Test block insertion successful")
                
                # Clean up test block
                with engine.begin() as conn:
                    conn.execute(
                        text("DELETE FROM blocks WHERE hash = :hash"),
                        {"hash": test_hash}
                    )
                logger.info("Test block deleted")
                return True
            else:
                logger.error("Test block not found after insertion")
                return False
    except Exception as e:
        logger.error(f"Error testing block insertion: {e}")
        return False

def resync_blocks_from_node():
    """Force resyncing some recent blocks from the node to fix database"""
    try:
        # Create RPC client
        rpc = RadiantRPC()
        
        # Get current block count from node
        node_height = rpc.get_block_count()
        logger.info(f"Current node height: {node_height}")
        
        # Set up a database session
        db = next(get_db())
        
        # Create block parser
        parser = BlockParser(rpc, db)
        
        # Sync a few recent blocks to restore consistency
        start_height = max(0, node_height - 10)
        end_height = node_height
        
        logger.info(f"Re-syncing blocks from {start_height} to {end_height}")
        
        for height in range(start_height, end_height + 1):
            try:
                # Get block hash
                block_hash = rpc.get_block_hash(height)
                
                # Get full block data
                block_data = rpc.get_block(block_hash, verbose=True)
                
                # Parse and store block
                parser.parse_block(block_data, height, block_hash)
                
                logger.info(f"Successfully re-synced block {height}")
                
                # Small delay to avoid overloading node
                time.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Error syncing block {height}: {e}")
        
        # Verify blocks were inserted
        block_count = check_block_count()
        
        return block_count > 0
        
    except Exception as e:
        logger.error(f"Error re-syncing blocks: {e}")
        return False

def main():
    """Main function to fix database connectivity issues"""
    logger.info("=== RXinDexer Database Connectivity Fix ===")
    
    # Check environment
    check_docker_environment()
    
    # Check database connectivity
    if not check_database_connectivity():
        logger.error("Cannot connect to database. Please check database service is running.")
        return False
    
    # Check table status
    if not check_table_status():
        logger.error("Required tables are missing. Please run database initialization first.")
        return False
    
    # Check current state
    sync_state = check_sync_state()
    block_count = check_block_count()
    
    # Test block insertion
    if not test_block_insertion():
        logger.error("Block insertion test failed. Database write issue detected.")
        return False
    
    logger.info("Block insertion test passed. Database writes are working.")
    
    # Fix sync state mismatch if needed
    if block_count == 0 and (sync_state and sync_state.current_height > 0):
        logger.warning("Detected sync state / block data mismatch.")
        fix_sync_state_mismatch(block_count, sync_state)
    
    # If no blocks, try to resync some from the node
    if block_count == 0:
        logger.info("No blocks found in database. Attempting to resync from node.")
        if resync_blocks_from_node():
            logger.info("Successfully resynced blocks from node.")
        else:
            logger.error("Failed to resync blocks from node.")
    
    logger.info("=== Database Connectivity Fix Complete ===")
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
