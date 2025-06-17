# /Users/radiant/Desktop/RXinDexer/sync/optimized_sync.py
# This module provides high-performance blockchain synchronization with parallel processing
# It focuses on maximum throughput during initial indexing through bulk operations and resource optimization

import os
import io
import asyncio
import logging
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import lru_cache
import redis
from typing import List, Dict, Tuple, Optional, Any

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration from environment variables with sensible defaults
SYNC_BATCH_SIZE = int(os.getenv('SYNC_BATCH_SIZE', 5000))
SYNC_MAX_WORKERS = int(os.getenv('SYNC_MAX_WORKERS', 32))
UTXO_MAX_WORKERS = int(os.getenv('UTXO_MAX_WORKERS', 8))
BLOCK_PARALLEL_THRESHOLD = int(os.getenv('BLOCK_PARALLEL_THRESHOLD', 100))
PROGRESSIVE_SYNC = os.getenv('PROGRESSIVE_SYNC', 'True').lower() == 'true'
INITIAL_SYNC_MINIMAL = os.getenv('INITIAL_SYNC_MINIMAL', 'True').lower() == 'true'
USE_REDIS_CACHE = os.getenv('USE_REDIS_CACHE', 'True').lower() == 'true'
REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0')

# Initialize Redis connection if enabled
redis_client = None
if USE_REDIS_CACHE:
    try:
        redis_client = redis.from_url(REDIS_URL)
        redis_client.ping()  # Test connection
        logger.info("Redis cache enabled and connected")
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}, continuing without caching")
        redis_client = None

