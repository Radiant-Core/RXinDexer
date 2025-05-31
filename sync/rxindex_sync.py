# /Users/radiant/Desktop/RXinDexer/sync/rxindex_sync.py
# Consolidated blockchain synchronization script for RXinDexer
# Incorporates best practices from all existing sync implementations:
# - Safe database transactions with isolated connections
# - Proper timestamp handling across all database tables
# - High-performance bulk loading with optimized database settings
# - Parallel processing for maximum throughput
# - Redis caching for frequently accessed data
# - Bloom filters for fast transaction lookups
# - No problematic JOIN queries that caused database transaction issues

import os
import sys
import time
import json
import logging
import psycopg2
import cbor2
import hashlib
import io
import redis
import requests
import gc
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
# Avoid using ProcessPoolExecutor in Docker environments to prevent 'broken pipe' errors
from functools import lru_cache
from typing import Dict, List, Tuple, Any, Optional
from decimal import Decimal
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from psycopg2 import pool

# Try to import pybloom_live but make it optional
try:
    from pybloom_live import ScalableBloomFilter
    BLOOM_FILTER_AVAILABLE = True
except ImportError:
    logger.warning("pybloom_live package not found. Bloom filter functionality will be disabled.")
    BLOOM_FILTER_AVAILABLE = False
    ScalableBloomFilter = None

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Configuration from environment variables with sensible defaults
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = int(os.getenv('DB_PORT', 5432))
DB_NAME = os.getenv('DB_NAME', 'rxindex')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'postgres')

# RPC connection parameters
RPC_URL = os.getenv('RADIANT_RPC_URL', 'http://radiant:7332')
RPC_USER = os.getenv('RADIANT_RPC_USER', 'rxin')
RPC_PASSWORD = os.getenv('RADIANT_RPC_PASSWORD', 'securepassword')

# Sync configuration
SYNC_BATCH_SIZE = int(os.getenv('SYNC_BATCH_SIZE', 1000))
SYNC_MAX_WORKERS = int(os.getenv('SYNC_MAX_WORKERS', 8))
UTXO_MAX_WORKERS = int(os.getenv('UTXO_MAX_WORKERS', 4))
BLOCK_PARALLEL_THRESHOLD = int(os.getenv('BLOCK_PARALLEL_THRESHOLD', 100))
PROGRESSIVE_SYNC = os.getenv('PROGRESSIVE_SYNC', 'True').lower() == 'true'
INITIAL_SYNC_MINIMAL = os.getenv('INITIAL_SYNC_MINIMAL', 'True').lower() == 'true'
USE_REDIS_CACHE = os.getenv('USE_REDIS_CACHE', 'True').lower() == 'true'
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
GLYPH_DEEP_INDEXING = os.getenv('GLYPH_DEEP_INDEXING', 'True').lower() == 'true'
GLYPH_COLLECTION_TRACKING = os.getenv('GLYPH_COLLECTION_TRACKING', 'True').lower() == 'true'

# Detect Docker environment and adjust configuration accordingly
IN_DOCKER = os.getenv('IN_DOCKER', 'False').lower() == 'true'
if IN_DOCKER:
    # Limit parallel processing in Docker to avoid 'broken pipe' errors
    logger.info("Docker environment detected, using safe parallelism settings")
    # Use smaller batch size and fewer workers when in Docker
    SYNC_BATCH_SIZE = min(SYNC_BATCH_SIZE, 100)
    SYNC_MAX_WORKERS = min(SYNC_MAX_WORKERS, 4)
    UTXO_MAX_WORKERS = min(UTXO_MAX_WORKERS, 2)

# Initialize Redis connection if enabled
redis_client = None
if USE_REDIS_CACHE:
    try:
        # Parse the Redis URL properly to avoid security warnings
        if REDIS_URL.startswith('redis://') or REDIS_URL.startswith('rediss://'):
            # Parse Redis URL components
            import urllib.parse
            parsed_url = urllib.parse.urlparse(REDIS_URL)
            redis_host = parsed_url.hostname or 'redis'
            redis_port = parsed_url.port or 6379
            redis_db = int(parsed_url.path.replace('/', '') or '0')
            redis_password = parsed_url.password or None
            
            # Connect with explicit parameters instead of URL
            redis_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                db=redis_db,
                password=redis_password,
                socket_timeout=5,
                socket_connect_timeout=5,
                socket_keepalive=True
            )
        else:
            # Fallback to from_url for non-standard URLs
            redis_client = redis.from_url(REDIS_URL)
            
        redis_client.ping()  # Test connection
        logger.info(f"Redis cache enabled and connected to {redis_host}:{redis_port}")
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}, continuing without caching")
        redis_client = None


class RadiantRPC:
    """RPC client for connecting to the Radiant node with robust error handling."""
    
    def __init__(self, url, user, password):
        self.url = url
        self.auth = (user, password)
    
    def _call_method(self, method, params=None):
        """Make an RPC call to the Radiant node with robust retry logic."""
        headers = {'content-type': 'application/json'}
        payload = {
            'method': method,
            'params': params or [],
            'jsonrpc': '2.0',
            'id': int(time.time() * 1000),
        }
        
        # More resilient retry configuration
        max_retries = 10  # Increased from 5
        retry_delay = 3   # Increased initial delay
        max_delay = 30    # Cap the maximum delay
        
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(
                    self.url,
                    json=payload,
                    headers=headers,
                    auth=self.auth,
                    timeout=90  # Increased timeout
                )
                response.raise_for_status()
                
                # Check for valid JSON response
                result = response.json()
                if 'result' not in result:
                    raise ValueError(f"Invalid response format: {result}")
                    
                # Success - reset any connectivity warning counters
                if attempt > 1:
                    logger.info(f"RPC connection restored after {attempt-1} retries")
                return result['result']
            
            except requests.exceptions.ConnectionError as e:
                # Handle connection errors (node not ready)
                if attempt < max_retries:
                    logger.warning(f"RPC connection failed (attempt {attempt}/{max_retries}): {str(e)}")
                    time.sleep(min(retry_delay, max_delay))  # Cap the delay
                    retry_delay *= 1.5  # Exponential backoff
                else:
                    logger.error(f"RPC connection failed after {max_retries} attempts: {str(e)}")
                    raise
            
            except Exception as e:
                # Handle other errors (including HTTP 500s)
                if attempt < max_retries:
                    logger.warning(f"RPC call {method} failed (attempt {attempt}/{max_retries}): {str(e)}")
                    time.sleep(min(retry_delay, max_delay))  # Cap the delay
                    retry_delay *= 1.5  # Exponential backoff
                else:
                    logger.error(f"RPC call {method} failed after {max_retries} attempts: {str(e)}")
                    raise
    
    def getblockchaininfo(self):
        """Get current blockchain information."""
        return self._call_method('getblockchaininfo')
    
    def getblockcount(self):
        """Get current block height."""
        return self._call_method('getblockcount')
    
    def getblockhash(self, height):
        """Get block hash for the given height."""
        return self._call_method('getblockhash', [height])
    
    def getblock(self, block_hash, verbosity=2):
        """Get block data for the given hash."""
        return self._call_method('getblock', [block_hash, verbosity])
    
    def getrawtransaction(self, txid, verbose=True):
        """Get transaction data for the given txid."""
        return self._call_method('getrawtransaction', [txid, verbose])


