# /Users/radiant/Desktop/RXinDexer/docker_optimized_sync.py
# This is a Docker-compatible version of the optimized sync adapter
# Modified to avoid multiprocessing issues in containerized environments

import os
import sys
import time
import logging
import psycopg2
import json
from typing import List, Dict, Tuple, Optional, Any
import redis
import io
import requests
from decimal import Decimal
import cbor2
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Configuration from environment variables with sensible defaults
SYNC_BATCH_SIZE = int(os.getenv('SYNC_BATCH_SIZE', 100))
SYNC_MAX_WORKERS = int(os.getenv('SYNC_MAX_WORKERS', 4))
UTXO_MAX_WORKERS = int(os.getenv('UTXO_MAX_WORKERS', 2))
BLOCK_PARALLEL_THRESHOLD = int(os.getenv('BLOCK_PARALLEL_THRESHOLD', 20))
PROGRESSIVE_SYNC = os.getenv('PROGRESSIVE_SYNC', 'True').lower() == 'true'
INITIAL_SYNC_MINIMAL = os.getenv('INITIAL_SYNC_MINIMAL', 'True').lower() == 'true'
USE_REDIS_CACHE = os.getenv('USE_REDIS_CACHE', 'True').lower() == 'true'
REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0')
RPC_RATE_LIMIT = int(os.getenv('RPC_RATE_LIMIT', 10))

# Database connection parameters
DB_HOST = os.getenv('DB_HOST', 'db')
DB_PORT = int(os.getenv('DB_PORT', 5432))
DB_NAME = os.getenv('DB_NAME', 'rxindexer')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'postgres')

# Alternative database connection string
DATABASE_URL = os.getenv('DATABASE_URL', f'postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}')

# RPC connection parameters
RPC_URL = os.getenv('RADIANT_RPC_URL', 'http://radiant:7332')
RPC_USER = os.getenv('RADIANT_RPC_USER', 'rxin')
RPC_PASSWORD = os.getenv('RADIANT_RPC_PASSWORD', 'securepassword')

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

def get_db_connection():
    """Get a PostgreSQL database connection"""
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        conn.autocommit = False
        logger.info(f"Connected to database {DB_NAME} on {DB_HOST}")
        return conn
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        raise

def get_rpc_client():
    """Get a Radiant RPC client using requests"""
    try:
        # Simple test to check connection
        response = requests.post(
            RPC_URL,
            auth=(RPC_USER, RPC_PASSWORD),
            json={"jsonrpc": "1.0", "id": "test", "method": "getblockchaininfo", "params": []}
        )
        response.raise_for_status()
        data = response.json()
        if 'error' in data and data['error'] is not None:
            raise Exception(f"RPC error: {data['error']}")
        
        # Get current chain height
        chain_height = data['result']['blocks']
        logger.info(f"Connected to Radiant node, chain height: {chain_height}")
        return chain_height
    except Exception as e:
        logger.error(f"RPC connection error: {e}")
        raise

def rpc_call(method, params=None, retries=5, retry_delay=1):
    """Make an RPC call with retry logic and rate limiting"""
    if params is None:
        params = []
    
    for attempt in range(1, retries + 1):
        try:
            response = requests.post(
                RPC_URL,
                auth=(RPC_USER, RPC_PASSWORD),
                json={"jsonrpc": "1.0", "id": method, "method": method, "params": params}
            )
            response.raise_for_status()
            data = response.json()
            
            if 'error' in data and data['error'] is not None:
                raise Exception(f"RPC error: {data['error']}")
            
            return data['result']
        except Exception as e:
            if attempt < retries:
                logger.warning(f"RPC call {method} failed (attempt {attempt}/{retries}): {e}")
                time.sleep(retry_delay)
            else:
                logger.error(f"RPC call {method} failed after {retries} attempts: {e}")
                raise
        
        # Rate limiting
        time.sleep(1.0 / RPC_RATE_LIMIT)

def get_block_with_cache(height):
    """Get block data with caching"""
    cache_key = f"block:{height}"
    
    # Try to get from cache first
    if redis_client:
        cached_data = redis_client.get(cache_key)
        if cached_data:
            try:
                return cbor2.loads(cached_data)
            except Exception as e:
                logger.warning(f"Error deserializing cached block {height}: {e}")
    
    # Not in cache, get from RPC
    block_hash = rpc_call("getblockhash", [height])
    block_data = rpc_call("getblock", [block_hash, 2])
    
    # Cache the result
    if redis_client:
        try:
            redis_client.setex(cache_key, 3600, cbor2.dumps(block_data))  # Cache for 1 hour
        except Exception as e:
            logger.warning(f"Error caching block {height}: {e}")
    
    return block_data

