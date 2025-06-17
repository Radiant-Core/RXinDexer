#!/usr/bin/env python
# /Users/radiant/Desktop/RXinDexer/serialize_sync.py
# This script creates a fully serialized version of the sync process to ensure stable RPC connections.
# It eliminates all parallel processing and optimizes for stability over speed.

import os
import subprocess
import logging
import time
import psycopg2
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Database connection
DATABASE_URL = os.environ.get(
    "DATABASE_URL", 
    "postgresql://postgres:postgres@localhost:5432/rxindexer"
)

def get_db_session():
    """Create a database session."""
    try:
        engine = create_engine(DATABASE_URL)
        Session = sessionmaker(bind=engine)
        return Session()
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        return None

def reset_sync_state():
    """Reset the sync state in the database."""
    try:
        db = get_db_session()
        if not db:
            return False
            
        # Delete any test blocks (height > 900000)
        db.execute(text("DELETE FROM blocks WHERE height > 900000"))
        
        # Reset sync state to height 0
        db.execute(text("UPDATE sync_state SET height = 0, is_syncing = true"))
        
        # Commit changes
        db.commit()
        db.close()
        
        logger.info("Sync state reset to height 0")
        return True
    except Exception as e:
        logger.error(f"Failed to reset sync state: {e}")
        return False

def create_serialized_sync():
    """Create a serialized sync implementation file."""
    try:
        # Create a serialized sync file
        serialized_sync = '''#!/usr/bin/env python
# This is a serialized sync implementation for RXinDexer
# Created to solve RPC connection issues

import os
import sys
import time
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/app/logs/serialized_sync.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Import the RPC client
sys.path.append('/app')
from src.sync.rpc_client import RadiantRPC
from src.parser.block_parser import BlockParser

# Configure RPC
os.environ['RPC_MIN_REQUEST_INTERVAL'] = '3.0'  # More conservative settings
os.environ['RPC_THROTTLE_FACTOR'] = '2.0'
os.environ['CIRCUIT_RESET_TIMEOUT'] = '60'
os.environ['RADIANT_RPC_TIMEOUT'] = '120'
os.environ['RPC_MAX_RETRIES'] = '30'

# Database connection
DATABASE_URL = os.environ.get(
    "DATABASE_URL", 
    "postgresql://postgres:postgres@rxindexer-db:5432/rxindexer"
)

def get_db_session():
    """Create a database session."""
    try:
        engine = create_engine(DATABASE_URL)
        Session = sessionmaker(bind=engine)
        return Session()
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        return None

def get_sync_state(db):
    """Get the current sync state."""
    result = db.execute(text("SELECT height, is_syncing FROM sync_state LIMIT 1")).fetchone()
    if result:
        return {"height": result[0], "is_syncing": result[1]}
    else:
        return {"height": 0, "is_syncing": False}

def update_sync_state(db, height, is_syncing=True):
    """Update the sync state."""
    db.execute(
        text("UPDATE sync_state SET height = :height, is_syncing = :is_syncing"),
        {"height": height, "is_syncing": is_syncing}
    )
    db.commit()
    logger.info(f"Updated sync state: height={height}, is_syncing={is_syncing}")

def process_single_block(rpc, db, parser, height):
    """Process a single block at the specified height."""
    try:
        # Get the block hash
        block_hash = rpc.getblockhash(height)
        
        # Get the full block data
        block = rpc.getblock(block_hash, 2)  # Verbosity 2 to get transaction details
        
        # Process the block
        parser.parse_block(block, height, block_hash)
        
        # Commit the changes for this block
        db.commit()
        
        logger.info(f"Successfully processed block at height {height}")
        return True
    except Exception as e:
        logger.error(f"Error processing block {height}: {str(e)}")
        db.rollback()
        return False

def main():
    """Main serialized sync process."""
    start_time = time.time()
    logger.info("Starting serialized sync process")
    
    # Create database session
    db = get_db_session()
    if not db:
        logger.error("Failed to create database session")
        return
    
    # Initialize RPC client with only 1 connection
    rpc = RadiantRPC(pool_size=1)
    
    # Initialize block parser
    parser = BlockParser(rpc, db)
    
    # Get sync state
    sync_state = get_sync_state(db)
    current_height = sync_state['height']
    logger.info(f"Current sync state: height={current_height}, is_syncing={sync_state['is_syncing']}")
    
    # Get blockchain height
    try:
        chain_height = rpc.getblockcount()
        logger.info(f"Blockchain height: {chain_height}")
    except Exception as e:
        logger.error(f"Failed to get blockchain height: {str(e)}")
        return
    
    # Update sync state to syncing
    update_sync_state(db, current_height, True)
    
    # Process blocks one by one
    success_count = 0
    retry_count = 0
    max_retries = 3  # Maximum retries per block
    
    # Batch commit every N blocks
    batch_size = 10
    blocks_since_commit = 0
    
    for height in range(current_height + 1, chain_height + 1):
        retries = 0
        success = False
        
        while retries <= max_retries and not success:
            if retries > 0:
                logger.info(f"Retry #{retries} for block {height}")
                time.sleep(5 * retries)  # Exponential backoff
            
            success = process_single_block(rpc, db, parser, height)
            
            if not success:
                retries += 1
                retry_count += 1
        
        if success:
            success_count += 1
            blocks_since_commit += 1
            
            # Update sync state every 10 blocks
            if height % 10 == 0:
                update_sync_state(db, height, True)
                logger.info(f"Progress: {height}/{chain_height} ({(height/chain_height)*100:.2f}%)")
            
            # Commit every batch_size blocks
            if blocks_since_commit >= batch_size:
                db.commit()
                blocks_since_commit = 0
                logger.info(f"Committed batch at height {height}")
        else:
            logger.error(f"Failed to process block {height} after {max_retries} retries, skipping")
    
    # Final sync state update
    update_sync_state(db, chain_height, False)
    
    # Final stats
    end_time = time.time()
    duration = end_time - start_time
    logger.info(f"Sync completed: processed {success_count} blocks in {duration:.2f} seconds")
    logger.info(f"Total retries: {retry_count}")
    logger.info(f"Final height: {chain_height}")

if __name__ == "__main__":
    main()
'''
        
        # Write the serialized sync implementation to a file in the container
        cmd = f'''
        docker exec rxindexer-indexer bash -c 'cat > /app/serialized_sync.py << EOL
{serialized_sync}
EOL
chmod +x /app/serialized_sync.py'
        '''
        
        subprocess.run(cmd, shell=True, check=True)
        logger.info("Created serialized sync implementation")
        
        # Stop the indexer service
        subprocess.run(["docker", "exec", "rxindexer-indexer", "bash", "-c", "pkill -f 'python /app/src/indexer.py' || true"], check=False)
        logger.info("Stopped the indexer service")
        
        # Start our serialized sync
        subprocess.run(["docker", "exec", "-d", "rxindexer-indexer", "bash", "-c", "python /app/serialized_sync.py > /app/logs/serialized_output.log 2>&1"], check=True)
        logger.info("Started serialized sync")
        
        return True
    except Exception as e:
        logger.error(f"Error creating serialized sync: {e}")
        return False

if __name__ == "__main__":
    # Reset sync state
    reset_sync_state()
    
    # Create and start serialized sync
    create_serialized_sync()
    
    print("Serialized sync started. Check logs with:")
    print("docker exec rxindexer-indexer tail -f /app/logs/serialized_sync.log")
