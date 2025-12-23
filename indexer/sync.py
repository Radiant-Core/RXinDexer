# Blockchain synchronization logic
import os
import requests
import time
import datetime
from database.models import Block
from sqlalchemy.orm import Session
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter

RADIANT_NODE_HOST = os.getenv("RADIANT_NODE_HOST", "radiant-node")
RADIANT_NODE_RPCPORT = int(os.getenv("RADIANT_NODE_RPCPORT", 7332))
RADIANT_NODE_RPCUSER = os.getenv("RADIANT_NODE_RPCUSER", "dockeruser")
RADIANT_NODE_RPCPASSWORD = os.getenv("RADIANT_NODE_RPCPASSWORD", "dockerpass")

# RPC Configuration
# NOTE: requests' `timeout` can be a tuple (connect_timeout, read_timeout).
# This prevents hanging forever on a stuck connect while still allowing long reads for big blocks.
RPC_TIMEOUT = int(os.getenv("RPC_READ_TIMEOUT", "120"))
RPC_CONNECT_TIMEOUT = float(os.getenv("RPC_CONNECT_TIMEOUT", "5"))
RPC_MAX_RETRIES = 5
RPC_RETRY_BACKOFF = 2  # Exponential backoff multiplier

# Global session with retry logic and connection pooling
session = requests.Session()
session.auth = (RADIANT_NODE_RPCUSER, RADIANT_NODE_RPCPASSWORD)
session.headers.update({"content-type": "application/json"})

# Configure connection pooling (retries handled by rpc_call loop)
adapter = HTTPAdapter(max_retries=0, pool_connections=10, pool_maxsize=10)
session.mount("http://", adapter)
session.mount("https://", adapter)

def _rpc_timeout(timeout):
    """Normalize timeout into (connect_timeout, read_timeout) for requests."""
    if isinstance(timeout, (tuple, list)) and len(timeout) == 2:
        return (float(timeout[0]), float(timeout[1]))
    if timeout is None:
        read_timeout = float(RPC_TIMEOUT)
    else:
        read_timeout = float(timeout)
    return (float(RPC_CONNECT_TIMEOUT), read_timeout)

