# /Users/radiant/Desktop/RXinDexer/src/models/block.py
# This file defines the Block model for storing blockchain block data.
# It handles block headers, metadata, and relationships to transactions.

from sqlalchemy import Column, Integer, String, Float, DateTime, BigInteger, ForeignKey, Index
from sqlalchemy.orm import relationship
from datetime import datetime

from .database import Base

class Block(Base):
    """
    Model representing a blockchain block in the Radiant network.
    Stores block headers and metadata for efficient querying.
    """
    __tablename__ = "blocks"

    # Primary block identification
    hash = Column(String(64), primary_key=True, nullable=False)
    height = Column(Integer, nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default='now()')
    
    # Create indexes for common queries
    __table_args__ = (
        Index('idx_blocks_height', 'height'),
    )
    
    def __repr__(self):
        return f"<Block(height={self.height}, hash={self.hash}, tx_count={self.transaction_count})>"
