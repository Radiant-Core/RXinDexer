# /Users/radiant/Desktop/RXinDexer/src/sync/parallel_processor.py
# This file implements parallel processing for blockchain synchronization.
# It enables concurrent block fetching and processing to significantly accelerate sync speed.

import logging
import time
from typing import List, Dict, Any, Callable, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

class ParallelBlockProcessor:
    """
    Handles parallel processing of blockchain blocks to accelerate synchronization.
    Uses thread pools to fetch and parse blocks concurrently while maintaining order.
    """
    
    def __init__(self, max_workers: int = 1):
        """
        Initialize the parallel block processor.
        
        Args:
            max_workers: Maximum number of worker threads to use for parallel processing
        """
        self.max_workers = max_workers
    
    def process_blocks_parallel(self, 
                               block_range: range,
                               fetch_block_func: Callable[[int], Tuple[str, Dict[str, Any]]],
                               process_block_func: Callable[[Dict[str, Any], int, str], bool],
                               max_concurrent_tasks: int = 8,
                               checkpoint_interval: int = 50) -> Tuple[int, List[int]]:
        """
        Process a range of blocks in parallel while maintaining order integrity.
        
        Args:
            block_range: Range of block heights to process
            fetch_block_func: Function to fetch a block given its height
            process_block_func: Function to process a block given block data, height, and hash
            max_concurrent_tasks: Maximum number of concurrent tasks (default: 8)
            checkpoint_interval: How often to create processing checkpoints (default: 50)
            
        Returns:
            Tuple containing: (number of blocks processed, list of failed block heights)
        """
        blocks_processed = 0
        failed_blocks = []
        
        # Use environment variable or default to number of CPUs - 1 (minimum 1)
        import os
        default_workers = max(1, (os.cpu_count() or 2) - 1)
        actual_workers = min(max_concurrent_tasks, int(os.environ.get("SYNC_WORKERS", default_workers)))
        
        logger.info(f"Starting parallel block processing with {actual_workers} workers")
        
        # Process blocks in smaller chunks to maintain checkpoints and control memory usage
        block_list = list(block_range)
        chunk_size = min(len(block_list), checkpoint_interval, 1000)  # Cap chunk size at 1000 blocks
        
        for chunk_start in range(0, len(block_list), chunk_size):
            chunk_end = min(chunk_start + chunk_size, len(block_list))
            current_chunk = block_list[chunk_start:chunk_end]
            
            # Skip empty chunks
            if not current_chunk:
                continue
                
            chunk_start_height = current_chunk[0]
            chunk_end_height = current_chunk[-1]
            logger.info(f"Processing block chunk {chunk_start_height} to {chunk_end_height}")
            
            # Create a dictionary to store fetched blocks by height
            fetched_blocks = {}
            fetch_errors = {}
            
            # Process blocks in batches to balance parallelism and memory usage
            batch_size = max(1, actual_workers * 2)  # Process 2x worker count per batch
            
            for batch_start in range(0, len(current_chunk), batch_size):
                batch = current_chunk[batch_start:batch_start + batch_size]
                batch_results = {}
                
                # Fetch and process blocks in parallel
                with ThreadPoolExecutor(max_workers=actual_workers) as executor:
                    # Submit all fetch and process tasks
                    future_to_height = {}
                    for height in batch:
                        future = executor.submit(self._fetch_and_process_block, 
                                              fetch_block_func, process_block_func, height)
                        future_to_height[future] = height
                    
                    # Process results as they complete
                    for future in as_completed(future_to_height):
                        height = future_to_height[future]
                        try:
                            success, result = future.result()
                            if success:
                                block_hash, block_data = result
                                batch_results[height] = (block_hash, block_data, True)
                            else:
                                batch_results[height] = (None, str(result), False)
                                logger.warning(f"Failed to process block {height}: {result}")
                        except Exception as e:
                            error_msg = str(e)
                            batch_results[height] = (None, error_msg, False)
                            logger.error(f"Unexpected error processing block {height}: {error_msg}")
                
                # Update tracking based on batch results
                for height in batch:
                    if height not in batch_results:
                        failed_blocks.append(height)
                        continue
                        
                    block_hash, result, success = batch_results[height]
                    if success:
                        blocks_processed += 1
                        if blocks_processed % 10 == 0:  # Log progress every 10 blocks
                            logger.info(f"Processed {blocks_processed} blocks (at height {height})")
                    else:
                        failed_blocks.append(height)
                        logger.warning(f"Failed to process block {height}: {result}")
                
                # Small sleep to prevent overwhelming the system
                time.sleep(0.1)
                
                # Check for early termination
                if os.environ.get("STOP_SYNC"):
                    logger.warning("Received stop signal, terminating early")
                    return blocks_processed, failed_blocks
                
            # Log progress after each chunk
            logger.info(f"Processed chunk {chunk_start_height}-{chunk_end_height}: {blocks_processed} blocks complete, {len(failed_blocks)} failed")
        
        return blocks_processed, failed_blocks
    
    def _fetch_and_process_block(self, 
                                fetch_block_func: Callable[[int], Tuple[str, Dict[str, Any]]],
                                process_block_func: Callable[[Dict[str, Any], int, str], bool],
                                height: int) -> Tuple[bool, Any]:
        """
        Fetch and process a single block with error handling.
        
        Args:
            fetch_block_func: Function to fetch block data
            process_block_func: Function to process block data
            height: Block height to process
            
        Returns:
            Tuple of (success, result) where result is either (block_hash, block_data) on success
            or an error message on failure
        """
        try:
            # Fetch the block
            block_hash, block_data = fetch_block_func(height)
            
            # Process the block
            success = process_block_func(block_data, height, block_hash)
            if not success:
                return False, f"Block processing failed for height {height}"
                
            return True, (block_hash, block_data)
            
        except Exception as e:
            return False, str(e)
