# /Users/radiant/Desktop/RXinDexer/run_optimized_sync.py
# This script runs the optimized sync module with all performance enhancements enabled

import asyncio
import os
import sys
import logging
import importlib.util
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("optimized-sync-runner")

# Set optimized environment variables
os.environ["SYNC_BATCH_SIZE"] = "5000"
os.environ["SYNC_MAX_WORKERS"] = "32"
os.environ["UTXO_MAX_WORKERS"] = "8"
os.environ["BLOCK_PARALLEL_THRESHOLD"] = "100"
os.environ["PROGRESSIVE_SYNC"] = "true"
os.environ["INITIAL_SYNC_MINIMAL"] = "true"
os.environ["USE_REDIS_CACHE"] = "true"
os.environ["REDIS_URL"] = "redis://redis:6379/0"

async def main():
    logger.info("Starting optimized sync with enhanced performance settings")
    
    # Import database and RPC client modules
    # This is a placeholder - actual imports will depend on your project structure
    try:
        from src.db import get_db_connection
        from src.rpc import get_rpc_client
        
        # Get database connection and RPC client
        db_connection = get_db_connection()
        rpc_client = get_rpc_client()
        
        # Import and run optimized sync
        from src.sync.optimized_sync import run_optimized_sync
        
        logger.info("Running optimized sync with enhanced settings")
        result = await run_optimized_sync(db_connection, rpc_client)
        
        if result.get('success', False):
            logger.info(f"Sync completed successfully in {result.get('elapsed_seconds', 0):.2f} seconds")
            logger.info(f"Processed {result.get('blocks_processed', 0)} blocks")
            logger.info(f"Performance: {result.get('blocks_per_second', 0):.2f} blocks/second")
        else:
            logger.error(f"Sync failed: {result.get('error', 'Unknown error')}")
            
    except ImportError as e:
        logger.error(f"Error importing required modules: {e}")
        logger.info("Attempting to use direct imports...")
        
        # Alternative approach using direct module loading
        try:
            # Add current directory to path
            sys.path.append("/app")
            
            # Direct import of the optimized sync module
            spec = importlib.util.spec_from_file_location(
                "optimized_sync", 
                "/app/src/sync/optimized_sync.py"
            )
            optimized_sync = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(optimized_sync)
            
            # Get database connection and RPC client from your application
            # This depends on your application's structure
            logger.info("Setting up database and RPC connections...")
            
            # This is where you would establish the connections
            # For now, we'll just log a message
            logger.info("Direct module loading successful, but cannot run without proper DB/RPC connections")
            logger.info("Please run the optimized sync module directly from your application")
            
        except Exception as e:
            logger.error(f"Failed to load module directly: {e}")
    
    except Exception as e:
        logger.error(f"Unexpected error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
