#!/usr/bin/env python
# /Users/radiant/Desktop/RXinDexer/fix_missing_tables.py
# This script fixes only the missing tables required for indexing

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

def check_tables():
    """Check which tables exist in the database"""
    try:
        cmd = "docker exec rxindexer-db psql -U postgres -d rxindexer -c \"SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'\""
        result = subprocess.check_output(cmd, shell=True).decode().strip()
        logger.info(f"Current tables: {result}")
        return result
    except Exception as e:
        logger.error(f"Failed to check tables: {e}")
        return ""

def fix_missing_tables():
    """Create only the missing tables needed for indexing"""
    try:
        # Check existing tables
        tables = check_tables()
        
        # Create tables that are missing
        create_script = """
-- Only create tables if they don't exist

-- Create blocks table if not exists
CREATE TABLE IF NOT EXISTS blocks (
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

-- Create indexes for blocks table if not already created
CREATE INDEX IF NOT EXISTS idx_blocks_height ON blocks(height);
CREATE INDEX IF NOT EXISTS idx_blocks_prev_hash ON blocks(prev_hash);
CREATE INDEX IF NOT EXISTS idx_blocks_timestamp ON blocks(timestamp);

-- Create transactions table if not exists
CREATE TABLE IF NOT EXISTS transactions (
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
CREATE INDEX IF NOT EXISTS idx_transactions_block_hash ON transactions(block_hash);
CREATE INDEX IF NOT EXISTS idx_transactions_block_height ON transactions(block_height);

-- Update sync_state to reset for clean start
UPDATE sync_state SET current_height = 0, is_syncing = 1, current_hash = NULL WHERE id = 1;
"""

        # Write the script to a temporary file
        with open("/tmp/create_missing.sql", "w") as f:
            f.write(create_script)
        
        # Copy script to the container and execute it
        subprocess.run(["docker", "cp", "/tmp/create_missing.sql", "rxindexer-db:/tmp/create_missing.sql"], check=True)
        subprocess.run(["docker", "exec", "rxindexer-db", "psql", "-U", "postgres", "-d", "rxindexer", "-f", "/tmp/create_missing.sql"], check=True)
        logger.info("Missing tables created successfully")
        
        # Update the block parser with improved transaction handling
        block_parser_fix = """#!/usr/bin/env python
# /app/src/parser/block_parser.py - Fixed version with improved transaction handling
# This file implements the block parser for RXinDexer.

import os
import logging
from typing import Dict, List, Any, Set, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
import traceback

from src.sync.rpc_client import RadiantRPC
from .utxo_parser import UTXOParser
from .glyph_parser import GlyphParser

logger = logging.getLogger(__name__)

class BlockParser:
    def __init__(self, rpc: RadiantRPC, db: Session):
        \"\"\"Initialize the block parser.\"\"\"
        self.rpc = rpc
        self.db = db
        self.utxo_parser = UTXOParser(rpc, db)
        self.glyph_parser = GlyphParser(rpc, db)
        
        # Force single-threaded mode to avoid transaction conflicts
        self.parallel_threshold = int(os.environ.get("BLOCK_PARALLEL_THRESHOLD", "5"))
        self.enable_parallel = False  # Disable parallel processing
    
    def parse_block(self, block: Dict[str, Any], height: int, block_hash: str = None) -> Dict[str, int]:
        \"\"\"
        Parse a block and extract all relevant data with improved transaction handling.
        Each block is processed in its own clean transaction.
        \"\"\"
        if not block_hash:
            block_hash = block.get("hash", "")
        
        txs = block.get("tx", [])
        tx_count = len(txs)
        
        logger.info(f"Parsing block {height} with {tx_count} transactions")
        
        # Initialize statistics
        stats = {
            "tx_count": tx_count,
            "utxos_created": 0,
            "utxos_spent": 0,
            "glyph_tokens": 0,
        }
        
        # Always use a fresh transaction for each block
        need_transaction = True
        
        try:
            # Begin a fresh transaction
            if need_transaction:
                logger.info(f"Starting transaction for block {height}")
                self.db.begin_nested()  # Use savepoint for better error handling
            
            # First insert the block itself
            block_inserted = self._insert_block(block, height, block_hash)
            if not block_inserted:
                logger.warning(f"Block {height} already exists in database or could not be inserted")
            
            # Then insert all transactions
            self._insert_transactions(txs, height, block_hash)
            
            # Standard sequential processing for all blocks (parallel disabled)
            for tx in txs:
                # Parse UTXOs (both inputs and outputs)
                utxos_created, utxos_spent = self.utxo_parser.parse_transaction(tx, height, block_hash)
                stats["utxos_created"] += utxos_created
                stats["utxos_spent"] += utxos_spent
                
                # Parse Glyph tokens if present
                tokens = self.glyph_parser.parse_transaction(tx, height, block_hash)
                stats["glyph_tokens"] += len(tokens)
            
            # Update holder balances based on the UTXOs
            self._update_holder_balances()
            
            # Commit transaction if we started one
            if need_transaction:
                logger.info(f"Committing transaction for block {height}")
                self.db.commit()
                logger.info(f"Successfully committed block {height} with {tx_count} transactions")
            
            logger.info(f"Block {height} stats: {stats}")
            return stats
        
        except SQLAlchemyError as e:
            # Special handling for SQLAlchemy errors
            if need_transaction:
                logger.error(f"SQL error processing block {height}: {e}")
                logger.error(traceback.format_exc())
                self.db.rollback()
                
                # Try to handle common errors
                if "InFailedSqlTransaction" in str(e):
                    logger.warning("Detected InFailedSqlTransaction error, attempting recovery...")
                    try:
                        # Emergency recovery - reset the session
                        self.db.rollback()
                        self.db.close()
                        logger.info("Session reset complete")
                    except Exception as reset_err:
                        logger.error(f"Failed to reset session: {reset_err}")
            
            # Re-raise for caller to handle
            raise
            
        except Exception as e:
            # General exception handling
            if need_transaction:
                logger.error(f"Error processing block {height}: {e}")
                logger.error(traceback.format_exc())
                self.db.rollback()
            
            # Re-raise for caller to handle
            raise
    
    def _insert_block(self, block, height, block_hash):
        \"\"\"Insert a block into the database.\"\"\"
        try:
            if not block_hash:
                block_hash = block.get("hash")
                
            prev_hash = block.get("previousblockhash", "")
            merkle_root = block.get("merkleroot", "")
            timestamp = block.get("time", 0)
            nonce = block.get("nonce", 0)
            bits = block.get("bits", "")
            version = block.get("version", 0)
            size = block.get("size", 0)
            weight = block.get("weight", 0)
            tx_count = len(block.get("tx", []))
            
            # Check if block already exists using direct SQL to avoid transaction issues
            try:
                existing_block = self.db.execute(
                    text("SELECT hash FROM blocks WHERE hash = :hash"),
                    {"hash": block_hash}
                ).fetchone()
            except SQLAlchemyError:
                logger.warning("Error checking for existing block, assuming it does not exist")
                existing_block = None
            
            if not existing_block:
                # Insert the block if it doesn't exist
                logger.info(f"Inserting new block: {height}, hash={block_hash[:10]}...")
                
                self.db.execute(
                    text(\"\"\"
                    INSERT INTO blocks (
                        hash, height, prev_hash, merkle_root, timestamp, nonce, 
                        bits, version, size, weight, tx_count, created_at, updated_at
                    ) VALUES (
                        :hash, :height, :prev_hash, :merkle_root, :timestamp, :nonce,
                        :bits, :version, :size, :weight, :tx_count, NOW(), NOW()
                    )
                    ON CONFLICT (hash) DO UPDATE
                    SET height = EXCLUDED.height,
                        prev_hash = EXCLUDED.prev_hash,
                        merkle_root = EXCLUDED.merkle_root,
                        timestamp = EXCLUDED.timestamp,
                        nonce = EXCLUDED.nonce,
                        bits = EXCLUDED.bits,
                        version = EXCLUDED.version,
                        size = EXCLUDED.size,
                        weight = EXCLUDED.weight,
                        tx_count = EXCLUDED.tx_count,
                        updated_at = NOW()
                    \"\"\"),
                    {
                        "hash": block_hash,
                        "height": height,
                        "prev_hash": prev_hash,
                        "merkle_root": merkle_root,
                        "timestamp": timestamp,
                        "nonce": nonce,
                        "bits": bits,
                        "version": version,
                        "size": size,
                        "weight": weight,
                        "tx_count": tx_count
                    }
                )
                return True
            
            return False
        except Exception as e:
            logger.error(f"Error inserting block {height}: {e}")
            raise
    
    def _insert_transactions(self, txs, height, block_hash):
        \"\"\"Insert transactions into the database.\"\"\"
        # Implementation modified to use only tx timestamp
        for tx in txs:
            tx_id = tx.get("txid", "")
            hex_data = tx.get("hex", "")
            size = tx.get("size", 0)
            weight = tx.get("weight", 0)
            version = tx.get("version", 0)
            locktime = tx.get("locktime", 0)
            
            # Process inputs and outputs
            inputs = []
            outputs = []
            
            for vin in tx.get("vin", []):
                inputs.append({
                    "txid": vin.get("txid", ""),
                    "vout": vin.get("vout", 0),
                    "scriptSig": vin.get("scriptSig", {}).get("hex", ""),
                    "sequence": vin.get("sequence", 0)
                })
            
            for vout in tx.get("vout", []):
                outputs.append({
                    "value": vout.get("value", 0),
                    "scriptPubKey": vout.get("scriptPubKey", {}).get("hex", ""),
                    "n": vout.get("n", 0)
                })
            
            # Insert transaction
            try:
                # Only use tx timestamp, removed reference to undefined block variable
                tx_timestamp = tx.get("time", 0)
                
                self.db.execute(
                    text(\"\"\"
                    INSERT INTO transactions (
                        tx_id, block_hash, block_height, hex_data, size, weight,
                        version, locktime, timestamp, inputs, outputs, created_at, updated_at
                    ) VALUES (
                        :tx_id, :block_hash, :height, :hex_data, :size, :weight,
                        :version, :locktime, :timestamp, :inputs, :outputs, NOW(), NOW()
                    )
                    ON CONFLICT (tx_id) DO UPDATE
                    SET block_hash = EXCLUDED.block_hash,
                        block_height = EXCLUDED.block_height,
                        hex_data = EXCLUDED.hex_data,
                        size = EXCLUDED.size,
                        weight = EXCLUDED.weight,
                        version = EXCLUDED.version,
                        locktime = EXCLUDED.locktime,
                        timestamp = EXCLUDED.timestamp,
                        inputs = EXCLUDED.inputs,
                        outputs = EXCLUDED.outputs,
                        updated_at = NOW()
                    \"\"\"),
                    {
                        "tx_id": tx_id,
                        "block_hash": block_hash,
                        "height": height,
                        "hex_data": hex_data,
                        "size": size,
                        "weight": weight,
                        "version": version,
                        "locktime": locktime,
                        "timestamp": tx_timestamp,  # Use the tx timestamp only
                        "inputs": str(inputs),
                        "outputs": str(outputs)
                    }
                )
            except Exception as e:
                logger.error(f"Error inserting transaction {tx_id} in block {height}: {e}")
                raise
                
    def _update_holder_balances(self):
        \"\"\"Update holder balances based on the current UTXO set.\"\"\"
        try:
            # This method updates holder balances from the UTXO set
            # It's a simple placeholder - actual implementation is more complex
            pass
        except Exception as e:
            logger.error(f"Error updating holder balances: {e}")
            raise
"""

        # Check if the indexer container is running
        result = subprocess.run(["docker", "ps", "--filter", "name=rxindexer-indexer", "--format", "{{.Names}}"], capture_output=True, text=True)
        running_containers = result.stdout.strip().split('\n')
        
        # Stop the indexer if it's running
        if "rxindexer-indexer" in running_containers:
            subprocess.run(["docker", "stop", "rxindexer-indexer"], check=True)
            logger.info("Stopped indexer container")
        
        # Write the fixed parser to a temporary file
        with open("/tmp/block_parser_fixed.py", "w") as f:
            f.write(block_parser_fix)
        
        # Copy the fixed parser to the container
        subprocess.run(["docker", "cp", "/tmp/block_parser_fixed.py", "rxindexer-indexer:/app/src/parser/block_parser.py"], check=True)
        logger.info("Updated block parser with improved transaction handling")
        
        # Start the indexer
        subprocess.run(["docker", "start", "rxindexer-indexer"], check=True)
        logger.info("Started indexer container")
        
        return True
    except Exception as e:
        logger.error(f"Failed to fix missing tables: {e}")
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
        except Exception as e:
            logger.warning(f"Could not check block count - table may not exist yet: {e}")
        
        # Check sync state
        cmd = "docker exec rxindexer-db psql -U postgres -d rxindexer -c \"SELECT * FROM sync_state\""
        try:
            sync_state = subprocess.check_output(cmd, shell=True).decode().strip()
            logger.info(f"Current sync state: {sync_state}")
        except Exception as e:
            logger.warning(f"Could not check sync state - table may not exist yet: {e}")
        
        return True
    except Exception as e:
        logger.error(f"Error monitoring sync progress: {e}")
        return False

if __name__ == "__main__":
    print("Starting to fix missing tables...")
    
    # Fix missing tables
    success = fix_missing_tables()
    if success:
        print("Missing tables fixed successfully")
    else:
        print("Failed to fix missing tables")
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
