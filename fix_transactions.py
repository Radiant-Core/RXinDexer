#!/usr/bin/env python3
# /Users/radiant/Desktop/RXinDexer/fix_transactions.py
# This script fixes the transaction insertion method in the block parser
# and verifies block and transaction persistence.

import os
import time
import logging
import json
import subprocess
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# The missing transaction insertion method
TRANSACTION_METHOD = """
def _insert_transactions(self, txs, height, block_hash):
    \"\"\"
    Insert transactions into the database.
    Uses the current session transaction.
    
    Args:
        txs: Transaction data from Radiant Node
        height: Block height
        block_hash: Block hash
    \"\"\"
    try:
        for tx_index, tx in enumerate(txs):
            # Get transaction hash
            tx_hash = tx.get("hash") or tx.get("txid")
            if not tx_hash:
                logger.warning(f"Transaction at index {tx_index} in block {height} has no hash")
                continue
                
            # Get transaction size and weight
            size = tx.get("size", 0)
            weight = tx.get("weight", 0)
            
            # Get lock time
            lock_time = tx.get("locktime", 0)
            
            # Get input and output counts
            vin = tx.get("vin", [])
            vout = tx.get("vout", [])
            input_count = len(vin)
            output_count = len(vout)
            
            # Insert the transaction
            logger.debug(f"Inserting transaction {tx_hash[:10]}...")
            
            self.db.execute(
                text(\"\"\"
                INSERT INTO transactions (
                    hash, block_hash, block_height, index_in_block, timestamp,
                    size, weight, lock_time, input_count, output_count,
                    created_at, updated_at
                ) VALUES (
                    :hash, :block_hash, :height, :index, :timestamp,
                    :size, :weight, :lock_time, :input_count, :output_count,
                    NOW(), NOW()
                )
                ON CONFLICT (hash) DO UPDATE
                SET block_hash = EXCLUDED.block_hash,
                    block_height = EXCLUDED.block_height,
                    index_in_block = EXCLUDED.index_in_block,
                    timestamp = EXCLUDED.timestamp,
                    size = EXCLUDED.size,
                    weight = EXCLUDED.weight,
                    lock_time = EXCLUDED.lock_time,
                    input_count = EXCLUDED.input_count,
                    output_count = EXCLUDED.output_count,
                    updated_at = NOW()
                \"\"\"),
                {
                    "hash": tx_hash,
                    "block_hash": block_hash,
                    "height": height,
                    "index": tx_index,
                    "timestamp": tx.get("time", 0),
                    "size": size,
                    "weight": weight,
                    "lock_time": lock_time,
                    "input_count": input_count,
                    "output_count": output_count
                }
            )
            
        # Force a flush to detect any errors early
        self.db.flush()
        logger.info(f"Inserted {len(txs)} transactions for block {height}")
        
    except Exception as e:
        logger.error(f"Error inserting transactions for block {height}: {str(e)}")
        raise
"""

def patch_block_parser():
    """Create a patch script to add the missing method to the block_parser.py file"""
    patch_script = f"""#!/bin/bash
# Add missing _insert_transactions method to block_parser.py

# First, check if file exists
if [ ! -f "/app/src/parser/block_parser.py" ]; then
    echo "Error: block_parser.py not found"
    exit 1
fi

# Use grep to find where the _insert_block method ends
line_num=$(grep -n "^    def _insert_block" /app/src/parser/block_parser.py | cut -d: -f1)
if [ -z "$line_num" ]; then
    echo "Error: Could not find _insert_block method"
    exit 1
fi

# Find where the next method starts after _insert_block
end_line=$(tail -n +$line_num /app/src/parser/block_parser.py | grep -n "^    def " | sed -n 2p | cut -d: -f1)
if [ -z "$end_line" ]; then
    echo "Error: Could not find the end of _insert_block method"
    exit 1
fi

# Calculate the insertion point
insert_line=$((line_num + end_line - 1))

# Create a temporary file with the new method
cat > /tmp/method.py << 'EOL'
{TRANSACTION_METHOD}
EOL

# Use sed to insert the new method at the calculated line
sed -i "${{insert_line}}r /tmp/method.py" /app/src/parser/block_parser.py

# Verify the insertion
if grep -q "_insert_transactions" /app/src/parser/block_parser.py; then
    echo "Successfully added _insert_transactions method"
    # Clean up
    rm /tmp/method.py
    exit 0
else
    echo "Failed to add _insert_transactions method"
    exit 1
fi
"""

    # Write the patch script to a temporary file
    with open("fix_transactions_patch.sh", "w") as f:
        f.write(patch_script)
    
    # Copy the script to the container
    logger.info("Copying patch script to container...")
    copy_result = subprocess.run([
        "docker", "cp", "fix_transactions_patch.sh", 
        "rxindexer-indexer:/app/fix_transactions_patch.sh"
    ], capture_output=True, text=True)
    
    if copy_result.returncode != 0:
        logger.error(f"Failed to copy patch script: {copy_result.stderr}")
        return False
    
    # Make it executable
    chmod_result = subprocess.run([
        "docker", "exec", "rxindexer-indexer", 
        "chmod", "+x", "/app/fix_transactions_patch.sh"
    ], capture_output=True, text=True)
    
    if chmod_result.returncode != 0:
        logger.error(f"Failed to make patch script executable: {chmod_result.stderr}")
        return False
    
    # Run the patch script
    logger.info("Running transaction patch script...")
    patch_result = subprocess.run([
        "docker", "exec", "rxindexer-indexer", 
        "/app/fix_transactions_patch.sh"
    ], capture_output=True, text=True)
    
    logger.info(f"Patch output: {patch_result.stdout}")
    if patch_result.stderr:
        logger.error(f"Patch errors: {patch_result.stderr}")
    
    return "Successfully added _insert_transactions method" in patch_result.stdout

