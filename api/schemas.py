# Pydantic schemas for API requests and responses
from pydantic import BaseModel, field_serializer
from typing import List, Any, Optional, Dict
from datetime import datetime

class BlockResponse(BaseModel):
    hash: str
    height: int
    time: int
    tx: list
    tx_count: Optional[int] = None

class TransactionResponse(BaseModel):
    txid: str
    block_id: Optional[int]
    amount: Optional[float]
    time: Optional[str]

class NFTCollectionResponse(BaseModel):
    collection: str
    nft_count: int

class UserProfileResponse(BaseModel):
    address: str
    containers: Any
    created_at: Optional[str]

class NFTResponse(BaseModel):
    """NFT token data with full metadata.
    
    NFTs include users, containers, and object NFTs.
    - Users: token_type_name = 'user'
    - Containers: token_type_name = 'container'
    - Objects: token_type_name is null or other
    """
    token_id: str
    txid: Optional[str] = None  # Genesis transaction ID
    type: Optional[str] = None  # Script-derived: nft, mutable_nft, delegate
    token_type_name: Optional[str] = None  # Payload type: user, container, or null (object)
    name: Optional[str] = None
    ticker: Optional[str] = None
    description: Optional[str] = None
    nft_metadata: Optional[Dict[str, Any]] = None
    attrs: Optional[Dict[str, Any]] = None  # Custom attributes
    author: Optional[str] = None  # Author ref
    container: Optional[str] = None  # Container ref
    protocols: Optional[List[int]] = None
    protocol_type: Optional[int] = None
    immutable: Optional[bool] = None
    location: Optional[str] = None  # Linked payload ref
    owner: Optional[str] = None
    collection: Optional[str] = None  # Legacy field
    genesis_height: Optional[int] = None
    latest_height: Optional[int] = None
    reveal_txid: Optional[str] = None
    current_txid: Optional[str] = None
    current_vout: Optional[int] = None
    icon_mime_type: Optional[str] = None
    icon_url: Optional[str] = None
    holder_count: Optional[int] = None
    created_at: Optional[Any] = None
    updated_at: Optional[Any] = None
    
    @field_serializer('created_at', 'updated_at')
    def serialize_datetime(self, value):
        if isinstance(value, datetime):
            return value.isoformat()
        return value
    
    class Config:
        from_attributes = True


class FTTokenTableRowResponse(BaseModel):
    id: int
    ref: str
    token_type: str
    name: str
    ticker: Optional[str] = None
    height: Optional[int] = None
    created_at: Optional[Any] = None
    updated_at: Optional[Any] = None

    has_image: bool = False

    holder_count: Optional[int] = None
    circulating_supply: Optional[str] = None
    max_supply: Optional[str] = None
    burned_supply: Optional[str] = None
    difficulty: Optional[int] = None
    minted_supply: Optional[str] = None
    premine_percent: Optional[float] = None
    is_minable: bool = False
    mined_percent: Optional[float] = None

    @field_serializer('created_at', 'updated_at')
    def serialize_datetime(self, value):
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    class Config:
        from_attributes = True


class FTDuplicateRowResponse(BaseModel):
    ref: str
    name: str
    ticker: Optional[str] = None
    height: Optional[int] = None
    holder_count: int = 0
    has_image: bool = False


class FTDuplicatesResponse(BaseModel):
    canonical: FTDuplicateRowResponse
    duplicates: List[FTDuplicateRowResponse]
    is_canonical: bool

