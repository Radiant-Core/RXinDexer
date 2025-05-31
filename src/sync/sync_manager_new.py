# /Users/radiant/Desktop/RXinDexer/src/sync/sync_manager_new.py
# This file implements the blockchain synchronization manager.
# It handles fetching blocks, detecting reorgs, and updating the database with resilient transaction handling.

import os
import time
import logging
import contextlib
from typing import Dict, List, Any, Optional, Tuple
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from sqlalchemy import text, create_engine
from sqlalchemy.exc import SQLAlchemyError, PendingRollbackError

from src.models import SyncState, UTXO, GlyphToken, Holder
from src.models.database import get_db, engine
from .rpc_client import RadiantRPC
from ..parser.block_parser import BlockParser
from .checkpoint_manager import CheckpointManager
from .parallel_processor import ParallelBlockProcessor

# Load environment variables
load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)

@contextlib.contextmanager
def safe_db_transaction(session):
    """
    Context manager for safely handling database transactions with proper rollback.
    
    This version handles PendingRollbackError and other SQLAlchemy exceptions
    that may occur during transaction processing.
    
    Args:
        session: SQLAlchemy session to use for the transaction
        
    Yields:
        The session for use within the with block
        
    Raises:
        Exception: Re-raises any exceptions after rollback
    """
    try:
        yield session
        session.commit()
    except Exception as e:
        logger.warning(f"Transaction failed, rolling back: {str(e)}")
        session.rollback()
        raise


