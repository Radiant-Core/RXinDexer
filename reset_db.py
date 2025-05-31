#!/usr/bin/env python
# /Users/radiant/Desktop/RXinDexer/reset_db.py
# This script directly resets the sync_state table to resolve database issues
# and get the indexer running again after transaction failures

import logging
import psycopg2
import os
import time
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Database connection parameters
DB_HOST = os.environ.get("DB_HOST", "db")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "rxindexer")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "postgres")

def reset_sync_state():
    """
    Directly reset the sync_state table to a clean state.
    This bypasses any transaction issues by using a fresh connection.
    """
    logger.info("Attempting to reset sync_state table...")
    
    try:
        # Connect directly to PostgreSQL with psycopg2
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        
        # Set autocommit to avoid transaction blocks
        conn.set_session(autocommit=True)
        
        # Create a cursor
        cursor = conn.cursor()
        
        # Check if sync_state table exists
        cursor.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'sync_state')")
        table_exists = cursor.fetchone()[0]
        
        if not table_exists:
            logger.info("sync_state table doesn't exist. Creating it...")
            cursor.execute("""
            CREATE TABLE sync_state (
                id INTEGER PRIMARY KEY,
                current_height INTEGER NOT NULL DEFAULT 0,
                is_syncing SMALLINT NOT NULL DEFAULT 0,
                last_updated_at FLOAT,
                last_error TEXT
            )
            """)
            logger.info("sync_state table created successfully")
        
        # Check if there's an existing record
        cursor.execute("SELECT COUNT(*) FROM sync_state WHERE id = 1")
        record_exists = cursor.fetchone()[0] > 0
        
        if record_exists:
            # Update existing record with safe defaults
            logger.info("Resetting existing sync_state record...")
            cursor.execute("""
            UPDATE sync_state 
            SET is_syncing = 0, 
                last_error = NULL,
                last_updated_at = %s
            WHERE id = 1
            """, (time.time(),))
        else:
            # Insert a new record
            logger.info("Creating new sync_state record...")
            cursor.execute("""
            INSERT INTO sync_state (id, current_height, is_syncing, last_updated_at)
            VALUES (1, 0, 0, %s)
            """, (time.time(),))
        
        # Verify the result
        cursor.execute("SELECT * FROM sync_state WHERE id = 1")
        record = cursor.fetchone()
        logger.info(f"Sync state record now: {record}")
        
        # Close cursor and connection
        cursor.close()
        conn.close()
        
        logger.info("Successfully reset sync_state table")
        return True
    except Exception as e:
        logger.error(f"Failed to reset sync_state: {str(e)}")
        return False

if __name__ == "__main__":
    reset_sync_state()
