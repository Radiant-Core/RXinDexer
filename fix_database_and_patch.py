#!/usr/bin/env python
# /Users/radiant/Desktop/RXinDexer/fix_database_and_patch.py
# This script fixes the RPC patch syntax and resets the database to resolve transaction errors.

import os
import logging
import subprocess
import time

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def fix_rpc_patch():
    """Fix the syntax error in the RPC patch."""
    try:
        # Create a corrected patch to modify the RPC client
        patch_content = """
# RPC client patch to improve stability - fixed version
import time
import os

# Reduce connection pool size
os.environ['RPC_POOL_SIZE'] = '1'
os.environ['RPC_MIN_REQUEST_INTERVAL'] = '3.0'
os.environ['RPC_THROTTLE_FACTOR'] = '2.0'
os.environ['CIRCUIT_RESET_TIMEOUT'] = '60'
os.environ['RADIANT_RPC_TIMEOUT'] = '120'
os.environ['RPC_MAX_RETRIES'] = '30'
"""

        # Apply the patch by writing it to a file in the container
        cmd = [
            "docker", "exec", "rxindexer-indexer", 
            "bash", "-c", f"cat > /app/src/sync/rpc_patch.py << 'EOT'\n{patch_content}\nEOT"
        ]
        subprocess.run(cmd, check=True)
        logger.info("Fixed RPC client patch")
        
        # Fix the block parser transaction bug directly
        cmd = [
            "docker", "exec", "rxindexer-indexer",
            "sed", "-i", "s/timestamp\": tx.get(\"time\", 0) or block.get(\"time\", 0)/timestamp\": tx.get(\"time\", 0)/g", 
            "/app/src/parser/block_parser.py"
        ]
        try:
            subprocess.run(cmd, check=True)
            logger.info("Fixed block parser bug")
        except:
            logger.info("Block parser already fixed or sed command failed, continuing")
        
        # Disable parallel processing
        cmd = [
            "docker", "exec", "rxindexer-indexer",
            "bash", "-c", 
            "sed -i 's/self.enable_parallel = os.environ.get/self.enable_parallel = False # os.environ.get/g' /app/src/parser/block_parser.py"
        ]
        subprocess.run(cmd, check=True)
        logger.info("Disabled parallel processing in block parser")
        
        return True
    except Exception as e:
        logger.error(f"Error fixing RPC patch: {e}")
        return False

def reset_database_completely():
    """Reset the database completely to resolve transaction errors."""
    try:
        # Stop the indexer first
        subprocess.run(["docker", "stop", "rxindexer-indexer"], check=True)
        logger.info("Stopped indexer container")
        
        # Execute SQL to reset all tables
        cmd = """
        docker exec rxindexer-db psql -U postgres -d rxindexer -c "
        -- Reset sync state
        TRUNCATE sync_state;
        INSERT INTO sync_state (id, current_height, is_syncing, created_at, updated_at) 
        VALUES (1, 0, 1, NOW(), NOW());
        
        -- Clear blocks and transactions
        TRUNCATE blocks CASCADE;
        TRUNCATE transactions CASCADE;
        "
        """
        subprocess.run(cmd, shell=True, check=True)
        logger.info("Reset database tables")
        
        # Start the indexer
        subprocess.run(["docker", "start", "rxindexer-indexer"], check=True)
        logger.info("Started indexer container")
        
        # Give it time to initialize
        time.sleep(10)
        return True
    except Exception as e:
        logger.error(f"Failed to reset database: {e}")
        return False

if __name__ == "__main__":
    print("Starting complete fix with database reset...")
    
    # Fix RPC patch
    fix_rpc_patch()
    
    # Reset database
    reset_database_completely()
    
    print("Fixes applied. Checking logs in 15 seconds...")
    time.sleep(15)
    
    # Check logs
    subprocess.run(["docker", "exec", "rxindexer-indexer", "tail", "-n", "30", "/app/logs/indexer.log"], check=True)