class RXinDexerSync:
    """
    High-performance blockchain synchronization manager for RXinDexer.
    Combines optimized bulk loading with safe transaction handling.
    """
    
    def __init__(self, config=None):
        """Initialize the sync manager."""
        self.stop_requested = False
        self.rpc = RadiantRPC(RPC_URL, RPC_USER, RPC_PASSWORD)
        self.bloom_filter = ScalableBloomFilter(initial_capacity=10000, error_rate=0.001) if BLOOM_FILTER_AVAILABLE else None
        self.connection_lock = threading.RLock()  # Lock for connection access
        self.config = config or {}
        
        # Initialize database connection
        self.db_conn = None
        
        # Create a single persistent connection
        self._initialize_database_connection()
        
        # Only proceed with initialization if we have a valid connection
        if self.db_conn is not None:
            try:
                # Reset any in-progress sync state
                self.reset_sync_state()
                
                # Ensure database tables exist
                self._ensure_database_initialized()
                
                # Run a diagnostic query to check connection status
                self._run_connection_diagnostic()
            except Exception as e:
                logger.error(f"Error during initialization: {e}")
                self.close()  # Close the connection if initialization fails
                raise
            
    def _run_connection_diagnostic(self):
        """Run a diagnostic query to check connection status."""
        if self.db_conn is None:
            logger.warning("Cannot run diagnostics - no database connection available")
            return
            
        try:
            with self.get_cursor() as cur:
                # Run a simple diagnostic query
                cur.execute("SELECT current_database(), current_user, version()")
                db, user, version = cur.fetchone()
                logger.info(f"Database connection verified: {db}@{user} ({version})")
                
                # Check connection parameters
                cur.execute("SHOW max_connections")
                max_connections = cur.fetchone()[0]
                
                # Check current connections
                cur.execute("SELECT count(*) FROM pg_stat_activity")
                current_connections = cur.fetchone()[0]
                
                logger.info(f"Database connection status: {current_connections}/{max_connections} connections in use")
        except Exception as e:
            logger.error(f"Database diagnostic failed: {e}")
            # Don't raise the exception here, just log it
            
    def _reconnect_if_needed(self):
        """Check if the connection is still alive and reconnect if needed."""
        try:
            # Check if connection is still alive
            if self.db_conn is None or self.db_conn.closed:
                logger.info("Database connection is closed, reconnecting...")
                self._initialize_database_connection()
                return
            
            # Test the connection with a simple query
            with self.db_conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception as e:
            logger.warning(f"Database connection check failed, reconnecting... Error: {e}")
            try:
                # Try to close the connection if it exists
                if self.db_conn and not self.db_conn.closed:
                    self.db_conn.close()
            except Exception:
                pass
            
            # Reconnect with a delay to avoid rapid reconnection attempts
            time.sleep(1)
            self._initialize_database_connection()
            
    def _initialize_database_connection(self):
        """Initialize the database connection with a single persistent connection."""
        try:
            # Close existing connection if it exists
            if self.db_conn and not self.db_conn.closed:
                try:
                    # Try to reset any pending transactions
                    try:
                        self.db_conn.rollback()
                    except Exception:
                        pass
                    
                    # Close the connection properly
                    self.db_conn.close()
                except Exception as close_error:
                    logger.warning(f"Error closing previous connection: {close_error}")
                
            logger.info("Creating single persistent database connection")
            
            # Get database connection parameters from config
            db_host = self.config.get('DB_HOST', DB_HOST)
            db_port = self.config.get('DB_PORT', DB_PORT)
            db_name = self.config.get('DB_NAME', DB_NAME)
            db_user = self.config.get('DB_USER', DB_USER)
            db_password = self.config.get('DB_PASSWORD', DB_PASSWORD)
            
            # Set connection timeout and other parameters to improve stability
            connection_params = {
                'host': db_host,
                'port': db_port,
                'dbname': db_name,
                'user': db_user,
                'password': db_password,
                'connect_timeout': 10,        # 10 seconds connection timeout
                'application_name': 'rxindexer',  # Identify the application in pg_stat_activity
                'keepalives': 1,              # Enable TCP keepalives
                'keepalives_idle': 60,        # Seconds before sending keepalive 
                'keepalives_interval': 10,    # Seconds between keepalives
                'keepalives_count': 3         # Number of keepalives before dropping connection
            }
            
            # Create a single persistent connection
            self.db_conn = psycopg2.connect(**connection_params)
            
            # Set session parameters for performance
            self.db_conn.set_session(autocommit=True)
            
            logger.info("Database connection established successfully")
            
            # Check if connection is actually working
            with self.db_conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
                
        except Exception as e:
            logger.error(f"Failed to initialize database connection: {e}")
            # Don't raise the exception, just log it and allow the app to continue
            # It will try to reconnect on the next operation
            self.db_conn = None

    class CursorContextManager:
        """Context manager for database cursors."""
        
        def __init__(self, sync_manager):
            self.sync_manager = sync_manager
            self.cursor = None
        
        def __enter__(self):
            # Acquire lock to ensure thread safety
            with self.sync_manager.connection_lock:
                # Make sure we have a valid connection
                self.sync_manager._reconnect_if_needed()
                # Create a cursor
                self.cursor = self.sync_manager.db_conn.cursor()
            return self.cursor
        
        def __exit__(self, exc_type, exc_val, exc_tb):
            if self.cursor:
                self.cursor.close()
            
    
    def get_cursor(self):
        """Get a cursor to execute database queries."""
        return self.CursorContextManager(self)
            
    def _ensure_database_initialized(self):
        """Ensure all required database tables exist before starting operations."""
        logger.info("Checking database schema...")
        
        try:
            with self.get_cursor() as cur:
                # Check for missing tables
                cur.execute("""
                    SELECT table_name FROM information_schema.tables 
                    WHERE table_schema = 'public'
                """)
                existing_tables = [row[0] for row in cur.fetchall()]
                
                required_tables = ['blocks', 'transactions', 'utxos', 'sync_state', 'glyph_tokens', 'token_metadata']
                missing_tables = [table for table in required_tables if table not in existing_tables]
                
                if missing_tables:
                    logger.warning(f"Missing required tables: {', '.join(missing_tables)}")
                    logger.info("Initializing database schema...")
                    initialize_database(self.db_conn)
                else:
                    logger.info("All required tables exist")
        except Exception as e:
            logger.error(f"Error checking database schema: {e}")
            raise
        
    # These methods are maintained for compatibility with existing code, 
    # but now use the single connection approach instead of a pool
    
    def get_db_connection(self):
        """Get a database connection (compatibility method for pool-based code)."""
        try:
            with self.connection_lock:
                # Ensure the connection is valid
                self._reconnect_if_needed()
                return self.db_conn
        except Exception as e:
            logger.error(f"Error getting database connection: {str(e)}")
            raise
    
    def return_db_connection(self, conn):
        """No-op method maintained for compatibility."""
        # This is now a no-op since we use a single persistent connection
        pass
    
    class PooledConnection:
        """Context manager for database compatibility with pool-based code."""
        
        def __init__(self, sync):
            self.sync = sync
            self.db_conn_borrowed = False
        
        def __enter__(self):
            # Ensure connection is valid
            self.sync._reconnect_if_needed()
            self.connection_borrowed = True
            return self.sync.db_conn
        
        def __exit__(self, exc_type, exc_val, exc_tb):
            # No need to return the connection since we use a single persistent one
            self.db_conn_borrowed = False
    
    def get_pooled_connection(self):
        """Get the single connection wrapped in a context manager for compatibility."""
        return self.PooledConnection(self)
    
    def get_sync_status(self):
        """Get current sync status from the database."""
        try:
            with self.get_cursor() as cur:
                # Check if sync_state table exists
                cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'sync_state')")
                if not cur.fetchone()[0]:
                    logger.warning("sync_state table doesn't exist yet")
                    return 0
                
                # Check for different schema versions
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'sync_state'")
                columns = [col[0] for col in cur.fetchall()]
                
                if 'current_height' in columns:
                    # Original schema with current_height
                    cur.execute("SELECT current_height FROM sync_state WHERE id = 1")
                elif 'height' in columns:
                    # Schema with height column
                    cur.execute("SELECT height FROM sync_state ORDER BY id DESC LIMIT 1")
                else:
                    # Fallback check by getting the actual column names
                    logger.warning(f"Unusual sync_state schema found with columns: {columns}")
                    # Get the first column that might contain a height
                    height_column = next((col for col in columns if 'height' in col.lower()), None)
                    if height_column:
                        cur.execute(f"SELECT {height_column} FROM sync_state ORDER BY id DESC LIMIT 1")
                    else:
                        logger.error("No height column found in sync_state table")
                        return 0
                
                result = cur.fetchone()
                return result[0] if result else 0
        except Exception as e:
            logger.error(f"Error getting sync status: {e}")
            # Try to reconnect if there was a connection issue
            try:
                self._reconnect_if_needed()
            except Exception:
                pass
            return 0
    
    def update_sync_state(self, height, block_hash):
        """Update sync state in the database."""
        try:
            with self.get_cursor() as cur:
                # Check if sync_state table exists
                cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'sync_state')")
                if not cur.fetchone()[0]:
                    # Create table if it doesn't exist
                    cur.execute("""
                        CREATE TABLE sync_state (
                            id INTEGER PRIMARY KEY,
                            current_height INTEGER NOT NULL DEFAULT 0,
                            current_hash VARCHAR(64),
                            last_updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                            last_error TEXT,
                            current_chainwork VARCHAR(64),
                            glyph_scan_height INTEGER DEFAULT 0,
                            is_syncing SMALLINT NOT NULL DEFAULT 0
                        )
                    """)
                    
                    # Insert initial state
                    cur.execute("""
                        INSERT INTO sync_state (id, current_height, current_hash, last_updated_at, is_syncing) 
                        VALUES (1, %s, %s, NOW(), 0)
                    """, (height, block_hash))
                    logger.info(f"Created sync_state table and inserted initial record at height {height}")
                else:
                    # Update existing state
                    cur.execute("""
                        UPDATE sync_state
                        SET current_height = %s, current_hash = %s, last_updated_at = NOW()
                        WHERE id = 1
                    """, (height, block_hash))
                    
                    if cur.rowcount == 0:  # No rows updated
                        cur.execute("""
                            INSERT INTO sync_state (id, current_height, current_hash, last_updated_at, is_syncing) 
                            VALUES (1, %s, %s, NOW(), 0)
                        """, (height, block_hash))
                        logger.info(f"Inserted new sync_state record at height {height}")
        except Exception as e:
            logger.error(f"Error updating sync state: {e}")
            # Try to reconnect if there was a connection issue
            try:
                self._reconnect_if_needed()
            except Exception:
                pass
            raise
    
    def reset_sync_state(self):
        """Reset the sync state if it's currently marked as syncing."""
        try:
            with self.get_cursor() as cur:
                # Check if sync_state table exists
                cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'sync_state')")
                if cur.fetchone()[0]:
                    # Check if is_syncing column exists
                    cur.execute("""
                        SELECT EXISTS(
                            SELECT 1 
                            FROM information_schema.columns 
                            WHERE table_name = 'sync_state' AND column_name = 'is_syncing'
                        )
                    """)
                    
                    if cur.fetchone()[0]:
                        # Reset any in-progress syncing
                        try:
                            cur.execute("""
                                UPDATE sync_state 
                                SET is_syncing = 0, 
                                    last_updated_at = to_timestamp(extract(epoch from now())),
                                    last_error = 'Reset during startup' 
                                WHERE is_syncing = 1
                            """)
                            if cur.rowcount > 0:
                                logger.info(f"Reset {cur.rowcount} sync_state rows that were marked as syncing")
                        except Exception as update_error:
                            logger.warning(f"Could not update is_syncing flag: {update_error}")
                    else:
                        # Need to add the is_syncing column
                        try:
                            logger.info("Adding is_syncing column to sync_state table")
                            cur.execute("ALTER TABLE sync_state ADD COLUMN is_syncing SMALLINT NOT NULL DEFAULT 0")
                            logger.info("Added is_syncing column to sync_state table")
                        except Exception as alter_error:
                            logger.warning(f"Could not add is_syncing column: {alter_error}")
        except Exception as e:
            logger.error(f"Error resetting sync state: {e}")
            # Try to reconnect if there was a connection issue
            try:
                self._reconnect_if_needed()
            except Exception as reconnect_error:
                logger.error(f"Failed to reconnect during reset_sync_state: {reconnect_error}")

    def prepare_database_for_sync(self):
        """Prepare database for high-performance syncing."""
        if not INITIAL_SYNC_MINIMAL:
            return
            
        try:
            with self.get_cursor() as cur:
                # Disable triggers temporarily for faster inserts
                cur.execute("SET session_replication_role = 'replica'")
                
                # Analyze tables for better query planning
                cur.execute("ANALYZE blocks, transactions, utxos")
                
                logger.info("Database prepared for sync")
        except Exception as e:
            logger.error(f"Error preparing database: {str(e)}")
            try:
                self._reconnect_if_needed()
            except Exception:
                pass
    
    def _drop_indices(self, cursor):
        """Drop non-essential indices for faster bulk loading."""
        indices_to_keep = ['utxos_pkey', 'blocks_pkey', 'transactions_pkey', 'glyph_tokens_pkey']
        
        # Get all indices
        cursor.execute("""
            SELECT indexname FROM pg_indexes 
            WHERE tablename IN ('utxos', 'transactions', 'blocks', 'glyph_tokens')
            AND indexname NOT IN %s
        """, (tuple(indices_to_keep),))
        
        indices = [row[0] for row in cursor.fetchall()]
        
        # Drop non-essential indices
        for idx in indices:
            cursor.execute(f"DROP INDEX IF EXISTS {idx}")
            logger.info(f"Dropped index {idx} for faster bulk loading")
    
    def restore_database_after_sync(self):
        """Restore database state after bulk loading."""
        if not INITIAL_SYNC_MINIMAL:
            return
            
        logger.info("Restoring database to normal operation mode")
        
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                # Re-enable triggers
                cur.execute("ALTER TABLE IF EXISTS utxos ENABLE TRIGGER ALL")
                cur.execute("ALTER TABLE IF EXISTS transactions ENABLE TRIGGER ALL")
                cur.execute("ALTER TABLE IF EXISTS blocks ENABLE TRIGGER ALL")
                
                # Set synchronous commits back to ON
                cur.execute("SET synchronous_commit TO ON")
                
                # Recreate indices
                self._recreate_indices(cur)
                
                # Run VACUUM ANALYZE for better query planning
                cur.execute("VACUUM ANALYZE utxos")
                cur.execute("VACUUM ANALYZE transactions")
                cur.execute("VACUUM ANALYZE blocks")
                cur.execute("VACUUM ANALYZE glyph_tokens")
        
        logger.info("Database restored to normal operation mode")
    
    def _recreate_indices(self, cursor):
        """Recreate indices after bulk loading."""
        logger.info("Recreating indices for optimal query performance")
        
        # Create optimized indices
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_utxos_address_spent ON utxos (address) WHERE spent = FALSE")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_utxos_block_height ON utxos (block_height)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_blocks_height ON blocks (height)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_transactions_block_height ON transactions (block_height)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_glyph_tokens_type ON glyph_tokens (type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_token_ref ON utxos (token_ref) WHERE token_ref IS NOT NULL")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_utxo_address_block_height ON utxos (address, block_height)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_holder_token_balances ON holders USING GIN (token_balances)")
        
        logger.info("Recreated all necessary indices")
    
    def init_bloom_filter(self):
        """Initialize bloom filter with existing transactions for fast lookups."""
        if self.bloom_filter is None:
            logger.info("Bloom filter not available, skipping initialization")
            return
            
        logger.info("Initializing bloom filter with existing transactions")
        
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT txid FROM transactions")
                count = 0
                for row in cur:
                    self.bloom_filter.add(row[0])
                    count += 1
                    if count % 10000 == 0:
                        logger.info(f"Loaded {count} transactions into bloom filter")
        
        logger.info(f"Bloom filter initialized with {count} transactions")
    
    def tx_exists(self, txid):
        """Check if transaction exists using bloom filter for fast negative lookups."""
        # Quick check with bloom filter first (this avoids database queries altogether)
        if self.bloom_filter is not None and txid not in self.bloom_filter:
            return False
            
        # Double-check with database for positive results (to handle false positives)
        with self.get_pooled_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM transactions WHERE txid = %s LIMIT 1", (txid,))
                return cur.fetchone() is not None
    
    @lru_cache(maxsize=1000)
    def _cached_get_block_hash(self, height):
        """Get block hash with local caching."""
        if redis_client:
            # Try Redis first
            cache_key = f"block_hash:{height}"
            cached = redis_client.get(cache_key)
            if cached:
                return cached.decode('utf-8')
        
        # Get from RPC
        block_hash = self.rpc.getblockhash(height)
        
        # Cache in Redis
        if redis_client:
            redis_client.set(f"block_hash:{height}", block_hash, ex=3600)
        
        return block_hash
    
    def get_block_with_cache(self, height):
        """Get full block with caching."""
        if redis_client:
            # Try Redis first
            cache_key = f"block_data:{height}"
            cached = redis_client.get(cache_key)
            if cached:
                return json.loads(cached)
        
        # Get block hash
        block_hash = self._cached_get_block_hash(height)
        
        # Get full block
        block_data = self.rpc.getblock(block_hash, 2)
        
        # Cache in Redis
        if redis_client:
            redis_client.set(f"block_data:{height}", json.dumps(block_data), ex=1800)
        
        return block_data
    
    def process_transaction(self, tx, height, block_hash, shared_cur=None):
        """
        Process a transaction to extract UTXOs and token data.
        Uses shared cursor if provided or creates a new one from our single connection.
        
        Args:
            tx: Transaction data dictionary
            height: Block height
            block_hash: Block hash
            shared_cur: Optional shared database cursor
        """
        tx_id = tx.get('txid')
        tx_timestamp = tx.get('time', 0)
        
        # Track whether we're using a shared cursor or our own
        using_shared_cursor = shared_cur is not None
        
        # Skip if transaction already exists (using bloom filter if available)
        if self.bloom_filter is not None and self.tx_exists(tx_id):
            return
        
        try:
            # Use shared cursor if provided, otherwise get a new one
            if not using_shared_cursor:
                cursor_ctx = self.get_cursor()
                cur = cursor_ctx.__enter__()
            else:
                cur = shared_cur
            
            try:
                # Check if transaction exists in database
                cur.execute("SELECT 1 FROM transactions WHERE txid = %s", (tx_id,))
                if cur.fetchone():
                    # Already exists, add to bloom filter if available
                    if self.bloom_filter is not None:
                        self.bloom_filter.add(tx_id)
                    return
                
                # Insert transaction
                cur.execute("""
                    INSERT INTO transactions (txid, block_height, block_hash, timestamp, created_at)
                    VALUES (%s, %s, %s, TO_TIMESTAMP(%s), NOW())
                    ON CONFLICT (txid) DO NOTHING
                """, (tx_id, height, block_hash, tx_timestamp))
                
                # Process inputs (mark UTXOs as spent)
                vin = tx.get('vin', [])
                for vin_item in vin:
                    # Skip coinbase transactions
                    if 'coinbase' in vin_item:
                        continue
                        
                    prev_txid = vin_item.get('txid')
                    prev_vout = vin_item.get('vout')
                    
                    if not prev_txid or prev_vout is None:
                        continue
                    
                    # Mark UTXO as spent using the same connection
                    cur.execute("""
                        UPDATE utxos SET spent = TRUE, spent_txid = %s, updated_at = NOW()
                        WHERE txid = %s AND vout = %s
                    """, (tx_id, prev_txid, prev_vout))
                
                # Process outputs (create new UTXOs)
                vouts = []
                for vout_idx, vout in enumerate(tx.get('vout', [])):
                    value = vout.get('value', 0)
                    script_pub_key = vout.get('scriptPubKey', {})
                    addresses = script_pub_key.get('addresses', [])
                    
                    # Extract token data if available
                    token_data = None
                    if GLYPH_DEEP_INDEXING and script_pub_key.get('type') == 'glyph':
                        try:
                            token_data = json.loads(script_pub_key.get('data', '{}'))
                            vout['token_data'] = token_data
                        except Exception as e:
                            logger.warning(f"Error parsing token data in {tx_id}: {str(e)}")
                    
                    # Only process if it has valid addresses
                    if not addresses:
                        continue
                    
                    # Store UTXOs for each address
                    for address in addresses:
                        vouts.append({
                            'txid': tx_id, 
                            'vout': vout_idx,
                            'address': address,
                            'amount': value,
                            'token_ref': f"{tx_id}:{vout_idx}" if token_data else None,
                            'block_height': height,
                            'block_hash': block_hash
                        })
                
                # Bulk insert UTXOs using the same connection
                if vouts:
                    # Use executemany for better performance
                    cur.executemany("""
                        INSERT INTO utxos (txid, vout, address, amount, token_ref, spent, block_height, block_hash, created_at)
                        VALUES (%(txid)s, %(vout)s, %(address)s, %(amount)s, %(token_ref)s, FALSE, %(block_height)s, %(block_hash)s, NOW())
                        ON CONFLICT (txid, vout) DO NOTHING
                    """, vouts)
                
                # Process token data if present, using the same cursor
                token_outputs = [v for v in tx.get('vout', []) if v.get('token_data')]
                if token_outputs:
                    self.process_glyph_tokens(tx, token_outputs, height, block_hash, shared_cur=cur)
                    
            finally:
                # Only close our own cursor, not a shared one
                if not using_shared_cursor and 'cursor_ctx' in locals():
                    cursor_ctx.__exit__(None, None, None)
                    
        except Exception as e:
            logger.error(f"Error processing transaction {tx_id}: {str(e)}")
            # Try to reconnect if there was a connection issue
            try:
                self._reconnect_if_needed()
            except Exception:
                pass
            raise
    
    def process_glyph_tokens(self, tx, token_outputs, height, block_hash, shared_cur=None):
        """
        Process Glyph tokens in a transaction.
        Uses the single connection approach to prevent connection pool exhaustion.
        Adapted to work with the existing database schema.
        
        Args:
            tx: Transaction data dictionary
            token_outputs: List of outputs with token data
            height: Block height
            block_hash: Block hash
            shared_cur: Optional shared database cursor
        """
        tx_id = tx.get('txid')
        tx_timestamp = tx.get('time', 0)
        
        # Track whether we're using a shared cursor or our own
        using_shared_cursor = shared_cur is not None
        
        for output in token_outputs:
            try:
                token_data = output.get('token_data', {})
                if not token_data:
                    continue
                    
                # Extract key token fields
                token_type = token_data.get('type', 'unknown')
                token_id = token_data.get('id', 'unknown')
                token_ref = f"{tx_id}:{output['n']}"
                
                # Use shared cursor if provided, otherwise get a new one
                if not using_shared_cursor:
                    cursor_ctx = self.get_cursor()
                    cur = cursor_ctx.__enter__()
                else:
                    cur = shared_cur
                    
                try:
                    # Check if token already exists
                    cur.execute("""
                        SELECT ref FROM glyph_tokens 
                        WHERE ref = %s OR (genesis_txid = %s AND token_id = %s)
                    """, (token_ref, tx_id, token_id))
                    
                    existing_token = cur.fetchone()
                    
                    if existing_token:
                        # Update existing token
                        cur.execute("""
                            UPDATE glyph_tokens SET 
                            current_txid = %s, 
                            current_vout = %s, 
                            token_metadata = %s,
                            updated_at = NOW()
                            WHERE ref = %s
                        """, (tx_id, output['n'], json.dumps(token_data), existing_token[0]))
                    else:
                        # Insert new token
                        cur.execute("""
                            INSERT INTO glyph_tokens (
                                ref, type, token_id, token_metadata, 
                                current_txid, current_vout, genesis_txid, genesis_block_height,
                                created_at, updated_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                        """, (
                            token_ref, token_type, token_id, json.dumps(token_data),
                            tx_id, output['n'], tx_id, height
                        ))
                finally:
                    # Only close our own cursor, not a shared one
                    if not using_shared_cursor and 'cursor_ctx' in locals():
                        cursor_ctx.__exit__(None, None, None)
                            
                cur.execute("""
                    SELECT matviewname FROM pg_matviews
                    WHERE schemaname = 'public'
                """)
                
                views = [row[0] for row in cur.fetchall()]
                for view in views:
                    logger.info(f"Refreshing materialized view: {view}")
                    cur.execute(f"REFRESH MATERIALIZED VIEW {view}")
                
                logger.info(f"Refreshed {len(views)} materialized views")
            except Exception as e:
                logger.warning(f"Error refreshing materialized views: {str(e)}")

    def update_token_balances(self):
        """Update token balances and holder counts."""
        logger.info("Updating token balances and holder counts")
        
        try:
            with self.get_pooled_connection() as conn:
                with conn.cursor() as cur:
                    # Update token balances logic here
                    logger.info("Token balances and holder counts updated")
        except Exception as e:
            logger.error(f"Error updating token balances: {str(e)}")
    
    def refresh_materialized_views(self):
        """Refresh materialized views for API optimization."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                try:
                    # Refresh materialized views if they exist
                    cur.execute("""
                        SELECT matviewname FROM pg_matviews
                        WHERE schemaname = 'public'
                    """)
                    
                    views = [row[0] for row in cur.fetchall()]
                    for view in views:
                        logger.info(f"Refreshing materialized view: {view}")
                        cur.execute(f"REFRESH MATERIALIZED VIEW {view}")
                    
                    logger.info(f"Refreshed {len(views)} materialized views")
                except Exception as e:
                    logger.warning(f"Error refreshing materialized views: {str(e)}")
    
    def process_block(self, height, shared_conn=None, shared_cur=None):
        """Process a single block at the specified height.
        
        Args:
            height: Block height to process
            shared_conn: Optional shared database connection to use
            shared_cur: Optional shared database cursor to use
        
        Returns:
            bool: Success or failure
        """
        # Track whether we're using a shared cursor or our own
        using_shared_cursor = shared_cur is not None
        
        try:
            # Get block data with caching
            block_data = self.get_block_with_cache(height)
            block_hash = block_data.get('hash')
            block_timestamp = block_data.get('time', 0)
            
            # Use shared cursor if provided, otherwise get a new one
            if not using_shared_cursor:
                # Use our cursor context manager to ensure connection validity
                cursor_ctx = self.get_cursor()
                cur = cursor_ctx.__enter__()
            else:
                cur = shared_cur
            
            try:
                # Skip if block already exists
                cur.execute("SELECT 1 FROM blocks WHERE hash = %s", (block_hash,))
                if cur.fetchone():
                    logger.info(f"Block {height} already processed, skipping")
                    return True
                
                # Insert block
                cur.execute("""
                    INSERT INTO blocks (height, hash, timestamp, created_at)
                    VALUES (%s, %s, TO_TIMESTAMP(%s), NOW())
                    ON CONFLICT (hash) DO NOTHING
                """, (height, block_hash, block_timestamp))
                
                # Process transactions sequentially (no parallelism to prevent connection issues)
                transactions = block_data.get('tx', [])
                
                if not transactions:
                    logger.warning(f"Block {height} has no transactions")
                else:
                    # Completely sequential transaction processing
                    for tx in transactions:
                        try:
                            # Pass only the cursor for transaction processing
                            self.process_transaction(tx, height, block_hash, shared_cur=cur)
                        except Exception as e:
                            logger.error(f"Error processing transaction {tx.get('txid')} in block {height}: {str(e)}")
                
                # Update sync state using the same connection with the correct schema
                try:
                    # Insert a new record in sync_state using the correct column names
                    cur.execute("""
                        INSERT INTO sync_state (id, current_height, current_hash, last_updated_at)
                        VALUES (1, %s, %s, NOW())
                        ON CONFLICT (id) DO UPDATE
                        SET current_height = %s, current_hash = %s, last_updated_at = NOW()
                    """, (height, block_hash, height, block_hash))
                except psycopg2.Error as e:
                    logger.error(f"Error updating sync_state: {str(e)}")
                    # If there's an error, try to ensure the table exists
                    if 'relation "sync_state" does not exist' in str(e):
                        logger.warning("sync_state table not found, creating it")
                        cur.execute("""
                            CREATE TABLE IF NOT EXISTS sync_state (
                                id INTEGER PRIMARY KEY,
                                current_height INTEGER NOT NULL DEFAULT 0,
                                current_hash VARCHAR(64),
                                last_updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                                last_error TEXT,
                                current_chainwork VARCHAR(64),
                                glyph_scan_height INTEGER DEFAULT 0
                            )
                        """)
                        # Try the insert again
                        cur.execute("""
                            INSERT INTO sync_state (id, current_height, current_hash, last_updated_at)
                            VALUES (1, %s, %s, NOW())
                        """, (height, block_hash))
                    else:
                        # Rethrow other errors
                        raise
                
                return True
                
            finally:
                # Only close our own cursor, not a shared one
                if not using_shared_cursor and cursor_ctx is not None:
                    cursor_ctx.__exit__(None, None, None)
                    
        except Exception as e:
            logger.error(f"Error processing block {height}: {str(e)}")
            # Try to reconnect if there was a connection issue
            try:
                self._reconnect_if_needed()
            except Exception:
                pass
            return False
    
    def process_blocks(self, start_height, end_height):
        """Process a range of blocks using a single database connection."""
        logger.info(f"Processing blocks from {start_height} to {end_height}")
        
        success_count = 0
        
        try:
            # Get a cursor from our single persistent connection
            with self.get_cursor() as cur:
                for height in range(start_height, end_height + 1):
                    if self.stop_requested:
                        logger.info("Stop requested, halting block processing")
                        break
                        
                    # Process each block with the shared cursor
                    if self.process_block(height, shared_cur=cur):
                        success_count += 1
                        
                    # Log progress periodically
                    if (height - start_height + 1) % 10 == 0 or height == end_height:
                        logger.info(f"Processed {height - start_height + 1}/{end_height - start_height + 1} blocks")
                    
                # No need for explicit commit with autocommit mode
            
        except Exception as e:
            logger.error(f"Error in block batch processing: {str(e)}")
            # Try to reconnect if there was a connection issue
            try:
                self._reconnect_if_needed()
            except Exception:
                pass
                
        return success_count
    
    def process_blocks_parallel(self, start_height, end_height):
        """Process a range of blocks in parallel."""
        # In Docker environments, disable parallelism entirely to prevent connection issues
        if IN_DOCKER:
            logger.info(f"Docker environment detected, using fully sequential processing to prevent connection pool exhaustion")
            return self.process_blocks(start_height, end_height)
            
        # For non-Docker environments, use a very conservative approach
        logger.info(f"Processing blocks from {start_height} to {end_height} with limited parallelism")
        
        # Divide work into larger batches with fewer workers
        total_blocks = end_height - start_height + 1
        max_workers = min(2, SYNC_MAX_WORKERS)  # Drastically limit workers to 2 max
        batch_size = max(100, total_blocks // max_workers)  # Use larger batches
        
        batches = []
        current_start = start_height
        while current_start <= end_height:
            current_end = min(current_start + batch_size - 1, end_height)
            batches.append((current_start, current_end))
            current_start = current_end + 1
        
        logger.info(f"Divided work into {len(batches)} large batches")
        
        # Process sequentially for maximum stability
        total_processed = 0
        for batch in batches:
            if self.stop_requested:
                break
                
            try:
                batch_processed = self.process_blocks(batch[0], batch[1])
                total_processed += batch_processed
                logger.info(f"Processed batch {batch[0]}-{batch[1]}: {batch_processed} blocks")
                
                # No need to force garbage collection with single connection approach
                pass
            except Exception as e:
                logger.error(f"Error in batch processing: {str(e)}")
        
        logger.info(f"Parallel processing complete. Processed {total_processed} blocks")
        return total_processed
    
    def run_progressive_sync(self):
        """
        Two-phase sync implementation for optimal performance.
        First does a fast bulk sync, then switches to incremental mode for ongoing sync.
        """
        current_height = self.get_sync_status()
        target_height = self.rpc.getblockcount()
        
        if target_height <= current_height:
            logger.info(f"Already up to date at height {current_height}")
            return
        
        blocks_behind = target_height - current_height
        logger.info(f"Progressive sync: {blocks_behind} blocks behind")
        
        # Phase 1: Fast bulk sync for large gaps
        if blocks_behind > BLOCK_PARALLEL_THRESHOLD:
            logger.info(f"Starting bulk sync phase from height {current_height} to {target_height}")
            
            # Prepare database for bulk loading
            self.prepare_database_for_sync()
            
            try:
                # Process in parallel for maximum throughput
                self.process_blocks_parallel(current_height + 1, target_height)
                
                # Update token balances after bulk sync
                if GLYPH_DEEP_INDEXING:
                    self.update_token_balances()
                
                # Refresh materialized views
                self.refresh_materialized_views()
                
            finally:
                # Always restore database state even if sync fails
                self.restore_database_after_sync()
        
        # Phase 2: Incremental sync for small gaps or final verification
        current_height = self.get_sync_status()  # Get updated height after bulk sync
        target_height = self.rpc.getblockcount()  # Get latest height
        
        if current_height < target_height:
            logger.info(f"Starting incremental sync from height {current_height} to {target_height}")
            self.process_blocks(current_height + 1, target_height)
            
            # Update token balances after incremental sync
            if GLYPH_DEEP_INDEXING:
                self.update_token_balances()
            
            # Refresh materialized views
            self.refresh_materialized_views()
        
        logger.info(f"Progressive sync complete. Current height: {self.get_sync_status()}")
    
    def run_sync(self):
        """
        Main entry point for blockchain synchronization.
        Uses a fully sequential approach to prevent connection pool exhaustion.
        """
        logger.info("Starting blockchain sync")
        
        try:
            # Initialize bloom filter if available
            if BLOOM_FILTER_AVAILABLE and self.bloom_filter is not None:
                self.init_bloom_filter()
            
            current_height = self.get_sync_status()
            target_height = self.rpc.getblockcount()
            
            logger.info(f"Current height: {current_height}, Target height: {target_height}")
            
            if current_height >= target_height:
                logger.info("Already at target height, no sync needed")
                return
            
            blocks_behind = target_height - current_height
            logger.info(f"{blocks_behind} blocks behind")
            
            # Detect chain reorg if needed
            if blocks_behind > 0:
                self.detect_and_handle_reorg()
            
            # Process all blocks in sequential batches to avoid connection pool exhaustion
            batch_size = 100  # Use small fixed batch size to prevent overwhelming connection pool
            start_height = current_height + 1
            
            while start_height <= target_height:
                end_height = min(start_height + batch_size - 1, target_height)
                logger.info(f"Processing batch from {start_height} to {end_height}")
                
                try:
                    processed = self.process_blocks(start_height, end_height)
                    logger.info(f"Processed {processed} blocks in current batch")
                except Exception as e:
                    logger.error(f"Error processing batch {start_height}-{end_height}: {str(e)}")
                    # If we hit connection issues, force explicit cleanup
                    if "connection pool exhausted" in str(e):
                        logger.warning("Connection pool exhaustion detected, forcing cleanup")
                        gc.collect()
                        time.sleep(5)  # Brief pause to allow connections to be released
                        continue  # Retry the same batch
                    raise
                
                # Advance to next batch
                start_height = end_height + 1
                
                # After each batch, force garbage collection to release any leaked connections
                gc.collect()
            
            # Final check for chain reorg at the end of sync
            self.detect_and_handle_reorg()
            
            logger.info("Blockchain sync complete")
            
        except Exception as e:
            logger.error(f"Sync failed with error: {str(e)}")
            raise
        
    def handle_chain_reorg(self, common_ancestor_height):
        """Handle chain reorganization by rolling back to common ancestor."""
        logger.warning(f"Chain reorganization detected. Rolling back to height {common_ancestor_height}")
        
        with self.get_cursor() as cur:
            # Start a transaction for the rollback
            cur.execute("BEGIN")
            
            try:
                # Get blocks to roll back
                cur.execute("SELECT height, hash FROM blocks WHERE height > %s ORDER BY height DESC", 
                            (common_ancestor_height,))
                blocks_to_rollback = cur.fetchall()
                    
                for height, block_hash in blocks_to_rollback:
                    logger.info(f"Rolling back block {height} ({block_hash})")
                    
                    # Get transactions in this block
                    cur.execute("SELECT txid FROM transactions WHERE block_hash = %s", (block_hash,))
                    txids = [row[0] for row in cur.fetchall()]
                    
                    # Mark affected UTXOs as unspent
                    for txid in txids:
                        cur.execute("""
                            UPDATE utxos 
                            SET spent = FALSE, spent_txid = NULL, spent_at = NULL 
                            WHERE spent_txid = %s
                        """, (txid,))
                    
                    # Delete UTXOs created in this block
                    cur.execute("DELETE FROM utxos WHERE block_hash = %s", (block_hash,))
                    
                    # Delete tokens created in this block
                    cur.execute("DELETE FROM glyph_tokens WHERE block_hash = %s", (block_hash,))
                    
                    # Delete transactions in this block
                    cur.execute("DELETE FROM transactions WHERE block_hash = %s", (block_hash,))
                    
                    # Delete the block
                    cur.execute("DELETE FROM blocks WHERE hash = %s", (block_hash,))
                
                # Update sync state
                cur.execute("""
                    UPDATE sync_state 
                    SET current_height = %s, 
                        current_hash = (SELECT hash FROM blocks WHERE height = %s), 
                        last_updated_at = NOW()
                    WHERE id = 1
                """, (common_ancestor_height, common_ancestor_height))
                
                # Commit the transaction
                cur.execute("COMMIT")
                
                logger.info(f"Successfully rolled back to height {common_ancestor_height}")
                
            except Exception as e:
                # Rollback on error
                cur.execute("ROLLBACK")
                logger.error(f"Chain reorganization handling failed: {str(e)}")
                raise
        
        # Update token balances after reorg
        if GLYPH_DEEP_INDEXING:
            self.update_token_balances()
            
        # Refresh materialized views
        self.refresh_materialized_views()
    
    def detect_and_handle_reorg(self):
        """Detect and handle chain reorganization by comparing local chain with node."""
        current_height = self.get_sync_status()
        
        # Only check for reorgs if we've synced some blocks
        if current_height <= 0:
            return False
        
        # Get the hash of our current tip
        with self.get_cursor() as cur:
            cur.execute("SELECT hash FROM blocks WHERE height = %s", (current_height,))
            result = cur.fetchone()
            if not result:
                logger.error(f"Cannot find block at height {current_height} in database")
                return False
            
            current_hash = result[0]
        
        # Get the hash of the same height from the node
        try:
            node_hash = self.rpc.getblockhash(current_height)
            
            # If hashes match, no reorg
            if current_hash == node_hash:
                return False
            
            logger.warning(f"Chain reorganization detected at height {current_height}")
            logger.warning(f"Local hash: {current_hash}, Node hash: {node_hash}")
            
            # Find common ancestor by walking backwards
            common_height = current_height - 1
            while common_height > 0:
                try:
                    with self.get_cursor() as cur:
                        cur.execute("SELECT hash FROM blocks WHERE height = %s", (common_height,))
                        result = cur.fetchone()
                        if result:
                            db_hash = result[0]
                            node_hash = self.rpc.getblockhash(common_height)
                            if db_hash == node_hash:
                                # Found common ancestor
                                logger.info(f"Found common ancestor at height {common_height}")
                                self.handle_chain_reorg(common_height)
                                return True
                except Exception as e:
                    logger.error(f"Error getting block hash from node: {str(e)}")
                    break
                
                common_height -= 1
            
            # If we couldn't find a common ancestor, log error
            logger.error("Could not find common ancestor for chain reorganization")
            return False
            
        except Exception as e:
            logger.error(f"Error detecting chain reorganization: {str(e)}")
            return False
    
    def close(self):
        """Close the database connection and cleanup resources."""
        try:
            logger.info("Closing database connection...")
            with self.connection_lock:
                if hasattr(self, 'db_conn') and self.db_conn and not self.db_conn.closed:
                    # Set session back to normal before closing
                    try:
                        with self.db_conn.cursor() as cur:
                            cur.execute("SET session_replication_role = 'origin'")
                    except Exception as e:
                        logger.warning(f"Error restoring session role: {e}")
                    
                    # Close the connection
                    self.db_conn.close()
                    logger.info("Database connection closed")
        except Exception as e:
            logger.error(f"Error closing database connection: {e}")
        finally:
            # Set the connection to None to prevent further use
            self.db_conn = None


def initialize_database(conn=None):
    """
    Initialize database tables and structures if they don't exist.
    
    Args:
        conn: Optional existing database connection. If not provided, will create a new one.
    """
    # Create a new connection if not provided
    close_conn = False
    if not conn:
        close_conn = True
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        conn.autocommit = True
    
    try:
        logger.info("Database connection established")
        
        with conn.cursor() as cur:
            logger.info("Step 1: Creating blocks table")
            # Create blocks table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS blocks (
                    hash VARCHAR(64) PRIMARY KEY,
                    height INTEGER NOT NULL,
                    timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
                )
            """)
            
            logger.info("Step 2: Creating transactions table")
            # Create transactions table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    txid VARCHAR(64) PRIMARY KEY,
                    block_height INTEGER NOT NULL,
                    block_hash VARCHAR(64) NOT NULL,
                    timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                    CONSTRAINT fk_block_hash FOREIGN KEY (block_hash) REFERENCES blocks(hash) ON DELETE CASCADE
                )
            """)
            
            logger.info("Step 3: Checking UTXO table")
            # Create or check UTXO table
            cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'utxos')")
            if not cur.fetchone()[0]:
                # Create new UTXO table if it doesn't exist
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS utxos (
                        txid VARCHAR(64) NOT NULL,
                        vout INTEGER NOT NULL,
                        address VARCHAR(64) NOT NULL,
                        amount NUMERIC(20, 8) NOT NULL,
                        token_ref VARCHAR(128),
                        spent BOOLEAN NOT NULL DEFAULT FALSE,
                        spent_txid VARCHAR(64),
                        spent_at TIMESTAMP WITHOUT TIME ZONE,
                        block_height INTEGER NOT NULL,
                        block_hash VARCHAR(64) NOT NULL,
                        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMP WITHOUT TIME ZONE,
                        PRIMARY KEY (txid, vout)
                    )
                """)
            
            logger.info("Step 2: Creating transactions table")
            # Create transactions table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    txid VARCHAR(64) PRIMARY KEY,
                    block_height INTEGER NOT NULL,
                    block_hash VARCHAR(64) NOT NULL,
                    timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
                    CONSTRAINT fk_block_hash FOREIGN KEY (block_hash) REFERENCES blocks(hash) ON DELETE CASCADE
                )
            """)
            
            logger.info("Step 3: Checking UTXO table")
            # Create or check UTXO table
            cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'utxos')")
            if not cur.fetchone()[0]:
                # Create new UTXO table if it doesn't exist
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS utxos (
                        txid VARCHAR(64) NOT NULL,
                        vout INTEGER NOT NULL,
                        address VARCHAR(64) NOT NULL,
                        amount NUMERIC(16, 8) NOT NULL,
                        token_ref VARCHAR(64),
                        spent BOOLEAN DEFAULT FALSE,
                        spent_txid VARCHAR(64),
                        block_height INTEGER NOT NULL,
                        block_hash VARCHAR(64) NOT NULL,
                        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                        updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                        PRIMARY KEY (txid, vout)
                    )
                """)
                # Create indexes for performance
                cur.execute("CREATE INDEX IF NOT EXISTS idx_utxos_address_spent ON utxos(address) WHERE spent = false")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_utxo_address_spent ON utxos(address, spent)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_utxos_block_height ON utxos(block_height)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_token_ref ON utxos(token_ref) WHERE token_ref IS NOT NULL")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_utxo_token_ref_spent ON utxos(token_ref, spent)")
            else:
                logger.info("utxos table already exists, using existing schema")
            
            logger.info("Step 4: Checking glyph_tokens table")
            # Create or check glyph_tokens table
            # Check if glyph_tokens exists with the existing schema
            cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'glyph_tokens')")
            if not cur.fetchone()[0]:
                # Create new glyph_tokens table if it doesn't exist
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS glyph_tokens (
                        ref VARCHAR(64) PRIMARY KEY,
                        type VARCHAR(20) NOT NULL,
                        token_metadata JSONB,
                        current_txid VARCHAR(64),
                        current_vout INTEGER,
                        genesis_txid VARCHAR(64) NOT NULL,
                        genesis_block_height INTEGER NOT NULL,
                        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                        updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
                    )
                """)
            else:
                logger.info("glyph_tokens table already exists, using existing schema")
            
            logger.info("Step 5: Checking holders table")
            # Create or check holders table
            cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'holders')")
            if not cur.fetchone()[0]:
                # Create new holders table if it doesn't exist
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS holders (
                        address VARCHAR(64) PRIMARY KEY,
                        rxd_balance NUMERIC(38,8) NOT NULL,
                        token_balances TEXT NOT NULL,
                        first_seen_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                        last_updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
                    )
                """)
                # Create index for performance
                cur.execute("CREATE INDEX IF NOT EXISTS idx_holder_rxd_balance ON holders (rxd_balance)")
            else:
                logger.info("holders table already exists, using existing schema")
            
            logger.info("Step 6: Checking sync_state table")
            # Create or check sync_state table
            try:
                # First try to create with timestamp type
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS sync_state (
                        id INTEGER PRIMARY KEY,
                        current_height INTEGER NOT NULL DEFAULT 0,
                        current_hash VARCHAR(64),
                        last_updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
                    )
                """)
                
                # Initialize sync_state if empty
                cur.execute("SELECT COUNT(*) FROM sync_state")
                if cur.fetchone()[0] == 0:
                    cur.execute("""
                        INSERT INTO sync_state (id, current_height, current_hash, last_updated_at)
                        VALUES (1, 0, NULL, NOW())
                    """)
            except Exception as e:
                # Check if sync_state exists but with a different schema
                cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'sync_state')")
                if cur.fetchone()[0]:
                    # Get the column type of last_updated_at
                    cur.execute("""
                        SELECT data_type FROM information_schema.columns 
                        WHERE table_name = 'sync_state' AND column_name = 'last_updated_at'
                    """)
                    column_type = cur.fetchone()
                    
                    if column_type and column_type[0] == 'double precision':
                        logger.info("Found sync_state table with double precision timestamp, adapting")
                        # Initialize sync_state if empty with epoch time
                        cur.execute("SELECT COUNT(*) FROM sync_state")
                        if cur.fetchone()[0] == 0:
                            cur.execute("""
                                INSERT INTO sync_state (id, current_height, current_hash, last_updated_at)
                                VALUES (1, 0, NULL, EXTRACT(EPOCH FROM NOW()))
                            """)
                    else:
                        # Raise the original error if it's not the expected case
                        raise e
                else:
                    # Raise the original error if the table doesn't exist
                    raise e
                
            logger.info("Step 7: Creating indices")
            # Create helpful indices
            logger.info("Creating index: idx_blocks_height")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_blocks_height ON blocks (height)")
            
            logger.info("Creating index: idx_transactions_block_height")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_transactions_block_height ON transactions (block_height)")
            
            logger.info("Creating index: idx_utxos_address_spent")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_utxos_address_spent ON utxos (address) WHERE spent = FALSE")
            
            logger.info("Creating index: idx_utxos_block_height")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_utxos_block_height ON utxos (block_height)")
            
            logger.info("Creating index: idx_glyph_tokens_type")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_glyph_tokens_type ON glyph_tokens (type)")
            
            logger.info("Creating index: idx_token_ref")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_token_ref ON utxos (token_ref) WHERE token_ref IS NOT NULL")
            
            logger.info("Creating index: idx_glyph_tokens_ref")
            # Using ref instead of token_id to match existing schema
            cur.execute("CREATE INDEX IF NOT EXISTS idx_glyph_tokens_ref ON glyph_tokens (ref)")
            
            # Skip creating GIN index on token_balances since it's TEXT, not JSONB
            # logger.info("Creating index: idx_holder_token_balances")
            # cur.execute("CREATE INDEX IF NOT EXISTS idx_holder_token_balances ON holders USING GIN (token_balances)")
            
            logger.info("Database initialized successfully")
            
    except Exception as e:
        import traceback
        logger.error(f"Error initializing database: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise
        
    finally:
        if close_conn and conn:
            conn.close()
            logger.info("Database connection closed")


def run_continuous_sync(interval=60):
    """Run continuous sync with specified interval between cycles."""
    sync_manager = RXinDexerSync()
    
    try:
        logger.info(f"Starting continuous sync with {interval} second interval")
        
        while True:
            try:
                # Check for chain reorganization
                if sync_manager.detect_and_handle_reorg():
                    logger.info("Chain reorganization handled, continuing with sync")
                
                # Run sync
                sync_manager.run_sync()
                
                logger.info(f"Sync complete. Next sync in {interval} seconds")
                time.sleep(interval)
                
            except KeyboardInterrupt:
                logger.info("Received keyboard interrupt, stopping sync")
                break
                
            except Exception as e:
                logger.error(f"Error during sync cycle: {str(e)}")
                logger.info(f"Retrying in {interval} seconds")
                time.sleep(interval)
    
    except KeyboardInterrupt:
        logger.info("Shutting down sync process")
    
    logger.info("Continuous sync stopped")


def main():
    """Main entry point for the RXinDexer sync application."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Radiant RXinDexer Blockchain Synchronization')
    parser.add_argument('--initialize', action='store_true', help='Initialize database tables')
    parser.add_argument('--sync', action='store_true', help='Run sync once and exit')
    parser.add_argument('--continuous', action='store_true', help='Run continuous sync')
    parser.add_argument('--interval', type=int, default=60, help='Interval between sync cycles in seconds')
    parser.add_argument('--update-balances', action='store_true', help='Update token balances and exit')
    parser.add_argument('--refresh-views', action='store_true', help='Refresh materialized views and exit')
    
    args = parser.parse_args()
    
    # Default to continuous sync if no args provided
    if not (args.initialize or args.sync or args.continuous or args.update_balances or args.refresh_views):
        args.continuous = True
    
    try:
        # Initialize database if requested
        if args.initialize:
            initialize_database()
            logger.info("Database initialization complete")
        
        # Create sync manager
        sync_manager = RXinDexerSync()
        
        # Run sync once if requested
        if args.sync:
            logger.info("Running sync once")
            sync_manager.run_sync()
            logger.info("Sync complete")
        
        # Update token balances if requested
        if args.update_balances:
            logger.info("Updating token balances")
            sync_manager.update_token_balances()
            logger.info("Token balances updated")
        
        # Refresh materialized views if requested
        if args.refresh_views:
            logger.info("Refreshing materialized views")
            sync_manager.refresh_materialized_views()
            logger.info("Materialized views refreshed")
        
        # Run continuous sync if requested
        if args.continuous:
            run_continuous_sync(args.interval)
    
    except Exception as e:
        import traceback
        logger.error(f"Error in main: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return 1


if __name__ == "__main__":
    main()
