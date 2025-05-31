# /Users/radiant/Desktop/RXinDexer/src/api/nft_endpoints.py
# This file defines FastAPI endpoints for NFT metadata, collections, and transfers.
# It provides rich query capabilities for blockchain explorers and wallets.

from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, or_, and_
import logging

from src.models import (
    NFTMetadata, NFTCollection, NFTTransfer,
    get_db
)
from src.api.schemas import (
    NFTResponse, NFTCollectionResponse, NFTTransferResponse,
    NFTsListResponse, NFTCollectionsListResponse, NFTTransfersListResponse
)
from src.utils.cache import cache_decorator, CACHE_TTL
from src.utils.pagination import PaginationParams, paginate_results

# Remove the prefix here as it's already defined in main.py
router = APIRouter()
logger = logging.getLogger(__name__)

# NFT collections listing (defined first to avoid conflict with token_id pattern)
@router.get("/nfts/collections", response_model=NFTCollectionsListResponse, tags=["NFTs"])
@cache_decorator(ttl=CACHE_TTL)
async def list_collections(
    creator_address: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = Query("created_at", enum=["created_at", "name", "volume"]),
    sort_dir: str = Query("desc", enum=["asc", "desc"]),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    List and search NFT collections.
    
    Parameters:
    - creator_address: Filter by creator
    - search: Search in name and description
    - sort_by: Field to sort by
    - sort_dir: Sort direction
    - limit: Maximum number of collections to return
    - offset: Number of collections to skip (for pagination)
    
    Returns:
    - List of collections matching the filters with pagination info
    """
    # Build query
    query = db.query(NFTCollection)
    
    # Apply filters
    if creator_address:
        query = query.filter(NFTCollection.creator_address == creator_address)
    
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                NFTCollection.name.ilike(search_term),
                NFTCollection.description.ilike(search_term)
            )
        )
    
    # Count total items
    total_items = query.count()
    
    # Apply sorting
    if sort_by == "created_at":
        order_col = NFTCollection.created_at
    elif sort_by == "name":
        order_col = NFTCollection.name
    else:  # volume
        order_col = NFTCollection.total_volume
    
    if sort_dir == "asc":
        query = query.order_by(order_col)
    else:
        query = query.order_by(desc(order_col))
    
    # Apply pagination
    collections = query.offset(offset).limit(limit).all()
    
    # Construct response
    response = {
        "items": [
            {
                "collection_id": collection.collection_id,
                "name": collection.name,
                "description": collection.description,
                "creator_address": collection.creator_address,
                "banner_image_url": collection.banner_image_url,
                "item_count": db.query(func.count(NFTMetadata.id))
                    .filter(NFTMetadata.collection_id == collection.collection_id)
                    .scalar() or 0,
                "floor_price": collection.floor_price,
                "total_volume": collection.total_volume
            } for collection in collections
        ],
        "pagination": {
            "total": total_items,
            "offset": offset,
            "limit": limit
        }
    }
    
    return response

# NFT collection retrieval
@router.get("/nfts/collection/{collection_id}", response_model=NFTCollectionResponse, tags=["NFTs"])
@cache_decorator(ttl=CACHE_TTL)
async def get_nft_collection(
    collection_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    Retrieve information about an NFT collection and its contents.
    
    Parameters:
    - collection_id: The unique identifier of the collection
    - limit: Maximum number of NFTs to return (default: 50, max: 100)
    - offset: Number of NFTs to skip (for pagination)
    
    Returns:
    - Collection details and a list of NFTs in the collection
    """
    collection = db.query(NFTCollection).filter(
        NFTCollection.collection_id == collection_id
    ).first()
    
    if not collection:
        raise HTTPException(status_code=404, detail=f"Collection {collection_id} not found")
    
    # Count total NFTs in collection
    total_nfts = db.query(func.count(NFTMetadata.id)).filter(
        NFTMetadata.collection_id == collection_id
    ).scalar()
    
    # Get NFTs in collection with pagination
    nfts = db.query(NFTMetadata).filter(
        NFTMetadata.collection_id == collection_id
    ).order_by(NFTMetadata.name).offset(offset).limit(limit).all()
    
    # Construct response
    response = {
        "collection_id": collection.collection_id,
        "name": collection.name,
        "description": collection.description,
        "creator_address": collection.creator_address,
        "total_items": total_nfts,
        "banner_image_url": collection.banner_image_url,
        "external_url": collection.external_url,
        "floor_price": collection.floor_price,
        "total_volume": collection.total_volume,
        "metadata": collection.metadata,
        "nfts": [
            {
                "token_id": nft.token_id,
                "name": nft.name,
                "image_url": nft.image_url,
                "owner_address": nft.owner_address
            } for nft in nfts
        ],
        "pagination": {
            "total": total_nfts,
            "offset": offset,
            "limit": limit
        }
    }
    
    return response

# Single NFT retrieval
@router.get("/nfts/{token_id}", response_model=NFTResponse, tags=["NFTs"])
@cache_decorator(ttl=CACHE_TTL)
async def get_nft(
    token_id: str,
    db: Session = Depends(get_db)
):
    """
    Retrieve detailed information about a specific NFT by its token ID.
    
    Parameters:
    - token_id: The unique identifier of the NFT
    
    Returns:
    - Detailed NFT metadata including attributes and ownership information
    """
    nft = db.query(NFTMetadata).filter(NFTMetadata.token_id == token_id).first()
    if not nft:
        raise HTTPException(status_code=404, detail=f"NFT with token_id {token_id} not found")
    
    # Get collection information if available
    collection = None
    if nft.collection_id:
        collection = db.query(NFTCollection).filter(
            NFTCollection.collection_id == nft.collection_id
        ).first()
    
    # Get transfer history
    transfers = db.query(NFTTransfer).filter(
        NFTTransfer.token_id == token_id
    ).order_by(desc(NFTTransfer.block_height)).limit(10).all()
    
    # Construct response
    response = {
        "token_id": nft.token_id,
        "name": nft.name,
        "description": nft.description,
        "image_url": nft.image_url,
        "animation_url": nft.animation_url,
        "external_url": nft.external_url,
        "attributes": nft.attributes,
        "owner_address": nft.owner_address,
        "creator_address": nft.creator_address,
        "creation_height": nft.creation_height,
        "creation_txid": nft.creation_txid,
        "last_transfer_height": nft.last_transfer_height,
        "last_transfer_txid": nft.last_transfer_txid,
        "collection": {
            "collection_id": collection.collection_id,
            "name": collection.name,
            "description": collection.description
        } if collection else None,
        "media_metadata": nft.media_metadata,
        "recent_transfers": [
            {
                "transaction_id": t.transaction_id,
                "from_address": t.from_address,
                "to_address": t.to_address,
                "timestamp": t.timestamp,
                "value": t.value
            } for t in transfers
        ]
    }
    
    return response

# NFT collection retrieval
@router.get("/nfts/collection/{collection_id}", response_model=NFTCollectionResponse, tags=["NFTs"])
@cache_decorator(ttl=CACHE_TTL)
async def get_nft_collection(
    collection_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    Retrieve information about an NFT collection and its contents.
    
    Parameters:
    - collection_id: The unique identifier of the collection
    - limit: Maximum number of NFTs to return (default: 50, max: 100)
    - offset: Number of NFTs to skip (for pagination)
    
    Returns:
    - Collection details and a list of NFTs in the collection
    """
    collection = db.query(NFTCollection).filter(
        NFTCollection.collection_id == collection_id
    ).first()
    
    if not collection:
        raise HTTPException(status_code=404, detail=f"Collection {collection_id} not found")
    
    # Count total NFTs in collection
    total_nfts = db.query(func.count(NFTMetadata.id)).filter(
        NFTMetadata.collection_id == collection_id
    ).scalar()
    
    # Get NFTs in collection with pagination
    nfts = db.query(NFTMetadata).filter(
        NFTMetadata.collection_id == collection_id
    ).order_by(NFTMetadata.name).offset(offset).limit(limit).all()
    
    # Construct response
    response = {
        "collection_id": collection.collection_id,
        "name": collection.name,
        "description": collection.description,
        "creator_address": collection.creator_address,
        "total_items": total_nfts,
        "banner_image_url": collection.banner_image_url,
        "external_url": collection.external_url,
        "floor_price": collection.floor_price,
        "total_volume": collection.total_volume,
        "metadata": collection.metadata,
        "nfts": [
            {
                "token_id": nft.token_id,
                "name": nft.name,
                "image_url": nft.image_url,
                "owner_address": nft.owner_address
            } for nft in nfts
        ],
        "pagination": {
            "total": total_nfts,
            "offset": offset,
            "limit": limit
        }
    }
    
    return response

# NFT search/list endpoint
@router.get("/nfts", response_model=NFTsListResponse, tags=["NFTs"])
@cache_decorator(ttl=CACHE_TTL)
async def list_nfts(
    collection_id: Optional[str] = None,
    owner_address: Optional[str] = None,
    creator_address: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = Query("created_at", enum=["created_at", "name", "last_transfer"]),
    sort_dir: str = Query("desc", enum=["asc", "desc"]),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    Search and list NFTs with various filtering options.
    
    Parameters:
    - collection_id: Filter by collection ID
    - owner_address: Filter by current owner
    - creator_address: Filter by creator
    - search: Search in name and description
    - sort_by: Field to sort by (created_at, name, last_transfer)
    - sort_dir: Sort direction (asc, desc)
    - limit: Maximum number of NFTs to return
    - offset: Number of NFTs to skip (for pagination)
    
    Returns:
    - List of NFTs matching the filters with pagination info
    """
    # Build query
    query = db.query(NFTMetadata)
    
    # Apply filters
    if collection_id:
        query = query.filter(NFTMetadata.collection_id == collection_id)
    
    if owner_address:
        query = query.filter(NFTMetadata.owner_address == owner_address)
    
    if creator_address:
        query = query.filter(NFTMetadata.creator_address == creator_address)
    
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                NFTMetadata.name.ilike(search_term),
                NFTMetadata.description.ilike(search_term)
            )
        )
    
    # Count total items matching filters
    total_items = query.count()
    
    # Apply sorting
    if sort_by == "created_at":
        order_col = NFTMetadata.creation_height
    elif sort_by == "name":
        order_col = NFTMetadata.name
    else:  # last_transfer
        order_col = NFTMetadata.last_transfer_height
    
    if sort_dir == "asc":
        query = query.order_by(order_col)
    else:
        query = query.order_by(desc(order_col))
    
    # Apply pagination
    nfts = query.offset(offset).limit(limit).all()
    
    # Construct response
    response = {
        "items": [
            {
                "token_id": nft.token_id,
                "name": nft.name,
                "description": nft.description,
                "image_url": nft.image_url,
                "collection_id": nft.collection_id,
                "owner_address": nft.owner_address,
                "creator_address": nft.creator_address
            } for nft in nfts
        ],
        "pagination": {
            "total": total_items,
            "offset": offset,
            "limit": limit
        }
    }
    
    return response

