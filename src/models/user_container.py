# /Users/radiant/Desktop/RXinDexer/src/models/user_container.py
# This file defines models for user profiles and containers in the Radiant blockchain.
# It tracks user identities, activity metrics, and container relationships for explorer functionality.

from datetime import datetime
from sqlalchemy import Column, String, Integer, ForeignKey, Boolean, DateTime, Index, UniqueConstraint, Table
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy.orm import relationship

from .database import Base, JSONType, ArrayType

# Use custom types for cross-database compatibility
JsonColumn = JSONType
StringArrayColumn = ArrayType(String)


# Association table for many-to-many relationship between containers and contents
container_contents = Table(
    'container_contents',
    Base.metadata,
    Column('container_id', Integer, ForeignKey('containers.id', ondelete='CASCADE'), primary_key=True),
    Column('content_id', Integer, ForeignKey('containers.id', ondelete='CASCADE'), primary_key=True),
    Column('position', Integer, nullable=True),
    Column('added_at', DateTime, server_default=func.now())
)


# Association table for many-to-many relationship between users and addresses
user_addresses = Table(
    'user_addresses',
    Base.metadata,
    Column('user_id', Integer, ForeignKey('user_profiles.id', ondelete='CASCADE'), primary_key=True),
    Column('address', String(64), primary_key=True),
    Column('linked_at', DateTime, server_default=func.now()),
    Column('is_primary', Boolean, default=False)
)


class UserProfile(Base):
    """
    Represents a user profile on the Radiant blockchain.
    Links multiple addresses to a single user identity and tracks activity metrics.
    """
    __tablename__ = "user_profiles"
    
    # Primary key and identifiers
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), unique=True, nullable=False, index=True,
                    doc="Unique identifier of the user profile")
    
    # Profile information
    username = Column(String(64), nullable=True, unique=True, 
                     doc="Public username for the profile")
    display_name = Column(String(255), nullable=True,
                         doc="Display name for the profile")
    bio = Column(String(1024), nullable=True,
                doc="User biography/description")
    avatar_url = Column(String(1024), nullable=True,
                       doc="URL to avatar image")
    
    # Profile metadata
    profile_data = Column(JsonColumn, default={}, nullable=False,
                         doc="Additional profile metadata as JSONB")
    
    # Activity metrics
    nft_count = Column(Integer, default=0, nullable=False,
                      doc="Number of NFTs owned by this user")
    token_count = Column(Integer, default=0, nullable=False,
                        doc="Number of different tokens owned by this user")
    container_count = Column(Integer, default=0, nullable=False,
                           doc="Number of containers owned by this user")
    first_activity = Column(DateTime, nullable=True,
                           doc="Timestamp of first blockchain activity")
    last_activity = Column(DateTime, nullable=True,
                          doc="Timestamp of last blockchain activity")
    
    # Timestamps
    created_at = Column(DateTime, server_default=func.now(),
                       doc="When this profile was first created")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(),
                       doc="When this profile was last updated")
    
    # Verification and status
    is_verified = Column(Boolean, default=False, nullable=False,
                        doc="Whether this profile is verified")
    status = Column(String(32), default="active", nullable=False,
                   doc="Status of the profile: active, inactive, suspended")
    
    # Relationships
    # We'll handle the addresses manually through the user_addresses table
    # instead of using a relationship
    owned_containers = relationship("Container", back_populates="owner")
    
    def __repr__(self):
        """String representation of the user profile"""
        return f"<UserProfile(user_id='{self.user_id}', username='{self.username}')>"


class Container(Base):
    """
    Represents a container on the Radiant blockchain.
    Containers can hold NFTs, tokens, or other containers in nested hierarchies.
    """
    __tablename__ = "containers"
    
    # Primary key and identifiers
    id = Column(Integer, primary_key=True, autoincrement=True)
    container_id = Column(String(64), unique=True, nullable=False, index=True,
                         doc="Unique identifier of the container")
    
    # Container metadata
    name = Column(String(255), nullable=True,
                 doc="Name of the container")
    description = Column(String(2048), nullable=True,
                        doc="Description of the container")
    container_type = Column(String(32), nullable=False,
                           doc="Type of container: collection, folder, gallery, etc.")
    
    # Content information
    content_count = Column(Integer, default=0, nullable=False,
                          doc="Number of items in this container")
    content_types = Column(StringArrayColumn, default=[],
                          doc="Types of content in this container")
    
    # Ownership and permissions
    owner_id = Column(Integer, ForeignKey('user_profiles.id', ondelete='SET NULL'),
                     nullable=True, index=True)
    owner_address = Column(String(64), nullable=True, index=True,
                          doc="Address that owns this container")
    is_public = Column(Boolean, default=True, nullable=False,
                      doc="Whether this container is publicly viewable")
    
    # Timestamps and transaction data
    created_at = Column(DateTime, server_default=func.now(),
                       doc="When this container was first created")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(),
                       doc="When this container was last updated")
    creation_txid = Column(String(64), nullable=True,
                          doc="Transaction ID where the container was created")
    
    # Container metadata
    container_data = Column(JsonColumn, default={}, nullable=False,
                          doc="Additional container data as JSONB")
    
    # Relationships
    owner = relationship("UserProfile", back_populates="owned_containers")
    parent_containers = relationship(
        "Container",
        secondary=container_contents,
        primaryjoin=id==container_contents.c.content_id,
        secondaryjoin=id==container_contents.c.container_id,
        backref="contents"
    )
    
    # Indexes for common queries
    __table_args__ = (
        Index('idx_container_owner_type', owner_address, container_type),
        Index('idx_container_public', is_public),
    )
    
    def __repr__(self):
        """String representation of the container"""
        return f"<Container(container_id='{self.container_id}', name='{self.name}', type='{self.container_type}')>"


class ContainerHistory(Base):
    """
    Tracks the history of changes to containers including content additions/removals.
    Provides an audit trail for container modifications.
    """
    __tablename__ = "container_history"
    
    # Primary key and identifiers
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Container reference
    container_id = Column(Integer, ForeignKey('containers.id', ondelete='CASCADE'),
                         nullable=False, index=True)
    
    # Action information
    action_type = Column(String(32), nullable=False, index=True,
                        doc="Type of action: create, add_content, remove_content, update, delete")
    content_id = Column(String(64), nullable=True,
                       doc="ID of the content affected (if applicable)")
    content_type = Column(String(32), nullable=True,
                         doc="Type of content affected: nft, token, container")
    
    # Transaction data
    txid = Column(String(64), nullable=True, index=True,
                 doc="Transaction ID where the action occurred")
    block_height = Column(Integer, nullable=True, index=True,
                         doc="Block height where the action occurred")
    timestamp = Column(DateTime, nullable=False, index=True,
                      doc="Timestamp when the action occurred")
    
    # Actor information
    actor_address = Column(String(64), nullable=True, index=True,
                          doc="Address that performed the action")
    
    # Additional data
    history_data = Column(JsonColumn, default={}, nullable=False,
                        doc="Additional action data as JSONB")
    
    # Indexes for common queries
    __table_args__ = (
        Index('idx_container_history_time', container_id, timestamp),
    )
    
    def __repr__(self):
        """String representation of the container history entry"""
        return f"<ContainerHistory(container_id={self.container_id}, action='{self.action_type}', time='{self.timestamp}')>"