def rpc_call(method, params=None, timeout=None):
    """Make RPC call with retry logic and configurable timeout."""
    url = f"http://{RADIANT_NODE_HOST}:{RADIANT_NODE_RPCPORT}"
    payload = {
        "method": method,
        "params": params or [],
        "id": 1,
        "jsonrpc": "2.0"
    }
    effective_timeout = _rpc_timeout(timeout)
    last_error = None
    
    for attempt in range(RPC_MAX_RETRIES):
        try:
            response = session.post(url, json=payload, timeout=effective_timeout)
            response.raise_for_status()
            result = response.json()
            if 'error' in result and result['error']:
                raise Exception(result['error'])
            return result['result']
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_error = e
            wait_time = RPC_RETRY_BACKOFF ** attempt
            if attempt < RPC_MAX_RETRIES - 1:
                print(f"[rpc_call] {method} attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            continue
        except Exception as e:
            # Non-retryable error
            raise e
    
    raise last_error or Exception(f"RPC call {method} failed after {RPC_MAX_RETRIES} attempts")

def get_last_synced_height(db: Session):
    last_block = db.query(Block).order_by(Block.height.desc()).first()
    return last_block.height if last_block else 0

def fetch_block_data(height, max_retries=3):
    """Fetch block hash and then block data for a given height with retry logic."""
    last_error = None
    
    for attempt in range(max_retries):
        try:
            block_hash = rpc_call("getblockhash", [height], timeout=30)
            # Use longer timeout for getblock as blocks can be very large
            block = rpc_call("getblock", [block_hash, 2], timeout=RPC_TIMEOUT)
            return block
        except Exception as e:
            last_error = e
            wait_time = RPC_RETRY_BACKOFF ** attempt
            if attempt < max_retries - 1:
                print(f"[fetch_block_data] Block {height} attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            continue
    
    print(f"[fetch_block_data] Error fetching block {height} after {max_retries} attempts: {last_error}")
    raise last_error

import time as _prof_time

def sync_blocks(db: Session, parse_tx_callback=None, batch_size=None):
    """Full sync loop: fetch and store all blocks from last synced height using batch/bulk inserts. Adaptive batching for performance."""
    latest_height = rpc_call("getblockcount")
    db_tip = get_last_synced_height(db)
    start_height = db_tip + 1
    
    # Node restart detection: if DB is ahead of node, the node likely restarted
    if start_height > latest_height:
        node_regression = start_height - 1 - latest_height
        if node_regression > 100:  # Significant regression suggests node restart
            print(f"[RESTART_DETECTED] Node appears to have restarted. DB tip: {db_tip}, Node tip: {latest_height}, Regression: {node_regression} blocks")
            print(f"[RESTART_DETECTED] Waiting for node to catch up before resuming sync...")
        else:
            print(f"No new blocks to sync. Waiting for new blocks... (DB tip: {db_tip}, node tip: {latest_height})")
        sys.stdout.flush()
        return
    
    # Safety check: ensure we don't request blocks beyond current node capacity
    if start_height > latest_height:
        print(f"[SYNC_BOUNDARY] Start height {start_height} exceeds node tip {latest_height}. Waiting...")
        sys.stdout.flush()
        return
    
    # Calculate sync lag and adaptive batch size
    sync_lag = latest_height - start_height + 1
    if not batch_size:
        # ROBUST MODE: Reduced batch sizes for stability with large blocks
        if sync_lag > 50000:  
            batch_size = 100  # Reduced from 500 for stability
        elif sync_lag > 10000:  
            batch_size = 50   # Reduced from 100
        elif sync_lag > 1000:  
            batch_size = 20   # Reduced from 50
        else:  
            batch_size = 10   # Standard crawl
    
    is_catchup_mode = sync_lag > 1000
    
    # Update parser with current sync lag for spent check decisions
    from indexer.parser import set_current_sync_lag
    set_current_sync_lag(sync_lag)
    
    print(f"Starting sync from block {start_height} to {latest_height} (lag: {sync_lag}, batch_size: {batch_size}, catchup_mode: {is_catchup_mode})"); sys.stdout.flush()
    
    # Batch processing loop
    for batch_start_height in range(start_height, latest_height + 1, batch_size):
        batch_end_height = min(batch_start_height + batch_size - 1, latest_height)
        current_batch_size = batch_end_height - batch_start_height + 1
        
        blocks_to_insert = []
        txs_to_parse = []
        block_ids = []
        rpc_time = 0
        insert_time = 0
        parse_time = 0
        commit_time = 0
        batch_start_time = _prof_time.time()
        
        try:
            # Check node tip periodically
            if (batch_start_height - start_height) % (batch_size * 10) == 0:
                 current_node_tip = rpc_call("getblockcount")
                 latest_height = current_node_tip # Update latest height
            
            t0 = _prof_time.time()
            
            # Parallel Block Fetching with reduced concurrency for stability
            # Using only 2 workers to prevent overwhelming the node with large blocks
            fetched_blocks_map = {}
            fetch_errors = []
            max_workers = 2  # Reduced from 4 for stability with large blocks
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_height = {executor.submit(fetch_block_data, h): h for h in range(batch_start_height, batch_end_height + 1)}
                
                for future in as_completed(future_to_height):
                    h = future_to_height[future]
                    try:
                        data = future.result()
                        fetched_blocks_map[h] = data
                    except Exception as exc:
                        print(f"[sync_blocks][ERROR] Block {h} generated an exception: {exc}")
                        fetch_errors.append(h)
            
            # If some blocks failed, try to fetch them individually with longer timeout
            if fetch_errors:
                print(f"[sync_blocks] Retrying {len(fetch_errors)} failed blocks individually...")
                for h in fetch_errors[:]:
                    try:
                        time.sleep(2)  # Wait before retry
                        data = fetch_block_data(h, max_retries=5)
                        fetched_blocks_map[h] = data
                        fetch_errors.remove(h)
                        print(f"[sync_blocks] Successfully recovered block {h}")
                    except Exception as exc:
                        print(f"[sync_blocks][FATAL] Block {h} unrecoverable: {exc}")
            
            if fetch_errors:
                raise Exception(f"Failed to fetch blocks after retries: {fetch_errors}")
            
            # Reassemble in order
            fetched_blocks = [fetched_blocks_map[h] for h in range(batch_start_height, batch_end_height + 1) if h in fetched_blocks_map]

            fetch_duration = _prof_time.time() - t0
            rpc_time += fetch_duration
            if fetch_duration > 5.0:
                print(f"[sync_blocks][WARNING] Slow block fetch: {current_batch_size} blocks took {fetch_duration:.2f}s"); sys.stdout.flush()
            
            # Process fetched blocks
            for block in fetched_blocks:
                block_time = datetime.datetime.utcfromtimestamp(block['time'])
                new_block = Block(hash=block['hash'], height=block['height'], timestamp=block_time)
                blocks_to_insert.append(new_block)
                txs_to_parse.append((block['tx'], block_time))

            if not blocks_to_insert:
                continue

            # Bulk Insert
            try:
                # Partition management
                from database.partition_manager import PartitionManager
                min_height = min(b.height for b in blocks_to_insert)
                max_height = max(b.height for b in blocks_to_insert)
                partition_manager = PartitionManager(db)
                partition_manager.ensure_partitions_covering_range(min_height, max_height)
                
                # Proactive partitions
                milestone_crossed = False
                last_milestone = (min_height // 1000) * 1000
                next_milestone = last_milestone + 1000
                if min_height < next_milestone <= max_height or max_height == latest_height:
                    milestone_crossed = True
                    partition_manager.create_partitions_ahead(max_height, look_ahead_partitions=3)
                    
                t1 = _prof_time.time()
                db.bulk_save_objects(blocks_to_insert)
                db.flush()
                insert_time += _prof_time.time() - t1
                
                t2 = _prof_time.time()
                db.commit()
                commit_time += _prof_time.time() - t2
                
                print(f"[sync_blocks][BULK] Inserted {len(blocks_to_insert)} blocks (heights {blocks_to_insert[0].height}-{blocks_to_insert[-1].height})"); sys.stdout.flush()
                
                # Fetch IDs
                hashes = [b.hash for b in blocks_to_insert]
                block_rows = db.query(Block.hash, Block.id).filter(Block.hash.in_(hashes)).all()
                hash_to_id = {h: id for h, id in block_rows}
                
                block_ids = [hash_to_id.get(b.hash) for b in blocks_to_insert]
                
                # Parse Transactions - batch all blocks together for efficiency
                if parse_tx_callback:
                    t3 = _prof_time.time()
                    # Collect all transactions with their metadata
                    all_txs = []
                    for (txs, block_time), block_id, block_obj in zip(txs_to_parse, block_ids, blocks_to_insert):
                        if block_id:
                            for tx in txs:
                                tx['_block_id'] = block_id
                                tx['_block_time'] = block_time
                                tx['_block_height'] = block_obj.height
                            all_txs.extend(txs)
                    
                    # Parse all transactions in one call
                    if all_txs:
                        # Use first block's metadata as default (will be overridden per-tx)
                        first_block_id = block_ids[0] if block_ids else None
                        first_block_time = txs_to_parse[0][1] if txs_to_parse else None
                        first_block_height = blocks_to_insert[0].height if blocks_to_insert else None
                        parse_tx_callback(all_txs, db, block_id=first_block_id, block_time=first_block_time, block_height=first_block_height)
                    parse_time += _prof_time.time() - t3

                # Stats
                batch_duration = _prof_time.time() - batch_start_time
                if not is_catchup_mode or batch_duration > 10.0:
                     print(f"[sync_blocks][PROFILE] Batch RPC: {rpc_time:.2f}s, Insert: {insert_time:.2f}s, Commit: {commit_time:.2f}s, Parse: {parse_time:.2f}s, Total: {batch_duration:.2f}s")
                
                if is_catchup_mode:
                    remaining = latest_height - batch_end_height
                    print(f"[CATCHUP] Height {batch_end_height}/{latest_height} ({remaining} remaining) - Batch: {batch_duration:.1f}s")
                sys.stdout.flush()

                # THROTTLING: Prevent resource starvation
                # SAFE MODE: Aggressive sleeping to prevent I/O freeze
                # If batch took > 5s, sleep 2s to let DB flush WAL/Checkpoints.
                if batch_duration > 5.0:
                    time.sleep(2.0)
                elif batch_duration > 1.0:
                    time.sleep(0.5)
                else:
                    time.sleep(0.1)
                
            except Exception as e:
                import traceback
                print(f"[PROFILE][EXCEPTION] {e}"); traceback.print_exc(); sys.stdout.flush()
                db.rollback()
                break # Stop sync on DB error to prevent gaps
                
        except Exception as e:
            print(f"Error syncing batch {batch_start_height}-{batch_end_height}: {e}"); sys.stdout.flush()
            break

    print("Sync complete."); sys.stdout.flush()

if __name__ == "__main__":
    from database.session import get_session
    from indexer.parser import parse_transactions
    with get_session() as db:
        sync_blocks(db, parse_tx_callback=parse_transactions)
