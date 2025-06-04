# /Users/radiant/Desktop/RXinDexer/src/models/transaction.py
# This file defines the Transaction model for storing blockchain transaction data.
# It represents transaction details and relationships to inputs, outputs, and blocks.

from sqlalchemy import Column, Integer, String, Float, DateTime, BigInteger, ForeignKey, Text, Boolean, Index
from sqlalchemy.orm import relationship
from datetime import datetime

from .database import Base

class Transaction(Base):
    """
    Model representing a blockchain transaction in the Radiant network.
    Stores transaction details and maintains relationships to inputs and outputs.
    """
    __tablename__ = "transactions"

    # Primary transaction identification
    txid = Column(String(64), primary_key=True, index=True)
    
    # Block information
    block_height = Column(Integer, ForeignKey("blocks.height"), nullable=False, index=True)
    block_hash = Column(String(64), ForeignKey("blocks.hash"), nullable=False)
    block_time = Column(DateTime, nullable=False, index=True)
    
    # Transaction details
    version = Column(Integer, nullable=False)
    size = Column(Integer, nullable=False)
    vsize = Column(Integer, nullable=False)
    weight = Column(Integer, nullable=False)
    locktime = Column(Integer, nullable=False)
    hex = Column(Text, nullable=True)  # Full transaction hex, may be null to save space
    
    # Transaction metadata
    fee = Column(BigInteger, nullable=True)  # in satoshis
    is_coinbase = Column(Boolean, default=False, nullable=False)
    
    # Confirmation status
    confirmations = Column(Integer, default=0, nullable=False)
    
    # Indexing metadata
    indexed_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    # These would typically be defined if we had input/output models
    # inputs = relationship("TxInput", back_populates="transaction")
    # outputs = relationship("TxOutput", back_populates="transaction")
    
    # Create indexes for common queries
    __table_args__ = (
        Index('ix_transactions_block_time', 'block_time'),
    )
    
    def __repr__(self):
        return f"<Transaction(txid={self.txid}, block_height={self.block_height})>"
