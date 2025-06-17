#!/usr/bin/env python3
# /Users/radiant/Desktop/RXinDexer/benchmark_sync.py
# This script benchmarks the block synchronization performance of RXinDexer.

import os
import time
import logging
import argparse
from datetime import datetime
from typing import Dict, Tuple, List, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('benchmark.log')
    ]
)
logger = logging.getLogger('benchmark')

def run_benchmark(start_block: int, end_block: int, num_workers: int = 4) -> Dict[str, float]:
    """
    Run a benchmark test for the block synchronization.
    
    Args:
        start_block: Starting block height
        end_block: Ending block height
        num_workers: Number of worker threads to use
        
    Returns:
        Dictionary containing benchmark results
    """
    from src.sync.sync_manager import SyncManager
    from src.models.database import get_db, init_db
    from src.sync.rpc_client import RadiantRPC
    
    # Initialize database
    db = next(get_db())
    init_db()
    
    # Initialize RPC client with optimized settings
    rpc = RadiantRPC()
    
    # Create sync manager
    sync_manager = SyncManager(db=db)
    
    # Set environment variables for this benchmark
    os.environ['SYNC_WORKERS'] = str(num_workers)
    os.environ['RPC_POOL_SIZE'] = str(num_workers * 2)
    os.environ['SYNC_MAX_WORKERS'] = str(num_workers * 2)
    
    logger.info(f"Starting benchmark from block {start_block} to {end_block} with {num_workers} workers")
    
    # Run the benchmark
    start_time = time.time()
    
    try:
        # Process blocks in batches
        processed, failed = sync_manager.process_blocks_batch(start_block, end_block)
        
        # Calculate metrics
        total_time = time.time() - start_time
        blocks_per_second = (end_block - start_block + 1) / total_time if total_time > 0 else 0
        
        # Log results
        logger.info(f"Benchmark completed in {total_time:.2f} seconds")
        logger.info(f"Processed {processed} blocks with {len(failed)} failures")
        logger.info(f"Average speed: {blocks_per_second:.2f} blocks/second")
        
        return {
            'start_block': start_block,
            'end_block': end_block,
            'total_blocks': end_block - start_block + 1,
            'processed_blocks': processed,
            'failed_blocks': len(failed),
            'total_time_seconds': total_time,
            'blocks_per_second': blocks_per_second,
            'num_workers': num_workers,
            'timestamp': datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Benchmark failed: {str(e)}", exc_info=True)
        raise

def main():
    parser = argparse.ArgumentParser(description='Benchmark RXinDexer block synchronization')
    parser.add_argument('start_block', type=int, help='Starting block height')
    parser.add_argument('end_block', type=int, help='Ending block height')
    parser.add_argument('--workers', type=int, default=4, help='Number of worker threads')
    parser.add_argument('--runs', type=int, default=1, help='Number of benchmark runs')
    
    args = parser.parse_args()
    
    logger.info(f"Starting benchmark with {args.runs} runs, {args.workers} workers")
    logger.info(f"Block range: {args.start_block} to {args.end_block}")
    
    results = []
    
    for run in range(1, args.runs + 1):
        logger.info(f"\n=== Run {run}/{args.runs} ===")
        result = run_benchmark(args.start_block, args.end_block, args.workers)
        results.append(result)
    
    # Print summary
    logger.info("\n=== Benchmark Summary ===")
    for i, result in enumerate(results, 1):
        logger.info(
            f"Run {i}: {result['blocks_per_second']:.2f} blocks/sec "
            f"({result['total_blocks']} blocks in {result['total_time_seconds']:.2f}s)"
        )
    
    if len(results) > 1:
        avg_bps = sum(r['blocks_per_second'] for r in results) / len(results)
        logger.info(f"\nAverage performance: {avg_bps:.2f} blocks/second")

if __name__ == "__main__":
    main()