def process_block(height):
    """Process a single block"""
    try:
        block_data = get_block_with_cache(height)
        
        # Extract block info
        block_info = {
            'height': height,
            'hash': block_data['hash'],
            'time': block_data['time'],
            'nonce': block_data['nonce'],
            'size': block_data['size'],
            'tx_count': len(block_data['tx']),
            'processed_at': datetime.now().timestamp()
        }
        
        # Extract transactions
        transactions = []
        utxos = []
        
        for tx in block_data['tx']:
            tx_info = {
                'txid': tx['txid'],
                'block_height': height,
                'block_hash': block_data['hash'],
                'block_time': block_data['time'],
                'size': tx.get('size', 0),
                'fee': tx.get('fee', 0),
                'is_coinbase': len(tx['vin']) > 0 and 'coinbase' in tx['vin'][0]
            }
            transactions.append(tx_info)
            
            # Process outputs (UTXOs)
            for vout_idx, vout in enumerate(tx['vout']):
                if 'scriptPubKey' in vout and 'addresses' in vout['scriptPubKey']:
                    for address in vout['scriptPubKey']['addresses']:
                        utxo = {
                            'txid': tx['txid'],
                            'vout': vout_idx,
                            'address': address,
                            'value': float(vout['value']),
                            'block_height': height,
                            'block_hash': block_data['hash'],
                            'spent': False,
                            'spent_txid': None,
                            'spent_at': None
                        }
                        utxos.append(utxo)
            
            # Process inputs (mark UTXOs as spent)
            for vin in tx['vin']:
                if 'txid' in vin:  # Not coinbase
                    utxo_spent = {
                        'prev_txid': vin['txid'],
                        'prev_vout': vin['vout'],
                        'spent_txid': tx['txid'],
                        'spent_at': block_data['time']
                    }
        
        return {
            'block': block_info,
            'transactions': transactions,
            'utxos': utxos
        }
    except Exception as e:
        logger.error(f"Error processing block {height}: {e}")
        return None

def process_block_range(start_height, end_height):
    """Process a range of blocks using thread pool"""
    logger.info(f"Processing blocks {start_height} to {end_height}")
    results = []
    
    with ThreadPoolExecutor(max_workers=SYNC_MAX_WORKERS) as executor:
        future_to_height = {executor.submit(process_block, height): height for height in range(start_height, end_height + 1)}
        for future in as_completed(future_to_height):
            height = future_to_height[future]
            try:
                result = future.result()
                if result:
                    results.append(result)
            except Exception as e:
                logger.error(f"Block processing failed for height {height}: {e}")
    
    return results

def save_batch_to_db(batch_results, conn):
    """Save a batch of processed blocks to the database"""
    try:
        cursor = conn.cursor()
        
        # Collect all data
        blocks = []
        transactions = []
        utxos = []
        spent_utxos = []
        
        for result in batch_results:
            if not result:
                continue
            blocks.append(result['block'])
            transactions.extend(result['transactions'])
            utxos.extend(result['utxos'])
        
        # Insert blocks
        if blocks:
            blocks_sql = """
                INSERT INTO blocks (height, hash, timestamp, nonce, size, tx_count, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (height) DO NOTHING
            """
            blocks_data = [(b['height'], b['hash'], b['time'], b['nonce'], b['size'], b['tx_count'], b['processed_at']) for b in blocks]
            cursor.executemany(blocks_sql, blocks_data)
        
        # Insert transactions
        if transactions:
            txs_sql = """
                INSERT INTO transactions (txid, block_height, block_hash, block_time, size, fee, is_coinbase, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (txid) DO NOTHING
            """
            txs_data = [(t['txid'], t['block_height'], t['block_hash'], t['block_time'], t['size'], t['fee'], t['is_coinbase']) for t in transactions]
            cursor.executemany(txs_sql, txs_data)
        
        # Insert UTXOs
        if utxos:
            utxos_sql = """
                INSERT INTO utxos (txid, vout, address, value, block_height, block_hash, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (txid, vout) DO NOTHING
            """
            utxos_data = [(u['txid'], u['vout'], u['address'], u['value'], u['block_height'], u['block_hash']) for u in utxos]
            cursor.executemany(utxos_sql, utxos_data)
        
        # Mark UTXOs as spent
        if spent_utxos:
            spent_sql = """
                UPDATE utxos SET spent = TRUE, spent_txid = %s, spent_at = NOW()
                WHERE txid = %s AND vout = %s AND spent = FALSE
            """
            spent_data = [(s['spent_txid'], s['prev_txid'], s['prev_vout']) for s in spent_utxos]
            cursor.executemany(spent_sql, spent_data)
        
        conn.commit()
        logger.info(f"Saved batch: {len(blocks)} blocks, {len(transactions)} transactions, {len(utxos)} UTXOs")
        return True
    except Exception as e:
        logger.error(f"Error saving batch to database: {e}")
        conn.rollback()
        return False

