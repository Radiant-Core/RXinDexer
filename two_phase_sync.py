# /Users/radiant/Desktop/RXinDexer/two_phase_sync.py
# This script implements a two-phase sync approach for the RXinDexer blockchain indexer.
# The purpose is to dramatically speed up initial blockchain synchronization by separating the process into two phases:
# 1. Phase 1: Fast indexing of essential blockchain data (blocks, transactions, UTXOs) without token parsing
# 2. Phase 2: Detailed processing of token metadata, NFTs, and analytics after basic blockchain sync is complete

import os
import sys
import time
import logging
import psycopg2
import json
import asyncio
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, List, Any, Optional
import io
import signal

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/app/logs/two_phase_sync.log')
    ]
)
logger = logging.getLogger(__name__)

# Database connection parameters
DB_HOST = os.getenv('DB_HOST', 'db')
DB_PORT = int(os.getenv('DB_PORT', 5432))
DB_NAME = os.getenv('DB_NAME', 'rxindexer')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'postgres')

# RPC connection parameters - using Bitcoin Core in regtest mode for testing
RPC_URL = os.getenv('RADIANT_RPC_URL', 'http://radiant:18443')
RPC_USER = os.getenv('RADIANT_RPC_USER', 'rxin')
RPC_PASSWORD = os.getenv('RADIANT_RPC_PASSWORD', 'securepassword')
RPC_NETWORK = os.getenv('RADIANT_RPC_NETWORK', 'regtest')

# Sync configuration
MAX_WORKERS = int(os.getenv('SYNC_MAX_WORKERS', 32))
BATCH_SIZE = int(os.getenv('SYNC_BATCH_SIZE', 5000))
UTXO_BATCH_SIZE = int(os.getenv('UTXO_BATCH_SIZE', 10000))
STOP_SIGNAL_FILE = '/app/stop_sync'

# Track processing stats
stats = {
    'start_time': 0,
    'blocks_processed': 0,
    'txs_processed': 0,
    'utxos_processed': 0,
    'current_height': 0,
    'target_height': 0,
    'phase': 1
}

