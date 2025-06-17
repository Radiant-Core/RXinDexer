# /Users/radiant/Desktop/RXinDexer/src/models/glyph_token.py
# This file defines the GlyphToken model that stores information about Glyph tokens on the Radiant blockchain.
# It tracks token type, metadata, and current ownership information for each token.

from sqlalchemy import Column, String, JSON, ForeignKey, Integer, DateTime, Index
from sqlalchemy.orm import relationship
from .database import Base

class GlyphToken(Base):
    """
    Represents a Glyph token on the Radiant blockchain.
    Glyph tokens can be fungible, non-fungible, or dmint types.
    Matches the schema defined in db_init.sql with proper relationships.
    """
    __tablename__ = "glyph_tokens"

    # Primary key - matches the database schema
    ref = Column(String(64), primary_key=True, doc="Unique reference ID for the token")
    
    # Token properties
    type = Column(String(20), nullable=False, index=True, 
                 doc="Token type: 'fungible', 'non-fungible', or 'dmint'")
    token_metadata = Column(JSON, nullable=True, doc="Token metadata as JSON")
    
    # Current location of the token (references the UTXO where this token is located)
    current_txid = Column(String(64), nullable=True, 
                         doc="Transaction ID where this token currently exists")
    current_vout = Column(Integer, nullable=True, 
                         doc="Output index in the transaction where this token exists")
    
    # Creation information
    genesis_txid = Column(String(64), nullable=False, 
                         doc="Transaction ID where this token was first created")
    genesis_block_height = Column(Integer, nullable=False, index=True, 
                                 doc="Block height where this token was created")
    
    # Timestamps
    created_at = Column(DateTime, nullable=False, server_default='now()',
                      doc="Timestamp when this record was created")
    updated_at = Column(DateTime, nullable=False, server_default='now()',
                       onupdate='now()', 
                       doc="Timestamp when this record was last updated")
    
    # Relationships
    current_utxo = relationship("UTXO",
                              primaryjoin="and_(GlyphToken.current_txid==UTXO.txid, "
                                          "GlyphToken.current_vout==UTXO.vout)",
                              foreign_keys=[current_txid, current_vout],
                              viewonly=True)
    
    # Indexes for performance - matching those in db_init.sql
    __table_args__ = (
        Index('ix_glyph_tokens_genesis_block_height', 'genesis_block_height'),
        Index('ix_glyph_tokens_type', 'type'),
    )
    
    def __repr__(self):
        """String representation of the GlyphToken"""
        return f"<GlyphToken(ref='{self.ref[:8]}...', type='{self.type}')>"
