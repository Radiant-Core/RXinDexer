#!/usr/bin/env python
# /Users/radiant/Desktop/RXinDexer/fix_rpc_sync.py
# This file implements direct fixes to the RPC client and block syncing to address instability issues.

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

def fix_rpc_client():
    """Fix the RPC client to handle request-sent errors better."""
    try:
        # Create a patch to modify the RPC client
        patch_content = """
# RPC client patch to improve stability
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
        cmd = f"docker exec rxindexer-indexer bash -c \"echo '{patch_content}' > /app/src/sync/rpc_patch.py\""
        subprocess.run(cmd, shell=True, check=True)
        logger.info("Created RPC client patch")
        
        # Modify the RPC client initialization to apply the patch
        cmd = """
        docker exec rxindexer-indexer bash -c "
        sed -i '1s/^/import src.sync.rpc_patch\\n/' /app/src/sync/rpc_client.py
        "
        """
        subprocess.run(cmd, shell=True, check=True)
        logger.info("Applied RPC client patch")
        
        # Fix the block parser transaction bug
        cmd = """
        docker exec rxindexer-indexer bash -c "
        sed -i 's/timestamp\": tx.get(\"time\", 0) or block.get(\"time\", 0)/timestamp\": tx.get(\"time\", 0)/g' /app/src/parser/block_parser.py
        "
        """
        subprocess.run(cmd, shell=True, check=True)
        logger.info("Fixed block parser bug")
        
        # Disable parallel processing
        cmd = """
        docker exec rxindexer-indexer bash -c '
        sed -i "s/workers = min(multiprocessing.cpu_count(), max_workers)/workers = 1/g" /app/src/sync/parallel_processor.py
        sed -i "s/self.enable_parallel = .*/self.enable_parallel = False/g" /app/src/parser/block_parser.py
        '
        """
        subprocess.run(cmd, shell=True, check=True)
        logger.info("Disabled parallel processing")
        
        return True
    except Exception as e:
        logger.error(f"Error fixing RPC client: {e}")
        return False

def reset_sync_state():
    """Reset the sync state in the database."""
    try:
        cmd = """
        docker exec rxindexer-db psql -U postgres -d rxindexer -c "
        DELETE FROM blocks WHERE height > 900000;
        UPDATE sync_state SET current_height = 0, is_syncing = 1;
        "
        """
        subprocess.run(cmd, shell=True, check=True)
        logger.info("Reset sync state to height 0")
        return True
    except Exception as e:
        logger.error(f"Failed to reset sync state: {e}")
        return False

def restart_indexer():
    """Restart the indexer container."""
    try:
        subprocess.run(["docker", "restart", "rxindexer-indexer"], check=True)
        logger.info("Restarted indexer container")
        # Give it time to start up
        time.sleep(10)
        return True
    except Exception as e:
        logger.error(f"Failed to restart indexer: {e}")
        return False

def monitor_logs():
    """Monitor indexer logs for progress."""
    try:
        logger.info("Monitoring indexer logs (press Ctrl+C to stop)")
        subprocess.run(
            "docker exec rxindexer-indexer tail -f /app/logs/indexer.log | grep -E 'block|sync|error' --color=never",
            shell=True
        )
        return True
    except KeyboardInterrupt:
        logger.info("Stopped monitoring logs")
        return True
    except Exception as e:
        logger.error(f"Failed to monitor logs: {e}")
        return False

if __name__ == "__main__":
    print("Starting RPC client and sync fixes...")
    
    # Fix RPC client
    fix_rpc_client()
    
    # Reset sync state
    reset_sync_state()
    
    # Restart indexer
    restart_indexer()
    
    # Monitor logs
    print("Fixes applied. Starting log monitoring (press Ctrl+C to stop):")
    monitor_logs()