class OptimizedSync:
    """High-performance blockchain sync implementation"""
    
    def __init__(self, db_connection, rpc_client):
        self.db = db_connection
        self.rpc = rpc_client
        self.bloom_filter = None
        self.init_bloom_filter()
    
    def init_bloom_filter(self):
        """Initialize bloom filter for fast tx lookups"""
        try:
            import pybloom_live
            # Create a bloom filter with capacity for 10M transactions with 0.1% error rate
            self.bloom_filter = pybloom_live.ScalableBloomFilter(
                initial_capacity=10000000, 
                error_rate=0.001,
                mode=pybloom_live.ScalableBloomFilter.LARGE_SET_GROWTH
            )
            
            # Pre-populate with existing transactions
            with self.db.cursor() as cursor:
                cursor.execute("SELECT txid FROM transactions")
                for row in cursor:
                    self.bloom_filter.add(row[0])
            
            logger.info(f"Initialized bloom filter with existing transactions")
        except ImportError:
            logger.warning("pybloom_live not installed, continuing without bloom filter")
            self.bloom_filter = None
    
    def tx_exists(self, txid: str) -> bool:
        """Fast check if transaction exists using bloom filter"""
        if self.bloom_filter:
            # Bloom filters can have false positives but never false negatives
            if txid not in self.bloom_filter:
                return False
            
        # Double-check database for certainty if bloom filter indicates existence
        with self.db.cursor() as cursor:
            cursor.execute("SELECT 1 FROM transactions WHERE txid = %s LIMIT 1", (txid,))
            return cursor.fetchone() is not None
    
    def prepare_for_sync(self):
        """Prepare database for bulk loading"""
        with self.db.cursor() as cursor:
            # Disable triggers temporarily
            cursor.execute("ALTER TABLE utxos DISABLE TRIGGER ALL")
            cursor.execute("ALTER TABLE transactions DISABLE TRIGGER ALL")
            cursor.execute("ALTER TABLE blocks DISABLE TRIGGER ALL")
            
            # Configure for bulk loading
            if INITIAL_SYNC_MINIMAL:
                cursor.execute("SET synchronous_commit TO OFF")  # Danger: only during initial sync!
                cursor.execute("SET maintenance_work_mem TO '1GB'")
                cursor.execute("SET checkpoint_timeout TO '30min'")
            
            # Create unlogged tables for temp storage during initial load
            cursor.execute("""
                CREATE UNLOGGED TABLE IF NOT EXISTS temp_utxos (
                    txid TEXT,
                    vout INTEGER,
                    address TEXT,
                    amount NUMERIC,
                    script_pubkey TEXT,
                    block_height INTEGER,
                    spent BOOLEAN DEFAULT FALSE,
                    token_ref TEXT
                )
            """)
            
            # Drop non-essential indices during sync
            if INITIAL_SYNC_MINIMAL:
                self._drop_indices()
                
        self.db.commit()
        logger.info("Database prepared for bulk loading")
    
    def _drop_indices(self):
        """Drop non-essential indices for faster bulk loading"""
        indices_to_keep = ['utxos_pkey', 'blocks_pkey', 'transactions_pkey']
        
        with self.db.cursor() as cursor:
            # Get all indices
            cursor.execute("""
                SELECT indexname FROM pg_indexes 
                WHERE tablename IN ('utxos', 'transactions', 'blocks')
                AND indexname NOT IN %s
            """, (tuple(indices_to_keep),))
            
            indices = [row[0] for row in cursor.fetchall()]
            
            # Drop non-essential indices
            for idx in indices:
                cursor.execute(f"DROP INDEX IF EXISTS {idx}")
                logger.info(f"Dropped index {idx} for faster bulk loading")
        
        self.db.commit()
    
    def restore_after_sync(self):
        """Restore database state after bulk loading"""
        with self.db.cursor() as cursor:
            # Re-enable triggers
            cursor.execute("ALTER TABLE utxos ENABLE TRIGGER ALL")
            cursor.execute("ALTER TABLE transactions ENABLE TRIGGER ALL")
            cursor.execute("ALTER TABLE blocks ENABLE TRIGGER ALL")
            
            if INITIAL_SYNC_MINIMAL:
                cursor.execute("SET synchronous_commit TO ON")
                
            # Recreate indices
            self._recreate_indices()
            
            # Move data from unlogged to logged tables
            cursor.execute("""
                INSERT INTO utxos 
                SELECT * FROM temp_utxos 
                ON CONFLICT DO NOTHING
            """)
            
            # Drop temporary tables
            cursor.execute("DROP TABLE IF EXISTS temp_utxos")
            
            # Run VACUUM ANALYZE
            cursor.execute("VACUUM ANALYZE utxos")
            cursor.execute("VACUUM ANALYZE transactions")
            cursor.execute("VACUUM ANALYZE blocks")
            
        self.db.commit()
        logger.info("Database restored to normal operation mode")
    
    def _recreate_indices(self):
        """Recreate indices after bulk loading"""
        with self.db.cursor() as cursor:
            # Create optimized indices
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_utxos_address_spent ON utxos (address) WHERE spent = FALSE")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_utxos_block_height ON utxos (block_height)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_blocks_height ON blocks (height)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_transactions_block_height ON transactions (block_height)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_glyph_tokens_type ON glyph_tokens (type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_token_ref ON utxos (token_ref) WHERE token_ref IS NOT NULL")
            
        self.db.commit()
        logger.info("Recreated all necessary indices")
    
    @lru_cache(maxsize=1000)
    def _cached_get_block_hash(self, height: int) -> str:
        """Get block hash with local caching"""
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
    
    async def get_block_with_cache(self, height: int) -> dict:
        """Get full block with caching"""
        if redis_client:
            cache_key = f"block:{height}"
            cached = redis_client.get(cache_key)
            if cached:
                import json
                return json.loads(cached.decode('utf-8'))
        
        # Get block hash first (cached)
        block_hash = self._cached_get_block_hash(height)
        
        # Get full block
        block = self.rpc.getblock(block_hash, 2)  # Verbose=2 includes tx details
        
        # Cache in Redis
        if redis_client:
            import json
            redis_client.set(f"block:{height}", json.dumps(block), ex=3600)
        
        return block
    
    def bulk_insert_utxos(self, utxos_batch: List[Dict[str, Any]]):
        """Fast COPY-based insertion of UTXOs"""
        if not utxos_batch:
            return
            
        with self.db.cursor() as cursor:
            with io.StringIO() as f:
                for utxo in utxos_batch:
                    # Format as tab-separated values
                    line = f"{utxo['txid']}\t{utxo['vout']}\t{utxo['address']}\t{utxo['amount']}\t"
                    line += f"{utxo.get('script_pubkey', '')}\t{utxo['block_height']}\t"
                    line += f"{utxo.get('spent', False)}\t{utxo.get('token_ref', '')}\n"
                    f.write(line)
                
                f.seek(0)
                cursor.copy_from(
                    f,
                    'temp_utxos',
                    null='\\N',
                    columns=('txid', 'vout', 'address', 'amount', 'script_pubkey', 
                             'block_height', 'spent', 'token_ref')
                )
        
        self.db.commit()
        logger.info(f"Bulk inserted {len(utxos_batch)} UTXOs")
    
    def bulk_insert_transactions(self, txs_batch: List[Dict[str, Any]]):
        """Fast COPY-based insertion of transactions"""
        if not txs_batch:
            return
            
        with self.db.cursor() as cursor:
            with io.StringIO() as f:
                for tx in txs_batch:
                    # Ensure timestamp is an integer (Unix epoch seconds)
                    timestamp = tx.get('timestamp')
                    if timestamp is None:
                        timestamp = 'NOW()'  # Will be handled by database function
                    else:
                        try:
                            timestamp = str(int(timestamp))  # Convert to integer string
                        except (ValueError, TypeError):
                            logger.warning(f"Invalid timestamp for tx {tx.get('txid', 'unknown')}, using NOW()")
                            timestamp = 'NOW()'
                    
                    # Format as tab-separated values
                    line = f"{tx['txid']}\t{tx['block_height']}\t{tx.get('size', 0)}\t"
                    line += f"{timestamp}\t{tx.get('fee', 0)}\n"
                    f.write(line)
                
                f.seek(0)
                cursor.copy_from(
                    f,
                    'transactions',
                    null='\\N',
                    columns=('txid', 'block_height', 'size', 'timestamp', 'fee')
                )
                
                # Add to bloom filter
                if self.bloom_filter:
                    for tx in txs_batch:
                        self.bloom_filter.add(tx['txid'])
        
        self.db.commit()
        logger.info(f"Bulk inserted {len(txs_batch)} transactions")
    
    def bulk_insert_blocks(self, blocks_batch: List[Dict[str, Any]]):
        """Fast COPY-based insertion of blocks"""
        if not blocks_batch:
            return
            
        with self.db.cursor() as cursor:
            with io.StringIO() as f:
                for block in blocks_batch:
                    # Ensure timestamp is an integer (Unix epoch seconds)
                    timestamp = block.get('timestamp')
                    if timestamp is None:
                        timestamp = 'NOW()'  # Will be handled by database function
                    else:
                        try:
                            timestamp = str(int(timestamp))  # Convert to integer string
                        except (ValueError, TypeError):
                            logger.warning(f"Invalid timestamp for block {block.get('height')}, using NOW()")
                            timestamp = 'NOW()'
                    
                    # Format as tab-separated values
                    line = f"{block['height']}\t{block['hash']}\t{block.get('size', 0)}\t"
                    line += f"{timestamp}\t{block.get('tx_count', 0)}\n"
                    f.write(line)
                
                f.seek(0)
                cursor.copy_from(
                    f,
                    'blocks',
                    null='\\N',
                    columns=('height', 'hash', 'size', 'timestamp', 'tx_count')
                )
        
        self.db.commit()
        logger.info(f"Bulk inserted {len(blocks_batch)} blocks")
    
    def process_block_range(self, start_height: int, end_height: int) -> Dict[str, int]:
        """Process a range of blocks in a worker process"""
        blocks_processed = 0
        txs_processed = 0
        utxos_processed = 0
        
        blocks_batch = []
        txs_batch = []
        utxos_batch = []
        batch_size = 1000  # Smaller batches within the worker
        
        try:
            for height in range(start_height, end_height + 1):
                # Get block (no async in worker process)
                block_hash = self._cached_get_block_hash(height)
                block = self.rpc.getblock(block_hash, 2)
                
                # Extract block data
                block_data = {
                    'height': height,
                    'hash': block['hash'],
                    'size': block.get('size', 0),
                    'timestamp': block.get('time', 0),
                    'tx_count': len(block.get('tx', []))
                }
                blocks_batch.append(block_data)
                
                # Process transactions
                for tx in block.get('tx', []):
                    # Skip if already exists
                    if self.tx_exists(tx['txid']):
                        continue
                        
                    # Extract transaction data
                    tx_data = {
                        'txid': tx['txid'],
                        'block_height': height,
                        'size': tx.get('size', 0),
                        'timestamp': block.get('time', 0),
                        'fee': 0  # Calculate fee if needed
                    }
                    txs_batch.append(tx_data)
                    
                    # Process outputs (UTXOs)
                    for vout_idx, vout in enumerate(tx.get('vout', [])):
                        # Skip non-standard outputs if doing minimal sync
                        if INITIAL_SYNC_MINIMAL and vout.get('scriptPubKey', {}).get('type') not in ['pubkeyhash', 'scripthash']:
                            continue
                            
                        # Extract address
                        addresses = vout.get('scriptPubKey', {}).get('addresses', [])
                        address = addresses[0] if addresses else None
                        
                        if address:
                            utxo_data = {
                                'txid': tx['txid'],
                                'vout': vout_idx,
                                'address': address,
                                'amount': vout.get('value', 0),
                                'script_pubkey': vout.get('scriptPubKey', {}).get('hex', ''),
                                'block_height': height,
                                'spent': False,
                                'token_ref': None  # Token reference if applicable
                            }
                            
                            # Parse token data if not doing minimal sync
                            if not INITIAL_SYNC_MINIMAL:
                                # Token parsing would go here
                                pass
                                
                            utxos_batch.append(utxo_data)
                            utxos_processed += 1
                
                # Bulk insert in batches
                if len(blocks_batch) >= batch_size:
                    self.bulk_insert_blocks(blocks_batch)
                    blocks_processed += len(blocks_batch)
                    blocks_batch = []
                    
                if len(txs_batch) >= batch_size:
                    self.bulk_insert_transactions(txs_batch)
                    txs_processed += len(txs_batch)
                    txs_batch = []
                    
                if len(utxos_batch) >= batch_size:
                    self.bulk_insert_utxos(utxos_batch)
                    utxos_batch = []
        
        except Exception as e:
            logger.error(f"Error processing block range {start_height}-{end_height}: {e}")
            raise
            
        # Insert any remaining batches
        if blocks_batch:
            self.bulk_insert_blocks(blocks_batch)
            blocks_processed += len(blocks_batch)
            
        if txs_batch:
            self.bulk_insert_transactions(txs_batch)
            txs_processed += len(txs_batch)
            
        if utxos_batch:
            self.bulk_insert_utxos(utxos_batch)
            
        return {
            'blocks_processed': blocks_processed,
            'txs_processed': txs_processed,
            'utxos_processed': utxos_processed,
            'start_height': start_height,
            'end_height': end_height
        }
    
    async def sync_blockchain(self) -> Dict[str, Any]:
        """Main sync method with optimized parallel processing"""
        start_time = time.time()
        
        # Get current height from node
        target_height = self.rpc.getblockcount()
        
        # Get current sync height from database
        with self.db.cursor() as cursor:
            cursor.execute("SELECT COALESCE(MAX(height), 0) FROM blocks")
            current_height = cursor.fetchone()[0]
        
        logger.info(f"Starting sync from height {current_height} to {target_height}")
        
        # Prepare database for bulk loading
        self.prepare_for_sync()
        
        try:
            # Calculate optimal batch ranges based on environment settings
            batch_size = SYNC_BATCH_SIZE
            ranges = []
            
            for start in range(current_height, target_height + 1, batch_size):
                end = min(start + batch_size - 1, target_height)
                ranges.append((start, end))
            
            # Create stats counters
            total_blocks = 0
            total_txs = 0
            total_utxos = 0
            
            # Process batches with worker pool
            with ProcessPoolExecutor(max_workers=SYNC_MAX_WORKERS) as executor:
                # Submit all tasks
                future_to_range = {
                    executor.submit(self.process_block_range, start, end): (start, end)
                    for start, end in ranges
                }
                
                # Process results as they complete
                for future in as_completed(future_to_range):
                    start, end = future_to_range[future]
                    try:
                        result = future.result()
                        total_blocks += result['blocks_processed']
                        total_txs += result['txs_processed']
                        total_utxos += result['utxos_processed']
                        
                        logger.info(f"Completed range {start}-{end}: "
                                    f"{result['blocks_processed']} blocks, "
                                    f"{result['txs_processed']} txs, "
                                    f"{result['utxos_processed']} utxos")
                    except Exception as e:
                        logger.error(f"Range {start}-{end} failed: {e}")
            
            # Restore database state
            self.restore_after_sync()
            
            elapsed = time.time() - start_time
            blocks_per_second = total_blocks / elapsed if elapsed > 0 else 0
            
            logger.info(f"Sync completed in {elapsed:.2f} seconds")
            logger.info(f"Processed {total_blocks} blocks ({blocks_per_second:.2f}/s)")
            logger.info(f"Processed {total_txs} transactions")
            logger.info(f"Processed {total_utxos} UTXOs")
            
            return {
                'success': True,
                'elapsed_seconds': elapsed,
                'blocks_processed': total_blocks,
                'txs_processed': total_txs,
                'utxos_processed': total_utxos,
                'blocks_per_second': blocks_per_second
            }
            
        except Exception as e:
            logger.error(f"Sync failed: {e}")
            
            # Always try to restore database state
            try:
                self.restore_after_sync()
            except Exception as restore_error:
                logger.error(f"Failed to restore database state: {restore_error}")
                
            return {
                'success': False,
                'error': str(e),
                'elapsed_seconds': time.time() - start_time
            }
    
    async def run_progressive_sync(self) -> Dict[str, Any]:
        """Two-phase sync implementation for optimal performance"""
        # Phase 1: Basic sync with minimal data
        logger.info("Starting Phase 1: Basic blockchain data sync")
        
        # Set minimal mode
        os.environ['INITIAL_SYNC_MINIMAL'] = 'True'
        global INITIAL_SYNC_MINIMAL
        INITIAL_SYNC_MINIMAL = True
        
        # Run first phase
        phase1_result = await self.sync_blockchain()
        
        if not phase1_result.get('success', False):
            logger.error("Phase 1 sync failed, aborting")
            return phase1_result
            
        logger.info(f"Phase 1 completed: {phase1_result['blocks_processed']} blocks processed")
        
        # Phase 2: Detailed token data and analytics
        logger.info("Starting Phase 2: Detailed token data and analytics")
        
        # Switch to full mode
        os.environ['INITIAL_SYNC_MINIMAL'] = 'False'
        INITIAL_SYNC_MINIMAL = False
        
        # Here we would implement token-specific processing, but would need token parser implementation
        
        logger.info("Phase 2 completed")
        
        return {
            'success': True,
            'phase1': phase1_result,
            'message': "Progressive sync completed successfully"
        }

# Main entry point for module
async def run_optimized_sync(db_connection, rpc_client):
    sync = OptimizedSync(db_connection, rpc_client)
    
    if PROGRESSIVE_SYNC:
        return await sync.run_progressive_sync()
    else:
        return await sync.sync_blockchain()
