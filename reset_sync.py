#!/usr/bin/env python
# /Users/radiant/Desktop/RXinDexer/reset_sync.py
# This script resets the sync state and cleans up test blocks to fix indexing issues.
# It properly resets the sync data without removing valid blockchain data.

import os
import logging
import argparse
import time
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def get_db_connection_string():
    """Get the database connection string from environment or use default."""
    # Use environment variable if set, otherwise use default
    db_url = os.environ.get('DATABASE_URL')
    
    # If running inside container, use the Docker network name
    if os.environ.get("IN_DOCKER", "false").lower() == "true":
        if not db_url:
            db_url = "postgresql://postgres:postgres@rxindexer-db:5432/rxindexer"
    else:
        # Otherwise use localhost
        if not db_url:
            db_url = "postgresql://postgres:postgres@localhost:5432/rxindexer"
    
    logger.info(f"Using database connection: {db_url}")
    return db_url

def reset_sync_state(connection, keep_real_blocks=True):
    """Reset the sync state in the database."""
    try:
        # Check if we have any real blockchain data
        has_real_blocks = False
        if keep_real_blocks:
            # Look for blocks with heights < 900000 (assuming test blocks are above this)
            result = connection.execute(text(
                "SELECT COUNT(*) FROM blocks WHERE height < 900000"
            ))
            real_block_count = result.scalar()
            has_real_blocks = real_block_count > 0
            logger.info(f"Found {real_block_count} real blocks in database")
        
        # Remove test blocks (heights > 900000)
        connection.execute(text(
            "DELETE FROM blocks WHERE height > 900000"
        ))
        logger.info("Removed test blocks")
        
        if has_real_blocks:
            # Find the highest real block
            result = connection.execute(text(
                "SELECT MAX(height) FROM blocks"
            ))
            highest_block = result.scalar() or 0
            
            # Update sync state to this block height
            connection.execute(text(
                "UPDATE sync_state SET current_height = :height, is_syncing = 1"
            ), {"height": highest_block})
            logger.info(f"Reset sync state to height {highest_block}")
        else:
            # Reset sync state to 0
            connection.execute(text(
                "UPDATE sync_state SET current_height = 0, is_syncing = 1, " 
                "current_hash = NULL, current_chainwork = NULL, "
                "last_updated_at = NOW(), glyph_scan_height = 0"
            ))
            logger.info("Reset sync state to height 0")
        
        # Commit the transaction
        connection.execute(text("COMMIT"))
        logger.info("Sync state reset successfully")
        return True
        
    except SQLAlchemyError as e:
        logger.error(f"Database error: {e}")
        connection.execute(text("ROLLBACK"))
        return False
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        connection.execute(text("ROLLBACK"))
        return False

def optimize_rpc_settings():
    """Update RPC client configuration in containers for better stability."""
    try:
        # Set environment variables for RPC client with more aggressive throttling
        os.system("""
docker exec rxindexer-indexer bash -c '
echo "# RPC optimized settings - added $(date)" >> /app/.env
echo "export RPC_MIN_REQUEST_INTERVAL=2.0" >> /app/.env
echo "export RPC_THROTTLE_FACTOR=1.8" >> /app/.env
echo "export CIRCUIT_RESET_TIMEOUT=30" >> /app/.env
echo "export CIRCUIT_FAILURE_THRESHOLD=10" >> /app/.env
echo "export HEALTH_CHECK_INTERVAL=25" >> /app/.env
echo "export CONNECTION_RETRY_DELAY=15" >> /app/.env
echo "export RADIANT_RPC_TIMEOUT=90" >> /app/.env
echo "export SYNC_MAX_WORKERS=2" >> /app/.env
echo "export RPC_MAX_RETRIES=20" >> /app/.env
echo "export ENABLE_PARALLEL_PROCESSING=false" >> /app/.env
'""")
        
        # Also create a temporary configuration file to use when restarting
        os.system("""
docker exec rxindexer-indexer bash -c 'cat > /app/throttle_config.py << EOL
import os

# Set environment variables
os.environ["RPC_MIN_REQUEST_INTERVAL"] = "2.0"
os.environ["RPC_THROTTLE_FACTOR"] = "1.8"
os.environ["CIRCUIT_RESET_TIMEOUT"] = "30"
os.environ["CIRCUIT_FAILURE_THRESHOLD"] = "10"
os.environ["HEALTH_CHECK_INTERVAL"] = "25"
os.environ["CONNECTION_RETRY_DELAY"] = "15"
os.environ["RADIANT_RPC_TIMEOUT"] = "90"
os.environ["SYNC_MAX_WORKERS"] = "2"
os.environ["RPC_MAX_RETRIES"] = "20"
os.environ["ENABLE_PARALLEL_PROCESSING"] = "false"
EOL'""")
        
        # Execute the config script at container startup
        os.system("""
docker exec rxindexer-indexer bash -c 'echo "python /app/throttle_config.py" >> /app/start.sh'""")
        
        logger.info("RPC settings comprehensively optimized")
        return True
    except Exception as e:
        logger.error(f"Failed to optimize RPC settings: {e}")
        return False

def restart_containers():
    """Restart the necessary containers."""
    try:
        os.system("docker restart rxindexer-indexer")
        logger.info("Indexer container restarted")
        return True
    except Exception as e:
        logger.error(f"Failed to restart containers: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description='Reset RXinDexer sync state')
    parser.add_argument('--keep-real-blocks', action='store_true', 
                        help='Keep real blockchain data (removes only test blocks)')
    args = parser.parse_args()
    
    try:
        # Create database engine
        db_url = get_db_connection_string()
        engine = create_engine(db_url)
        
        # Create a connection
        with engine.connect() as connection:
            # Begin a transaction
            connection.execute(text("BEGIN"))
            
            # Reset sync state
            if reset_sync_state(connection, args.keep_real_blocks):
                logger.info("Database reset complete")
            else:
                logger.error("Failed to reset database")
                return False
        
        # Optimize RPC settings
        optimize_rpc_settings()
        
        # Restart containers
        restart_containers()
        
        logger.info("Sync reset complete. Indexer will begin syncing from reset point.")
        return True
        
    except Exception as e:
        logger.error(f"Error: {e}")
        return False

if __name__ == "__main__":
    main()
