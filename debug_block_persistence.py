#!/usr/bin/env python3
# /Users/radiant/Desktop/RXinDexer/debug_block_persistence.py
# This script debugs block persistence issues by manually inserting a test block
# and verifying database connectivity and transaction commits.

import os
import sys
import logging
import json
from sqlalchemy import text
from pathlib import Path
from datetime import datetime

# Add parent dir to path for imports
sys.path.append(str(Path(__file__).resolve().parent))

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Import required modules
from src.models.database import engine, get_db
from src.sync.rpc_client import RadiantRPC

def verify_sync_state():
    """Verify the sync state table contains data"""
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT * FROM sync_state")).fetchone()
            if result:
                print("Sync state found:")
                print(f"  Current height: {result.current_height}")
                print(f"  Current hash: {result.current_hash}")
                print(f"  Is syncing: {result.is_syncing}")
                print(f"  Last updated: {result.last_updated_at}")
                return True
            else:
                print("No sync state record found!")
                return False
    except Exception as e:
        print(f"Error checking sync state: {e}")
        return False

def verify_block_persistence():
    """Check if blocks are actually being persisted to the database"""
    try:
        with engine.connect() as conn:
            block_count = conn.execute(text("SELECT COUNT(*) FROM blocks")).scalar()
            print(f"Total blocks in database: {block_count}")
            
            if block_count > 0:
                # Get the highest block
                highest_block = conn.execute(text(
                    "SELECT height, hash, timestamp FROM blocks ORDER BY height DESC LIMIT 1"
                )).fetchone()
                print(f"Highest block: {highest_block.height}, hash={highest_block.hash}")
            
            tx_count = conn.execute(text("SELECT COUNT(*) FROM transactions")).scalar()
            print(f"Total transactions in database: {tx_count}")
            
            return block_count > 0
    except Exception as e:
        print(f"Error checking block persistence: {e}")
        return False

