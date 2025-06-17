# /Users/radiant/Desktop/RXinDexer/src/models/utxo.py
# This file defines the UTXO model that represents unspent transaction outputs in the Radiant blockchain.
# It tracks the ownership, amount, and status of each UTXO for accurate balance calculations.

from sqlalchemy import Column, String, Integer, Numeric, Boolean, ForeignKey, Index, DateTime
from sqlalchemy.orm import relationship
from .database import Base

class UTXO(Base):
    """
    Represents an Unspent Transaction Output (UTXO) in the Radiant blockchain.
    Matches the schema defined in db_init.sql with proper relationships.
    """
    __tablename__ = "utxos"

    # Primary key columns - matches the database schema
    txid = Column(String(64), ForeignKey("transactions.txid"), primary_key=True, 
                 doc="Transaction ID that created this UTXO")
    vout = Column(Integer, primary_key=True, doc="Output index in the transaction")
    
    # UTXO properties
    address = Column(String(64), nullable=False, index=True, doc="Address that owns this UTXO")
    amount = Column(Numeric(16, 8), nullable=False, doc="Amount of RXD in this UTXO")
    
    # Token reference (if this UTXO contains a token)
    token_ref = Column(String(64), nullable=True, index=True, 
                      doc="Token reference if this UTXO contains a token")
    
    # UTXO status
    spent = Column(Boolean, default=False, nullable=False, doc="Whether this UTXO has been spent")
    spent_txid = Column(String(64), nullable=True, doc="Transaction ID that spent this UTXO")
    
    # Block information
    block_height = Column(Integer, nullable=False, index=True, 
                         doc="Block height where this UTXO was created")
    block_hash = Column(String(64), nullable=False, 
                       doc="Block hash where this UTXO was created")
    
    # Timestamps
    created_at = Column(DateTime, nullable=False, server_default='now()',
                      doc="Timestamp when this record was created")
    updated_at = Column(DateTime, nullable=False, server_default='now()', 
                       onupdate='now()', doc="Timestamp when this record was last updated")
    
    # Relationships
    transaction = relationship("Transaction", back_populates="utxos",
                             foreign_keys=[txid])
    
    # Indexes for performance - matching those in db_init.sql
    __table_args__ = (
        Index('idx_utxo_address_spent', 'address', 'spent'),
        Index('idx_utxo_token_ref_spent', 'token_ref', 'spent'),
        Index('ix_utxos_address', 'address'),
        Index('ix_utxos_block_height', 'block_height'),
        Index('ix_utxos_token_ref', 'token_ref'),
    )
    
    def __repr__(self):
        """String representation of the UTXO"""
        status = "spent" if self.spent else "unspent"
        return f"<UTXO({self.txid[:8]}...:{self.vout}, {self.amount} RXD, {status})>"
