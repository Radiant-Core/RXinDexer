# /Users/radiant/Desktop/RXinDexer/src/api/schemas.py
# This file defines Pydantic schemas for API request/response validation.
# It ensures consistent data formatting for all API endpoints.

from typing import List, Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel, Field


# ---- NFT Schemas ----

class NFTBase(BaseModel):
    """Base schema for NFT responses"""
    token_id: str
    name: str
    description: Optional[str] = None
    image_url: Optional[str] = None
    owner_address: Optional[str] = None
    creator_address: Optional[str] = None
    
    class Config:
        orm_mode = True


class NFTCollectionBase(BaseModel):
    """Base schema for NFT collection responses"""
    collection_id: str
    name: str
    description: Optional[str] = None
    
    class Config:
        orm_mode = True


class NFTTransferBase(BaseModel):
    """Base schema for NFT transfer responses"""
    transaction_id: str
    from_address: Optional[str] = None
    to_address: Optional[str] = None
    timestamp: datetime
    value: Optional[str] = None
    
    class Config:
        orm_mode = True


class Pagination(BaseModel):
    """Pagination information"""
    total: int
    offset: int
    limit: int


class NFTTransferDetail(NFTTransferBase):
    """Detailed NFT transfer information"""
    block_height: int
    block_hash: str


class NFTAttributeItem(BaseModel):
    """Individual NFT attribute"""
    trait_type: str
    value: Any


class NFTCollectionSummary(BaseModel):
    """Summary information about an NFT collection"""
    collection_id: str
    name: str
    description: Optional[str] = None


class NFTTransfer(BaseModel):
    """NFT transfer record"""
    transaction_id: str
    from_address: Optional[str] = None
    to_address: Optional[str] = None
    timestamp: datetime
    value: Optional[str] = None


class NFTResponse(BaseModel):
    """Detailed NFT response"""
    token_id: str
    name: str
    description: Optional[str] = None
    image_url: Optional[str] = None
    animation_url: Optional[str] = None
    external_url: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None
    owner_address: Optional[str] = None
    creator_address: Optional[str] = None
    creation_height: int
    creation_txid: str
    last_transfer_height: int
    last_transfer_txid: str
    collection: Optional[NFTCollectionSummary] = None
    media_metadata: Optional[Dict[str, Any]] = None
    recent_transfers: List[NFTTransfer]


class NFTCollectionItem(BaseModel):
    """NFT item in a collection"""
    token_id: str
    name: str
    image_url: Optional[str] = None
    owner_address: Optional[str] = None


class NFTCollectionResponse(BaseModel):
    """Detailed NFT collection response"""
    collection_id: str
    name: str
    description: Optional[str] = None
    creator_address: Optional[str] = None
    total_items: int
    banner_image_url: Optional[str] = None
    external_url: Optional[str] = None
    floor_price: Optional[str] = None
    total_volume: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    nfts: List[NFTCollectionItem]
    pagination: Pagination


class NFTSummary(BaseModel):
    """Summary information about an NFT"""
    token_id: str
    name: str
    description: Optional[str] = None
    image_url: Optional[str] = None
    collection_id: Optional[str] = None
    owner_address: Optional[str] = None
    creator_address: Optional[str] = None


class NFTsListResponse(BaseModel):
    """Response for listing multiple NFTs"""
    items: List[NFTSummary]
    pagination: Pagination


class NFTCollectionSummary(BaseModel):
    """Summary information about an NFT collection"""
    collection_id: str
    name: str
    description: Optional[str] = None
    creator_address: Optional[str] = None
    banner_image_url: Optional[str] = None
    item_count: int
    floor_price: Optional[str] = None
    total_volume: Optional[str] = None


class NFTCollectionsListResponse(BaseModel):
    """Response for listing multiple NFT collections"""
    items: List[NFTCollectionSummary]
    pagination: Pagination


class NFTTransfersListResponse(BaseModel):
    """Response for listing NFT transfers"""
    token_id: str
    token_name: str
    total_transfers: int
    transfers: List[NFTTransferDetail]
    pagination: Pagination


class NFTTransferResponse(BaseModel):
    """Detailed response for a single NFT transfer"""
    token_id: str
    token_name: str
    transaction_id: str
    from_address: Optional[str] = None
    to_address: Optional[str] = None
    timestamp: datetime
    value: Optional[str] = None
    block_height: int
    block_hash: str
    transaction_fee: Optional[str] = None
    confirmed: bool
    metadata: Optional[Dict[str, Any]] = None


# ---- Token Schemas ----

class TokenBase(BaseModel):
    """Base schema for token responses"""
    token_id: str
    name: str
    type: str  # "nft" or "fungible"
    image_url: Optional[str] = None


