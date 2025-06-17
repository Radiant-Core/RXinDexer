#!/usr/bin/env python
# /Users/radiant/Desktop/RXinDexer/fix_db_transactions.py
# This file implements a fix for database transaction errors in the RXinDexer

import subprocess
import logging
import time

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def reset_database():
    """Reset database tables and transactions to fix aborted transactions"""
    try:
        # Stop the indexer service first
        subprocess.run(["docker", "stop", "rxindexer-indexer"], check=True)
        logger.info("Stopped indexer container")
        
        # Repair the database by recreating sync state
        cmd = """
        docker exec rxindexer-db psql -U postgres -d rxindexer -c "
        -- Reset all transactions
        TRUNCATE sync_state;
        TRUNCATE blocks CASCADE;
        TRUNCATE transactions CASCADE;
        
        -- Create fresh sync state
        INSERT INTO sync_state (id, current_height, is_syncing, glyph_scan_height, created_at, updated_at) 
        VALUES (1, 0, 1, 0, NOW(), NOW());
        "
        """
        subprocess.run(cmd, shell=True, check=True)
        logger.info("Database tables reset successfully")
        
        # Start the indexer service
        subprocess.run(["docker", "start", "rxindexer-indexer"], check=True)
        logger.info("Started indexer container")
        
        return True
    except Exception as e:
        logger.error(f"Error resetting database: {e}")
        return False

def monitor_sync_progress():
    """Monitor the sync progress to verify blocks are being processed"""
    try:
        time.sleep(30)  # Wait for indexer to initialize
        
        # Check if blocks are being inserted
        cmd = "docker exec rxindexer-db psql -U postgres -d rxindexer -c \"SELECT COUNT(*) FROM blocks\""
        block_count = subprocess.check_output(cmd, shell=True).decode().strip()
        logger.info(f"Current block count: {block_count}")
        
        # Check sync state
        cmd = "docker exec rxindexer-db psql -U postgres -d rxindexer -c \"SELECT current_height, is_syncing FROM sync_state\""
        sync_state = subprocess.check_output(cmd, shell=True).decode().strip()
        logger.info(f"Current sync state: {sync_state}")
        
        # Check recent logs
        cmd = "docker exec rxindexer-indexer tail -n 20 /app/logs/indexer.log"
        logs = subprocess.check_output(cmd, shell=True).decode().strip()
        logger.info(f"Recent logs:\n{logs}")
        
        return True
    except Exception as e:
        logger.error(f"Error monitoring sync progress: {e}")
        return False

if __name__ == "__main__":
    print("Starting database transaction fix...")
    
    # Reset database
    success = reset_database()
    if success:
        print("Database reset successfully")
    else:
        print("Failed to reset database")
        exit(1)
    
    # Monitor sync progress
    print("Monitoring sync progress (this will take about 30 seconds)...")
    monitor_sync_progress()
    
    print("\nFix completed. To check API endpoints, try:")
    print("  curl http://localhost:8000/api/v1/sync/status")
    print("  curl http://localhost:8000/api/v1/blocks/latest")
    print("\nNext steps:")
    print("1. Keep monitoring the indexer logs: docker exec rxindexer-indexer tail -f /app/logs/indexer.log")
    print("2. Check sync status API endpoint to verify sync progress")
    print("3. Allow the indexer to run for some time to sync blocks")
