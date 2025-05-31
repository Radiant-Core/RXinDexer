# /Users/radiant/Desktop/RXinDexer/src/scripts/standardize_timestamps.py
# This script standardizes timestamp fields across all database tables
# to ensure consistent timestamp handling throughout the RXinDexer application

import os
import sys
import psycopg2
import logging
from datetime import datetime
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Database connection parameters
DB_HOST = os.getenv('DB_HOST', 'db')
DB_PORT = int(os.getenv('DB_PORT', 5432))
DB_NAME = os.getenv('DB_NAME', 'rxindexer')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'postgres')

def get_db_connection():
    """Create a database connection."""
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )
    conn.autocommit = True  # Avoid transaction issues
    return conn

def convert_unix_to_timestamp(unix_timestamp):
    """Convert Unix timestamp to PostgreSQL timestamp."""
    if unix_timestamp is None:
        return None
    return datetime.fromtimestamp(unix_timestamp)

def standardize_sync_state_timestamps():
    """
    Convert sync_state.last_updated_at from double precision (Unix timestamp) 
    to timestamp without time zone to match other tables.
    """
    logger.info("Standardizing sync_state timestamps...")
    
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # First, check if the column exists and is double precision
            cur.execute("""
                SELECT data_type 
                FROM information_schema.columns 
                WHERE table_name = 'sync_state' AND column_name = 'last_updated_at'
            """)
            
            result = cur.fetchone()
            if result and result[0] == 'double precision':
                # Get the current Unix timestamp value
                cur.execute("SELECT id, last_updated_at FROM sync_state")
                rows = cur.fetchall()
                
                # Add a temporary column
                cur.execute("""
                    ALTER TABLE sync_state 
                    ADD COLUMN last_updated_at_temp timestamp without time zone
                """)
                
                # Convert and update values
                for row in rows:
                    sync_id, unix_timestamp = row
                    if unix_timestamp is not None:
                        pg_timestamp = convert_unix_to_timestamp(unix_timestamp)
                        cur.execute("""
                            UPDATE sync_state 
                            SET last_updated_at_temp = %s
                            WHERE id = %s
                        """, (pg_timestamp, sync_id))
                    else:
                        # If null, set to current time
                        cur.execute("""
                            UPDATE sync_state 
                            SET last_updated_at_temp = NOW()
                            WHERE id = %s
                        """, (sync_id,))
                
                # Drop the old column and rename the new one
                cur.execute("""
                    ALTER TABLE sync_state 
                    DROP COLUMN last_updated_at,
                    ALTER COLUMN last_updated_at_temp SET DEFAULT now(),
                    ALTER COLUMN last_updated_at_temp SET NOT NULL,
                    RENAME COLUMN last_updated_at_temp TO last_updated_at
                """)
                
                logger.info("Successfully standardized sync_state timestamps")
            else:
                logger.info("No standardization needed for sync_state timestamps")

def add_missing_fields():
    """Add any missing fields required by the APIs."""
    logger.info("Adding missing fields to database tables...")
    
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # Check if we need to add token_supply to glyph_tokens
            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'glyph_tokens' AND column_name = 'token_supply'
            """)
            
            if not cur.fetchone():
                logger.info("Adding token_supply to glyph_tokens table")
                cur.execute("""
                    ALTER TABLE glyph_tokens 
                    ADD COLUMN token_supply numeric(38,8) DEFAULT 1
                """)
            
            # Check if we need to add minter_address to glyph_tokens
            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'glyph_tokens' AND column_name = 'minter_address'
            """)
            
            if not cur.fetchone():
                logger.info("Adding minter_address to glyph_tokens table")
                cur.execute("""
                    ALTER TABLE glyph_tokens 
                    ADD COLUMN minter_address character varying(64)
                """)
            
            # Check if we need to add collection_id to glyph_tokens
            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'glyph_tokens' AND column_name = 'collection_id'
            """)
            
            if not cur.fetchone():
                logger.info("Adding collection_id to glyph_tokens table")
                cur.execute("""
                    ALTER TABLE glyph_tokens 
                    ADD COLUMN collection_id character varying(64)
                """)
            
            # Improve token_metadata to be JSONB for better performance
            cur.execute("""
                SELECT data_type 
                FROM information_schema.columns 
                WHERE table_name = 'glyph_tokens' AND column_name = 'token_metadata'
            """)
            
            result = cur.fetchone()
            if result and result[0] == 'json':
                logger.info("Converting token_metadata from JSON to JSONB for better performance")
                cur.execute("""
                    ALTER TABLE glyph_tokens 
                    ALTER COLUMN token_metadata TYPE jsonb USING token_metadata::jsonb
                """)
            
            # Improve token_balances in holders table to be JSONB
            cur.execute("""
                SELECT data_type 
                FROM information_schema.columns 
                WHERE table_name = 'holders' AND column_name = 'token_balances'
            """)
            
            result = cur.fetchone()
            if result and result[0] == 'text':
                logger.info("Converting token_balances from TEXT to JSONB for better performance")
                cur.execute("""
                    ALTER TABLE holders 
                    ALTER COLUMN token_balances TYPE jsonb USING token_balances::jsonb
                """)
            
            # Check if we need to add transaction_count to holders
            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'holders' AND column_name = 'transaction_count'
            """)
            
            if not cur.fetchone():
                logger.info("Adding transaction_count to holders table")
                cur.execute("""
                    ALTER TABLE holders 
                    ADD COLUMN transaction_count integer DEFAULT 0
                """)
            
            # Ensure we have proper indexes for token balances
            cur.execute("""
                SELECT indexname 
                FROM pg_indexes 
                WHERE tablename = 'holders' AND indexname = 'idx_holder_token_balances'
            """)
            
            if not cur.fetchone():
                logger.info("Adding GIN index for token_balances to improve query performance")
                cur.execute("""
                    CREATE INDEX idx_holder_token_balances ON holders USING GIN (token_balances)
                """)
                
            logger.info("Successfully added missing fields")

