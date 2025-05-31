# /Users/radiant/Desktop/RXinDexer/migrate_database.py
# This script standardizes the database schema for RXinDexer
# It resolves timestamp inconsistencies and adds missing fields required by APIs

import os
import sys
import logging
import psycopg2
from datetime import datetime

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
    """Create a database connection with autocommit mode."""
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )
    conn.autocommit = True
    return conn

def standardize_timestamps():
    """
    Standardize timestamp fields across all tables to use
    PostgreSQL's 'timestamp without time zone' type.
    """
    logger.info("Standardizing timestamp fields across all tables...")
    
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # First, check if sync_state.last_updated_at is double precision
            cur.execute("""
                SELECT data_type 
                FROM information_schema.columns 
                WHERE table_name = 'sync_state' AND column_name = 'last_updated_at'
            """)
            
            result = cur.fetchone()
            if result and result[0] == 'double precision':
                logger.info("Converting sync_state.last_updated_at from double precision to timestamp")
                
                # Get current values
                cur.execute("SELECT id, last_updated_at FROM sync_state")
                rows = cur.fetchall()
                
                # Add temporary column
                cur.execute("""
                    ALTER TABLE sync_state 
                    ADD COLUMN last_updated_at_temp timestamp without time zone
                """)
                
                # Convert and update values
                for row in rows:
                    sync_id, unix_timestamp = row
                    if unix_timestamp is not None:
                        # Convert Unix timestamp to PostgreSQL timestamp
                        pg_timestamp = datetime.fromtimestamp(unix_timestamp)
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
                
                # Replace original column with the temporary one - each ALTER statement separately
                cur.execute("ALTER TABLE sync_state DROP COLUMN last_updated_at")
                cur.execute("ALTER TABLE sync_state ALTER COLUMN last_updated_at_temp SET DEFAULT now()")
                cur.execute("ALTER TABLE sync_state ALTER COLUMN last_updated_at_temp SET NOT NULL")
                cur.execute("ALTER TABLE sync_state RENAME COLUMN last_updated_at_temp TO last_updated_at")
                
                logger.info("Successfully standardized sync_state timestamps")
            else:
                logger.info("sync_state.last_updated_at is already a timestamp type or doesn't exist")

def enhance_glyph_tokens_table():
    """Add missing fields to glyph_tokens table required by APIs."""
    logger.info("Enhancing glyph_tokens table...")
    
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # Check if token_supply exists
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
            
            # Check if minter_address exists
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
            
            # Check if collection_id exists
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
            
            # Improve token_metadata to JSONB
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
            
            logger.info("Successfully enhanced glyph_tokens table")

def enhance_holders_table():
    """Add missing fields to holders table required by APIs."""
    logger.info("Enhancing holders table...")
    
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # Improve token_balances to JSONB
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
            
            # Check if transaction_count exists
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
            
            # Add token_count field
            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'holders' AND column_name = 'token_count'
            """)
            
            if not cur.fetchone():
                logger.info("Adding token_count to holders table")
                cur.execute("""
                    ALTER TABLE holders 
                    ADD COLUMN token_count integer DEFAULT 0
                """)
            
            # Add index on token_balances
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
            
            logger.info("Successfully enhanced holders table")

def enhance_utxos_table():
    """Add missing fields and indexes to utxos table."""
    logger.info("Enhancing utxos table...")
    
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # Check for script_type field
            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'utxos' AND column_name = 'script_type'
            """)
            
            if not cur.fetchone():
                logger.info("Adding script_type to utxos table")
                cur.execute("""
                    ALTER TABLE utxos 
                    ADD COLUMN script_type character varying(20)
                """)
            
            # Add composite index on address and block_height for faster history queries
            cur.execute("""
                SELECT indexname 
                FROM pg_indexes 
                WHERE tablename = 'utxos' AND indexname = 'idx_utxo_address_block_height'
            """)
            
            if not cur.fetchone():
                logger.info("Adding composite index on address and block_height")
                cur.execute("""
                    CREATE INDEX idx_utxo_address_block_height ON utxos (address, block_height)
                """)
            
            logger.info("Successfully enhanced utxos table")

def create_api_optimized_views():
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
                CREATE MATERIALIZED VIEW IF NOT EXISTS token_holder_view AS
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
            
            # Create or replace rich_list_view
            cur.execute("""
                CREATE MATERIALIZED VIEW IF NOT EXISTS rich_list_view AS
                SELECT 
                    address,
                    SUM(CASE WHEN spent = FALSE THEN amount ELSE 0 END) as balance
                FROM utxos
                GROUP BY address
                ORDER BY balance DESC
                LIMIT 1000
                WITH DATA
            """)
            
            # Create index on rich_list_view
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_rich_list_view_address
                ON rich_list_view (address)
            """)
            
            logger.info("Successfully created optimized views for API endpoints")

def create_or_enhance_nft_tables():
    """Create or enhance NFT-related tables."""
    logger.info("Creating or enhancing NFT-related tables...")
    
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # Check if nft_metadata table exists
            cur.execute("""
                SELECT tablename 
                FROM pg_tables 
                WHERE tablename = 'nft_metadata'
            """)
            
            if not cur.fetchone():
                logger.info("Creating nft_metadata table")
                cur.execute("""
                    CREATE TABLE nft_metadata (
                        token_id character varying(64) PRIMARY KEY,
                        name character varying(255),
                        description text,
                        media_url text,
                        thumbnail_url text,
                        attributes jsonb DEFAULT '{}'::jsonb,
                        properties jsonb DEFAULT '{}'::jsonb,
                        creator_address character varying(64),
                        creation_txid character varying(64),
                        creation_timestamp timestamp without time zone,
                        created_at timestamp without time zone DEFAULT now(),
                        updated_at timestamp without time zone DEFAULT now()
                    )
                """)
            
            # Check if nft_collections table exists
            cur.execute("""
                SELECT tablename 
                FROM pg_tables 
                WHERE tablename = 'nft_collections'
            """)
            
            if not cur.fetchone():
                logger.info("Creating nft_collections table")
                cur.execute("""
                    CREATE TABLE nft_collections (
                        collection_id character varying(64) PRIMARY KEY,
                        name character varying(255),
                        description text,
                        creator_address character varying(64),
                        creation_txid character varying(64),
                        creation_timestamp timestamp without time zone,
                        floor_price numeric(38,8) DEFAULT 0,
                        volume_24h numeric(38,8) DEFAULT 0,
                        volume_total numeric(38,8) DEFAULT 0,
                        item_count integer DEFAULT 0,
                        holder_count integer DEFAULT 0,
                        created_at timestamp without time zone DEFAULT now(),
                        updated_at timestamp without time zone DEFAULT now()
                    )
                """)
            
            logger.info("Successfully created or enhanced NFT-related tables")

def main():
    """Main entry point."""
    try:
        logger.info("Starting database migration process")
        
        # Standardize timestamps
        standardize_timestamps()
        
        # Enhance tables with required fields
        enhance_glyph_tokens_table()
        enhance_holders_table()
        enhance_utxos_table()
        
        # Create or enhance NFT tables
        create_or_enhance_nft_tables()
        
        # Create optimized views for APIs
        create_api_optimized_views()
        
        logger.info("Database migration completed successfully")
    except Exception as e:
        logger.error(f"Error during database migration: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
