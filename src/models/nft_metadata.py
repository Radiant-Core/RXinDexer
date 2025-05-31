# /Users/radiant/Desktop/RXinDexer/src/models/nft_metadata_fixed.py
# This file defines the NFT metadata model for tracking non-fungible tokens on the Radiant blockchain.
# It stores token attributes, media content, and ownership information for NFT explorer functionality.

from datetime import datetime
from sqlalchemy import Column, String, Integer, ForeignKey, Boolean, DateTime, Index, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from .database import Base, JSONType

# Use JSONType which supports both PostgreSQL JSONB and SQLite TEXT as JSON
# This allows our model to work with both database engines
JsonColumn = JSONType


class NFTMetadata(Base):
    """
    Represents a non-fungible token (NFT) on the Radiant blockchain.
    Stores the token's metadata, attributes, and ownership information.
    """
    __tablename__ = "nft_metadata"

    # Primary key and identifiers
    id = Column(Integer, primary_key=True, autoincrement=True)
    token_id = Column(String(64), unique=True, nullable=False, index=True, 
                      doc="Unique identifier of the NFT token")
    
    # Token metadata
    name = Column(String(255), nullable=True, doc="Name of the NFT")
    description = Column(String(2048), nullable=True, doc="Description of the NFT")
    image_url = Column(String(1024), nullable=True, doc="URL to the NFT image")
    animation_url = Column(String(1024), nullable=True, doc="URL to the NFT animation/video")
    external_url = Column(String(1024), nullable=True, doc="URL to external website")
    media_type = Column(String(64), nullable=True, doc="MIME type of the media content")
    attributes = Column(JsonColumn, default={}, nullable=False, 
                       doc="Token attributes as JSONB: {trait_type: value}")
    
    # Ownership and creation info
    creator_address = Column(String(64), nullable=True, index=True, 
                           doc="Address that created/minted the NFT")
    owner_address = Column(String(64), nullable=True, index=True,
                          doc="Current owner address of the NFT")
    
    # Timestamps and transaction data
    created_at = Column(DateTime, server_default=func.now(), 
                       doc="When this NFT was first created")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), 
                       doc="When this NFT was last updated")
    creation_txid = Column(String(64), nullable=True, doc="Transaction ID where the NFT was created")
    creation_height = Column(Integer, nullable=True, doc="Block height where the NFT was created")
    last_transfer_txid = Column(String(64), nullable=True, doc="Transaction ID of the last transfer")
    last_transfer_height = Column(Integer, nullable=True, doc="Block height of the last transfer")
    
    # Collection relationship (optional)
    collection_id = Column(String(64), nullable=True, index=True, 
                          doc="Collection identifier this NFT belongs to")
    
    # Additional metadata
    token_data = Column(JsonColumn, default={}, nullable=False, 
                     doc="Additional arbitrary token data as JSONB")
    media_metadata = Column(JsonColumn, default={}, nullable=False,
                         doc="Metadata about media content")
    
    # Flags
    is_burned = Column(Boolean, default=False, nullable=False, 
                      doc="Whether this NFT has been burned/destroyed")
    
    # Indexes for common queries
    __table_args__ = (
        Index('idx_nft_creator_timestamp', creator_address, created_at),
        Index('idx_nft_owner_timestamp', owner_address, updated_at),
        Index('idx_nft_collection', collection_id),
    )
    
    def __repr__(self):
        """String representation of the NFT metadata"""
        return f"<NFTMetadata(token_id='{self.token_id}', name='{self.name}', owner='{self.owner_address}')>"


class NFTCollection(Base):
    """
    Represents a collection of NFTs with shared properties and creator.
    Used for grouping related NFTs and tracking collection statistics.
    """
    __tablename__ = "nft_collections"
    
    # Primary key and identifiers
    id = Column(Integer, primary_key=True, autoincrement=True)
    collection_id = Column(String(64), unique=True, nullable=False, index=True, 
                         doc="Unique identifier of the collection")
    
    # Collection metadata
    name = Column(String(255), nullable=True, doc="Name of the collection")
    description = Column(String(2048), nullable=True, doc="Description of the collection")
    creator_address = Column(String(64), nullable=True, index=True, 
                           doc="Address that created the collection")
    banner_image_url = Column(String(1024), nullable=True, doc="URL to collection banner image")
    external_url = Column(String(1024), nullable=True, doc="URL to collection external website")
    token_prefix = Column(String(64), nullable=True, doc="Common prefix used in collection tokens")
    
    # Collection statistics
    token_count = Column(Integer, default=0, nullable=False, 
                        doc="Number of NFTs in this collection")
    holder_count = Column(Integer, default=0, nullable=False,
                         doc="Number of unique holders of NFTs in this collection")
    floor_price = Column(String(32), nullable=True, 
                        doc="Lowest price of an NFT in this collection")
    total_volume = Column(String(32), default="0", nullable=False,
                          doc="Total volume traded for this collection")
    
    # Timestamps
    created_at = Column(DateTime, server_default=func.now(), 
                       doc="When this collection was first created")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), 
                       doc="When this collection was last updated")
    
    # Additional metadata
    collection_data = Column(JsonColumn, default={}, nullable=False, 
                           doc="Additional arbitrary collection data as JSONB")
    
    # Indexes for common queries
    __table_args__ = (
        Index('idx_collection_creator_timestamp', creator_address, created_at),
        Index('idx_collection_token_prefix', token_prefix),
    )
    
    def __repr__(self):
        """String representation of the NFT collection"""
        return f"<NFTCollection(collection_id='{self.collection_id}', name='{self.name}', token_count={self.token_count})>"


class NFTTransfer(Base):
    """
    Represents a transfer of an NFT from one address to another.
    Tracks the complete ownership history of NFTs.
    """
    __tablename__ = "nft_transfers"
    
    # Primary key and identifiers
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Transfer metadata
    token_id = Column(String(64), nullable=False, index=True,
                     doc="Token ID of the NFT being transferred")
    from_address = Column(String(64), nullable=True, index=True, 
                         doc="Sender address (null for mints)")
    to_address = Column(String(64), nullable=True, index=True, 
                       doc="Receiver address (null for burns)")
    
    # Transaction data
    transaction_id = Column(String(64), nullable=False, index=True, 
                          doc="Transaction ID where the transfer occurred")
    block_height = Column(Integer, nullable=False, index=True, 
                         doc="Block height where the transfer occurred")
    block_hash = Column(String(64), nullable=True, index=True,
                       doc="Hash of the block where the transfer occurred")
    timestamp = Column(DateTime, nullable=False, index=True, 
                      doc="Timestamp when the transfer occurred")
    
    # Price information (if applicable)
    value = Column(String(32), nullable=True, 
                  doc="Value/price of the NFT in this transfer (if it was a sale)")
    
    # Additional data
    transfer_data = Column(JsonColumn, default={}, nullable=False,
                          doc="Additional data about the transfer")
    
    # Unique constraint to prevent duplicate transfers
    __table_args__ = (
        UniqueConstraint('token_id', 'transaction_id', name='uq_nft_transfer_txid'),
        Index('idx_nft_transfer_time', block_height, timestamp),
    )
    
    def __repr__(self):
        """String representation of the NFT transfer"""
        return f"<NFTTransfer(token_id='{self.token_id}', from='{self.from_address}', to='{self.to_address}', block={self.block_height})>"