def ensure_required_api_fields():
    """
    Check if all fields required by the API endpoints are present
    and add any missing ones.
    """
    logger.info("Ensuring all required API fields are present...")
    
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # Check if nft_metadata table has the necessary fields
            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'nft_metadata' AND column_name = 'media_url'
            """)
            
            if not cur.fetchone():
                logger.info("Adding media_url to nft_metadata table")
                cur.execute("""
                    ALTER TABLE nft_metadata 
                    ADD COLUMN media_url text,
                    ADD COLUMN thumbnail_url text,
                    ADD COLUMN attributes jsonb DEFAULT '{}'::jsonb,
                    ADD COLUMN properties jsonb DEFAULT '{}'::jsonb
                """)
            
            # Check if nft_collections table has the necessary fields
            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'nft_collections' AND column_name = 'floor_price'
            """)
            
            if not cur.fetchone():
                logger.info("Adding market data fields to nft_collections table")
                cur.execute("""
                    ALTER TABLE nft_collections 
                    ADD COLUMN floor_price numeric(38,8) DEFAULT 0,
                    ADD COLUMN volume_24h numeric(38,8) DEFAULT 0,
                    ADD COLUMN volume_total numeric(38,8) DEFAULT 0,
                    ADD COLUMN item_count integer DEFAULT 0,
                    ADD COLUMN holder_count integer DEFAULT 0
                """)
            
            logger.info("Successfully ensured all required API fields are present")

def create_api_views():
    """Create optimized views for API endpoints."""
    logger.info("Creating optimized views for API endpoints...")
    
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # Create or replace address_balance_view
            cur.execute("""
                CREATE OR REPLACE VIEW address_balance_view AS
                SELECT 
                    address,
                    SUM(CASE WHEN spent = FALSE THEN amount ELSE 0 END) as balance,
                    COUNT(CASE WHEN spent = FALSE THEN 1 END) as utxo_count,
                    COUNT(CASE WHEN spent = FALSE AND token_ref IS NOT NULL THEN 1 END) as token_utxo_count
                FROM utxos
                GROUP BY address
            """)
            
            # Create or replace token_holder_view
            cur.execute("""
                CREATE OR REPLACE MATERIALIZED VIEW token_holder_view AS
                SELECT 
                    token_ref,
                    COUNT(DISTINCT address) as holder_count
                FROM utxos
                WHERE spent = FALSE AND token_ref IS NOT NULL
                GROUP BY token_ref
                WITH DATA
            """)
            
            # Create index on the materialized view
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_token_holder_view_token_ref
                ON token_holder_view (token_ref)
            """)
            
            logger.info("Successfully created optimized views for API endpoints")

def main():
    """Main entry point."""
    try:
        logger.info("Starting database standardization process")
        
        # Standardize timestamps
        standardize_sync_state_timestamps()
        
        # Add missing fields
        add_missing_fields()
        
        # Ensure API required fields
        ensure_required_api_fields()
        
        # Create API views
        create_api_views()
        
        logger.info("Database standardization completed successfully")
    except Exception as e:
        logger.error(f"Error during database standardization: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
