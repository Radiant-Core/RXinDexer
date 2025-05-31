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
    
    def __init__(self, max_workers: int = 8):
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
            max_concurrent_tasks: Maximum number of concurrent tasks
            checkpoint_interval: How often to create processing checkpoints
            
        Returns:
            Tuple containing: (number of blocks processed, list of failed block heights)
        """
        blocks_processed = 0
        failed_blocks = []
        
        # Use a smaller number of workers than requested to avoid overwhelming RPC
        actual_workers = min(self.max_workers, max_concurrent_tasks)
        logger.info(f"Starting parallel block processing with {actual_workers} workers")
        
        # Process blocks in smaller chunks to maintain checkpoints
        block_list = list(block_range)
        chunk_size = min(len(block_list), checkpoint_interval)
        
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
            
            # First, fetch blocks in parallel
            with ThreadPoolExecutor(max_workers=actual_workers) as executor:
                # Submit all fetch tasks
                future_to_height = {
                    executor.submit(fetch_block_func, height): height
                    for height in current_chunk
                }
                
                # Process results as they complete
                for future in as_completed(future_to_height):
                    height = future_to_height[future]
                    try:
                        block_hash, block_data = future.result()
                        fetched_blocks[height] = (block_hash, block_data)
                    except Exception as e:
                        logger.warning(f"Failed to fetch block at height {height}: {str(e)}")
                        fetch_errors[height] = str(e)
            
            # Now process the fetched blocks in order
            for height in current_chunk:
                if height in fetch_errors:
                    failed_blocks.append(height)
                    continue
                    
                if height not in fetched_blocks:
                    logger.warning(f"Block at height {height} was not fetched, skipping")
                    failed_blocks.append(height)
                    continue
                
                block_hash, block_data = fetched_blocks[height]
                try:
                    success = process_block_func(block_data, height, block_hash)
                    if success:
                        blocks_processed += 1
                    else:
                        failed_blocks.append(height)
                except Exception as e:
                    logger.error(f"Error processing block {height}: {str(e)}")
                    failed_blocks.append(height)
            
            # Log progress after each chunk
            logger.info(f"Processed chunk {chunk_start_height}-{chunk_end_height}: {blocks_processed} blocks complete, {len(failed_blocks)} failed")
        
        return blocks_processed, failed_blocks
