# /Users/radiant/Desktop/RXinDexer/src/models/sync_state.py
# This file defines the SyncState model that tracks blockchain synchronization progress.
# It stores the latest synced block and helps manage chain reorganizations.

from sqlalchemy import Column, String, Integer, func, Text, Float
from sqlalchemy.types import TIMESTAMP
from .database import Base


class SyncState(Base):
    """
    Tracks the synchronization state of the indexer.
    Stores information about the latest synced block and chain status.
    """
    __tablename__ = "sync_state"

    # Only one row will exist in this table
    id = Column(Integer, primary_key=True, default=1, doc="Always 1, only one sync state exists")
    
    # Latest block information
    current_height = Column(Integer, nullable=False, default=0, doc="Current synced block height")
    current_hash = Column(String(64), nullable=True, doc="Hash of the current synced block")
    current_chainwork = Column(String(64), nullable=True, doc="Chainwork of the current chain")
    
    # Sync status
    is_syncing = Column(Integer, default=0, doc="Whether sync is in progress (0=stopped, 1=running)")
    last_error = Column(Text, nullable=True, doc="Last error encountered during sync")
    # NOTE: Database stores this as TIMESTAMP WITHOUT TIME ZONE, but we use Float here for compatibility
    # with existing code that uses time.time() values. Be cautious when updating this field directly in SQL.
    last_updated_at = Column(Float, nullable=True, doc="Unix timestamp of when sync was last started (for timeout detection)")
    
    # Timestamps
    created_at = Column(TIMESTAMP, server_default=func.now(), doc="When this record was created")
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now(), doc="When sync state was last updated")
    
    def __repr__(self):
        """String representation of the SyncState"""
        return f"<SyncState(current_height={self.current_height}, is_syncing={self.is_syncing})>"
