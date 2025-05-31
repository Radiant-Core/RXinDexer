# /Users/radiant/Desktop/RXinDexer/src/models/glyph_token.py
# This file defines the GlyphToken model that stores information about Glyph tokens on the Radiant blockchain.
# It tracks token type, metadata, and current ownership information for each token.

from sqlalchemy import Column, String, JSON, ForeignKey, Integer
from sqlalchemy.sql import func
from sqlalchemy.types import TIMESTAMP
from .database import Base


class GlyphToken(Base):
    """
    Represents a Glyph token on the Radiant blockchain.
    Glyph tokens can be fungible, non-fungible, or dmint types.
    """
    __tablename__ = "glyph_tokens"

    # Primary key
    ref = Column(String(64), primary_key=True, doc="Unique reference ID for the token")
    
    # Token properties
    type = Column(String(20), nullable=False, index=True, doc="Token type: 'fungible', 'non-fungible', or 'dmint'")
    token_metadata = Column(JSON, nullable=True, doc="Token metadata as JSON")
    
    # Current location of the token
    current_txid = Column(String(64), nullable=True, doc="Current transaction ID where this token exists")
    current_vout = Column(Integer, nullable=True, doc="Current output index in the transaction")
    
    # Creation information
    genesis_txid = Column(String(64), nullable=False, doc="Transaction ID where this token was first created")
    genesis_block_height = Column(Integer, nullable=False, index=True, doc="Block height where this token was created")
    
    # Timestamps
    created_at = Column(TIMESTAMP, server_default=func.now(), doc="Timestamp when this record was created")
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now(), doc="Timestamp when this record was last updated")
    
    def __repr__(self):
        """String representation of the GlyphToken"""
        return f"<GlyphToken(ref='{self.ref}', type='{self.type}')>"
