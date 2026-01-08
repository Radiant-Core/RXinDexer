"""
GraphQL types for RXinDexer API.

Defines Strawberry types for database models.
"""

import strawberry
from typing import Optional, List
from datetime import datetime


@strawberry.type
class BlockType:
    """GraphQL type for blocks."""
    id: int
    hash: str
    height: int
    timestamp: datetime
    tx_count: int = 0


@strawberry.type
class TransactionType:
    """GraphQL type for transactions."""
    id: int
    txid: str
    version: int
    locktime: int
    block_id: Optional[int]
    block_height: int
    created_at: Optional[datetime]


@strawberry.type
class UTXOType:
    """GraphQL type for UTXOs."""
    id: int
    txid: str
    vout: int
    address: Optional[str]
    value: int  # In satoshis
    spent: bool
    spent_in_txid: Optional[str]
    script_type: Optional[str]
    contract_type: Optional[str]
    glyph_ref: Optional[str]


@strawberry.type
class GlyphType:
    """GraphQL type for Glyphs (tokens - NFTs, FTs, Containers)."""
    id: int
    ref: str
    token_type: str
    name: str
    ticker: Optional[str]
    type: str
    description: str
    immutable: Optional[bool]
    author: str
    container: str
    is_container: bool
    spent: bool
    fresh: bool
    melted: bool
    sealed: bool
    value: Optional[int]
    burned_supply: int
    location: Optional[str]
    height: Optional[int]
    timestamp: Optional[int]
    embed_type: Optional[str]
    remote_url: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


@strawberry.type
class TokenStatsType:
    """GraphQL type for token statistics."""
    total_tokens: int
    total_nfts: int
    total_fts: int
    total_containers: int
    total_users: int


@strawberry.type
class BlockchainStatsType:
    """GraphQL type for blockchain statistics."""
    latest_block_height: int
    total_transactions: int
    total_utxos: int
    total_tokens: int
    sync_status: str


@strawberry.type
class PaginationInfo:
    """Pagination information for list queries."""
    total: int
    limit: int
    offset: int
    has_next: bool
    has_prev: bool


@strawberry.type
class GlyphConnection:
    """Paginated list of glyphs."""
    items: List[GlyphType]
    pagination: PaginationInfo


@strawberry.type
class BlockConnection:
    """Paginated list of blocks."""
    items: List[BlockType]
    pagination: PaginationInfo


@strawberry.type
class TransactionConnection:
    """Paginated list of transactions."""
    items: List[TransactionType]
    pagination: PaginationInfo
