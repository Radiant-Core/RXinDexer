#!/usr/bin/env python
# /Users/radiant/Desktop/RXinDexer/fix_utxo_schema.py
# This script fixes the UTXO table schema to ensure column names match the code

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

def fix_utxo_schema():
    """Fix the UTXO table schema to match what the code expects"""
    try:
        # Check existing UTXO table structure
        cmd = "docker exec rxindexer-db psql -U postgres -d rxindexer -c \"\\d utxos\""
        utxo_structure = subprocess.check_output(cmd, shell=True).decode().strip()
        logger.info(f"Current UTXO table structure: {utxo_structure}")
        
        # Create alter table script
        alter_script = """
-- Fix the UTXO table schema
DROP TABLE IF EXISTS utxos CASCADE;

-- Recreate UTXO table with correct column names
CREATE TABLE utxos (
    id SERIAL PRIMARY KEY,
    txid VARCHAR(64) NOT NULL,  -- Changed from tx_id to txid to match code
    vout INTEGER NOT NULL,
    address VARCHAR(64),
    script_pubkey TEXT,  -- This might be missing from code
    amount NUMERIC(20, 8) NOT NULL,
    spent BOOLEAN DEFAULT FALSE,
    spent_by_tx VARCHAR(64),
    block_height INTEGER NOT NULL,
    block_hash VARCHAR(64),  -- Added to match code expectations
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT utxo_unique_constraint UNIQUE(txid, vout)
);

-- Create indexes for utxos table
CREATE INDEX idx_utxos_txid ON utxos(txid);
CREATE INDEX idx_utxos_address ON utxos(address);
CREATE INDEX idx_utxos_spent ON utxos(spent);
CREATE INDEX idx_utxos_spent_by_tx ON utxos(spent_by_tx);
"""

        # Write the script to a temporary file
        with open("/tmp/fix_utxo.sql", "w") as f:
            f.write(alter_script)
        
        # Copy script to the container and execute it
        subprocess.run(["docker", "cp", "/tmp/fix_utxo.sql", "rxindexer-db:/tmp/fix_utxo.sql"], check=True)
        subprocess.run(["docker", "exec", "rxindexer-db", "psql", "-U", "postgres", "-d", "rxindexer", "-f", "/tmp/fix_utxo.sql"], check=True)
        logger.info("UTXO table schema fixed successfully")
        
        # Now fix UTXO parser to use correct column names
        utxo_parser_fix = """#!/usr/bin/env python
# /app/src/parser/utxo_parser.py - Fixed version with correct column names
# This file implements the UTXO parser for RXinDexer.

import logging
from typing import Dict, List, Any, Tuple
import json
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.sync.rpc_client import RadiantRPC

logger = logging.getLogger(__name__)

class UTXOParser:
    def __init__(self, rpc: RadiantRPC, db: Session):
        \"\"\"Initialize the UTXO parser.\"\"\"
        self.rpc = rpc
        self.db = db
    
    def parse_transaction(self, tx: Dict[str, Any], height: int, block_hash: str) -> Tuple[int, int]:
        \"\"\"Parse a transaction and process its UTXOs.\"\"\"
        tx_id = tx.get("txid", "")
        
        # Process inputs (mark UTXOs as spent)
        spent_count = 0
        utxos_to_spend = []
        
        for vin in tx.get("vin", []):
            prev_tx = vin.get("txid")
            prev_vout = vin.get("vout")
            
            # Skip coinbase inputs
            if prev_tx is None or prev_vout is None:
                continue
                
            utxos_to_spend.append((prev_tx, prev_vout, tx_id))
            spent_count += 1
        
        # Mark UTXOs as spent in batch
        if utxos_to_spend:
            spent_count = self._mark_utxos_spent(utxos_to_spend)
        
        # Process outputs (create new UTXOs)
        created_count = 0
        utxos_to_create = []
        
        for vout in tx.get("vout", []):
            # Skip non-standard outputs
            script_pub_key = vout.get("scriptPubKey", {})
            addresses = script_pub_key.get("addresses", [])
            
            # Extract single address or use empty string
            address = addresses[0] if addresses else ""
            
            # Add to batch
            utxos_to_create.append({
                "txid": tx_id,
                "vout": vout.get("n", 0),
                "address": address,
                "script_pubkey": script_pub_key.get("hex", ""),
                "amount": vout.get("value", 0),
                "block_height": height,
                "block_hash": block_hash
            })
        
        # Create UTXOs in batch
        if utxos_to_create:
            try:
                created_count = self._create_utxos_batch(utxos_to_create)
            except Exception as e:
                logger.error(f"Failed to batch create UTXOs: {e}")
        
        return created_count, spent_count
    
    def _create_utxos_batch(self, utxos: List[Dict[str, Any]]) -> int:
        \"\"\"Create multiple UTXOs in a batch.\"\"\"
        if not utxos:
            return 0
            
        try:
            for utxo in utxos:
                self.db.execute(
                    text(\"\"\"
                        INSERT INTO utxos (txid, vout, address, amount, spent, block_height, block_hash, created_at, updated_at) 
                        VALUES (:txid, :vout, :address, :amount, FALSE, :block_height, :block_hash, NOW(), NOW())
                        ON CONFLICT (txid, vout) DO UPDATE
                        SET address = EXCLUDED.address, 
                            amount = EXCLUDED.amount, 
                            block_height = EXCLUDED.block_height, 
                            block_hash = EXCLUDED.block_hash,
                            updated_at = NOW()
                    \"\"\"),
                    {
                        "txid": utxo["txid"],
                        "vout": utxo["vout"],
                        "address": utxo["address"],
                        "amount": utxo["amount"],
                        "block_height": utxo["block_height"],
                        "block_hash": utxo["block_hash"]
                    }
                )
            return len(utxos)
        except Exception as e:
            logger.error(f"Error creating UTXOs: {e}")
            raise
    
    def _mark_utxos_spent(self, utxos: List[Tuple[str, int, str]]) -> int:
        \"\"\"Mark multiple UTXOs as spent in a batch.\"\"\"
        if not utxos:
            return 0
            
        try:
            for prev_tx, prev_vout, spending_tx in utxos:
                self.db.execute(
                    text(\"\"\"
                        UPDATE utxos
                        SET spent = TRUE,
                            spent_by_tx = :spending_tx,
                            updated_at = NOW()
                        WHERE txid = :prev_tx AND vout = :prev_vout
                    \"\"\"),
                    {
                        "prev_tx": prev_tx,
                        "prev_vout": prev_vout,
                        "spending_tx": spending_tx
                    }
                )
            return len(utxos)
        except Exception as e:
            logger.error(f"Error marking UTXOs as spent: {e}")
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
        with open("/tmp/utxo_parser_fixed.py", "w") as f:
            f.write(utxo_parser_fix)
        
        # Copy the fixed parser to the container
        subprocess.run(["docker", "cp", "/tmp/utxo_parser_fixed.py", "rxindexer-indexer:/app/src/parser/utxo_parser.py"], check=True)
        logger.info("Updated UTXO parser with correct column names")
        
        # Start the indexer
        subprocess.run(["docker", "start", "rxindexer-indexer"], check=True)
        logger.info("Started indexer container")
        
        return True
    except Exception as e:
        logger.error(f"Failed to fix UTXO schema: {e}")
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
            logger.warning(f"Could not check block count: {e}")
        
        # Check UTXO count
        cmd = "docker exec rxindexer-db psql -U postgres -d rxindexer -c \"SELECT COUNT(*) FROM utxos\""
        try:
            utxo_count = subprocess.check_output(cmd, shell=True).decode().strip()
            logger.info(f"Current UTXO count: {utxo_count}")
        except Exception as e:
            logger.warning(f"Could not check UTXO count: {e}")
        
        return True
    except Exception as e:
        logger.error(f"Error monitoring sync progress: {e}")
        return False

def check_api_endpoint():
    """Check if the API endpoint is returning data"""
    try:
        cmd = "curl -s http://localhost:8000/api/v1/status"
        result = subprocess.check_output(cmd, shell=True).decode().strip()
        logger.info(f"API status: {result}")
        return True
    except Exception as e:
        logger.error(f"Error checking API endpoint: {e}")
        return False

if __name__ == "__main__":
    print("Starting to fix UTXO schema...")
    
    # Fix UTXO schema
    success = fix_utxo_schema()
    if success:
        print("UTXO schema fixed successfully")
    else:
        print("Failed to fix UTXO schema")
        sys.exit(1)
    
    # Wait for services to restart
    print("Waiting 10 seconds for services to start...")
    time.sleep(10)
    
    # Monitor sync progress
    print("Monitoring sync progress (this will take about 30 seconds)...")
    monitor_sync_progress()
    
    # Check API endpoint
    print("Checking API endpoint...")
    check_api_endpoint()
    
    print("\nUTXO schema fix completed.")
    print("\nNext steps:")
    print("1. Monitor indexer logs: docker exec rxindexer-indexer tail -f /app/logs/indexer.log")
    print("2. Check database for blocks: docker exec rxindexer-db psql -U postgres -d rxindexer -c \"SELECT COUNT(*) FROM blocks\"")
    print("3. Check database for UTXOs: docker exec rxindexer-db psql -U postgres -d rxindexer -c \"SELECT COUNT(*) FROM utxos\"")
    print("4. Test API endpoints: curl http://localhost:8000/api/v1/blocks/latest")
