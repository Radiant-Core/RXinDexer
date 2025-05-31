# /Users/radiant/Desktop/RXinDexer/src/models/utxo.py
# This file defines the UTXO model that represents unspent transaction outputs in the Radiant blockchain.
# It tracks the ownership, amount, and status of each UTXO for accurate balance calculations.

from sqlalchemy import Column, String, Integer, Numeric, Boolean, ForeignKey, Index
from sqlalchemy.sql import func
from sqlalchemy.types import TIMESTAMP
from .database import Base


class UTXO(Base):
    """
    Represents an Unspent Transaction Output (UTXO) in the Radiant blockchain.
    """
    __tablename__ = "utxos"

    # Primary key columns
    txid = Column(String(64), primary_key=True, doc="Transaction ID that created this UTXO")
    vout = Column(Integer, primary_key=True, doc="Output index in the transaction")
    
    # UTXO properties
    address = Column(String(64), nullable=False, index=True, doc="Address that owns this UTXO")
    amount = Column(Numeric(16, 8), nullable=False, doc="Amount of RXD in this UTXO")
    
    # Glyph token reference (if this UTXO contains a token)
    token_ref = Column(String(64), nullable=True, index=True, doc="Token reference if this UTXO contains a Glyph token")
    
    # UTXO status
    spent = Column(Boolean, default=False, doc="Whether this UTXO has been spent")
    spent_txid = Column(String(64), nullable=True, doc="Transaction ID that spent this UTXO (if spent)")
    
    # Block information
    block_height = Column(Integer, nullable=False, index=True, doc="Block height where this UTXO was created")
    block_hash = Column(String(64), nullable=False, doc="Block hash where this UTXO was created")
    
    # Timestamps
    created_at = Column(TIMESTAMP, server_default=func.now(), doc="Timestamp when this record was created")
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now(), doc="Timestamp when this record was last updated")

    # Indexes for performance
    __table_args__ = (
        Index('idx_utxo_address_spent', 'address', 'spent'),
        Index('idx_utxo_token_ref_spent', 'token_ref', 'spent'),
    )
    
    def __repr__(self):
        """String representation of the UTXO"""
        return f"<UTXO(txid='{self.txid}', vout={self.vout}, address='{self.address}', amount={self.amount})>"