# NFT transfer history for a specific token
@router.get("/tokens/{token_id}/transfers", response_model=NFTTransfersListResponse, tags=["Tokens"])
@cache_decorator(ttl=CACHE_TTL)
async def get_token_transfers(
    token_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    Get transfer history for a specific token.
    
    Parameters:
    - token_id: The token ID to get transfers for
    - limit: Maximum number of transfers to return
    - offset: Number of transfers to skip (for pagination)
    
    Returns:
    - List of transfers for the token with pagination info
    """
    # Verify token exists
    token = db.query(NFTMetadata).filter(NFTMetadata.token_id == token_id).first()
    if not token:
        raise HTTPException(status_code=404, detail=f"Token {token_id} not found")
    
    # Count total transfers
    total_transfers = db.query(func.count(NFTTransfer.id)).filter(
        NFTTransfer.token_id == token_id
    ).scalar()
    
    # Get transfers with pagination
    transfers = db.query(NFTTransfer).filter(
        NFTTransfer.token_id == token_id
    ).order_by(desc(NFTTransfer.block_height)).offset(offset).limit(limit).all()
    
    # Construct response
    response = {
        "token_id": token_id,
        "token_name": token.name,
        "total_transfers": total_transfers,
        "transfers": [
            {
                "transaction_id": t.transaction_id,
                "block_height": t.block_height,
                "block_hash": t.block_hash,
                "from_address": t.from_address,
                "to_address": t.to_address,
                "timestamp": t.timestamp,
                "value": t.value
            } for t in transfers
        ],
        "pagination": {
            "total": total_transfers,
            "offset": offset,
            "limit": limit
        }
    }
    
    return response

# List NFT collections
@router.get("/nfts/collections", response_model=NFTCollectionsListResponse, tags=["NFTs"])
@cache_decorator(ttl=CACHE_TTL)
async def list_collections(
    creator_address: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = Query("created_at", enum=["created_at", "name", "volume"]),
    sort_dir: str = Query("desc", enum=["asc", "desc"]),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    List and search NFT collections.
    
    Parameters:
    - creator_address: Filter by creator
    - search: Search in name and description
    - sort_by: Field to sort by
    - sort_dir: Sort direction
    - limit: Maximum number of collections to return
    - offset: Number of collections to skip (for pagination)
    
    Returns:
    - List of collections matching the filters with pagination info
    """
    # Build query
    query = db.query(NFTCollection)
    
    # Apply filters
    if creator_address:
        query = query.filter(NFTCollection.creator_address == creator_address)
    
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                NFTCollection.name.ilike(search_term),
                NFTCollection.description.ilike(search_term)
            )
        )
    
    # Count total items
    total_items = query.count()
    
    # Apply sorting
    if sort_by == "created_at":
        order_col = NFTCollection.created_at
    elif sort_by == "name":
        order_col = NFTCollection.name
    else:  # volume
        order_col = NFTCollection.total_volume
    
    if sort_dir == "asc":
        query = query.order_by(order_col)
    else:
        query = query.order_by(desc(order_col))
    
    # Apply pagination
    collections = query.offset(offset).limit(limit).all()
    
    # Get item counts for each collection
    collection_ids = [c.collection_id for c in collections]
    counts = {}
    
    if collection_ids:
        count_results = db.query(
            NFTMetadata.collection_id, 
            func.count(NFTMetadata.id).label('count')
        ).filter(
            NFTMetadata.collection_id.in_(collection_ids)
        ).group_by(NFTMetadata.collection_id).all()
        
        counts = {r[0]: r[1] for r in count_results}
    
    # Construct response
    response = {
        "items": [
            {
                "collection_id": collection.collection_id,
                "name": collection.name,
                "description": collection.description,
                "creator_address": collection.creator_address,
                "banner_image_url": collection.banner_image_url,
                "item_count": counts.get(collection.collection_id, 0),
                "floor_price": collection.floor_price,
                "total_volume": collection.total_volume
            } for collection in collections
        ],
        "pagination": {
            "total": total_items,
            "offset": offset,
            "limit": limit
        }
    }
    
    return response