def insert_test_block():
    """Try to manually insert a test block to verify database writes work"""
    test_block = {
        "hash": "test_block_hash_" + datetime.now().strftime("%Y%m%d%H%M%S"),
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
    
    print("Attempting to insert test block...")
    
    try:
        # Insert using SQLAlchemy Core with raw SQL for maximum compatibility
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
            print("Test block inserted successfully (Core transaction committed)")
            
        # Also try with ORM session (using the same approach as the indexer)
        db = next(get_db())
        try:
            db.execute(
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
                {
                    **test_block,
                    "hash": test_block["hash"] + "_orm"  # Make hash unique
                }
            )
            db.commit()
            print("Test block inserted successfully (ORM session committed)")
        except Exception as e:
            db.rollback()
            print(f"Error inserting test block with ORM: {e}")
        finally:
            db.close()
        
        # Verify the test blocks are present
        return verify_test_blocks([test_block["hash"], test_block["hash"] + "_orm"])
        
    except Exception as e:
        print(f"Error inserting test block: {e}")
        return False

def verify_test_blocks(hashes):
    """Verify that the test blocks are actually in the database"""
    try:
        with engine.connect() as conn:
            found_blocks = []
            for hash in hashes:
                result = conn.execute(
                    text("SELECT hash, height FROM blocks WHERE hash = :hash"),
                    {"hash": hash}
                ).fetchone()
                if result:
                    found_blocks.append(result.hash)
                    print(f"Found test block: {result.hash}, height={result.height}")
                else:
                    print(f"Test block {hash} NOT found in database!")
            
            return len(found_blocks) == len(hashes)
    except Exception as e:
        print(f"Error verifying test blocks: {e}")
        return False

def fix_sync_state():
    """Fix the sync state to match actual blocks in database"""
    try:
        with engine.connect() as conn:
            # Get the highest block height
            highest_block = conn.execute(text(
                "SELECT height, hash FROM blocks ORDER BY height DESC LIMIT 1"
            )).fetchone()
            
            if not highest_block:
                print("No blocks in database, can't fix sync state")
                return False
            
            # Update sync state
            conn.execute(
                text("""
                UPDATE sync_state
                SET current_height = :height,
                    current_hash = :hash,
                    last_updated_at = NOW()
                WHERE id = 1
                """),
                {"height": highest_block.height, "hash": highest_block.hash}
            )
            conn.commit()
            print(f"Sync state updated to match highest block: height={highest_block.height}")
            return True
    except Exception as e:
        print(f"Error fixing sync state: {e}")
        return False

def check_database_connectivity():
    """Verify database connectivity and transaction handling"""
    print("\n=== Database Connectivity Test ===")
    
    # Check simple connection
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1")).scalar()
            print(f"Database connection test: {'SUCCESS' if result == 1 else 'FAILED'}")
    except Exception as e:
        print(f"Database connection failed: {e}")
        return False
    
    # Check if tables exist
    try:
        with engine.connect() as conn:
            tables = ["blocks", "transactions", "sync_state", "utxos"]
            for table in tables:
                exists = conn.execute(text(
                    "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :table)"
                ), {"table": table}).scalar()
                print(f"Table '{table}' exists: {'YES' if exists else 'NO'}")
    except Exception as e:
        print(f"Error checking tables: {e}")
        
    return True

def extract_blocks_from_db():
    """Extract sample blocks from the database for verification"""
    try:
        # Export a few blocks for inspection
        with engine.connect() as conn:
            # Check if blocks table has rows
            count = conn.execute(text("SELECT COUNT(*) FROM blocks")).scalar()
            if count == 0:
                print("No blocks found in the database to extract.")
                return
                
            # Get some sample blocks
            blocks = conn.execute(text("""
                SELECT hash, height, prev_hash, merkle_root, timestamp
                FROM blocks
                ORDER BY height ASC
                LIMIT 5
            """)).fetchall()
            
            # Get the most recent blocks
            recent_blocks = conn.execute(text("""
                SELECT hash, height, prev_hash, merkle_root, timestamp
                FROM blocks
                ORDER BY height DESC
                LIMIT 5
            """)).fetchall()
            
            # Combine results for display
            all_samples = blocks + recent_blocks
            
            print(f"\nSample blocks from database ({len(all_samples)}):")
            for block in all_samples:
                print(f"  Block {block.height}: hash={block.hash[:10]}..., prev={block.prev_hash[:10]}...")
                
    except Exception as e:
        print(f"Error extracting blocks: {e}")

def main():
    """Main function to run all checks and fixes"""
    print("\n======= RXinDexer Block Persistence Debug Tool =======")
    print(f"Running at: {datetime.now()}")
    
    # Check database connectivity first
    if not check_database_connectivity():
        print("⚠️ Database connectivity issues detected. Stopping.")
        return False
        
    # Check current state
    print("\n=== Current Database State ===")
    sync_state_ok = verify_sync_state()
    blocks_ok = verify_block_persistence()
    
    if blocks_ok:
        extract_blocks_from_db()
        print("✅ Blocks found in database, system appears functional.")
        return True
        
    # If we get here, we have a problem with block persistence
    print("\n⚠️ Problem detected: blocks are not being properly persisted!")
    
    # Test inserting a block manually
    print("\n=== Testing Block Insertion ===")
    insert_success = insert_test_block()
    
    if insert_success:
        print("\n✅ Manual block insertion succeeded!")
        print("The issue appears to be with the indexer's block parsing/saving logic, not the database itself.")
        
        # Try to fix sync state if needed
        if sync_state_ok and not blocks_ok:
            print("\nAttempting to fix sync state to match actual blocks...")
            fix_sync_state()
    else:
        print("\n❌ Manual block insertion failed.")
        print("This indicates a deeper database connectivity or transaction issue.")
    
    return True

if __name__ == "__main__":
    success = main()
    print("\n======= Debug Complete =======")
    sys.exit(0 if success else 1)
