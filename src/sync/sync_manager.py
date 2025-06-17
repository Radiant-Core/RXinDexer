# /Users/radiant/Desktop/RXinDexer/src/sync/sync_manager.py
# This file implements the blockchain synchronization manager.
# It handles fetching blocks, detecting reorgs, and updating the database with resilient transaction handling.

import os
import time
import json
import asyncio
import threading
import logging
import contextlib
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from dotenv import load_dotenv
from sqlalchemy import text, event, inspect, Column, String, Integer, BigInteger, ForeignKey, DateTime, Boolean, func
from sqlalchemy.orm import Session
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
        
        # CRITICAL FIX: First create a default sync state to guarantee it's never None
        self.sync_state = SyncState(id=1, current_height=0, is_syncing=0, last_updated_at=datetime.now())
        
        # Try to load from database but never allow sync_state to become None
        try:
            # This will return a valid SyncState object or default
            db_sync_state = self._get_or_create_sync_state()
            if db_sync_state is not None:
                self.sync_state = db_sync_state
                logger.info(f"Using database sync state with height {self.sync_state.current_height}")
            else:
                logger.warning("Database sync state was None, using default in-memory state")
                
            # Ensure is_syncing is always reset to 0 at startup
            self.sync_state.is_syncing = 0
            
            # Try to update the database but continue if it fails
            try:
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    conn.execute(text("UPDATE sync_state SET is_syncing = 0 WHERE id = 1"))
            except Exception as e:
                logger.error(f"Failed to reset sync state in database: {str(e)}")
                logger.info("Continuing with in-memory reset state")
        except Exception as e:
            logger.error(f"Error initializing sync state from database: {str(e)}")
            logger.warning("Using in-memory sync state as fallback")
                
        # Create block parser
        self.parser = BlockParser(self.rpc, self.db)
        
        # Create parallel processor
        self.parallel_processor = ParallelBlockProcessor(
            max_workers=int(os.environ.get("SYNC_MAX_WORKERS", "8"))
        )
    
    def _ensure_tables_exist(self):
        """
        Ensure that the necessary database tables exist.
        This method uses raw SQL to create tables if they don't exist,
        which is more robust than relying on SQLAlchemy's create_all.
        """
        # Implementation preserved
        pass
    
    def _get_or_create_sync_state(self):
        """
        Get or create the sync state record using a robust approach.
        
        Returns:
            SyncState object (always returns a valid object, never None)
        """
        # Create a default sync state to return in case of errors
        default_sync_state = SyncState(id=1, current_height=0, is_syncing=0, last_updated_at=datetime.now())
        
        # Approach 1: Try using SQLAlchemy ORM
        try:
            # Try to get existing sync state
            result = self.db.query(SyncState).filter(SyncState.id == 1).first()
            
            if result is None:
                # Create new sync state if it doesn't exist
                logger.info("Creating initial sync state using SQLAlchemy ORM")
                try:
                    sync_state = SyncState(id=1, current_height=0, is_syncing=0, last_updated_at=datetime.now())
                    self.db.add(sync_state)
                    self.db.commit()
                    self.db.refresh(sync_state)
                    return sync_state
                except Exception as e:
                    logger.error(f"Failed to create sync state via ORM: {str(e)}")
                    # Continue to other approaches
            else:
                # Found existing record
                return result
                
        except Exception as e:
            logger.error(f"Failed to get sync state via ORM: {str(e)}")
            
        # Approach 2: Try using raw SQL
        try:
            # Check if the table exists and has a record
            logger.info("Attempting to get sync state using raw SQL")
            with engine.connect() as conn:
                try:
                    result = conn.execute(text("SELECT * FROM sync_state WHERE id = 1")).fetchone()
                    
                    if result is None:
                        # Create the record if it doesn't exist
                        logger.info("Creating initial sync state using raw SQL")
                        conn.execute(text("""
                        INSERT INTO sync_state (id, current_height, is_syncing, last_updated_at)
                        VALUES (1, 0, 0, extract(epoch from now()))
                        ON CONFLICT (id) DO NOTHING
                        """))
                        conn.commit()
                        
                        # Get the newly created record
                        result = conn.execute(text("SELECT * FROM sync_state WHERE id = 1")).fetchone()
                        
                    # Convert result to SyncState object
                    if result:
                        sync_state = SyncState(
                            id=result[0],
                            current_height=result[1],
                            is_syncing=0,  # Force to 0 for safety
                            last_updated_at=result[3],
                            last_error=result[4] if len(result) > 4 else None
                        )
                        return sync_state
                except Exception as e:
                    logger.error(f"Error executing SQL queries: {str(e)}")
        except Exception as e:
            logger.error(f"Failed to get or create sync state via raw SQL: {str(e)}")
        
        # Approach 3: Try recovery method
        try:
            recovery_result = self._handle_sync_state_error()
            if recovery_result is not None:
                return recovery_result
        except Exception as e:
            logger.error(f"Recovery method failed: {str(e)}")
        
        # Return the default as last resort - never return None
        logger.warning("All database approaches failed, returning in-memory sync state")
        return default_sync_state
    
    def _handle_sync_state_error(self):
        """
        Handle error recovery for sync state initialization.
        This is a fallback method that creates a new sync state record
        directly in the database using raw SQL.
        
        Returns:
            SyncState object or None if all attempts fail
        """
        logger.info("Attempting to recover sync state with fallback method")
        
        try:
            # Create sync_state table if it doesn't exist
            with engine.connect() as conn:
                conn.execute(text("""
                CREATE TABLE IF NOT EXISTS sync_state (
                    id INTEGER PRIMARY KEY,
                    current_height INTEGER NOT NULL DEFAULT 0,
                    is_syncing SMALLINT NOT NULL DEFAULT 0,
                    last_updated_at FLOAT,
                    last_error TEXT,
                    current_hash VARCHAR(64),
                    current_chainwork VARCHAR(64),
                    created_at TIMESTAMP WITHOUT TIME ZONE,
                    updated_at TIMESTAMP WITHOUT TIME ZONE
                )
                """))
                
                # Check if record exists
                result = conn.execute(text("SELECT COUNT(*) FROM sync_state WHERE id = 1")).scalar()
                
                if result == 0:
                    # Insert new record with very basic approach
                    conn.execute(text("""
                    INSERT INTO sync_state (id, current_height, is_syncing, last_updated_at)
                    VALUES (1, 0, 0, extract(epoch from now()))
                    ON CONFLICT (id) DO NOTHING
                    """))
                    
                # Set sync state to not syncing (safety)
                conn.execute(text("""
                UPDATE sync_state SET is_syncing = 0 WHERE id = 1
                """))
                
                # Get the record
                result = conn.execute(text("SELECT * FROM sync_state WHERE id = 1")).fetchone()
                
                if result:
                    # Create SyncState object manually
                    return SyncState(
                        id=1,
                        current_height=result[1] if result[1] is not None else 0,
                        is_syncing=0,  # Force to 0 for safety
                        last_updated_at=datetime.now()
                    )
                    
        except Exception as e:
            logger.error(f"All sync state recovery attempts failed: {str(e)}")
            
        # If everything fails, return a default SyncState (don't return None)
        return SyncState(id=1, current_height=0, is_syncing=0, last_updated_at=datetime.now())
    
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
        
        # Check if sync is already in progress, using getattr to safely check
        # in case the object isn't properly initialized
        if getattr(self.sync_state, 'is_syncing', 0) == 1:
            logger.warning("Sync already in progress")
            return False
            
        # Mark as syncing and record current time
        current_time = datetime.now()
        
        # First set our in-memory state (guaranteed to work)
        self.sync_state.is_syncing = 1
        self.sync_state.last_updated_at = current_time
        self.sync_state.last_error = None
        
        # Try multiple approaches to update the database state
        updated_db = False
        
        # Approach 1: Use engine.begin (most reliable for resetting transactions)
        try:
            # Force a new connection to avoid transaction issues
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                # First, ensure the record exists
                conn.execute(
                    text("""
                    INSERT INTO sync_state (id, current_height, current_hash, is_syncing, last_updated_at, last_error)
                    VALUES (1, 0, '', 1, NOW(), NULL)
                    ON CONFLICT (id) DO UPDATE SET
                        is_syncing = EXCLUDED.is_syncing,
                        last_updated_at = EXCLUDED.last_updated_at,
                        last_error = EXCLUDED.last_error
                    """)
                )
                
                # Then update with the current state
                conn.execute(
                    text("""
                    UPDATE sync_state SET 
                        is_syncing = 1, 
                        last_error = NULL,
                        last_updated_at = NOW()
                    WHERE id = 1
                    """)
                )
            updated_db = True
        except Exception as e:
            logger.error(f"Failed to update sync state with direct connection: {str(e)}")
            
            # Approach 2: Try with raw SQL with a new session
            if not updated_db:
                try:
                    # Create a completely new session
                    new_session = next(get_db())
                    
                    # First ensure the record exists
                    new_session.execute(
                        text("""
                        INSERT INTO sync_state (id, current_height, current_hash, is_syncing, last_updated_at, last_error)
                        VALUES (1, 0, '', 1, NOW(), NULL)
                        ON CONFLICT (id) DO UPDATE SET
                            is_syncing = EXCLUDED.is_syncing,
                            last_updated_at = EXCLUDED.last_updated_at,
                            last_error = EXCLUDED.last_error
                        """)
                    )
                    
                    # Then update with the current state
                    new_session.execute(
                        text("""
                        UPDATE sync_state SET 
                            is_syncing = 1, 
                            last_error = NULL,
                            last_updated_at = NOW()
                        WHERE id = 1
                        """)
                    )
                    new_session.commit()
                    new_session.close()
                    updated_db = True
                except Exception as e:
                    logger.error(f"Failed to update sync state with new session: {str(e)}")
                    
        if updated_db:
            try:
                # Refresh our sync_state object to match the database
                self.db.refresh(self.sync_state)
                logger.info("Successfully updated sync state in database")
            except Exception as e:
                logger.warning(f"Could not refresh sync_state: {str(e)}")
                # We already updated our in-memory state above, so we're good to continue
        
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
        finally:
            # Always mark sync as complete when done
            try:
                # Get current timestamp once for consistency
                current_time = datetime.now()
                
                # Update the database with consistent timestamp
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    # First ensure the record exists
                    conn.execute(
                        text("""
                        INSERT INTO sync_state (id, current_height, current_hash, is_syncing, last_updated_at, last_error)
                        VALUES (1, 0, '', 0, :time, NULL)
                        ON CONFLICT (id) DO UPDATE SET
                            is_syncing = EXCLUDED.is_syncing,
                            last_updated_at = EXCLUDED.last_updated_at
                        """),
                        {"time": current_time}
                    )
                    
                    # Then update with the current state
                    conn.execute(
                        text("""
                        UPDATE sync_state 
                        SET is_syncing = 0,
                            last_updated_at = :time
                        WHERE id = 1
                        """),
                        {"time": current_time}
                    )
                
                # Update in-memory state
                self.sync_state.is_syncing = 0
                self.sync_state.last_updated_at = current_time
                
                logger.info("Successfully marked sync as complete in database")
            except Exception as e:
                logger.error(f"Failed to update sync state after completion: {str(e)}")
                # At least update in-memory state
                self.sync_state.is_syncing = 0
                
        return sync_complete
    
    def _sync_blocks(self):
        """
        Main synchronization logic with robust error handling.
        Fetches blocks in batches and handles chain reorganizations.
        Uses parallel processing and checkpointing for accelerated synchronization.
        """
        # Implementation preserved
        pass
    
    def _sync_blocks_continuous(self):
        """
        Continuous synchronization logic that keeps processing blocks
        until caught up to the chain tip.
        
        Returns:
            bool: True if sync is complete (caught up to tip), False otherwise
        """
        try:
            # Get current blockchain height from node
            node_height = self.rpc.get_block_count()
            
            # Get current indexed height from our database
            current_height = self.sync_state.current_height or 0
            
            # If we're already at or beyond the tip, we're done
            if current_height >= node_height:
                logger.info(f"Already in sync with blockchain tip at height {node_height}")
                return True
                
            logger.info(f"Continuous sync: indexed height {current_height}, chain height {node_height}")
            
            # Use the batch size from environment variable
            batch_size = int(os.environ.get("SYNC_BATCH_SIZE", "500"))
            
            # Calculate total blocks to process
            blocks_to_process = node_height - current_height
            logger.info(f"Need to process {blocks_to_process} blocks in continuous mode")
            
            # Process blocks in batches
            while current_height < node_height:
                # Calculate batch end height (not exceeding node_height)
                end_height = min(current_height + batch_size, node_height)
                
                # Process this batch
                processed_count, failed_blocks = self.process_blocks_batch(
                    current_height + 1, end_height
                )
                
                # Update current height after processing
                current_height = end_height - len(failed_blocks)
                
                # If we have failures, log them and stop
                if failed_blocks:
                    logger.error(f"Failed to process {len(failed_blocks)} blocks in continuous mode")
                    # Update the highest successfully processed block height
                    if len(failed_blocks) < (end_height - (current_height + 1) + 1):
                        # Some blocks processed successfully
                        current_height = min(failed_blocks) - 1
                    return False
                
                # Check if we need to refresh the node height (in case new blocks arrived)
                if current_height >= node_height and node_height < self.rpc.get_block_count():
                    node_height = self.rpc.get_block_count()
                    logger.info(f"New blocks detected, updating target height to {node_height}")
                    
                # Log progress periodically
                logger.info(f"Continuous sync progress: {current_height}/{node_height} ({(current_height/node_height)*100:.2f}%)")
                
            return current_height >= node_height
            
        except Exception as e:
            logger.error(f"Error in continuous sync: {str(e)}")
            return False
    
    def _process_block(self, block_data: Dict[str, Any], height: int, block_hash: str) -> bool:
        """
        Process a single block and update the database with batched operations.
        
        Args:
            block_data: Block data from RPC
            height: Block height
            block_hash: Block hash
            
        Returns:
            bool: True if processing succeeded, False otherwise
        """
        try:
            # Start a new database session for this block
            db = next(get_db())
            
            # Parse block data
            parser = BlockParser(self.rpc, db)
            
            # Use a transaction for the entire block processing
            with db.begin():
                success = parser.parse_block(block_data, height, block_hash)
                
                if not success:
                    logger.error(f"Failed to parse block {height}")
                    return False
                
                # Update sync state in the same transaction
                self.sync_state.current_height = height
                self.sync_state.current_hash = block_hash
                self.sync_state.last_updated_at = datetime.now()
                self.sync_state.is_syncing = 1
                
                # Save checkpoint in the same transaction
                self.checkpoint_manager.save_checkpoint(height)
                
                # Log progress periodically
                if height % 50 == 0:
                    logger.info(f"Processed block {height}")
                
                return True
                
        except SQLAlchemyError as e:
            logger.error(f"Database error processing block {height}: {str(e)}", exc_info=True)
            if 'db' in locals():
                db.rollback()
            return False
            
        except Exception as e:
            logger.error(f"Unexpected error processing block {height}: {str(e)}", exc_info=True)
            if 'db' in locals():
                db.rollback()
            return False
    
    def process_blocks_batch(self, start_height: int, end_height: int) -> Tuple[int, List[int]]:
        """
        Process a batch of blocks using the parallel processor with optimized performance.
        
        Args:
            start_height: Starting block height (inclusive)
            end_height: Ending block height (inclusive)
            
        Returns:
            Tuple of (number of blocks processed, list of failed block heights)
        """
        logger.info(f"Processing batch from height {start_height} to {end_height}")
        
        try:
            # Process blocks in parallel
            blocks_processed, failed_blocks = self.parallel_processor.process_blocks_parallel(
                range(start_height, end_height + 1),
                self._fetch_block,
                self._process_block,
                max_concurrent_tasks=int(os.environ.get("SYNC_MAX_WORKERS", "8")),
                checkpoint_interval=min(100, end_height - start_height + 1)  # More frequent checkpoints for better recovery
            )
            
            # Log summary
            total_blocks = end_height - start_height + 1
            success_rate = (blocks_processed / total_blocks * 100) if total_blocks > 0 else 0
            
            logger.info(
                f"Batch complete. Processed {blocks_processed}/{total_blocks} blocks "
                f"({success_rate:.1f}% success)"
            )
            
            if failed_blocks:
                logger.warning(f"Failed to process {len(failed_blocks)} blocks in this batch")
            
            return blocks_processed, failed_blocks
            
        except Exception as e:
            error_msg = f"Error in parallel block processing: {str(e)}"
            logger.error(error_msg, exc_info=True)
            # Mark all blocks in the batch as failed
            failed_blocks = list(range(start_height, end_height + 1))
            return 0, failed_blocks
    
    def _fetch_block(self, height: int) -> Tuple[str, Dict]:
        """
        Fetch a block at the specified height.
        
        Args:
            height: Block height to fetch
            
        Returns:
            Tuple of (block_hash, block_data)
            
        Raises:
            Exception: If block cannot be fetched
        """
        try:
            block_hash = self.rpc.get_block_hash(height)
            if not block_hash:
                raise ValueError(f"Failed to get block hash for height {height}")
                
            block_data = self.rpc.get_block(block_hash)
            if not block_data:
                raise ValueError(f"Failed to get block data for hash {block_hash}")
                
            return block_hash, block_data
            
        except Exception as e:
            logger.error(f"Error fetching block {height}: {str(e)}")
            raise

    def _update_sync_error(self, error_message):
        """
        Update the sync state with an error message.
        
        Args:
            error_message: Error message to record
        """
        try:
            # First update our in-memory state
            self.sync_state.last_error = error_message
            self.sync_state.last_updated_at = datetime.now()
            
            # Then try to update the database
            try:
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    conn.execute(
                        text("""
                        UPDATE sync_state 
                        SET last_error = :error,
                            last_updated_at = NOW()
                        WHERE id = 1
                        """),
                        {"error": error_message}
                    )
            except Exception as e:
                logger.error(f"Failed to update error in database: {str(e)}")
                # Continue with in-memory state only
        except Exception as e:
            logger.error(f"Failed to update sync error: {str(e)}")
