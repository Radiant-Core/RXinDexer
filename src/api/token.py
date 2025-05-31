# /Users/radiant/Desktop/RXinDexer/src/api/token.py
# This file implements the API endpoints for Glyph token-related queries.
# It provides token metadata, ownership information, and transfer history.

import logging
from typing import Dict, List, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.models import get_db, GlyphToken, UTXO
from src.utils.pagination import PaginationParams, paginate_results
from src.utils.cache import get_cached, cache_result, cache_decorator, CACHE_TTL

# Import RPC client from the selector for development mode compatibility
from src.sync.rpc_selector import RadiantRPC

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/{ref}")
@cache_decorator(ttl=CACHE_TTL)
async def get_token_info(
    ref: str,
    db: Session = Depends(get_db)
):
    """
    Get information about a specific Glyph token.
    
    Args:
        ref: Token reference
        
    Returns:
        Token metadata and current status
    """
    
    # Query the token
    token = db.query(GlyphToken).filter(GlyphToken.ref == ref).first()
    
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    
    # Get current owner
    current_owner = None
    if token.current_txid and token.current_vout is not None:
        utxo = db.query(UTXO).filter(
            UTXO.txid == token.current_txid,
            UTXO.vout == token.current_vout,
            UTXO.spent == False
        ).first()
        
        if utxo:
            current_owner = utxo.address
    
    # Construct response
    result = {
        "ref": token.ref,
        "type": token.type,
        "metadata": token.metadata,
        "genesis_txid": token.genesis_txid,
        "genesis_block_height": token.genesis_block_height,
        "current_owner": current_owner
    }
    
    return result

@router.get("/")
@cache_decorator(ttl=CACHE_TTL)
async def list_tokens(
    token_type: Optional[str] = Query(None, description="Filter by token type (fungible, non-fungible, dmint)"),
    pagination: PaginationParams = Depends(),
    db: Session = Depends(get_db)
):
    """
    List all Glyph tokens with optional filtering.
    
    Args:
        token_type: Filter by token type
        pagination: Pagination parameters
        
    Returns:
        List of tokens matching the criteria
    """
    # Build query
    query = db.query(GlyphToken)
    
    if token_type:
        query = query.filter(GlyphToken.type == token_type)
    
    # Apply pagination
    results, pagination_data = paginate_results(query, pagination)
    
    # Format tokens
    tokens = []
    for token in results:
        tokens.append({
            "ref": token.ref,
            "type": token.type,
            "genesis_block_height": token.genesis_block_height
        })
    
    return {
        "tokens": tokens,
        "pagination": pagination_data
    }

@router.get("/{ref}/history")
@cache_decorator(ttl=CACHE_TTL)
async def get_token_history(
    ref: str,
    pagination: PaginationParams = Depends(),
    db: Session = Depends(get_db)
):
    """
    Get the transfer history of a token.
    
    Args:
        ref: Token reference
        pagination: Pagination parameters
        
    Returns:
        List of transactions involving the token
    """
    # Check if token exists
    token = db.query(GlyphToken).filter(GlyphToken.ref == ref).first()
    
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    
    # Get all UTXOs for this token
    query = db.query(UTXO).filter(UTXO.token_ref == ref)
    
    # Apply pagination
    results, pagination_data = paginate_results(query, pagination)
    
    # Format history
    history = []
    for utxo in results:
        history.append({
            "txid": utxo.txid,
            "vout": utxo.vout,
            "address": utxo.address,
            "block_height": utxo.block_height,
            "spent": utxo.spent,
            "spent_txid": utxo.spent_txid
        })
    
    return {
        "ref": ref,
        "history": history,
        "pagination": pagination_data
    }