class SyncManager:
    """
    Manages blockchain synchronization process.
    Responsible for fetching blocks, handling reorgs, and updating the database.
    Includes parallel processing and checkpoint recovery for optimized performance.
    """
    
    def __init__(self, db: Session = None):
        """
        Initialize the sync manager with robust error handling.
        
        Args:
            db: Database session (optional, will create one if not provided)
        """
        # Get database session if not provided
        if db is None:
            self.db = next(get_db())
        else:
            self.db = db
            
        # Create RPC client for blockchain communication
        self.rpc = RadiantRPC()
        
        # Ensure database tables exist
        logger.info("Ensuring database tables exist")
        self._ensure_tables_exist()
        logger.info("Database tables created or verified")
        
        # Initialize checkpoint manager
        self.checkpoint_manager = CheckpointManager(self.db)
        
        # Get or create sync state
        self.sync_state = self._get_or_create_sync_state()
        
        # Reset sync state if it was left in a syncing state
        if self.sync_state.is_syncing == 1:
            logger.info("Found sync state with is_syncing=1, resetting")
            try:
                with engine.begin() as conn:
                    conn.execute(text("UPDATE sync_state SET is_syncing = 0 WHERE id = 1"))
                self.db.refresh(self.sync_state)
            except Exception as e:
                logger.error(f"Failed to reset sync state: {str(e)}")
                # This is handled in _get_or_create_sync_state
                
        # Create block parser
        self.parser = BlockParser(self.rpc, self.db)
        
        # Create parallel processor
        self.parallel_processor = ParallelBlockProcessor(
            self.rpc,
            process_func=self._process_block,
            max_workers=int(os.environ.get("SYNC_MAX_WORKERS", "8"))
        )
    
    def _ensure_tables_exist(self):
        """
        Ensure that the necessary database tables exist.
        This method uses raw SQL to create tables if they don't exist,
        which is more robust than relying on SQLAlchemy's create_all.
        """
        # Implementation unchanged
        pass
    
    def _get_or_create_sync_state(self):
        """
        Get or create the sync state record using a robust approach.
        
        Returns:
            SyncState object
        """
        # Implementation unchanged
        pass
    
    def _handle_sync_state_error(self):
        """
        Handle error recovery for sync state initialization.
        This is a fallback method that creates a new sync state record
        directly in the database using raw SQL.
        """
        # Implementation unchanged
        pass
    
    def start_sync(self, continuous=False):
        """
        Start the blockchain synchronization process.
        Updates the sync state and processes new blocks.
        
        Args:
            continuous: If True, keeps syncing without pauses until caught up
            
        Returns:
            bool: True if sync is complete (caught up to tip), False otherwise
        """
        logger.info("Starting blockchain synchronization")
        
        # Check if sync is already in progress
        if self.sync_state.is_syncing == 1:
            logger.warning("Sync already in progress")
            return False
            
        # Mark as syncing and record current time
        current_time = time.time()
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("""
                    UPDATE sync_state SET 
                        is_syncing = 1, 
                        last_error = NULL,
                        last_updated_at = :current_time
                    WHERE id = 1
                    """),
                    {"current_time": current_time}
                )
            self.db.refresh(self.sync_state)
        except Exception as e:
            logger.error(f"Failed to update sync state: {str(e)}")
            # Continue anyway with our in-memory object
            self.sync_state.is_syncing = 1
            self.sync_state.last_updated_at = current_time
        
        # Run the sync process
        sync_complete = False
        try:
            # If in continuous mode, keep syncing until caught up
            if continuous:
                sync_complete = self._sync_blocks_continuous()
            else:
                self._sync_blocks()
                # Check if we're caught up to the tip
                try:
                    node_height = self.rpc.get_block_count()
                    sync_complete = (self.sync_state.current_height >= node_height)
                except Exception as e:
                    logger.warning(f"Failed to check if sync is complete: {str(e)}")
                    sync_complete = False
        except Exception as e:
            logger.error(f"Sync process failed: {str(e)}")
            self._update_sync_error(str(e))
            sync_complete = False
        
        # Mark as not syncing
        try:
            with engine.begin() as conn:
                conn.execute(text("UPDATE sync_state SET is_syncing = 0 WHERE id = 1"))
            self.db.refresh(self.sync_state)
        except Exception as e:
            logger.error(f"Failed to update sync state: {str(e)}")
            # Make sure our in-memory object reflects the state
            self.sync_state.is_syncing = 0
            
        if sync_complete:
            logger.info("Sync process completed - caught up to chain tip")
        else:
            logger.info("Sync process completed - more blocks available")
            
        return sync_complete
    
    def _sync_blocks(self):
        """
        Main synchronization logic with robust error handling.
        Fetches blocks in batches and handles chain reorganizations.
        Uses parallel processing and checkpointing for accelerated synchronization.
        """
        # Implementation unchanged
        pass
    
    def _sync_blocks_continuous(self):
        """
        Continuous synchronization logic that keeps processing blocks
        until caught up to the chain tip.
        
        Returns:
            bool: True if sync is complete (caught up to tip), False otherwise
        """
        try:
            # Get the latest block height from Radiant node with retries
            max_retries = 5
            retry_count = 0
            node_height = None
            
            while retry_count < max_retries and node_height is None:
                try:
                    node_height = self.rpc.get_block_count()
                except Exception as e:
                    retry_count += 1
                    backoff_time = 2 ** retry_count  # Exponential backoff
                    logger.warning(f"Failed to get block count (attempt {retry_count}/{max_retries}): {str(e)}")
                    logger.warning(f"Retrying in {backoff_time} seconds...")
                    time.sleep(backoff_time)
            
            if node_height is None:
                logger.error("Failed to get block count after maximum retries")
                return False
                
            # Get current indexed height
            current_height = self.sync_state.current_height
            
            # Check if we're already caught up
            if current_height >= node_height:
                logger.info(f"Already caught up with the blockchain at height {current_height}")
                return True
                
            # Calculate how many blocks we need to sync
            blocks_to_sync = node_height - current_height
            
            # Get batch size from environment or use default
            batch_size = int(os.environ.get("SYNC_BATCH_SIZE", "500"))
            
            # Calculate number of batches
            num_batches = (blocks_to_sync + batch_size - 1) // batch_size
            
            logger.info(f"Starting continuous sync: {blocks_to_sync} blocks pending in {num_batches} batches")
            
            # Process batches until caught up
            batches_processed = 0
            sync_complete = False
            
            while batches_processed < num_batches and not sync_complete:
                # Calculate batch range
                start_height = current_height + 1
                end_height = min(start_height + batch_size - 1, node_height)
                
                # Process this batch
                logger.info(f"Processing batch {batches_processed + 1}/{num_batches}: blocks {start_height}-{end_height}")
                
                # Use parallel processing for this batch
                try:
                    # Process blocks in parallel
                    batch_heights = list(range(start_height, end_height + 1))
                    results = self.parallel_processor.process_blocks(batch_heights)
                    
                    # Handle any failed blocks sequentially
                    failed_blocks = {height for height, success in results.items() if not success}
                    
                    if failed_blocks:
                        logger.info(f"Attempting to process {len(failed_blocks)} failed blocks sequentially")
                        for height in sorted(failed_blocks):
                            # Use existing retry logic for failed blocks
                            block_retry_count = 0
                            max_block_retries = 3
                            
                            while block_retry_count < max_block_retries:
                                try:
                                    # Get block hash
                                    block_hash = self.rpc.get_block_hash(height)
                                    # Get full block data
                                    block_data = self.rpc.get_block(block_hash)
                                    # Process block
                                    self._process_block(block_data, height, block_hash)
                                    # Success, remove from failed list
                                    failed_blocks.remove(height)
                                    break
                                except Exception as e:
                                    block_retry_count += 1
                                    logger.warning(f"Failed to process block {height} (attempt {block_retry_count}): {str(e)}")
                                    if block_retry_count < max_block_retries:
                                        time.sleep(1)  # Short pause before retry
                            
                            if height in failed_blocks:
                                logger.error(f"Failed to process block {height} after {max_block_retries} attempts")
                    
                    # Update current height to the highest successfully processed block
                    if not failed_blocks:
                        # All blocks successful
                        current_height = end_height
                    else:
                        # Find the highest consecutive block that was successfully processed
                        consecutive_height = start_height
                        for h in range(start_height, end_height + 1):
                            if h in failed_blocks:
                                break
                            consecutive_height = h
                        current_height = consecutive_height
                    
                    # Update sync state in database
                    with engine.begin() as conn:
                        conn.execute(
                            text("""
                            UPDATE sync_state SET 
                                current_height = :height,
                                last_updated_at = extract(epoch from now())
                            WHERE id = 1
                            """),
                            {"height": current_height}
                        )
                    
                    # Update in-memory sync state
                    self.sync_state.current_height = current_height
                    
                    # Create checkpoint periodically
                    checkpoint_interval = int(os.environ.get("CHECKPOINT_INTERVAL", "1000"))
                    if current_height % checkpoint_interval == 0:
                        # Get the block hash for the checkpoint
                        checkpoint_hash = self.rpc.get_block_hash(current_height)
                        # Create checkpoint
                        self.checkpoint_manager.create_checkpoint(
                            current_height,
                            checkpoint_hash,
                            {"timestamp": time.time()}
                        )
                        logger.info(f"Created checkpoint at block height {current_height}")
                    
                    # Update progress
                    batches_processed += 1
                    sync_progress = (current_height / node_height) * 100
                    logger.info(f"Sync progress: {current_height}/{node_height} ({sync_progress:.2f}%)")
                    
                    # Check if we're caught up to the chain tip
                    if current_height >= node_height:
                        sync_complete = True
                    else:
                        # Check if new blocks have been added since we started
                        try:
                            latest_node_height = self.rpc.get_block_count()
                            if latest_node_height > node_height:
                                # More blocks have been added, recalculate
                                blocks_to_sync = latest_node_height - current_height
                                num_batches = (blocks_to_sync + batch_size - 1) // batch_size
                                node_height = latest_node_height
                                logger.info(f"Blockchain grew during sync, now targeting height {node_height}")
                        except Exception as e:
                            logger.warning(f"Failed to check for new blocks: {str(e)}")
                    
                except Exception as e:
                    logger.error(f"Batch processing failed: {str(e)}")
                    # If we failed, stop continuous processing
                    return False
            
            logger.info(f"Continuous sync completed, processed {batches_processed} batches")
            return sync_complete
            
        except Exception as e:
            logger.error(f"Continuous sync process failed: {str(e)}")
            self._update_sync_error(str(e))
            return False
    
    def _process_block(self, block_data, height, block_hash):
        """
        Process a single block with transaction safety.
        
        Args:
            block_data: Full block data
            height: Block height
            block_hash: Block hash
            
        Returns:
            bool: True if block was processed successfully
        """
        try:
            with safe_db_transaction(self.db):
                # Parse and store the block data
                self.parser.parse_block(block_data, height)
                return True
        except Exception as e:
            logger.error(f"Failed to process block {height}: {str(e)}")
            return False
    
    def _update_sync_error(self, error_message):
        """Update the sync state with an error message."""
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("""
                    UPDATE sync_state SET 
                        last_error = :error,
                        last_updated_at = extract(epoch from now())
                    WHERE id = 1
                    """),
                    {"error": error_message}
                )
            # Update our in-memory copy
            self.sync_state.last_error = error_message
        except Exception as e:
            logger.error(f"Failed to update sync error state: {str(e)}")
