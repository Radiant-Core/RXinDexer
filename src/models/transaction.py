# /Users/radiant/Desktop/RXinDexer/src/models/transaction.py
# This file defines the Transaction model for storing blockchain transaction data.
# It represents transaction details and relationships to blocks and UTXOs.

from sqlalchemy import Column, Integer, String, DateTime, BigInteger, ForeignKey, Text, Boolean, Index
from sqlalchemy.orm import relationship
from datetime import datetime

from .database import Base

class Transaction(Base):
    """
    Model representing a blockchain transaction in the Radiant network.
    Stores transaction details and maintains relationships to blocks and UTXOs.
    """
    __tablename__ = "transactions"

    # Primary transaction identification
    txid = Column(String(64), primary_key=True, index=True)
    
    # Block information - matches the database schema from db_init.sql
    block_hash = Column(String(64), ForeignKey("blocks.hash"), nullable=False, index=True)
    block_height = Column(Integer, nullable=False, index=True)
    
    # Transaction details
    version = Column(Integer, nullable=False)
    locktime = Column(Integer, nullable=False)
    
    # Size information
    size = Column(Integer, nullable=True)  # Size in bytes
    
    # Timestamps
    created_at = Column(DateTime, nullable=False, server_default='now()')
    updated_at = Column(DateTime, nullable=False, server_default='now()', onupdate='now()')
    
    # Relationships
    block = relationship("Block", back_populates="transactions")
    utxos = relationship("UTXO", back_populates="transaction", 
                        primaryjoin="Transaction.txid == UTXO.txid",
                        foreign_keys="[UTXO.txid]")
    
    # Create indexes for common queries
    __table_args__ = (
        Index('idx_transactions_block_height', 'block_height'),
        Index('idx_transactions_created_at', 'created_at'),
    )
    
    def __repr__(self):
        return f"<Transaction(txid={self.txid}, block_height={self.block_height})>"
