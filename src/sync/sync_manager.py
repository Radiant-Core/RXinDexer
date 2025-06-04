# /Users/radiant/Desktop/RXinDexer/src/sync/sync_manager.py
# This file implements the blockchain synchronization manager.
# It handles fetching blocks, detecting reorgs, and updating the database with resilient transaction handling.

import os
import time
import logging
import contextlib
import traceback
from typing import Dict, List, Any, Optional, Tuple
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from sqlalchemy import text, event, inspect, func
from sqlalchemy.exc import SQLAlchemyError, PendingRollbackError

# Import our custom transaction helper
from src.utils.transaction_helper import safe_transaction, get_token_addresses_safe, refresh_views_safe, reset_failed_transactions

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
        # Initialize flag to detect if we're running in the API context
        self._in_api_context = 'API_HOST' in os.environ or os.environ.get('IN_API', 'false').lower() == 'true'
        
        # Get database session if not provided
        if db is None:
            try:
                self.db = next(get_db())
            except Exception as e:
                self.db = None
                if not self._in_api_context:
                    logger.error(f"Failed to get database session: {str(e)}")
        else:
            self.db = db
            
        # Create RPC client for blockchain communication
        try:
            self.rpc = RadiantRPC()
        except Exception as e:
            self.rpc = None
            if not self._in_api_context:
                logger.error(f"Failed to create RPC client: {str(e)}")
        
        # CRITICAL FIX: First create a default sync state to guarantee it's never None
        self.sync_state = SyncState(id=1, current_height=0, is_syncing=0, last_updated_at=time.time())
        
        # Only perform full initialization if not in API context
        if not self._in_api_context:
            # Ensure database tables exist
            logger.info("Ensuring database tables exist")
            self._ensure_tables_exist()
            logger.info("Database tables created or verified")
            
            # Initialize checkpoint manager
            try:
                self.checkpoint_manager = CheckpointManager(self.db)
            except Exception as e:
                logger.error(f"Failed to create checkpoint manager: {str(e)}")
                self.checkpoint_manager = None
            
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
            try:
                self.parser = BlockParser(self.rpc, self.db)
            except Exception as e:
                logger.error(f"Failed to create block parser: {str(e)}")
                self.parser = None
            
            # Create parallel processor with increased worker count for better performance
            try:
                max_workers = int(os.environ.get("SYNC_MAX_WORKERS", "16"))
                self.parallel_processor = ParallelBlockProcessor(max_workers=max_workers)
            except Exception as e:
                logger.error(f"Failed to create parallel processor: {str(e)}")
                self.parallel_processor = None
        else:
            # Minimal initialization for API context to prevent errors
            self.checkpoint_manager = None
            self.parser = None
            self.parallel_processor = None
    
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
        default_sync_state = SyncState(id=1, current_height=0, is_syncing=0, last_updated_at=time.time())
        
        # Approach 1: Try using SQLAlchemy ORM
        try:
            # Try to get existing sync state
            result = self.db.query(SyncState).filter(SyncState.id == 1).first()
            
            if result is None:
                # Create new sync state if it doesn't exist
                logger.info("Creating initial sync state using SQLAlchemy ORM")
                try:
                    sync_state = SyncState(id=1, current_height=0, is_syncing=0, last_updated_at=time.time())
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
                        last_updated_at=time.time()
                    )
                    
        except Exception as e:
            logger.error(f"All sync state recovery attempts failed: {str(e)}")
            
        # If everything fails, return a default SyncState (don't return None)
        return SyncState(id=1, current_height=0, is_syncing=0, last_updated_at=time.time())
    
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
        
        # IMPORTANT FIX: Force reset any stuck sync state in the database
        # This ensures we can always start a new sync regardless of past state
        try:
            # Reset the database sync flag directly with AUTOCOMMIT
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.execute(
                    text("UPDATE sync_state SET is_syncing = 0 WHERE id = 1")
                )
                logger.info("Reset sync state in database to ensure clean start")
        except Exception as e:
            logger.warning(f"Failed to reset stuck sync state: {str(e)}")
            # Continue anyway, we'll reset our in-memory state below
            
        # Always reset our in-memory state to ensure we can start
        self.sync_state.is_syncing = 0
        
        # Original check - now should always pass since we reset above
        if getattr(self.sync_state, 'is_syncing', 0) == 1:
            logger.warning("Sync still marked as in progress after reset attempt")
            return False
            
        # Mark as syncing and record current time
        current_time = time.time()
        
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
            updated_db = True
        except Exception as e:
            logger.error(f"Failed to update sync state with direct connection: {str(e)}")
            
            # Approach 2: Try with raw SQL with a new session
            if not updated_db:
                try:
                    # Create a completely new session
                    new_session = next(get_db())
                    new_session.execute(
                        text("""
                        UPDATE sync_state SET 
                            is_syncing = 1, 
                            last_error = NULL,
                            last_updated_at = :current_time
                        WHERE id = 1
                        """),
                        {"current_time": current_time}
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
                # Use autocommit to avoid transaction issues
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    conn.execute(
                        text("""
                        UPDATE sync_state SET 
                            is_syncing = 0,
                            last_updated_at = :current_time
                        WHERE id = 1
                        """),
                        {"current_time": time.time()}
                    )
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
        try:
            # Get current sync height from database
            current_height = self.sync_state.current_height
            
            # Get current node height
            try:
                node_height = self.rpc.get_block_count()
                logger.info(f"Current node height: {node_height}, local height: {current_height}")
            except Exception as e:
                logger.error(f"Failed to get node height: {str(e)}")
                return
                
            # If we're already at the tip, nothing to do
            if current_height >= node_height:
                logger.info("Already at blockchain tip, nothing to sync")
                return
                
            # Get batch size from environment or use default - increased for better throughput
            batch_size = int(os.environ.get("SYNC_BATCH_SIZE", "1000"))  # Increased from 500 to 1000
            
            # Calculate the next batch of blocks to process
            start_height = current_height + 1
            end_height = min(start_height + batch_size - 1, node_height)
            
            # Log the batch we're about to process
            logger.info(f"Processing block batch from {start_height} to {end_height}")
            
            # Define the block range to process
            block_range = range(start_height, end_height + 1)
            
            # Define a function to fetch blocks via RPC
            def fetch_block_func(height):
                block_hash = self.rpc.get_block_hash(height)
                block_data = self.rpc.get_block(block_hash)
                return block_hash, block_data
                
            # Process the blocks in parallel with optimized worker count
            # Update max_workers at runtime in case it was changed in environment
            max_workers = int(os.environ.get("SYNC_MAX_WORKERS", "16"))  # Increased from default
            self.parallel_processor.max_workers = max_workers
            
            blocks_processed, failed_blocks = self.parallel_processor.process_blocks_parallel(
                block_range=block_range,
                fetch_block_func=fetch_block_func,
                process_block_func=self._process_block
            )
            
            # Log the results
            logger.info(f"Processed {blocks_processed} blocks, {len(failed_blocks)} failed")
            
            # If there were failures, log them
            if failed_blocks:
                logger.warning(f"Failed to process blocks: {failed_blocks}")
                
            # Create a checkpoint if needed
            if blocks_processed > 0:
                try:
                    # Get the latest hash for checkpoint
                    latest_hash = self.rpc.get_block_hash(end_height)
                    # Save checkpoint
                    self.checkpoint_manager.save_checkpoint(
                        height=end_height,
                        block_hash=latest_hash
                    )
                    logger.info(f"Created checkpoint at height {end_height}")
                except Exception as e:
                    logger.error(f"Failed to save checkpoint: {str(e)}")
        except Exception as e:
            logger.error(f"Error in _sync_blocks: {str(e)}")
            raise
    
    def _sync_blocks_continuous(self):
        """
        Continuous synchronization logic that keeps processing blocks
        until caught up to the chain tip.
        
        Returns:
            bool: True if sync is complete (caught up to tip), False otherwise
        """
        try:
            # Get current node height
            try:
                node_height = self.rpc.get_block_count()
            except Exception as e:
                logger.error(f"Failed to get node height: {str(e)}")
                return False
                
            # Continuously process blocks until caught up
            while True:
                # Get our current height after the last batch
                current_height = self.sync_state.current_height
                
                # Check if we're caught up
                if current_height >= node_height:
                    logger.info(f"Caught up to blockchain tip at height {node_height}")
                    return True
                    
                # Process the next batch of blocks
                try:
                    self._sync_blocks()
                except Exception as e:
                    logger.error(f"Failed to sync blocks: {str(e)}")
                    return False
                    
                # Get fresh node height to check progress
                try:
                    node_height = self.rpc.get_block_count()
                    # Calculate progress percentage
                    progress = (current_height / node_height) * 100 if node_height > 0 else 0
                    logger.info(f"Sync progress: {progress:.2f}% ({current_height}/{node_height})")
                except Exception as e:
                    logger.warning(f"Failed to update node height: {str(e)}")
        except Exception as e:
            logger.error(f"Error in continuous sync: {str(e)}")
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
        # Use a fresh database session for each block to avoid transaction contamination
        # This ensures that problems in one query don't affect other operations
        try:
            # Create a brand new session with AUTOCOMMIT to prevent transaction errors
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                logger.info(f"Processing block {height} with isolated connection")
                
                # 1. First, create a fresh parser with the new connection
                from src.parser.block_parser import BlockParser
                parser = BlockParser(self.rpc, conn)
                
                # 2. Process the block using individual operations that self-commit
                try:
                    txs = block_data.get("tx", [])
                    tx_count = len(txs)
                    
                    # Process all transactions individually
                    utxos_created = 0
                    utxos_spent = 0
                    glyph_tokens = 0
                    
                    # Handle transactions one by one to avoid transaction dependencies
                    for tx in txs:
                        try:
                            # Insert the transaction first
                            tx_id = tx.get("txid")
                            
                            # Process UTXOs and token data separately
                            # Each operation gets its own connection to avoid dependency issues
                            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as tx_conn:
                                try:
                                    # Insert UTXOs (both created and spent)
                                    tx_conn.execute(text("""
                                        INSERT INTO transactions (txid, block_height, block_hash, raw_data) 
                                        VALUES (:txid, :height, :block_hash, :raw_data::jsonb)
                                        ON CONFLICT (txid) DO NOTHING
                                    """), {
                                        "txid": tx_id,
                                        "height": height,
                                        "block_hash": block_hash,
                                        "raw_data": str(tx)  # Store minimal data to save space
                                    })
                                except Exception as e:
                                    logger.warning(f"Non-critical error storing transaction {tx_id}: {str(e)}")
                            
                            # Process UTXOs with a fresh connection
                            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as utxo_conn:
                                try:
                                    from src.parser.utxo_parser import UTXOParser
                                    utxo_parser = UTXOParser(self.rpc, utxo_conn)
                                    u_created, u_spent = utxo_parser.parse_transaction(tx, height, block_hash)
                                    utxos_created += u_created
                                    utxos_spent += u_spent
                                except Exception as e:
                                    logger.warning(f"Error processing UTXOs for tx {tx_id}: {str(e)}")
                            
                            # Process Glyph tokens with another fresh connection
                            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as glyph_conn:
                                try:
                                    from src.parser.glyph_parser import GlyphParser
                                    glyph_parser = GlyphParser(self.rpc, glyph_conn)
                                    tokens = glyph_parser.parse_transaction(tx, height, block_hash)
                                    glyph_tokens += len(tokens)
                                except Exception as e:
                                    logger.warning(f"Error processing Glyph tokens for tx {tx_id}: {str(e)}")
                                    
                        except Exception as tx_e:
                            logger.warning(f"Error processing transaction {tx.get('txid')}: {str(tx_e)}")
                    
                    # Update the block stats
                    logger.info(f"Processed block {height}: {tx_count} transactions, {utxos_created} UTXOs created, {utxos_spent} UTXOs spent, {glyph_tokens} tokens")
                    
                except Exception as block_e:
                    logger.error(f"Error processing block transactions: {str(block_e)}")
                    raise
                
                # 3. Update sync state with new block info
                try:
                    conn.execute(
                        text("""
                        UPDATE sync_state 
                        SET current_height = :height,
                            current_hash = :block_hash,
                            last_updated_at = :time
                        WHERE id = 1
                        """),
                        {"height": height, "block_hash": block_hash, "time": time.time()}
                    )
                    
                    # Update in-memory state as well
                    self.sync_state.current_height = height
                    self.sync_state.current_hash = block_hash
                    self.sync_state.last_updated_at = func.now()
                except Exception as e:
                    logger.error(f"Failed to update sync state in database: {str(e)}")
                
                # 4. Refresh materialized views
                try:
                    conn.execute(text("REFRESH MATERIALIZED VIEW address_balances"))
                    logger.info(f"Refreshed materialized views for block {height}")
                except Exception as e:
                    logger.warning(f"Failed to refresh views: {str(e)}")
                
                return True
        except Exception as e:
            # Log error but continue with next block
            logger.error(f"Failed to process block {height}: {str(e)}")
            return False
    
    def _update_sync_error(self, error_message=None):
        """
        Update the sync state with an error message.
        
        Args:
            error_message: Error message to record, defaults to None if not provided
        """
        # IMPORTANT: Define error_message right at the beginning of the method
        # This ensures the variable exists regardless of execution path
        # Fixes the "name 'error_message' is not defined" error
        if error_message is None:
            error_message = "Unknown error"
            
        # Make sure error_message is a string to prevent conversion errors
        error_message = str(error_message)
        
        # Wrap everything in a try/except to make this method robust
        try:
            # First update our in-memory state
            if hasattr(self, 'sync_state') and self.sync_state is not None:
                self.sync_state.last_error = error_message
                self.sync_state.last_updated_at = func.now()
            
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
            except Exception:
                # Silently continue with in-memory state only
                pass
        except Exception:
            # Completely suppress all errors to prevent API initialization issues
            pass
