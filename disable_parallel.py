#!/usr/bin/env python
# /Users/radiant/Desktop/RXinDexer/disable_parallel.py
# This script disables parallel processing for the indexer to reduce RPC request load.
# It modifies the parallel_processor.py file directly to use one worker.

import os
import subprocess
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def modify_parallel_processor():
    """Modify the parallel_processor.py file to disable parallel processing."""
    try:
        # Create a command to directly modify the parallal_processor.py file using sed
        cmd = """
        docker exec rxindexer-indexer bash -c '
        sed -i "s/workers = min(multiprocessing.cpu_count(), max_workers)/workers = 1/g" /app/src/sync/parallel_processor.py
        sed -i "s/use_parallel = .*/use_parallel = False/g" /app/src/sync/parallel_processor.py
        '
        """
        
        logger.info("Modifying parallel processor to use only 1 worker")
        subprocess.run(cmd, shell=True, check=True)
        
        # Also modify the RPC client to improve stability
        cmd = """
        docker exec rxindexer-indexer bash -c '
        sed -i "s/self.pool_size = min(self.pool_size, .)/self.pool_size = 1/g" /app/src/sync/rpc_client.py
        '
        """
        
        logger.info("Setting RPC connection pool size to 1")
        subprocess.run(cmd, shell=True, check=True)
        
        # Restart the indexer
        subprocess.run(["docker", "restart", "rxindexer-indexer"], check=True)
        logger.info("Indexer restarted with new settings")
        
        return True
    except Exception as e:
        logger.error(f"Error disabling parallel processing: {e}")
        return False

if __name__ == "__main__":
    modify_parallel_processor()
