# /Users/radiant/Desktop/RXinDexer/src/api/token.py
# This file implements the API endpoints for Glyph token-related queries.
# It provides token metadata, ownership information, and transfer history.

import logging
import time
import traceback
from typing import Dict, List, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from src.models import get_db, GlyphToken, UTXO
from src.utils.pagination import PaginationParams, paginate_results
from src.utils.cache import get_cached, cache_result, cache_decorator, CACHE_TTL

# Import RPC client from the selector for development mode compatibility
from src.sync.rpc_selector import RadiantRPC

# Import security module
from src.api.security import get_api_key

# Create router with explicit API key dependency for ALL endpoints
router = APIRouter(
    dependencies=[Depends(get_api_key)]
)
logger = logging.getLogger(__name__)

@router.get("/{ref}")
@cache_decorator(ttl=CACHE_TTL)
async def get_token_info(
    ref: str,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key)
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
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key)
):
    """
    List all Glyph tokens with optional filtering.
    
    Args:
        token_type: Filter by token type
        pagination: Pagination parameters
        
    Returns:
        List of tokens matching the criteria
    """
    try:
        # Check if glyph_tokens table exists before attempting to query
        dialect = db.bind.dialect.name
        table_exists = False
        
        try:
            if dialect == 'postgresql':
                table_exists = db.execute(
                    text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'glyph_tokens')")
                ).scalar()
            elif dialect == 'sqlite':
                table_exists = db.execute(
                    text("SELECT EXISTS (SELECT name FROM sqlite_master WHERE type='table' AND name='glyph_tokens')")
                ).scalar()
            else:
                logger.warning(f"Unsupported dialect {dialect}, attempting to check table existence anyway")
                # Fallback approach - try querying the table, will raise exception if it doesn't exist
                db.query(GlyphToken).limit(1).all()
                table_exists = True
        except Exception as e:
            logger.error(f"Error checking if glyph_tokens table exists: {str(e)}")
            table_exists = False
        
        if not table_exists:
            logger.warning("glyph_tokens table does not exist, returning empty list")
            return {
                "tokens": [],
                "count": 0,
                "message": "Token data not yet available"
            }
        
        # Table exists, proceed with normal query
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
    except Exception as e:
        logger.error(f"Error in list_tokens: {str(e)}")
        logger.error(traceback.format_exc())
        
        # Return a graceful fallback response
        return {
            "tokens": [],
            "count": 0,
            "message": "Error retrieving token data: " + str(e),
            "error_type": type(e).__name__
        }

@router.get("/{ref}/history")
@cache_decorator(ttl=CACHE_TTL)
async def get_token_history(
    ref: str,
    pagination: PaginationParams = Depends(),
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key)
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

# Define with exact path matching that the test expects
@router.get("/stats", include_in_schema=True)
@cache_decorator(ttl=CACHE_TTL)
async def get_token_statistics(
    token_type: Optional[str] = Query(None, description="Filter by token type (fungible, non-fungible, dmint)"),
    db: Session = Depends(get_db)
):
    """
    Get overall token statistics
    
    Args:
        token_type: Optional filter by token type
        
    Returns:
        Statistics about tokens including counts, volume, and distribution
    """
    try:
        # Base query for all tokens
        query = db.query(GlyphToken)
        
        # Apply token type filter if provided
        if token_type:
            query = query.filter(GlyphToken.type == token_type)
        
        # Get total count
        total_tokens = query.count()
        
        # Get counts by type
        type_counts = {}
        for t_type in ["fungible", "non-fungible", "dmint"]:
            count = db.query(GlyphToken).filter(GlyphToken.type == t_type).count()
            type_counts[t_type] = count
        
        # Get unique holders count (simple approximation)
        unique_holders = db.query(UTXO.address).filter(
            UTXO.token_ref.isnot(None),
            UTXO.spent == False
        ).distinct().count()
        
        # Calculate basic statistics - always return valid data even when empty
        stats = {
            "total_tokens": total_tokens,
            "tokens_by_type": type_counts,
            "unique_holders": unique_holders,
            "latest_token": {
                "ref": "",
                "type": "",
                "genesis_txid": ""
            },
            "timestamp": int(time.time())  # Current timestamp as fallback
        }
        
        # Get most recent token
        latest_token = db.query(GlyphToken).order_by(
            GlyphToken.created_at.desc()
        ).first()
        
        if latest_token:
            stats["latest_token"] = {
                "ref": latest_token.ref,
                "type": latest_token.type,
                "genesis_txid": latest_token.genesis_txid
            }
            stats["timestamp"] = latest_token.created_at
            
        return stats
    except Exception as e:
        logger.error(f"Error getting token statistics: {str(e)}")
        raise HTTPException(status_code=500, detail="Error retrieving token statistics")
