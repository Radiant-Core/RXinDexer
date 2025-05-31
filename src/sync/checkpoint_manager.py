# /Users/radiant/Desktop/RXinDexer/src/sync/checkpoint_manager.py
# This file implements a checkpoint system for the blockchain synchronization process.
# It enables efficient recovery from interruptions by storing sync progress at regular intervals.

import logging
import json
import os
import time
from datetime import datetime
from typing import Dict, Any, Optional
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

class CheckpointManager:
    """
    Manages sync checkpoints to enable efficient recovery from interruptions.
    Stores progress at regular intervals and provides methods to restore from checkpoints.
    """
    
    def __init__(self, db: Session):
        """
        Initialize the checkpoint manager.
        
        Args:
            db: Database session to use for storing/retrieving checkpoints
        """
        from src.models.database import get_db
        # Use a separate database session for checkpoint operations to avoid transaction conflicts
        self.db = next(get_db())
        self._ensure_checkpoint_table()
        
    def _ensure_checkpoint_table(self):
        """Ensure that the checkpoint table exists in the database."""
        try:
            with self.db.begin():
                self.db.execute(text("""
                    CREATE TABLE IF NOT EXISTS sync_checkpoints (
                        id SERIAL PRIMARY KEY,
                        height INTEGER NOT NULL,
                        hash VARCHAR(64) NOT NULL,
                        timestamp TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                    );
                    CREATE INDEX IF NOT EXISTS idx_checkpoints_height ON sync_checkpoints (height);
                """))
            logger.info("Checkpoint table created or verified")
        except Exception as e:
            logger.error(f"Error ensuring checkpoint table exists: {str(e)}")
    
    def create_checkpoint(self, height: int, block_hash: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        Create a new checkpoint at the specified height.
        
        Args:
            height: Block height for the checkpoint
            block_hash: Block hash for the checkpoint
            metadata: Additional metadata to store with the checkpoint
            
        Returns:
            bool: True if checkpoint was created successfully
        """
        if metadata is None:
            metadata = {}
            
        # Add timestamp to metadata
        metadata['created_at'] = datetime.now().isoformat()
        
        try:
            with self.db.begin():
                self.db.execute(
                    text("""
                    INSERT INTO sync_checkpoints (height, hash, metadata)
                    VALUES (:height, :hash, cast(:metadata AS jsonb))
                    """),
                    {
                        "height": height,
                        "hash": block_hash,
                        "metadata": json.dumps(metadata)
                    }
                )
            logger.info(f"Created checkpoint at block height {height}")
            return True
        except Exception as e:
            logger.error(f"Failed to create checkpoint at height {height}: {str(e)}")
            return False
    
    def get_latest_checkpoint(self) -> Optional[Dict[str, Any]]:
        """
        Get the latest checkpoint from the database.
        
        Returns:
            Dictionary with checkpoint data or None if no checkpoints exist
        """
        try:
            result = self.db.execute(
                text("""
                SELECT height, hash, timestamp, metadata 
                FROM sync_checkpoints 
                ORDER BY height DESC 
                LIMIT 1
                """)
            ).fetchone()
            
            if result:
                checkpoint = {
                    "height": result[0],
                    "hash": result[1],
                    "timestamp": result[2],
                    "metadata": json.loads(result[3]) if result[3] else {}
                }
                logger.info(f"Retrieved latest checkpoint at height {checkpoint['height']}")
                return checkpoint
            else:
                logger.info("No checkpoints found")
                return None
        except Exception as e:
            logger.error(f"Error retrieving latest checkpoint: {str(e)}")
            return None
    
    def get_checkpoint_at_height(self, height: int) -> Optional[Dict[str, Any]]:
        """
        Get a checkpoint at a specific height.
        
        Args:
            height: The block height to retrieve checkpoint for
            
        Returns:
            Dictionary with checkpoint data or None if no checkpoint exists
        """
        try:
            result = self.db.execute(
                text("""
                SELECT height, hash, timestamp, metadata 
                FROM sync_checkpoints 
                WHERE height = :height
                """),
                {"height": height}
            ).fetchone()
            
            if result:
                checkpoint = {
                    "height": result[0],
                    "hash": result[1],
                    "timestamp": result[2],
                    "metadata": json.loads(result[3]) if result[3] else {}
                }
                logger.info(f"Retrieved checkpoint at height {height}")
                return checkpoint
            else:
                logger.info(f"No checkpoint found at height {height}")
                return None
        except Exception as e:
            logger.error(f"Error retrieving checkpoint at height {height}: {str(e)}")
            return None
    
    def get_nearest_checkpoint(self, target_height: int) -> Optional[Dict[str, Any]]:
        """
        Get the nearest checkpoint below the target height.
        
        Args:
            target_height: The target block height
            
        Returns:
            Dictionary with nearest checkpoint data or None if no checkpoints exist
        """
        try:
            result = self.db.execute(
                text("""
                SELECT height, hash, timestamp, metadata 
                FROM sync_checkpoints 
                WHERE height <= :target_height
                ORDER BY height DESC 
                LIMIT 1
                """),
                {"target_height": target_height}
            ).fetchone()
            
            if result:
                checkpoint = {
                    "height": result[0],
                    "hash": result[1],
                    "timestamp": result[2],
                    "metadata": json.loads(result[3]) if result[3] else {}
                }
                logger.info(f"Retrieved nearest checkpoint at height {checkpoint['height']} for target {target_height}")
                return checkpoint
            else:
                logger.info(f"No checkpoints found below height {target_height}")
                return None
        except Exception as e:
            logger.error(f"Error retrieving nearest checkpoint for height {target_height}: {str(e)}")
            return None
    
    def save_checkpoint(self, height: int, block_hash: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        Save a checkpoint at the specified height.
        This is an alias for create_checkpoint to maintain API compatibility.
        
        Args:
            height: Block height for the checkpoint
            block_hash: Block hash for the checkpoint
            metadata: Additional metadata to store with the checkpoint
            
        Returns:
            bool: True if checkpoint was created successfully
        """
        return self.create_checkpoint(height, block_hash, metadata)
        
    def prune_checkpoints(self, keep_last_n: int = 10, min_interval_blocks: int = 1000) -> int:
        """
        Prune old checkpoints to save database space.
        
        Args:
            keep_last_n: Number of most recent checkpoints to keep regardless of age
            min_interval_blocks: Minimum block interval to maintain between kept checkpoints
            
        Returns:
            int: Number of checkpoints deleted
        """
        try:
            # First, get all checkpoints ordered by height
            checkpoints = self.db.execute(
                text("""
                SELECT id, height FROM sync_checkpoints ORDER BY height DESC
                """)
            ).fetchall()
            
            if not checkpoints:
                return 0
                
            # Keep the most recent N checkpoints
            to_keep = set([cp[0] for cp in checkpoints[:keep_last_n]])
            
            # For older checkpoints, keep them only if they're at least min_interval_blocks apart
            if len(checkpoints) > keep_last_n:
                last_kept_height = checkpoints[keep_last_n-1][1]
                
                for checkpoint_id, height in checkpoints[keep_last_n:]:
                    if last_kept_height - height >= min_interval_blocks:
                        to_keep.add(checkpoint_id)
                        last_kept_height = height
            
            # Delete checkpoints not in the to_keep set
            if len(to_keep) < len(checkpoints):
                to_delete = len(checkpoints) - len(to_keep)
                
                with self.db.begin():
                    self.db.execute(
                        text("""
                        DELETE FROM sync_checkpoints 
                        WHERE id NOT IN :ids
                        """),
                        {"ids": tuple(to_keep) if to_keep else (0,)}
                    )
                
                logger.info(f"Pruned {to_delete} checkpoints, kept {len(to_keep)}")
                return to_delete
            else:
                logger.info("No checkpoints need pruning")
                return 0
        except Exception as e:
            logger.error(f"Error pruning checkpoints: {str(e)}")
            return 0
