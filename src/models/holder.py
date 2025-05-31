# /Users/radiant/Desktop/RXinDexer/src/models/holder.py
# This file defines the Holder model that tracks addresses and their balances.
# It stores RXD and token balances for wallet holder counting and balance queries.

from sqlalchemy import Column, String, Numeric, JSON, Index
from sqlalchemy.sql import func
from sqlalchemy.types import TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB
from .database import Base, JSONType

# Use custom JSON type for SQLite compatibility
JsonColumn = JSONType


class Holder(Base):
    """
    Represents a wallet address that holds RXD or Glyph tokens.
    Used for tracking balances and counting unique holders.
    """
    __tablename__ = "holders"

    # Primary key
    address = Column(String(64), primary_key=True, doc="Wallet address")
    
    # Balances
    rxd_balance = Column(Numeric(38, 8), default=0, nullable=False, doc="Current RXD balance - supports values up to 10^30")
    token_balances = Column(JsonColumn, default={}, nullable=False, doc="Token balances as JSON: {token_ref: amount}")
    
    # Timestamps
    first_seen_at = Column(TIMESTAMP, server_default=func.now(), doc="When this address was first seen")
    last_updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now(), doc="When balances were last updated")
    
    # Indexes for performance
    __table_args__ = (
        Index('idx_holder_rxd_balance', 'rxd_balance'),
    )
    
    def __repr__(self):
        """String representation of the Holder"""
        return f"<Holder(address='{self.address}', rxd_balance={self.rxd_balance})>"
