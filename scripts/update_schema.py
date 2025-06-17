#!/usr/bin/env python3
# /Users/radiant/Desktop/RXinDexer/scripts/update_schema.py
# This script updates the database schema to match the SQLAlchemy models.
# It's a one-time script to apply the schema changes we've made to the models.

import os
import sys
from pathlib import Path

# Add the project root to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError
from src.config import DATABASE_URL
from src.models import Base

def apply_schema_changes():
    """Apply schema changes to the database."""
    print("Connecting to the database...")
    engine = create_engine(DATABASE_URL)
    
    # Create a connection
    with engine.connect() as connection:
        # Start a transaction
        with connection.begin():
            print("Applying schema changes...")
            
            # Create all tables if they don't exist
            try:
                print("Creating tables if they don't exist...")
                Base.metadata.create_all(engine)
                
                # Add columns that might be missing
                print("Adding missing columns...")
                
                # Add missing columns to blocks table
                add_columns = [
                    """
                    DO $$
                    BEGIN
                        -- Add version if it doesn't exist
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                      WHERE table_name='blocks' AND column_name='version') THEN
                            ALTER TABLE blocks ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
                        END IF;
                        
                        -- Add prev_hash if it doesn't exist
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                      WHERE table_name='blocks' AND column_name='prev_hash') THEN
                            ALTER TABLE blocks ADD COLUMN prev_hash VARCHAR(64);
                        END IF;
                        
                        -- Add merkle_root if it doesn't exist
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                      WHERE table_name='blocks' AND column_name='merkle_root') THEN
                            ALTER TABLE blocks ADD COLUMN merkle_root VARCHAR(64) NOT NULL DEFAULT '';
                        END IF;
                        
                        -- Add bits if it doesn't exist
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                      WHERE table_name='blocks' AND column_name='bits') THEN
                            ALTER TABLE blocks ADD COLUMN bits INTEGER NOT NULL DEFAULT 0;
                        END IF;
                        
                        -- Add nonce if it doesn't exist
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                      WHERE table_name='blocks' AND column_name='nonce') THEN
                            ALTER TABLE blocks ADD COLUMN nonce INTEGER NOT NULL DEFAULT 0;
                        END IF;
                        
                        -- Add chainwork if it doesn't exist
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                      WHERE table_name='blocks' AND column_name='chainwork') THEN
                            ALTER TABLE blocks ADD COLUMN chainwork VARCHAR(64);
                        END IF;
                        
                        -- Add updated_at if it doesn't exist
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                      WHERE table_name='blocks' AND column_name='updated_at') THEN
                            ALTER TABLE blocks ADD COLUMN updated_at TIMESTAMP WITHOUT TIME ZONE 
                                            NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc');
                        END IF;
                    END $$;
                    """,
                    
                    # Add missing columns to transactions table
                    """
                    DO $$
                    BEGIN
                        -- Add updated_at if it doesn't exist
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                      WHERE table_name='transactions' AND column_name='updated_at') THEN
                            ALTER TABLE transactions ADD COLUMN updated_at TIMESTAMP WITHOUT TIME ZONE 
                                                NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc');
                        END IF;
                    END $$;
                    """,
                    
                    # Add missing columns to utxos table
                    """
                    DO $$
                    BEGIN
                        -- Add updated_at if it doesn't exist
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                      WHERE table_name='utxos' AND column_name='updated_at') THEN
                            ALTER TABLE utxos ADD COLUMN updated_at TIMESTAMP WITHOUT TIME ZONE 
                                          NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc');
                        END IF;
                    END $$;
                    """,
                    
                    # Add missing columns to glyph_tokens table
                    """
                    DO $$
                    BEGIN
                        -- Add updated_at if it doesn't exist
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                      WHERE table_name='glyph_tokens' AND column_name='updated_at') THEN
                            ALTER TABLE glyph_tokens ADD COLUMN updated_at TIMESTAMP WITHOUT TIME ZONE 
                                                 NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc');
                        END IF;
                    END $$;
                    """,
                    
                    # Add missing columns to sync_state table
                    """
                    DO $$
                    BEGIN
                        -- Add glyph_scan_height if it doesn't exist
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                      WHERE table_name='sync_state' AND column_name='glyph_scan_height') THEN
                            ALTER TABLE sync_state ADD COLUMN glyph_scan_height INTEGER NOT NULL DEFAULT 0;
                        END IF;
                        
                        -- Add updated_at if it doesn't exist
                        IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                      WHERE table_name='sync_state' AND column_name='updated_at') THEN
                            ALTER TABLE sync_state ADD COLUMN updated_at TIMESTAMP WITHOUT TIME ZONE 
                                                NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc');
                        END IF;
                    END $$;
                    """
                ]
                
                # Execute each ALTER TABLE statement
                for stmt in add_columns:
                    connection.execute(text(stmt))
                
                # Add indexes
                print("Adding indexes...")
                add_indexes = [
                    # Blocks table indexes
                    """
                    CREATE INDEX IF NOT EXISTS idx_blocks_prev_hash ON blocks(prev_hash);
                    """,
                    """
                    CREATE INDEX IF NOT EXISTS idx_blocks_timestamp ON blocks(timestamp);
                    """,
                    
                    # Transactions table indexes
                    """
                    CREATE INDEX IF NOT EXISTS idx_transactions_block_height ON transactions(block_height);
                    """,
                    """
                    CREATE INDEX IF NOT EXISTS idx_transactions_created_at ON transactions(created_at);
                    """,
                    
                    # UTXOs table indexes
                    """
                    CREATE INDEX IF NOT EXISTS idx_utxos_address ON utxos(address);
                    """,
                    """
                    CREATE INDEX IF NOT EXISTS idx_utxos_block_height ON utxos(block_height);
                    """,
                    """
                    CREATE INDEX IF NOT EXISTS idx_utxos_token_ref ON utxos(token_ref);
                    """,
                    
                    # Sync state index
                    """
                    CREATE INDEX IF NOT EXISTS idx_sync_state_current_height ON sync_state(current_height);
                    """
                ]
                
                # Execute each CREATE INDEX statement
                for stmt in add_indexes:
                    connection.execute(text(stmt))
                
                print("✅ Schema updates applied successfully!")
                
            except Exception as e:
                print(f"❌ Error applying schema changes: {e}")
                raise

if __name__ == "__main__":
    print("Starting database schema update...")
    apply_schema_changes()
    print("✅ Database schema update completed successfully!")
