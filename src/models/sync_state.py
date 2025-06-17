# /Users/radiant/Desktop/RXinDexer/src/models/sync_state.py
# This file defines the SyncState model that tracks blockchain synchronization progress.
# It stores the latest synced block and helps manage chain reorganizations.

from sqlalchemy import Column, String, Integer, Text, DateTime, Index
from sqlalchemy.sql import func
from .database import Base

class SyncState(Base):
    """
    Tracks the synchronization state of the indexer.
    Stores information about the latest synced block and chain status.
    Matches the schema defined in db_init.sql with proper types and constraints.
    """
    __tablename__ = "sync_state"

    # Primary key - only one row will exist in this table
    id = Column(Integer, primary_key=True, default=1, 
               doc="Always 1, only one sync state exists")
    
    # Latest block information
    current_height = Column(Integer, nullable=False, default=0, 
                          doc="Current synced block height")
    current_hash = Column(String(64), nullable=True, 
                         doc="Hash of the current synced block")
    current_chainwork = Column(String(64), nullable=True, 
                              doc="Chainwork of the current chain")
    
    # Sync status
    is_syncing = Column(Integer, default=0, 
                       doc="Whether sync is in progress (0=stopped, 1=running)")
    last_error = Column(Text, nullable=True, 
                       doc="Last error encountered during sync")
    last_updated_at = Column(DateTime, nullable=True, 
                            doc="Timestamp when sync was last updated")
    
    # Glyph scan status
    glyph_scan_height = Column(Integer, default=0, 
                             doc="Last block height scanned for Glyph tokens")
    
    # Timestamps
    created_at = Column(DateTime, nullable=False, server_default=func.now(),
                      doc="When this record was created")
    updated_at = Column(DateTime, nullable=False, server_default=func.now(),
                       onupdate=func.now(), 
                       doc="When this record was last updated")
    
    # Indexes for performance
    __table_args__ = (
        Index('ix_sync_state_current_height', 'current_height'),
    )
    
    def __repr__(self):
        """String representation of the SyncState"""
        status = "syncing" if self.is_syncing else "idle"
        return f"<SyncState(height={self.current_height}, status={status})>"
