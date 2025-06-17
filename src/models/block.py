# /Users/radiant/Desktop/RXinDexer/src/models/block.py
# This file defines the Block model for storing blockchain block data.
# It handles block headers, metadata, and relationships to transactions.

from sqlalchemy import Column, Integer, BigInteger, String, DateTime, Index
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
    
    # Block header fields
    version = Column(Integer, nullable=False)
    prev_hash = Column(String(64), nullable=True)  # Nullable for genesis block
    merkle_root = Column(String(64), nullable=False)
    timestamp = Column(Integer, nullable=False)  # Unix timestamp
    bits = Column(String(16), nullable=False)  # Changed to String to match '1d00ffff' format
    nonce = Column(BigInteger, nullable=False)
    chainwork = Column(String(64), nullable=True)
    
    # Block size information
    size = Column(Integer, nullable=True)  # Size in bytes
    weight = Column(Integer, nullable=True)  # Block weight
    tx_count = Column(Integer, nullable=True)  # Number of transactions
    
    # Timestamps
    created_at = Column(DateTime, nullable=False, server_default='now()')
    updated_at = Column(DateTime, nullable=False, server_default='now()', onupdate='now()')
    
    # Relationships
    transactions = relationship("Transaction", back_populates="block", cascade="all, delete-orphan")
    
    # Create indexes for common queries
    __table_args__ = (
        Index('idx_blocks_height', 'height'),
        Index('idx_blocks_prev_hash', 'prev_hash'),
        Index('idx_blocks_timestamp', 'timestamp'),
    )
    
    @property
    def transaction_count(self):
        """Return the number of transactions in this block."""
        return len(self.transactions) if hasattr(self, 'transactions') else 0
    
    def __repr__(self):
        return f"<Block(height={self.height}, hash={self.hash}, tx_count={self.transaction_count})>"
