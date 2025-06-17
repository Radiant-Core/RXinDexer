# /Users/radiant/Desktop/RXinDexer/src/indexer.py
# This file serves as the main entry point for the RXinDexer blockchain indexer.
# It initializes the sync process and monitors the blockchain for new blocks.

import os
import time
import logging
import argparse
import sys
from pathlib import Path
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("rxindexer.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Import after environment is loaded
from src.sync.sync_manager import SyncManager
from src.models.database import get_db
from src.db.init_db import create_tables
from src.db.init_functions import create_pg_functions

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="RXinDexer - Radiant Blockchain Indexer")
    
    parser.add_argument(
        "--init-db", 
        action="store_true", 
        help="Initialize database before starting"
    )
    
    parser.add_argument(
        "--sync-only", 
        action="store_true", 
        help="Only sync up to current height and exit"
    )
    
    parser.add_argument(
        "--resync", 
        action="store_true", 
        help="Resync from scratch (will delete all data)"
    )
    
    parser.add_argument(
        "--dev-mode", 
        action="store_true", 
        help="Run in development mode without connecting to Radiant node"
    )
    
    parser.add_argument(
        "--status", 
        action="store_true", 
        help="Show sync status and exit"
    )
    
    parser.add_argument(
        "--continuous", 
        action="store_true", 
        help="Run in continuous sync mode without pauses between batches"
    )
    
    parser.add_argument(
        "--batch-size", 
        type=int,
        default=500,
        help="Number of blocks to process in each batch"
    )
    
    parser.add_argument(
        "--max-workers", 
        type=int,
        default=8,
        help="Maximum number of worker threads for parallel processing"
    )
    
    return parser.parse_args()

def main():
    """Main entry point for the indexer."""
    args = parse_args()
    
    # Debug logging for command-line arguments
    logger.info(f"Command-line arguments: {vars(args)}")
    logger.info(f"sync_only flag: {args.sync_only}")
    logger.info(f"continuous flag: {args.continuous}")
    
    # Check for environment variable to override flags (in Docker environment)
    # This ensures the indexer runs continuously when deployed with Docker
    if os.environ.get('START_INDEXER') == 'true':
        if args.sync_only:
            logger.info("START_INDEXER=true detected, overriding sync-only flag")
            args.sync_only = False
        if not args.continuous:
            logger.info("START_INDEXER=true detected, enabling continuous mode")
            args.continuous = True
    
    
    # Get database session
    db = next(get_db())
    
    # Initialize database if requested
    if args.init_db:
        logger.info("Initializing database...")
        create_tables()
        # Create PostgreSQL functions needed for JSONB operations
        logger.info("Creating custom PostgreSQL functions...")
        create_pg_functions()
        
    # Set environment variables from command line arguments for performance tuning
    if args.batch_size:
        os.environ["SYNC_BATCH_SIZE"] = str(args.batch_size)
        logger.info(f"Setting batch size to {args.batch_size} blocks")
        
    if args.max_workers:
        os.environ["SYNC_MAX_WORKERS"] = str(args.max_workers)
        logger.info(f"Setting max workers to {args.max_workers} threads")
    
    # Create sync manager
    sync_manager = SyncManager(db)
    
    # Show status and exit if requested
    if args.status:
        status = sync_manager.get_sync_status()
        print(f"Current height: {status['current_height']}")
        print(f"Node height: {status['node_height']}")
        print(f"Progress: {status['progress']}%")
        print(f"Is syncing: {status['is_syncing']}")
        if status.get('last_error'):
            print(f"Last error: {status['last_error']}")
        return
    
    # Resync from scratch if requested
    if args.resync:
        if input("WARNING: This will delete all indexed data. Are you sure? (y/n): ").lower() != 'y':
            print("Aborting resync.")
            return
        
        logger.warning("Resetting database for full resync")
        from src.models.database import engine, Base
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
    
    # Start the sync process
    if args.dev_mode:
        logger.info("Running in development mode without connecting to Radiant node")
        try:
            # In dev mode, just keep the process alive without trying to sync
            logger.info("Indexer running in development mode, waiting for manual interruption")
            while True:
                time.sleep(60)  # Just keep alive in dev mode
        except KeyboardInterrupt:
            logger.info("Shutting down indexer...")
        return 0
    
    # Normal mode with Radiant node connection
    logger.info("Starting blockchain sync process")
    
    try:
        # Set initial sync mode based on continuous flag
        continuous_mode = args.continuous
        if continuous_mode:
            logger.info("Running in continuous sync mode without pauses between batches")
        
        # Start sync process
        if continuous_mode:
            # In continuous mode, we keep syncing without pauses until we catch up to the tip
            sync_complete = False
            while not sync_complete:
                sync_complete = sync_manager.start_sync(continuous=True)
                
                # If we've caught up to the tip, switch to monitoring mode
                if sync_complete and not args.sync_only:
                    logger.info("Caught up to blockchain tip, switching to monitoring mode")
                    sync_complete = False
                    time.sleep(5)  # Short pause before continuing
        else:
            # Standard single sync pass
            sync_manager.start_sync()
        
        # If sync-only flag is set, exit after initial sync
        if args.sync_only:
            logger.info("Initial sync completed, exiting as requested")
            return
        
        # Otherwise, keep monitoring for new blocks
        logger.info("Initial sync completed, monitoring for new blocks")
        
        while True:
            # In monitoring mode, we wait before checking for new blocks
            # unless continuous mode is enabled
            if not continuous_mode:
                time.sleep(30)  # Check for new blocks every 30 seconds
            sync_manager.start_sync(continuous=continuous_mode)
            
    except KeyboardInterrupt:
        logger.info("Shutting down indexer...")
    except Exception as e:
        logger.error(f"Indexer crashed: {str(e)}", exc_info=True)
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