def sync_blockchain():
    """Main function to sync blockchain data"""
    try:
        # Get database connection
        conn = get_db_connection()
        
        # Get current chain height from RPC
        chain_height = get_rpc_client()
        
        # Get current indexed height from database
        cursor = conn.cursor()
        cursor.execute("SELECT COALESCE(MAX(height), 0) FROM blocks")
        db_height = cursor.fetchone()[0]
        
        # Calculate total blocks to sync
        total_to_sync = chain_height - db_height
        if total_to_sync <= 0:
            logger.info(f"Already synced to height {db_height}, nothing to do")
            return
        
        logger.info(f"Starting sync from height {db_height+1} to {chain_height} ({total_to_sync} blocks)")
        
        # Sync in batches
        start_time = time.time()
        blocks_processed = 0
        
        for batch_start in range(db_height + 1, chain_height + 1, SYNC_BATCH_SIZE):
            batch_end = min(batch_start + SYNC_BATCH_SIZE - 1, chain_height)
            
            # Process this batch
            batch_results = process_block_range(batch_start, batch_end)
            
            # Save results to database
            if save_batch_to_db(batch_results, conn):
                blocks_processed += (batch_end - batch_start + 1)
                logger.info(f"Processed {blocks_processed}/{total_to_sync} blocks")
            
            # Update sync state in database
            cursor.execute("""
                INSERT INTO sync_state (current_height, is_syncing, last_updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    current_height = EXCLUDED.current_height,
                    is_syncing = EXCLUDED.is_syncing,
                    last_updated_at = EXCLUDED.last_updated_at
            """, (batch_end, True, time.time()))
            conn.commit()
        
        # Finalize sync
        cursor.execute("""
            UPDATE sync_state SET
                current_height = %s,
                is_syncing = FALSE,
                last_updated_at = %s
            WHERE id = 1
        """, (chain_height, time.time()))
        conn.commit()
        
        elapsed = time.time() - start_time
        logger.info(f"Sync completed: {blocks_processed} blocks in {elapsed:.2f} seconds ({blocks_processed/elapsed:.2f} blocks/sec)")
        
        return {
            'success': True,
            'blocks_processed': blocks_processed,
            'elapsed_seconds': elapsed,
            'blocks_per_second': blocks_processed / elapsed if elapsed > 0 else 0
        }
    except Exception as e:
        logger.error(f"Sync failed: {e}")
        return {
            'success': False,
            'error': str(e)
        }

def continuous_sync(interval=60):
    """Run sync continuously with specified interval"""
    while True:
        try:
            result = sync_blockchain()
            if not result or not result.get('success', False):
                logger.error(f"Sync failed: {result.get('error', 'unknown error')}")
            
            logger.info(f"Sleeping for {interval} seconds before next sync")
            time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Sync interrupted by user, exiting")
            break
        except Exception as e:
            logger.error(f"Error in continuous sync: {e}")
            logger.info(f"Sleeping for {interval} seconds before retry")
            time.sleep(interval)

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Docker-compatible optimized blockchain sync")
    parser.add_argument("--continuous", action="store_true", help="Run sync continuously")
    parser.add_argument("--interval", type=int, default=60, help="Interval between syncs in continuous mode (seconds)")
    
    args = parser.parse_args()
    
    if args.continuous:
        logger.info(f"Starting continuous sync with interval {args.interval} seconds")
        continuous_sync(args.interval)
    else:
        sync_blockchain()
