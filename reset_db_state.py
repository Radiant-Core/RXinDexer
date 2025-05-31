#!/usr/bin/env python
# /Users/radiant/Desktop/RXinDexer/reset_db_state.py
# This script completely resets the database state by dropping and recreating 
# the sync_state table to resolve persistent transaction issues

import time
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# Database connection parameters (for Docker environment)
DB_PARAMS = {
    'dbname': 'rxindexer',
    'user': 'postgres',
    'password': 'postgres',
    'host': 'localhost',
    'port': '5432'
}

def reset_db_state():
    """Reset the database state by completely recreating the sync_state table."""
    print("Attempting to reset database state...")
    
    # Connect to PostgreSQL with isolation level set to AUTOCOMMIT
    # This ensures each statement is executed in its own transaction
    conn = psycopg2.connect(**DB_PARAMS)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cursor = conn.cursor()
    
    try:
        # Drop the sync_state table if it exists
        print("Dropping sync_state table if it exists...")
        cursor.execute("DROP TABLE IF EXISTS sync_state")
        
        # Create a fresh sync_state table with all required columns
        print("Creating new sync_state table...")
        cursor.execute("""
        CREATE TABLE sync_state (
            id INTEGER PRIMARY KEY,
            current_height INTEGER NOT NULL DEFAULT 0,
            is_syncing SMALLINT NOT NULL DEFAULT 0,
            last_updated_at FLOAT,
            last_error TEXT,
            current_chainwork VARCHAR(64)
        )
        """)
        
        # Insert an initial record
        current_time = time.time()
        print(f"Inserting initial sync_state record with timestamp {current_time}...")
        cursor.execute("""
        INSERT INTO sync_state (id, current_height, is_syncing, last_updated_at)
        VALUES (1, 0, 0, %s)
        """, (current_time,))
        
        # Verify the table and record were created successfully
        cursor.execute("SELECT * FROM sync_state")
        record = cursor.fetchone()
        print(f"Sync state record created: {record}")
        
        print("Database state reset successfully!")
        return True
    except Exception as e:
        print(f"Error resetting database state: {e}")
        return False
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    reset_db_state()