class GlyphTokenResponse(BaseModel):
    """Glyph token data with full metadata and ownership details.
    
    A glyph token is a token created on the blockchain using the Glyph protocol.
    Tokens can be fungible (FT), DMINT, DAT, or delegate types.
    """
    token_id: str = None
    txid: Optional[str] = None  # Genesis transaction ID
    type: Optional[str] = None  # Script-derived: ft, dmint, dat, delegate
    # Core metadata fields
    name: Optional[str] = None
    description: Optional[str] = None
    ticker: Optional[str] = None
    token_type_name: Optional[str] = None  # User-defined type (user/container/object)
    immutable: Optional[bool] = None
    license: Optional[str] = None
    attrs: Optional[Dict[str, Any]] = None  # Custom attributes
    location: Optional[str] = None  # Linked payload ref
    owner: Optional[str] = None
    token_metadata: Optional[Dict[str, Any]] = None
    # Protocol info
    protocols: Optional[List[int]] = None
    protocol_type: Optional[int] = None
    # Author/Container refs
    author: Optional[str] = None
    container: Optional[str] = None
    author_name: Optional[str] = None
    author_image_url: Optional[str] = None
    # Supply fields
    max_supply: Optional[int] = None
    current_supply: Optional[int] = None
    premine: Optional[int] = None
    circulating_supply: Optional[int] = None
    burned_supply: Optional[int] = None
    # DMINT fields
    difficulty: Optional[int] = None
    max_height: Optional[int] = None
    reward: Optional[int] = None
    num_contracts: Optional[int] = None
    contract_references: Optional[List[Dict[str, Any]]] = None
    # Icon fields
    icon_mime_type: Optional[str] = None
    icon_url: Optional[str] = None
    # Location tracking
    genesis_height: Optional[int] = None
    latest_height: Optional[int] = None
    reveal_txid: Optional[str] = None
    current_txid: Optional[str] = None
    current_vout: Optional[int] = None
    deploy_method: Optional[str] = None
    # Stats
    holder_count: Optional[int] = None
    supply_updated_at: Optional[Any] = None
    created_at: Optional[Any] = None
    updated_at: Optional[Any] = None
    
    @field_serializer('created_at', 'updated_at', 'supply_updated_at')
    def serialize_datetime(self, value):
        if isinstance(value, datetime):
            return value.isoformat()
        return value
    
    class Config:
        from_attributes = True

class WalletResponse(BaseModel):
    address: str
    balance: float
    txs: list

class TopWalletResponse(BaseModel):
    address: str
    balance: float

class TopGlyphUserResponse(BaseModel):
    address: str
    token_count: int

class TopGlyphContainerResponse(BaseModel):
    container: Any
    user_count: int

class HolderCountResponse(BaseModel):
    count: int

class TokenFileResponse(BaseModel):
    """Token file/image data for wallets and explorers."""
    id: int
    token_id: str
    token_type: str
    file_key: Optional[str] = None
    mime_type: Optional[str] = None
    file_data: Optional[str] = None  # Base64 encoded
    remote_url: Optional[str] = None
    file_hash: Optional[str] = None
    file_size: Optional[int] = None
    
    class Config:
        from_attributes = True

class ContainerResponse(BaseModel):
    """Container/collection data."""
    id: int
    container_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    owner: Optional[str] = None
    token_count: int = 0
    container_metadata: Optional[Dict[str, Any]] = None
    
    class Config:
        from_attributes = True


class GlyphResponse(BaseModel):
    """Unified Glyph model response (new glyphs table).
    
    This is the primary token response format for the new unified schema.
    Replaces separate GlyphToken and NFT responses.
    """
    id: int
    ref: str  # Primary identifier (36-byte hex)
    token_type: str  # NFT, FT, DAT, CONTAINER, USER
    
    # Core metadata
    name: str
    ticker: Optional[str] = None
    type: str  # User-defined type from payload
    description: str = ''
    immutable: Optional[bool] = None
    attrs: Optional[Dict[str, Any]] = None
    
    # Author and container refs
    author: str = ''
    container: str = ''
    
    # Container-specific
    is_container: bool = False
    
    # State tracking
    spent: bool = False
    fresh: bool = True
    melted: bool = False
    sealed: bool = False
    swap_pending: bool = False
    
    # Value and location
    value: Optional[int] = None
    location: Optional[str] = None
    reveal_outpoint: Optional[str] = None
    height: Optional[int] = None
    timestamp: Optional[int] = None
    
    # File data
    embed_type: Optional[str] = None
    embed_data: Optional[str] = None
    remote_type: Optional[str] = None
    remote_url: Optional[str] = None
    
    # Timestamps
    created_at: Optional[Any] = None
    updated_at: Optional[Any] = None
    
    @field_serializer('created_at', 'updated_at')
    def serialize_datetime(self, value):
        if isinstance(value, datetime):
            return value.isoformat()
        return value
    
    class Config:
        from_attributes = True


class GlyphActionResponse(BaseModel):
    """Glyph action history entry."""
    id: int
    ref: str
    type: str  # mint, transfer, melt, swap, update, etc.
    txid: str
    height: int
    timestamp: Optional[Any] = None
    action_metadata: Optional[Dict[str, Any]] = None
    
    @field_serializer('timestamp')
    def serialize_datetime(self, value):
        if isinstance(value, datetime):
            return value.isoformat()
        return value
    
    class Config:
        from_attributes = True


class GlyphStatsResponse(BaseModel):
    """Glyph statistics."""
    total: int
    containers: int
    unique_authors: int
    by_type: Dict[str, int]