def restart_indexer():
    """Restart just the indexer container to apply the patch"""
    logger.info("Restarting indexer container...")
    
    # Stop the indexer
    stop_result = subprocess.run([
        "docker", "stop", "rxindexer-indexer"
    ], capture_output=True, text=True)
    
    if stop_result.returncode != 0:
        logger.error(f"Failed to stop indexer: {stop_result.stderr}")
        return False
    
    time.sleep(2)  # Brief pause
    
    # Start the indexer
    start_result = subprocess.run([
        "docker", "start", "rxindexer-indexer"
    ], capture_output=True, text=True)
    
    if start_result.returncode != 0:
        logger.error(f"Failed to start indexer: {start_result.stderr}")
        return False
    
    logger.info("Indexer restarted successfully")
    return True

def verify_database_state():
    """Check the database to verify blocks and transactions are being saved"""
    logger.info("Checking database state...")
    
    # Initial counts
    block_count = get_block_count()
    tx_count = get_transaction_count()
    logger.info(f"Initial block count: {block_count}, transaction count: {tx_count}")
    
    # Wait for a minute to let indexer process blocks
    logger.info("Waiting for 60 seconds to let indexer process blocks...")
    time.sleep(60)
    
    # Check counts again
    new_block_count = get_block_count()
    new_tx_count = get_transaction_count()
    logger.info(f"Updated block count: {new_block_count}, transaction count: {new_tx_count}")
    
    # Check if counts increased
    blocks_added = new_block_count > block_count
    txs_added = new_tx_count > tx_count
    
    if blocks_added:
        logger.info(f"✅ Block count increased by {new_block_count - block_count} blocks!")
    else:
        logger.warning("❌ No increase in block count.")
    
    if txs_added:
        logger.info(f"✅ Transaction count increased by {new_tx_count - tx_count} transactions!")
    else:
        logger.warning("❌ No increase in transaction count.")
    
    return blocks_added and txs_added

def get_block_count():
    """Get the current block count from the database"""
    result = subprocess.run([
        "docker", "exec", "rxindexer-db", 
        "psql", "-U", "postgres", "-d", "rxindexer", "-t",
        "-c", "SELECT COUNT(*) FROM blocks;"
    ], capture_output=True, text=True)
    
    if result.returncode == 0:
        count_text = result.stdout.strip()
        try:
            return int(count_text)
        except ValueError:
            logger.error(f"Could not parse block count: '{count_text}'")
            return 0
    else:
        logger.error(f"Failed to get block count: {result.stderr}")
        return 0

def get_transaction_count():
    """Get the current transaction count from the database"""
    result = subprocess.run([
        "docker", "exec", "rxindexer-db", 
        "psql", "-U", "postgres", "-d", "rxindexer", "-t",
        "-c", "SELECT COUNT(*) FROM transactions;"
    ], capture_output=True, text=True)
    
    if result.returncode == 0:
        count_text = result.stdout.strip()
        try:
            return int(count_text)
        except ValueError:
            logger.error(f"Could not parse transaction count: '{count_text}'")
            return 0
    else:
        logger.error(f"Failed to get transaction count: {result.stderr}")
        return 0

def check_api_endpoint():
    """Check the API endpoint to see if it reflects the database changes"""
    logger.info("Checking API endpoint...")
    
    result = subprocess.run([
        "curl", "-s", "http://localhost:8000/blocks/latest"
    ], capture_output=True, text=True)
    
    if result.returncode == 0:
        try:
            response = json.loads(result.stdout)
            logger.info(f"API Response: {json.dumps(response, indent=2)}")
            
            if response.get("height", 0) > 0:
                logger.info(f"✅ API returning block at height {response.get('height')}")
                return True
            else:
                logger.warning("❌ API response has zero block height")
                return False
        except json.JSONDecodeError:
            logger.error(f"API did not return valid JSON: {result.stdout}")
            return False
    else:
        logger.error(f"Failed to call API endpoint: {result.stderr}")
        return False

def check_transactions_endpoint():
    """Check the transactions API endpoint"""
    logger.info("Checking transactions API endpoint...")
    
    result = subprocess.run([
        "curl", "-s", "http://localhost:8000/transactions/recent"
    ], capture_output=True, text=True)
    
    if result.returncode == 0:
        try:
            response = json.loads(result.stdout)
            
            if isinstance(response, list) and len(response) > 0:
                logger.info(f"✅ API returning {len(response)} recent transactions")
                logger.info(f"Sample transaction: {json.dumps(response[0], indent=2)}")
                return True
            else:
                logger.warning("❌ API not returning any transactions")
                return False
        except json.JSONDecodeError:
            logger.error(f"API did not return valid JSON: {result.stdout}")
            return False
    else:
        logger.error(f"Failed to call transactions API endpoint: {result.stderr}")
        return False

def main():
    """Main function that orchestrates the fix process"""
    logger.info("=== RXinDexer Transaction Fix Script ===")
    
    # Apply the transaction insertion patch
    if not patch_block_parser():
        logger.error("Failed to patch the block parser. Aborting.")
        return False
    
    # Restart the indexer
    if not restart_indexer():
        logger.error("Failed to restart the indexer. Aborting.")
        return False
    
    # Give the indexer some time to start up
    logger.info("Waiting for indexer to initialize...")
    time.sleep(10)
    
    # Verify data is being saved
    success = verify_database_state()
    
    # Check API endpoints
    if success:
        logger.info("Database state looks good. Checking API endpoints...")
        check_api_endpoint()
        check_transactions_endpoint()
    
    logger.info("=== Fix Complete ===")
    logger.info("Monitor the indexer and API for continued functionality.")
    
    return True

if __name__ == "__main__":
    main()