class TwoPhaseSync:
    """Implements high-performance two-phase blockchain synchronization"""
    
    def __init__(self):
        self.db_conn = self._get_db_connection()
        self.rpc_client = self._get_rpc_client()
        self.stop_requested = False
        
        # Register signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle termination signals gracefully"""
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.stop_requested = True
    
    def _get_db_connection(self):
        """Connect to the PostgreSQL database"""
        try:
            conn = psycopg2.connect(
                host=DB_HOST,
                port=DB_PORT,
                dbname=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD
            )
            logger.info(f"Connected to database {DB_NAME} on {DB_HOST}")
            return conn
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise
    
    def _get_rpc_client(self):
        """Connect to the Radiant RPC service"""
        try:
            from bitcoinrpc.authproxy import AuthServiceProxy
            
            # The AuthServiceProxy uses http-basic auth, so we need to include credentials in the URL
            rpc_connection = AuthServiceProxy(
                f"http://{RPC_USER}:{RPC_PASSWORD}@{RPC_URL.replace('http://', '')}"
            )
            
            # Test connection
            info = rpc_connection.getblockchaininfo()
            logger.info(f"Connected to Radiant node, chain height: {info.get('blocks', 'unknown')}")
            
            return rpc_connection
        except ImportError:
            logger.error("bitcoinrpc module not found. Please install it with: pip install python-bitcoinrpc")
            raise
        except Exception as e:
            logger.error(f"Failed to connect to RPC: {e}")
            raise
    
    def prepare_for_phase1(self):
        """Configure database for Phase 1 (fast blockchain data sync)"""
        logger.info("Preparing database for Phase 1 (fast blockchain data sync)...")
        
        with self.db_conn.cursor() as cursor:
            # Set session-level parameters that can be changed safely
            try:
                cursor.execute("SET synchronous_commit = OFF")
                logger.info("Set synchronous_commit = OFF")
            except Exception as e:
                logger.warning(f"Could not set synchronous_commit: {e}")
                
            try:
                cursor.execute("SET work_mem = '256MB'")
                logger.info("Set work_mem = 256MB")
            except Exception as e:
                logger.warning(f"Could not set work_mem: {e}")
                
            try:
                cursor.execute("SET maintenance_work_mem = '1GB'")
                logger.info("Set maintenance_work_mem = 1GB")
            except Exception as e:
                logger.warning(f"Could not set maintenance_work_mem: {e}")
            
            # Skip settings that can't be changed at runtime
            # cursor.execute("SET checkpoint_timeout = '30min'")
            # cursor.execute("SET max_wal_size = '4GB'")
            
            try:
                cursor.execute("SET random_page_cost = 1.1")
                logger.info("Set random_page_cost = 1.1")
            except Exception as e:
                logger.warning(f"Could not set random_page_cost: {e}")
            
            # Disable triggers temporarily if they exist
            try:
                cursor.execute("ALTER TABLE utxos DISABLE TRIGGER ALL")
                logger.info("Disabled triggers on utxos table")
            except Exception as e:
                logger.warning(f"Could not disable triggers: {e}")
            
            # Update sync state to indicate Phase 1
            try:
                cursor.execute("""
                    UPDATE sync_state 
                    SET last_error = 'Starting two-phase sync - Phase 1: Fast blockchain data sync',
                        is_syncing = 1
                    WHERE id = (SELECT MAX(id) FROM sync_state)
                """)
                logger.info("Updated sync state to Phase 1")
            except Exception as e:
                logger.warning(f"Could not update sync state: {e}")
            
        self.db_conn.commit()
        logger.info("Database prepared for Phase 1")
    
    def prepare_for_phase2(self):
        """Configure database for Phase 2 (token data and analytics)"""
        logger.info("Preparing database for Phase 2 (token data and analytics)...")
        
        with self.db_conn.cursor() as cursor:
            # Enable regular transaction guarantees
            cursor.execute("SET synchronous_commit = ON")
            
            # Re-enable triggers
            try:
                cursor.execute("ALTER TABLE utxos ENABLE TRIGGER ALL")
                logger.info("Re-enabled triggers on utxos table")
            except Exception as e:
                logger.warning(f"Could not re-enable triggers: {e}")
            
            # Update sync state to indicate Phase 2
            cursor.execute("""
                UPDATE sync_state 
                SET last_error = 'Starting two-phase sync - Phase 2: Token data and analytics'
                WHERE id = (SELECT MAX(id) FROM sync_state)
            """)
            
        self.db_conn.commit()
        logger.info("Database prepared for Phase 2")
    
    def get_sync_status(self):
        """Get current sync status from the database"""
        with self.db_conn.cursor() as cursor:
            try:
                cursor.execute("SELECT * FROM sync_state ORDER BY id DESC LIMIT 1")
                state = cursor.fetchone()
                
                if state:
                    # Get column names
                    colnames = [desc[0] for desc in cursor.description]
                    state_dict = dict(zip(colnames, state))
                    
                    # Get block height from RPC
                    current_height = self.rpc_client.getblockcount()
                    
                    return {
                        "current_state": state_dict,
                        "node_height": current_height,
                        "sync_progress": state_dict.get('current_height', 0) / current_height if current_height else 0
                    }
                else:
                    return {"error": "No sync state found"}
            except Exception as e:
                logger.error(f"Error getting sync status: {e}")
                return {"error": str(e)}
    
    def bulk_insert_utxos(self, utxos_batch):
        """Fast bulk insertion of UTXOs using COPY"""
        if not utxos_batch:
            return 0
        
        inserted_count = 0
        
        with self.db_conn.cursor() as cursor:
            # Check if COPY will work with the current schema
            try:
                cursor.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'utxos'
                    ORDER BY ordinal_position
                """)
                columns = [row[0] for row in cursor.fetchall()]
                
                # Create StringIO buffer for COPY
                with io.StringIO() as buffer:
                    # Prepare data in tab-separated format
                    for utxo in utxos_batch:
                        line = []
                        for column in columns:
                            value = utxo.get(column, '')
                            # Convert to appropriate string representation
                            if value is None:
                                line.append('\\N')  # NULL in COPY format
                            elif isinstance(value, bool):
                                line.append('t' if value else 'f')
                            else:
                                line.append(str(value))
                        
                        buffer.write('\t'.join(line) + '\n')
                    
                    buffer.seek(0)
                    
                    # Execute COPY command
                    cursor.copy_from(buffer, 'utxos', null='\\N', columns=columns)
                    inserted_count = len(utxos_batch)
                    
            except Exception as e:
                logger.error(f"Error during COPY operation: {e}")
                # Fallback to individual inserts or prepared statement
                self.db_conn.rollback()  # Clear the failed transaction
                
                # Get a fresh connection after rollback
                self.db_conn.commit()
                inserted_count = self._fallback_insert_utxos(utxos_batch)
        
        if inserted_count > 0:
            self.db_conn.commit()
            logger.info(f"Bulk inserted {inserted_count} UTXOs")
        
        return inserted_count
    
    def _fallback_insert_utxos(self, utxos_batch):
        """Fallback method using prepared statement for UTXO inserts"""
        inserted_count = 0
        
        # Try to determine the actual insertion pattern used in the codebase
        with self.db_conn.cursor() as cursor:
            try:
                # Build a basic INSERT statement with ON CONFLICT clause
                cursor.execute("""
                    INSERT INTO utxos 
                    (txid, vout, address, amount, spent, block_height, block_hash, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (txid, vout) DO UPDATE
                    SET address = EXCLUDED.address,
                        amount = EXCLUDED.amount,
                        block_height = EXCLUDED.block_height,
                        block_hash = EXCLUDED.block_hash,
                        updated_at = NOW()
                """, (
                    utxos_batch[0]['txid'],
                    utxos_batch[0]['vout'],
                    utxos_batch[0]['address'],
                    utxos_batch[0]['amount'],
                    utxos_batch[0].get('spent', False),
                    utxos_batch[0]['block_height'],
                    utxos_batch[0].get('block_hash', '')
                ))
                inserted_count += 1
                
                # If the first one works, continue with the rest
                self.db_conn.commit()
                
                # Process the remaining UTXOs in smaller batches
                batch_size = 100
                for i in range(1, len(utxos_batch), batch_size):
                    batch = utxos_batch[i:i + batch_size]
                    values_str = []
                    values_flat = []
                    
                    for utxo in batch:
                        values_str.append("(%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())")
                        values_flat.extend([
                            utxo['txid'],
                            utxo['vout'],
                            utxo['address'],
                            utxo['amount'],
                            utxo.get('spent', False),
                            utxo['block_height'],
                            utxo.get('block_hash', '')
                        ])
                    
                    if values_str:
                        query = f"""
                            INSERT INTO utxos 
                            (txid, vout, address, amount, spent, block_height, block_hash, created_at, updated_at)
                            VALUES {','.join(values_str)}
                            ON CONFLICT (txid, vout) DO UPDATE
                            SET address = EXCLUDED.address,
                                amount = EXCLUDED.amount,
                                block_height = EXCLUDED.block_height,
                                block_hash = EXCLUDED.block_hash,
                                updated_at = NOW()
                        """
                        
                        cursor.execute(query, values_flat)
                        inserted_count += len(batch)
                        self.db_conn.commit()
                
            except Exception as e:
                self.db_conn.rollback()
                logger.error(f"Fallback insert failed: {e}")
                # Last resort: insert one by one
                return self._individual_insert_utxos(utxos_batch)
        
        return inserted_count
    
    def _individual_insert_utxos(self, utxos_batch):
        """Last resort: insert UTXOs one by one"""
        inserted_count = 0
        
        with self.db_conn.cursor() as cursor:
            for utxo in utxos_batch:
                try:
                    cursor.execute("""
                        INSERT INTO utxos 
                        (txid, vout, address, amount, spent, block_height, block_hash, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                        ON CONFLICT (txid, vout) DO UPDATE
                        SET address = EXCLUDED.address,
                            amount = EXCLUDED.amount,
                            block_height = EXCLUDED.block_height,
                            block_hash = EXCLUDED.block_hash,
                            updated_at = NOW()
                    """, (
                        utxo['txid'],
                        utxo['vout'],
                        utxo['address'],
                        utxo['amount'],
                        utxo.get('spent', False),
                        utxo['block_height'],
                        utxo.get('block_hash', '')
                    ))
                    inserted_count += 1
                    
                    # Commit every 100 inserts to avoid transaction bloat
                    if inserted_count % 100 == 0:
                        self.db_conn.commit()
                        
                except Exception as e:
                    logger.error(f"Error inserting UTXO {utxo.get('txid')}:{utxo.get('vout')}: {e}")
            
            # Final commit
            self.db_conn.commit()
        
        logger.info(f"Individually inserted {inserted_count} UTXOs")
        return inserted_count
    
    def update_sync_state(self, height, block_hash):
        """Update sync state in database"""
        with self.db_conn.cursor() as cursor:
            cursor.execute("""
                UPDATE sync_state 
                SET current_height = %s, 
                    current_hash = %s,
                    last_updated_at = %s,
                    updated_at = NOW()
                WHERE id = (SELECT MAX(id) FROM sync_state)
            """, (height, block_hash, time.time()))
            
            # Also add a checkpoint
            cursor.execute("""
                INSERT INTO sync_checkpoints 
                (height, hash, timestamp) 
                VALUES (%s, %s, NOW())
                ON CONFLICT (height) DO UPDATE
                SET hash = EXCLUDED.hash,
                    timestamp = NOW()
            """, (height, block_hash))
            
        self.db_conn.commit()
    
    def process_block(self, height):
        """Process a single block at the specified height"""
        # Check for stop request (from signal handler or stop file)
        if self.stop_requested or os.path.exists(STOP_SIGNAL_FILE):
            logger.info("Stop requested, halting block processing")
            return {
                'height': height,
                'processed': False,
                'error': 'Stop requested'
            }
        
        try:
            # Get block hash
            block_hash = self.rpc_client.getblockhash(height)
            
            # Get full block with transaction details
            block = self.rpc_client.getblock(block_hash, 2)  # Verbosity 2 includes tx details
            
            # For Phase 1, we only process UTXOs, not token data
            utxos = []
            
            # Process each transaction in the block
            for tx in block['tx']:
                txid = tx['txid']
                
                # Process outputs (UTXOs)
                for vout_idx, vout in enumerate(tx.get('vout', [])):
                    # Skip non-standard outputs in Phase 1
                    if stats['phase'] == 1 and vout.get('scriptPubKey', {}).get('type') not in ['pubkeyhash', 'scripthash']:
                        continue
                    
                    # Extract address
                    addresses = vout.get('scriptPubKey', {}).get('addresses', [])
                    address = addresses[0] if addresses else None
                    
                    if address:
                        utxo = {
                            'txid': txid,
                            'vout': vout_idx,
                            'address': address,
                            'amount': vout.get('value', 0),
                            'spent': False,
                            'block_height': height,
                            'block_hash': block_hash,
                            'created_at': 'NOW()',
                            'updated_at': 'NOW()'
                        }
                        utxos.append(utxo)
            
            # Insert UTXOs in bulk
            if utxos:
                utxo_count = self.bulk_insert_utxos(utxos)
            else:
                utxo_count = 0
            
            # Update sync state
            self.update_sync_state(height, block_hash)
            
            # Update stats
            stats['blocks_processed'] += 1
            stats['utxos_processed'] += utxo_count
            stats['current_height'] = height
            
            return {
                'height': height,
                'hash': block_hash,
                'tx_count': len(block['tx']),
                'utxo_count': utxo_count,
                'processed': True
            }
            
        except Exception as e:
            logger.error(f"Error processing block at height {height}: {e}")
            return {
                'height': height,
                'processed': False,
                'error': str(e)
            }
    
def worker_process_block_range(start_height, end_height, rpc_url, rpc_user, rpc_password):
    """Worker function that processes a range of blocks in a separate process"""
    # Create a new database connection in the worker process
    try:
        # Set up logging in the worker process
        worker_logger = logging.getLogger(f'worker-{start_height}-{end_height}')
        worker_logger.setLevel(logging.INFO)
        worker_logger.addHandler(logging.StreamHandler(sys.stdout))
        
        # Connect to database
        db_conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        
        # Connect to RPC
        from bitcoinrpc.authproxy import AuthServiceProxy
        rpc_client = AuthServiceProxy(f"http://{rpc_user}:{rpc_password}@{rpc_url.replace('http://', '')}")
        
        worker_logger.info(f"Worker started for block range {start_height}-{end_height}")
        
        results = []
        blocks_processed = 0
        utxos_processed = 0
        
        for height in range(start_height, end_height + 1):
            try:
                # Check for stop file
                if os.path.exists(STOP_SIGNAL_FILE):
                    worker_logger.info("Stop signal detected, halting worker")
                    break
                
                # Get block hash
                block_hash = rpc_client.getblockhash(height)
                
                # Get full block with transaction details
                block = rpc_client.getblock(block_hash, 2)  # Verbosity 2 includes tx details
                
                # Process UTXOs in the block
                utxos = []
                
                # Process each transaction in the block
                for tx in block['tx']:
                    txid = tx['txid']
                    
                    # Process outputs (UTXOs)
                    for vout_idx, vout in enumerate(tx.get('vout', [])):
                        # Get the script type
                        script_type = vout.get('scriptPubKey', {}).get('type')
                        
                        # Get addresses from the scriptPubKey
                        addresses = vout.get('scriptPubKey', {}).get('addresses', [])
                        
                        # Skip non-standard outputs in Phase 1
                        if script_type not in ['pubkeyhash', 'scripthash']:
                            continue
                        
                        address = addresses[0] if addresses else None
                        
                        if address:
                            utxo = {
                                'txid': txid,
                                'vout': vout_idx,
                                'address': address,
                                'amount': vout.get('value', 0),
                                'spent': False,
                                'block_height': height,
                                'block_hash': block_hash,
                                'created_at': 'NOW()',
                                'updated_at': 'NOW()'
                            }
                            utxos.append(utxo)
                
                # Insert UTXOs in bulk
                if utxos:
                    # Use StringIO for bulk insert
                    with db_conn.cursor() as cursor:
                        # Check if COPY will work with the current schema
                        cursor.execute("""
                            SELECT column_name FROM information_schema.columns
                            WHERE table_name = 'utxos'
                            ORDER BY ordinal_position
                        """)
                        columns = [row[0] for row in cursor.fetchall()]
                        
                        # Determine which columns we actually have data for
                        usable_columns = []
                        for col in columns:
                            if col in utxos[0] or col in ['created_at', 'updated_at']:
                                usable_columns.append(col)
                        
                        # Build the INSERT statement dynamically
                        placeholders = ', '.join(['%s'] * len(usable_columns))
                        columns_str = ', '.join(usable_columns)
                        
                        # Insert in batches of 1000
                        batch_size = 1000
                        for i in range(0, len(utxos), batch_size):
                            batch = utxos[i:i+batch_size]
                            values_list = []
                            
                            for utxo in batch:
                                row_values = []
                                for col in usable_columns:
                                    if col == 'created_at' or col == 'updated_at':
                                        row_values.append('NOW()')
                                    else:
                                        row_values.append(utxo.get(col))
                                values_list.append(tuple(row_values))
                            
                
                utxos_processed += len(utxos)
                blocks_processed += 1
                worker_logger.info(f"Processed block {height}, {len(utxos)} UTXOs, {blocks_processed}/{end_height-start_height+1} blocks")
                
            except Exception as e:
                worker_logger.error(f"Error processing block {height}: {e}")
                # Log the traceback for debugging
                import traceback
                worker_logger.error(traceback.format_exc())
        
        # Commit the changes
        db_conn.commit()
        db_conn.close()
        
        worker_logger.info(f"Worker completed for block range {start_height}-{end_height}, processed {blocks_processed} blocks and {utxos_processed} UTXOs")
        
        return {
            'start_height': start_height,
            'end_height': end_height,
            'blocks_processed': blocks_processed,
            'utxos_processed': utxos_processed
        }
    
    except Exception as e:
        worker_logger = logging.getLogger(f'worker-{start_height}-{end_height}')
        worker_logger.error(f"Worker error: {e}")
        # Log the traceback for debugging
        import traceback
        worker_logger.error(traceback.format_exc())
        return {
            'start_height': start_height,
            'end_height': end_height,
            'blocks_processed': 0,
            'utxos_processed': 0,
            'error': str(e)
        }

class TwoPhaseSync:
    """Implements high-performance two-phase blockchain synchronization"""
    
    def __init__(self):
        self.db_conn = self._get_db_connection()
        self.rpc_client = self._get_rpc_client()
        self.stop_requested = False
        
        # Register signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
    def _signal_handler(self, signum, frame):
        """Handle termination signals gracefully"""
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.stop_requested = True
    
    def _get_db_connection(self):
        """Connect to the PostgreSQL database"""
        try:
            conn = psycopg2.connect(
                host=DB_HOST,
                port=DB_PORT,
                dbname=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD
            )
            logger.info(f"Connected to database {DB_NAME} on {DB_HOST}")
            return conn
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise
    
    def _get_rpc_client(self):
        """Connect to the Radiant RPC service"""
        try:
            from bitcoinrpc.authproxy import AuthServiceProxy
            
            # The AuthServiceProxy uses http-basic auth, so we need to include credentials in the URL
            rpc_connection = AuthServiceProxy(
                f"http://{RPC_USER}:{RPC_PASSWORD}@{RPC_URL.replace('http://', '')}"
            )
            
            # Test the connection
            info = rpc_connection.getblockchaininfo()
            logger.info(f"Connected to Radiant node, chain: {info.get('chain')}, height: {info.get('blocks')}")
            
            return rpc_connection
        except Exception as e:
            logger.error(f"Failed to connect to Radiant RPC: {e}")
            raise
    
    def prepare_for_phase1(self):
        """Configure database for Phase 1 (fast blockchain data sync)"""
        try:
            with self.db_conn.cursor() as cursor:
                # Temporarily disable synchronous commits for better write performance
                cursor.execute("SET synchronous_commit = OFF")
                
                # Temporarily disable some triggers if needed
                try:
                    cursor.execute("ALTER TABLE utxos DISABLE TRIGGER ALL")
                except:
                    logger.warning("Could not disable triggers on utxos table, it may not exist yet")
                
                # Create sync state table if it doesn't exist
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS sync_state (
                        id SERIAL PRIMARY KEY,
                        current_height INTEGER DEFAULT 0,
                        current_hash VARCHAR(64),
                        target_height INTEGER DEFAULT 0,
                        glyph_scan_height INTEGER DEFAULT 0,
                        phase INTEGER DEFAULT 1,
                        sync_started_at TIMESTAMP DEFAULT NOW(),
                        last_updated_at FLOAT,
                        last_error TEXT,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                # Create checkpoints table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS sync_checkpoints (
                        height INTEGER PRIMARY KEY,
                        hash VARCHAR(64) NOT NULL,
                        timestamp TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                # Create blocks table if it doesn't exist
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS blocks (
                        hash VARCHAR(64) PRIMARY KEY,
                        height INTEGER UNIQUE NOT NULL,
                        prev_hash VARCHAR(64),
                        next_hash VARCHAR(64),
                        merkle_root VARCHAR(64),
                        timestamp INTEGER,
                        bits VARCHAR(8),
                        nonce VARCHAR(8),
                        size INTEGER,
                        version INTEGER,
                        transactions JSONB,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                # Create index on height
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_blocks_height ON blocks(height)")
                
                # Create transactions table if it doesn't exist
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS transactions (
                        txid VARCHAR(64) PRIMARY KEY,
                        block_height INTEGER NOT NULL,
                        block_hash VARCHAR(64) NOT NULL,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                # Create index on block_height
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_transactions_block_height ON transactions(block_height)")
                
                # Create UTXOs table if it doesn't exist
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS utxos (
                        txid VARCHAR(64) NOT NULL,
                        vout INTEGER NOT NULL,
                        address VARCHAR(100),
                        amount FLOAT,
                        spent BOOLEAN DEFAULT FALSE,
                        spending_txid VARCHAR(64),
                        spending_vin INTEGER,
                        block_height INTEGER NOT NULL,
                        block_hash VARCHAR(64) NOT NULL,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW(),
                        PRIMARY KEY (txid, vout)
                    )
                """)
                
                # Create indexes for better query performance
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_utxos_address ON utxos(address)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_utxos_spent ON utxos(spent)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_utxos_block_height ON utxos(block_height)")
                
                # Insert or update sync state
                cursor.execute("""
                    INSERT INTO sync_state (current_height, phase, sync_started_at, last_updated_at)
                    VALUES (0, 1, NOW(), %s)
                    ON CONFLICT DO NOTHING
                """, (time.time(),))
                
                self.db_conn.commit()
                logger.info("Database prepared for Phase 1 sync")
        except Exception as e:
            logger.error(f"Error preparing database for Phase 1: {e}")
            self.db_conn.rollback()
            raise
    
    def prepare_for_phase2(self):
        """Configure database for Phase 2 (token data and analytics)"""
        try:
            with self.db_conn.cursor() as cursor:
                # Create token_data table if it doesn't exist
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS token_data (
                        id SERIAL PRIMARY KEY,
                        txid VARCHAR(64) NOT NULL,
                        vout INTEGER NOT NULL,
                        data TEXT NOT NULL,
                        parsed BOOLEAN DEFAULT FALSE,
                        token_type VARCHAR(32),
                        metadata JSONB,
                        created_at TIMESTAMP DEFAULT NOW(),
                        parsed_at TIMESTAMP,
                        UNIQUE(txid, vout)
                    )
                """)
                
                # Create indexes for token data
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_token_data_txid ON token_data(txid)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_token_data_token_type ON token_data(token_type)")
                
                # Create tokens table if it doesn't exist
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS tokens (
                        id SERIAL PRIMARY KEY,
                        token_id VARCHAR(64) UNIQUE NOT NULL,
                        name VARCHAR(255),
                        symbol VARCHAR(32),
                        type VARCHAR(32),
                        decimals INTEGER,
                        total_supply NUMERIC(28, 8),
                        metadata JSONB,
                        mint_txid VARCHAR(64),
                        mint_block_height INTEGER,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                # Create token_balances table if it doesn't exist
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS token_balances (
                        id SERIAL PRIMARY KEY,
                        token_id VARCHAR(64) NOT NULL,
                        address VARCHAR(100) NOT NULL,
                        balance NUMERIC(28, 8) DEFAULT 0,
                        last_updated TIMESTAMP DEFAULT NOW(),
                        UNIQUE(token_id, address)
                    )
                """)
                
                # Create indexes for token balances
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_token_balances_token_id ON token_balances(token_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_token_balances_address ON token_balances(address)")
                
                # Create token_stats table if it doesn't exist
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS token_stats (
                        id SERIAL PRIMARY KEY,
                        token_id VARCHAR(64) UNIQUE NOT NULL,
                        holder_count INTEGER DEFAULT 0,
                        tx_count INTEGER DEFAULT 0,
                        last_price NUMERIC(28, 8),
                        last_updated TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                # Create address_balances table if it doesn't exist
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS address_balances (
                        id SERIAL PRIMARY KEY,
                        address VARCHAR(100) UNIQUE NOT NULL,
                        balance NUMERIC(28, 8) DEFAULT 0,
                        last_updated TIMESTAMP DEFAULT NOW()
                    )
                """)
                
                # Create rich list view if it doesn't exist
                cursor.execute("""
                    CREATE MATERIALIZED VIEW IF NOT EXISTS address_rich_list AS
                    SELECT 
                        address,
                        balance,
                        ROW_NUMBER() OVER (ORDER BY balance DESC) as rank
                    FROM address_balances
                    WHERE balance > 0
                    ORDER BY balance DESC
                """)
                
                # Create index on the rich list
                cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_address_rich_list_address ON address_rich_list(address)")
                
                # Add token_processed column to transactions table if it doesn't exist
                try:
                    cursor.execute("""
                        ALTER TABLE transactions 
                        ADD COLUMN IF NOT EXISTS token_processed BOOLEAN DEFAULT FALSE
                    """)
                except Exception as e:
                    logger.warning(f"Error adding token_processed column: {e}")
                
                # Update sync state for Phase 2
                cursor.execute("""
                    UPDATE sync_state 
                    SET phase = 2, last_updated_at = %s
                    WHERE id = (SELECT MAX(id) FROM sync_state)
                """, (time.time(),))
                
                self.db_conn.commit()
                logger.info("Database prepared for Phase 2 sync")
        except Exception as e:
            logger.error(f"Error preparing database for Phase 2: {e}")
            self.db_conn.rollback()
            raise
    
    def get_sync_status(self):
        """Get current sync status from the database"""
        try:
            # First get Radiant node height
            node_info = self.rpc_client.getblockchaininfo()
            node_height = node_info.get('blocks', 0)
            
            # Then get current sync state
            with self.db_conn.cursor() as cursor:
                cursor.execute("""
                    SELECT 
                        current_height, 
                        current_hash, 
                        target_height, 
                        phase, 
                        glyph_scan_height
                    FROM sync_state 
                    WHERE id = (SELECT MAX(id) FROM sync_state)
                """)
                
                row = cursor.fetchone()
                
                if row:
                    current_state = {
                        'current_height': row[0],
                        'current_hash': row[1],
                        'target_height': row[2],
                        'phase': row[3],
                        'glyph_scan_height': row[4]
                    }
                else:
                    current_state = {
                        'current_height': 0,
                        'current_hash': None,
                        'target_height': 0,
                        'phase': 1,
                        'glyph_scan_height': 0
                    }
                
                # Update the target height if needed
                if current_state['target_height'] < node_height:
                    cursor.execute("""
                        UPDATE sync_state 
                        SET target_height = %s, updated_at = NOW()
                        WHERE id = (SELECT MAX(id) FROM sync_state)
                    """, (node_height,))
                    
                    self.db_conn.commit()
                    current_state['target_height'] = node_height
            
            return {
                'node_height': node_height,
                'current_state': current_state,
                'sync_progress': (current_state['current_height'] / node_height) * 100 if node_height > 0 else 0
            }
        except Exception as e:
            logger.error(f"Error getting sync status: {e}")
            return {
                'node_height': 0,
                'current_state': {
                    'current_height': 0,
                    'current_hash': None,
                    'target_height': 0,
                    'phase': 1,
                    'glyph_scan_height': 0
                },
                'sync_progress': 0,
                'error': str(e)
            }
    
    def bulk_insert_utxos(self, utxos_batch):
        """Fast bulk insertion of UTXOs using COPY"""
        if not utxos_batch:
            return
        
        try:
            # Use StringIO for bulk copy
            with io.StringIO() as f:
                # Get the column names from the first UTXO
                columns = utxos_batch[0].keys()
                
                # Write the UTXO data in a format suitable for PostgreSQL COPY
                for utxo in utxos_batch:
                    values = []
                    for col in columns:
                        val = utxo.get(col)
                        if val is None:
                            values.append('\\N')  # PostgreSQL NULL representation
                        elif isinstance(val, bool):
                            values.append('t' if val else 'f')
                        elif col == 'created_at' or col == 'updated_at':
                            values.append('NOW()')
                        else:
                            values.append(str(val).replace('\t', '\\t').replace('\n', '\\n'))
                    
                    f.write('\t'.join(values) + '\n')
                
                f.seek(0)
                
                # Perform the COPY operation
                with self.db_conn.cursor() as cursor:
                    cursor.copy_from(f, 'utxos', columns=columns)
                
                self.db_conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error during UTXO bulk insert: {e}")
            self.db_conn.rollback()
            
            # Fall back to prepared statement inserts
            return self._fallback_insert_utxos(utxos_batch)
    
    def _fallback_insert_utxos(self, utxos_batch):
        """Fallback method using prepared statement for UTXO inserts"""
        if not utxos_batch:
            return
        
        try:
            logger.warning("Using fallback prepared statement for UTXO inserts")
            
            with self.db_conn.cursor() as cursor:
                # Get column names and placeholders
                columns = utxos_batch[0].keys()
                placeholders = ', '.join(['%s'] * len(columns))
                column_str = ', '.join(columns)
                
                # Build the query
                query = f"INSERT INTO utxos ({column_str}) VALUES ({placeholders}) ON CONFLICT (txid, vout) DO NOTHING"
                
                # Execute for each batch
                for i in range(0, len(utxos_batch), 1000):
                    batch = utxos_batch[i:i+1000]
                    
                    # Convert each UTXO to a tuple of values
                    values = []
                    for utxo in batch:
                        utxo_values = []
                        for col in columns:
                            val = utxo.get(col)
                            if col == 'created_at' or col == 'updated_at':
                                utxo_values.append('NOW()')
                            else:
                                utxo_values.append(val)
                        values.append(tuple(utxo_values))
                    
                    # Use executemany for bulk insert
                    cursor.executemany(query, values)
                
                self.db_conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error during fallback UTXO insert: {e}")
            self.db_conn.rollback()
            
            # Last resort: insert one by one
            return self._individual_insert_utxos(utxos_batch)
    
    def _individual_insert_utxos(self, utxos_batch):
        """Last resort: insert UTXOs one by one"""
        if not utxos_batch:
            return
        
        try:
            logger.warning("Using individual UTXO inserts (last resort)")
            
            with self.db_conn.cursor() as cursor:
                for utxo in utxos_batch:
                    # Build the column and value strings
                    columns = []
                    values = []
                    placeholders = []
                    
                    for col, val in utxo.items():
                        columns.append(col)
                        
                        if col == 'created_at' or col == 'updated_at':
                            placeholders.append('NOW()')
                        else:
                            placeholders.append('%s')
                            values.append(val)
                    
                    # Build and execute the query
                    query = f"INSERT INTO utxos ({', '.join(columns)}) VALUES ({', '.join(placeholders)}) ON CONFLICT (txid, vout) DO NOTHING"
                    cursor.execute(query, values)
                
                self.db_conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error during individual UTXO insert: {e}")
            self.db_conn.rollback()
            return False
    
    def update_sync_state(self, height, block_hash):
        """Update sync state in database"""
        try:
            with self.db_conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE sync_state 
                    SET current_height = %s, 
                        current_hash = %s,
                        last_updated_at = %s,
                        updated_at = NOW()
                    WHERE id = (SELECT MAX(id) FROM sync_state)
                """, (height, block_hash, time.time()))
                
                # Also add a checkpoint
                cursor.execute("""
                    INSERT INTO sync_checkpoints 
                    (height, hash, timestamp) 
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (height) DO UPDATE
                    SET hash = EXCLUDED.hash,
                        timestamp = NOW()
                """, (height, block_hash))
                
                self.db_conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error updating sync state: {e}")
            self.db_conn.rollback()
            return False
    
    def process_block(self, height):
        """Process a single block at the specified height"""
        try:
            # Get block hash
            block_hash = self.rpc_client.getblockhash(height)
            
            # Get full block with transaction details
            block = self.rpc_client.getblock(block_hash, 2)  # Verbosity 2 includes tx details
            
            # Process UTXOs in the block
            utxos = []
            
            # Process each transaction in the block
            for tx in block['tx']:
                txid = tx['txid']
                
                # Process outputs (UTXOs)
                for vout_idx, vout in enumerate(tx.get('vout', [])):
                    # Skip non-standard outputs in Phase 1
                    if vout.get('scriptPubKey', {}).get('type') not in ['pubkeyhash', 'scripthash']:
                        continue
                    
                    # Extract address
                    addresses = vout.get('scriptPubKey', {}).get('addresses', [])
                    address = addresses[0] if addresses else None
                    
                    if address:
                        utxo = {
                            'txid': txid,
                            'vout': vout_idx,
                            'address': address,
                            'amount': vout.get('value', 0),
                            'spent': False,
                            'block_height': height,
                            'block_hash': block_hash,
                            'created_at': 'NOW()',
                            'updated_at': 'NOW()'
                        }
                        utxos.append(utxo)
            
            # Insert UTXOs in bulk
            if utxos:
                self.bulk_insert_utxos(utxos)
            
            # Update sync state
            self.update_sync_state(height, block_hash)
            
            # Update stats
            stats['blocks_processed'] += 1
            stats['utxos_processed'] += len(utxos)
            stats['current_height'] = height
            
            return {
                'height': height,
                'hash': block_hash,
                'tx_count': len(block['tx']),
                'utxo_count': len(utxos),
                'processed': True
            }
        except Exception as e:
            logger.error(f"Error processing block at height {height}: {e}")
            return {
                'height': height,
                'processed': False,
                'error': str(e)
            }
    
    def process_block_range(self, start_height, end_height):
        """Process a range of blocks - this is now a wrapper for the worker function"""
        logger.info(f"Processing block range: {start_height} to {end_height}")
        
        # Call the worker function directly in the same process
        # (this is used when multiprocessing fails or for testing)
        return worker_process_block_range(
            start_height, 
            end_height,
            RPC_URL.replace('http://', ''),
            RPC_USER,
            RPC_PASSWORD
        )
    
    async def run_phase1(self):
        """Run Phase 1: Fast blockchain data sync"""
        logger.info("Starting Phase 1: Fast blockchain data sync")
        stats['phase'] = 1
        
        # Prepare database
        self.prepare_for_phase1()
        
        # Get current sync status
        sync_status = self.get_sync_status()
        current_height = sync_status.get('current_state', {}).get('current_height', 0)
        target_height = sync_status.get('node_height', 0)
        
        if target_height <= current_height:
            logger.info(f"Already synced to height {current_height}, no Phase 1 sync needed")
            return True
        
        logger.info(f"Phase 1 sync: {current_height} to {target_height} ({target_height - current_height} blocks)")
        
        # Reset stats
        stats['start_time'] = time.time()
        stats['blocks_processed'] = 0
        stats['txs_processed'] = 0
        stats['utxos_processed'] = 0
        stats['current_height'] = current_height
        stats['target_height'] = target_height
        
        # Calculate block ranges for parallel processing
        ranges = []
        for start in range(current_height, target_height + 1, BATCH_SIZE):
            end = min(start + BATCH_SIZE - 1, target_height)
            ranges.append((start, end))
        
        # First try with multiprocessing
        try:
            # Process in parallel with ProcessPoolExecutor
            total_blocks_processed = 0
            total_utxos_processed = 0
            
            with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
                # Submit worker tasks to process block ranges
                futures = []
                for start, end in ranges:
                    # Pass the necessary parameters directly to avoid pickling issues
                    future = executor.submit(
                        worker_process_block_range, 
                        start, 
                        end,
                        RPC_URL.replace('http://', ''),
                        RPC_USER,
                        RPC_PASSWORD
                    )
                    futures.append((future, start, end))
                
                # Process results as they complete
                for future, start, end in futures:
                    try:
                        result = future.result()
                        
                        # Update stats
                        blocks_processed = result.get('blocks_processed', 0)
                        utxos_processed = result.get('utxos_processed', 0)
                        
                        total_blocks_processed += blocks_processed
                        total_utxos_processed += utxos_processed
                        
                        # Update global stats
                        stats['blocks_processed'] += blocks_processed
                        stats['utxos_processed'] += utxos_processed
                        
                        # Report progress
                        elapsed = time.time() - stats['start_time']
                        blocks_per_second = total_blocks_processed / elapsed if elapsed > 0 else 0
                        utxos_per_second = total_utxos_processed / elapsed if elapsed > 0 else 0
                        
                        logger.info(f"Completed range {start}-{end}: {blocks_processed} blocks, {utxos_processed} UTXOs")
                        logger.info(f"Current speed: {blocks_per_second:.2f} blocks/s, {utxos_per_second:.2f} UTXOs/s")
                        
                    except Exception as e:
                        logger.error(f"Error in range {start}-{end}: {e}")
                    
                    # Check for stop request
                    if self.stop_requested or os.path.exists(STOP_SIGNAL_FILE):
                        logger.info("Stop requested, cancelling remaining tasks")
                        executor.shutdown(wait=False)
                        break
            
        except Exception as e:
            logger.error(f"Error during parallel processing: {e}")
            logger.warning("Falling back to sequential processing")
            
            # Sequential fallback if parallel processing fails
            total_blocks_processed = 0
            total_utxos_processed = 0
            
            for start, end in ranges:
                if self.stop_requested or os.path.exists(STOP_SIGNAL_FILE):
                    logger.info("Stop requested, halting processing")
                    break
                    
                try:
                    # Process range sequentially
                    result = worker_process_block_range(
                        start, 
                        end,
                        RPC_URL.replace('http://', ''),
                        RPC_USER,
                        RPC_PASSWORD
                    )
                    
                    # Update stats
                    blocks_processed = result.get('blocks_processed', 0)
                    utxos_processed = result.get('utxos_processed', 0)
                    
                    total_blocks_processed += blocks_processed
                    total_utxos_processed += utxos_processed
                    
                    # Update global stats
                    stats['blocks_processed'] += blocks_processed
                    stats['utxos_processed'] += utxos_processed
                    
                    # Report progress
                    elapsed = time.time() - stats['start_time']
                    blocks_per_second = total_blocks_processed / elapsed if elapsed > 0 else 0
                    utxos_per_second = total_utxos_processed / elapsed if elapsed > 0 else 0
                    
                    logger.info(f"Completed range {start}-{end}: {blocks_processed} blocks, {utxos_processed} UTXOs")
                    logger.info(f"Current speed: {blocks_per_second:.2f} blocks/s, {utxos_per_second:.2f} UTXOs/s")
                    
                except Exception as e:
                    logger.error(f"Error processing range {start}-{end} sequentially: {e}")
        
        # Calculate final statistics
        elapsed = time.time() - stats['start_time']
        blocks_per_second = stats['blocks_processed'] / elapsed if elapsed > 0 else 0
        utxos_per_second = stats['utxos_processed'] / elapsed if elapsed > 0 else 0
        
        logger.info(f"Phase 1 completed in {elapsed:.2f} seconds")
        logger.info(f"Processed {stats['blocks_processed']} blocks ({blocks_per_second:.2f} blocks/s)")
        logger.info(f"Processed {stats['utxos_processed']} UTXOs ({utxos_per_second:.2f} UTXOs/s)")
        
        # Check if we completed to the target height
        sync_status = self.get_sync_status()
        current_height = sync_status.get('current_state', {}).get('current_height', 0)
        
        if current_height >= target_height:
            logger.info(f"Phase 1 sync complete: reached target height {target_height}")
            return True
        else:
            logger.warning(f"Phase 1 sync incomplete: reached height {current_height} of {target_height}")
            return False
    
def worker_process_token_tx(txid, height, block_hash, rpc_url, rpc_user, rpc_password):
    """Worker function to process a token transaction in a separate process"""
    # Create new connections in the worker process
    try:
        # Set up logging
        worker_logger = logging.getLogger(f'token-worker-{txid[:8]}')
        worker_logger.setLevel(logging.INFO)
        worker_logger.addHandler(logging.StreamHandler(sys.stdout))
        
        # Connect to database
        db_conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        
        # Connect to RPC
        from bitcoinrpc.authproxy import AuthServiceProxy
        rpc_client = AuthServiceProxy(f"http://{rpc_user}:{rpc_password}@{rpc_url}")
        
        worker_logger.info(f"Processing token transaction {txid} at height {height}")
        
        # Get transaction details
        try:
            tx = rpc_client.getrawtransaction(txid, 1)
        except Exception as e:
            worker_logger.error(f"Error fetching transaction {txid}: {e}")
            return {
                'txid': txid,
                'height': height,
                'error': f"Error fetching transaction: {str(e)}",
                'tokens_processed': 0,
                'success': False
            }
        
        tokens_processed = 0
        
        try:
            # Check for Glyph tokens in transaction outputs
            for vout in tx.get('vout', []):
                # Look for OP_RETURN outputs that might contain token data
                if vout.get('scriptPubKey', {}).get('type') == 'nulldata':
                    asm = vout.get('scriptPubKey', {}).get('asm', '')
                    
                    # Check if this might be a Glyph token (very simple detection for demonstration)
                    if 'OP_RETURN' in asm and len(asm.split()) > 1:
                        worker_logger.info(f"Found potential token data in {txid} output {vout['n']}")
                        
                        # Parse hex data after OP_RETURN
                        hex_data = asm.split()[1]
                        
                        # In a real implementation, you would use the CBOR parser here
                        # For example:
                        # import cbor2
                        # try:
                        #     token_data = cbor2.loads(bytes.fromhex(hex_data))
                        #     # Process token data based on type
                        # except:
                        #     worker_logger.warning(f"Could not parse CBOR data in {txid}")
                        
                        # For now, just store the raw data
                        try:
                            with db_conn.cursor() as cursor:
                                # Check if the token_data table exists
                                cursor.execute("""
                                    SELECT EXISTS (
                                        SELECT FROM information_schema.tables 
                                        WHERE table_name = 'token_data'
                                    )
                                """)
                                table_exists = cursor.fetchone()[0]
                                
                                if not table_exists:
                                    # Create table if it doesn't exist
                                    cursor.execute("""
                                        CREATE TABLE IF NOT EXISTS token_data (
                                            id SERIAL PRIMARY KEY,
                                            txid VARCHAR(64) NOT NULL,
                                            vout INTEGER NOT NULL,
                                            data TEXT NOT NULL,
                                            parsed BOOLEAN DEFAULT FALSE,
                                            token_type VARCHAR(32),
                                            metadata JSONB,
                                            created_at TIMESTAMP DEFAULT NOW(),
                                            parsed_at TIMESTAMP,
                                            UNIQUE(txid, vout)
                                        )
                                    """)
                                    
                                    # Create index for faster lookups
                                    cursor.execute("""
                                        CREATE INDEX IF NOT EXISTS idx_token_data_txid ON token_data(txid)
                                    """)
                                
                                # Insert token data
                                cursor.execute("""
                                    INSERT INTO token_data (txid, vout, data, parsed_at)
                                    VALUES (%s, %s, %s, NOW())
                                    ON CONFLICT (txid, vout) DO UPDATE
                                    SET data = EXCLUDED.data, parsed_at = NOW()
                                """, (txid, vout['n'], hex_data))
                                
                                tokens_processed += 1
                        except Exception as e:
                            worker_logger.error(f"Error storing token data for {txid}: {e}")
            
            # Check if the transactions table has a token_processed column
            with db_conn.cursor() as cursor:
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.columns 
                        WHERE table_name = 'transactions' AND column_name = 'token_processed'
                    )
                """)
                column_exists = cursor.fetchone()[0]
                
                if column_exists:
                    # Mark transaction as processed
                    cursor.execute("""
                        UPDATE transactions
                        SET token_processed = true, updated_at = NOW()
                        WHERE txid = %s
                    """, (txid,))
                else:
                    # If the column doesn't exist, we'll add it and update
                    try:
                        cursor.execute("""
                            ALTER TABLE transactions
                            ADD COLUMN IF NOT EXISTS token_processed BOOLEAN DEFAULT FALSE
                        """)
                        
                        cursor.execute("""
                            UPDATE transactions
                            SET token_processed = true, updated_at = NOW()
                            WHERE txid = %s
                        """, (txid,))
                    except Exception as e:
                        worker_logger.error(f"Error updating transaction {txid}: {e}")
            
            db_conn.commit()
            worker_logger.info(f"Processed {tokens_processed} tokens in transaction {txid}")
            
            return {
                'txid': txid,
                'height': height,
                'tokens_processed': tokens_processed,
                'success': True
            }
            
        except Exception as e:
            db_conn.rollback()
            worker_logger.error(f"Error processing tokens in tx {txid}: {e}")
            return {
                'txid': txid,
                'height': height,
                'error': str(e),
                'tokens_processed': 0,
                'success': False
            }
        finally:
            # Close database connection
            db_conn.close()
            
    except Exception as e:
        logging.error(f"Worker error for token tx {txid}: {e}")
        return {
            'txid': txid,
            'height': height,
            'error': str(e),
            'tokens_processed': 0,
            'success': False
        }

    def update_analytics(self):
        """Update all analytics based on the current blockchain data"""
        logger.info("Updating analytics...")
        try:
            with self.db_conn.cursor() as cursor:
                # Update address balances
                logger.info("Updating address balances...")
                cursor.execute("""
                    INSERT INTO address_balances (address, balance, last_updated)
                    SELECT 
                        address, 
                        SUM(amount) as balance,
                        NOW() as last_updated
                    FROM utxos 
                    WHERE spent = FALSE 
                    GROUP BY address
                    ON CONFLICT (address) DO UPDATE
                    SET balance = EXCLUDED.balance,
                        last_updated = EXCLUDED.last_updated
                """)
                
                # Update token holder counts
                logger.info("Updating token holder counts...")
                cursor.execute("""
                    INSERT INTO token_stats (token_id, holder_count, last_updated)
                    SELECT 
                        t.token_id,
                        COUNT(DISTINCT tb.address) as holder_count,
                        NOW() as last_updated
                    FROM token_balances tb
                    JOIN tokens t ON tb.token_id = t.token_id
                    WHERE tb.balance > 0
                    GROUP BY t.token_id
                    ON CONFLICT (token_id) DO UPDATE
                    SET holder_count = EXCLUDED.holder_count,
                        last_updated = EXCLUDED.last_updated
                """)
                
                # Update rich list
                logger.info("Updating rich list...")
                cursor.execute("""
                    REFRESH MATERIALIZED VIEW IF EXISTS address_rich_list
                """)
                
                self.db_conn.commit()
                logger.info("Analytics updated successfully")
                return True
                
        except Exception as e:
            logger.error(f"Error updating analytics: {e}")
            return False
    
    async def run_phase2(self):
        """Run Phase 2: Token metadata and analytics processing"""
        logger.info("Starting Phase 2: Token metadata and analytics processing")
        stats['phase'] = 2
        
        # Prepare database for Phase 2
        self.prepare_for_phase2()
        
        # Get all token transactions that need to be processed
        try:
            # First, ensure the transactions table has a token_processed column
            with self.db_conn.cursor() as cursor:
                # Check if the column exists
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.columns 
                        WHERE table_name = 'transactions' AND column_name = 'token_processed'
                    )
                """)
                column_exists = cursor.fetchone()[0]
                
                if not column_exists:
                    # Add the column if it doesn't exist
                    cursor.execute("""
                        ALTER TABLE transactions
                        ADD COLUMN token_processed BOOLEAN DEFAULT FALSE
                    """)
                    self.db_conn.commit()
                    logger.info("Added token_processed column to transactions table")
            
            # Now get transactions that need processing
            with self.db_conn.cursor() as cursor:
                # First check if the transactions table exists
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'transactions'
                    )
                """)
                table_exists = cursor.fetchone()[0]
                
                if not table_exists:
                    logger.warning("Transactions table does not exist. Creating it now.")
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS transactions (
                            txid VARCHAR(64) PRIMARY KEY,
                            block_height INTEGER NOT NULL,
                            block_hash VARCHAR(64) NOT NULL,
                            token_processed BOOLEAN DEFAULT FALSE,
                            created_at TIMESTAMP DEFAULT NOW(),
                            updated_at TIMESTAMP DEFAULT NOW()
                        )
                    """)
                    self.db_conn.commit()
                    logger.info("Created transactions table")
                    
                    # Populate it from blocks table if that exists
                    cursor.execute("""
                        SELECT EXISTS (
                            SELECT FROM information_schema.tables 
                            WHERE table_name = 'blocks'
                        )
                    """)
                    blocks_table_exists = cursor.fetchone()[0]
                    
                    if blocks_table_exists:
                        logger.info("Populating transactions table from blocks table")
                        try:
                            cursor.execute("""
                                INSERT INTO transactions (txid, block_height, block_hash)
                                SELECT txid, height, hash FROM blocks
                                CROSS JOIN LATERAL jsonb_array_elements_text(transactions) AS txid
                                ON CONFLICT (txid) DO NOTHING
                            """)
                            self.db_conn.commit()
                        except Exception as e:
                            logger.error(f"Error populating transactions: {e}")
                
                # Query for transactions to process
                try:
                    cursor.execute("""
                        SELECT 
                            txid, 
                            block_height,
                            block_hash
                        FROM transactions 
                        WHERE token_processed = false 
                        ORDER BY block_height
                    """)
                    
                    token_txs = cursor.fetchall()
                    total_txs = len(token_txs)
                    
                    logger.info(f"Found {total_txs} token transactions to process in Phase 2")
                    
                    if total_txs == 0:
                        logger.info("No token transactions to process, Phase 2 complete")
                        return True
                except Exception as e:
                    logger.error(f"Error querying transactions: {e}")
                    return False
                
                # Process in batches
                batch_size = min(BATCH_SIZE, total_txs)
                num_batches = (total_txs + batch_size - 1) // batch_size
                
                stats['start_time'] = time.time()
                stats['tokens_processed'] = 0
                stats['txs_processed'] = 0
                
                # Try with parallel processing first
                try:
                    # Process each batch
                    for i in range(num_batches):
                        start_idx = i * batch_size
                        end_idx = min((i + 1) * batch_size, total_txs)
                        batch = token_txs[start_idx:end_idx]
                        
                        logger.info(f"Processing batch {i+1}/{num_batches} with {len(batch)} transactions")
                        
                        # Process transactions in parallel
                        with ProcessPoolExecutor(max_workers=TOKEN_MAX_WORKERS) as executor:
                            futures = []
                            for txid, height, block_hash in batch:
                                future = executor.submit(
                                    worker_process_token_tx, 
                                    txid, 
                                    height, 
                                    block_hash,
                                    RPC_URL.replace('http://', ''),
                                    RPC_USER,
                                    RPC_PASSWORD
                                )
                                futures.append(future)
                            
                            # Process results as they complete
                            for future in as_completed(futures):
                                try:
                                    result = future.result()
                                    stats['txs_processed'] += 1
                                    stats['tokens_processed'] += result.get('tokens_processed', 0)
                                    
                                    # Print progress
                                    if stats['txs_processed'] % 100 == 0:
                                        elapsed = time.time() - stats['start_time']
                                        txs_per_second = stats['txs_processed'] / elapsed if elapsed > 0 else 0
                                        tokens_per_second = stats['tokens_processed'] / elapsed if elapsed > 0 else 0
                                        progress = stats['txs_processed'] / total_txs * 100
                                        
                                        logger.info(f"Progress: {progress:.2f}% - {stats['txs_processed']}/{total_txs} txs - "
                                                   f"Speed: {txs_per_second:.2f} txs/s, {tokens_per_second:.2f} tokens/s")
                                except Exception as e:
                                    logger.error(f"Error processing token tx: {e}")
                                
                                # Check for stop request
                                if self.stop_requested or os.path.exists(STOP_SIGNAL_FILE):
                                    logger.info("Stop requested, cancelling remaining tasks")
                                    executor.shutdown(wait=False)
                                    break
                        
                        # Check for stop request
                        if self.stop_requested or os.path.exists(STOP_SIGNAL_FILE):
                            logger.info("Stop requested, halting batch processing")
                            break
                
                except Exception as e:
                    logger.error(f"Error during parallel token processing: {e}")
                    logger.warning("Falling back to sequential token processing")
                    
                    # Sequential fallback
                    for txid, height, block_hash in token_txs[stats['txs_processed']:]:
                        if self.stop_requested or os.path.exists(STOP_SIGNAL_FILE):
                            logger.info("Stop requested, halting processing")
                            break
                        
                        try:
                            # Process token transaction sequentially
                            result = worker_process_token_tx(
                                txid, 
                                height, 
                                block_hash,
                                RPC_URL.replace('http://', ''),
                                RPC_USER,
                                RPC_PASSWORD
                            )
                            
                            stats['txs_processed'] += 1
                            stats['tokens_processed'] += result.get('tokens_processed', 0)
                            
                            # Print progress
                            if stats['txs_processed'] % 100 == 0:
                                elapsed = time.time() - stats['start_time']
                                txs_per_second = stats['txs_processed'] / elapsed if elapsed > 0 else 0
                                tokens_per_second = stats['tokens_processed'] / elapsed if elapsed > 0 else 0
                                progress = stats['txs_processed'] / total_txs * 100
                                
                                logger.info(f"Progress: {progress:.2f}% - {stats['txs_processed']}/{total_txs} txs - "
                                           f"Speed: {txs_per_second:.2f} txs/s, {tokens_per_second:.2f} tokens/s")
                                
                        except Exception as e:
                            logger.error(f"Error processing token tx {txid} sequentially: {e}")
                
                # Calculate statistics
                elapsed = time.time() - stats['start_time']
                txs_per_second = stats['txs_processed'] / elapsed if elapsed > 0 else 0
                tokens_per_second = stats['tokens_processed'] / elapsed if elapsed > 0 else 0
                
                logger.info(f"Phase 2 completed in {elapsed:.2f} seconds")
                logger.info(f"Processed {stats['txs_processed']}/{total_txs} transactions ({txs_per_second:.2f} txs/s)")
                logger.info(f"Processed {stats['tokens_processed']} tokens ({tokens_per_second:.2f} tokens/s)")
                
                # Update sync state to indicate Phase 2 completion
                with self.db_conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE sync_state 
                        SET last_error = 'Two-phase sync completed - Phase 2: Token data and analytics done',
                            glyph_scan_height = current_height
                        WHERE id = (SELECT MAX(id) FROM sync_state)
                    """)
                    self.db_conn.commit()
                
                # Check if we completed all transactions
                if stats['txs_processed'] >= total_txs:
                    logger.info("Phase 2 sync complete: processed all token transactions")
                    return True
                else:
                    logger.warning(f"Phase 2 sync incomplete: processed {stats['txs_processed']}/{total_txs} transactions")
                    return False
                
        except Exception as e:
            logger.error(f"Error in Phase 2 processing: {e}")
            return False
    
    async def run(self):
        """Run the complete two-phase sync process"""
        try:
            # Run Phase 1
            phase1_success = await self.run_phase1()
            
            if not phase1_success:
                logger.warning("Phase 1 did not complete successfully, stopping sync process")
                return False
            
            # Run Phase 2
            phase2_success = await self.run_phase2()
            
            if not phase2_success:
                logger.warning("Phase 2 did not complete successfully")
                return False
            
            logger.info("Two-phase sync completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error in two-phase sync: {e}")
            return False
        finally:
            # Always make sure we restore normal database settings
            try:
                with self.db_conn.cursor() as cursor:
                    cursor.execute("SET synchronous_commit = ON")
                    
                    # Re-enable triggers
                    try:
                        cursor.execute("ALTER TABLE utxos ENABLE TRIGGER ALL")
                    except:
                        pass
                
                self.db_conn.commit()
            except:
                pass

async def main():
    """Main entry point"""
    # Clear any existing stop signal file
    if os.path.exists(STOP_SIGNAL_FILE):
        os.remove(STOP_SIGNAL_FILE)
    
    # Create logs directory if it doesn't exist
    os.makedirs('/app/logs', exist_ok=True)
    
    # Run the two-phase sync
    syncer = TwoPhaseSync()
    success = await syncer.run()
    
    if success:
        logger.info("Two-phase sync completed successfully")
        return 0
    else:
        logger.error("Two-phase sync failed")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