# List tokens owned by an address
@router.get("/address/{address}/tokens", tags=["Address"])
async def get_address_tokens(
    address: str,
    token_type: Optional[str] = Query(None, enum=["nft", "fungible", "all"]),
    collection_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    Get all tokens owned by a specific address.
    
    Parameters:
    - address: The address to get tokens for
    - token_type: Type of tokens to return (nft, fungible, all)
    - collection_id: Filter by collection ID
    - limit: Maximum number of tokens to return
    - offset: Number of tokens to skip (for pagination)
    
    Returns:
    - List of tokens owned by the address with pagination info
    """
    # For now, we'll only implement NFT tokens
    token_type = token_type or "nft"
    
    if token_type == "all" or token_type == "nft":
        # Build query for NFTs
        query = db.query(NFTMetadata).filter(NFTMetadata.owner_address == address)
        
        if collection_id:
            query = query.filter(NFTMetadata.collection_id == collection_id)
        
        # Count total items
        total_nfts = query.count()
        
        # Get NFTs with pagination
        nfts = query.order_by(desc(NFTMetadata.last_transfer_height)).offset(offset).limit(limit).all()
        
        # Get collection info for the NFTs
        collection_ids = list(set([nft.collection_id for nft in nfts if nft.collection_id]))
        collections = {}
        
        if collection_ids:
            collection_results = db.query(NFTCollection).filter(
                NFTCollection.collection_id.in_(collection_ids)
            ).all()
            
            collections = {c.collection_id: c for c in collection_results}
        
        # Construct NFT items
        nft_items = [
            {
                "token_id": nft.token_id,
                "name": nft.name,
                "type": "nft",
                "image_url": nft.image_url,
                "collection": {
                    "collection_id": nft.collection_id,
                    "name": collections[nft.collection_id].name if nft.collection_id in collections else None
                } if nft.collection_id else None,
                "last_transfer_height": nft.last_transfer_height,
                "last_transfer_txid": nft.last_transfer_txid
            } for nft in nfts
        ]
        
        # Return combined response
        return {
            "address": address,
            "total_nfts": total_nfts,
            "items": nft_items,
            "pagination": {
                "total": total_nfts,
                "offset": offset,
                "limit": limit
            }
        }
    
    # For fungible tokens (to be implemented when we add fungible token support)
    return {
        "address": address,
        "total_tokens": 0,
        "items": [],
        "pagination": {
            "total": 0,
            "offset": offset,
            "limit": limit
        }
    }