class AddressTokensResponse(BaseModel):
    """Response for listing tokens owned by an address"""
    address: str
    total_nfts: int
    items: List[Dict[str, Any]]
    pagination: Pagination


# ---- User Profile Schemas ----

class UserProfileBase(BaseModel):
    """Base schema for user profile responses"""
    user_id: str
    username: Optional[str] = None
    display_name: Optional[str] = None
    
    class Config:
        orm_mode = True


class AddressLink(BaseModel):
    """Address linked to a user profile"""
    address: str
    linked_at: datetime
    is_primary: bool


class UserProfileDetail(UserProfileBase):
    """Detailed user profile information"""
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    nft_count: int
    token_count: int
    container_count: int
    first_activity: Optional[datetime] = None
    last_activity: Optional[datetime] = None
    is_verified: bool
    status: str
    addresses: List[str]
    profile_metadata: Optional[Dict[str, Any]] = None


class UserProfileSummary(UserProfileBase):
    """Summary information about a user profile"""
    avatar_url: Optional[str] = None
    nft_count: int
    status: str


class UserProfilesListResponse(BaseModel):
    """Response for listing user profiles"""
    items: List[UserProfileSummary]
    pagination: Pagination


class UserProfileResponse(BaseModel):
    """Detailed user profile response"""
    user_id: str
    username: Optional[str] = None
    display_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    nft_count: int
    token_count: int
    container_count: int
    first_activity: Optional[datetime] = None
    last_activity: Optional[datetime] = None
    is_verified: bool
    status: str
    addresses: List[AddressLink]
    profile_metadata: Optional[Dict[str, Any]] = None
    owned_containers: List[Dict[str, Any]]
    recent_activity: List[Dict[str, Any]]


# ---- Container Schemas ----

class ContainerBase(BaseModel):
    """Base schema for container responses"""
    container_id: str
    name: str
    container_type: str
    
    class Config:
        orm_mode = True


class ContainerContent(BaseModel):
    """Content item in a container"""
    content_id: str
    content_type: str
    name: Optional[str] = None
    image_url: Optional[str] = None


class ContainerSummary(ContainerBase):
    """Summary information about a container"""
    owner_address: Optional[str] = None
    content_count: int
    is_public: bool


class ContainersListResponse(BaseModel):
    """Response for listing containers"""
    items: List[ContainerSummary]
    pagination: Pagination


class ContainerHistoryItem(BaseModel):
    """Container history entry"""
    action_type: str
    content_id: Optional[str] = None
    content_type: Optional[str] = None
    timestamp: datetime
    actor_address: Optional[str] = None
    txid: Optional[str] = None


class ContainerResponse(BaseModel):
    """Detailed container response"""
    container_id: str
    name: str
    description: Optional[str] = None
    container_type: str
    content_count: int
    content_types: List[str]
    owner_address: Optional[str] = None
    owner_profile: Optional[UserProfileSummary] = None
    is_public: bool
    created_at: datetime
    updated_at: datetime
    creation_txid: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    contents: List[ContainerContent]
    history: List[ContainerHistoryItem]
    pagination: Pagination


class ContainerContentsResponse(BaseModel):
    """Response for listing container contents"""
    container_id: str
    name: str
    container_type: str
    total_contents: int
    contents: List[ContainerContent]
    pagination: Pagination


# ---- Analytics Schemas ----

class TimeSeriesDataPoint(BaseModel):
    """Single data point in a time series"""
    timestamp: datetime
    value: float
    count: Optional[int] = None
    sum: Optional[float] = None
    avg: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None


class TimeSeriesResponse(BaseModel):
    """Response for time series data"""
    metric_type: str
    metric_scope: Optional[str] = None
    interval: str
    data: List[TimeSeriesDataPoint]


class RichListEntry(BaseModel):
    """Entry in a rich list"""
    address: str
    balance: str
    rank: int
    percentage: Optional[float] = None
    balance_change: Optional[str] = None
    rank_change: Optional[int] = None


class RichListResponse(BaseModel):
    """Response for rich list data"""
    token_type: str
    token_id: Optional[str] = None
    timestamp: datetime
    entries: List[RichListEntry]
    pagination: Pagination


class TokenDistributionGroup(BaseModel):
    """Distribution group for token holdings"""
    group_key: str
    address_count: int
    total_balance: str
    percentage: float
    address_count_change: Optional[int] = None
    balance_change: Optional[str] = None
    percentage_change: Optional[float] = None


class TokenDistributionResponse(BaseModel):
    """Response for token distribution data"""
    token_type: str
    token_id: Optional[str] = None
    timestamp: datetime
    group_type: str
    groups: List[TokenDistributionGroup]
