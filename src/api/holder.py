# /Users/radiant/Desktop/RXinDexer/src/api/holder.py
# This file implements the API endpoints for holder-related queries.
# It provides wallet holder counts for RXD and Glyph tokens.

import logging
from typing import Dict, List, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from src.models import get_db, Holder
from src.utils.cache import get_cached, cache_result

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/count/rxd")
async def get_rxd_holder_count(
    min_balance: float = Query(0, description="Minimum RXD balance to be counted as a holder"),
    db: Session = Depends(get_db)
):
    """
    Get the count of unique RXD holders.
    
    Args:
        min_balance: Minimum RXD balance to be counted as a holder
        
    Returns:
        Count of addresses with balance >= min_balance
    """
    # Try to get from cache first
    cache_key = f"holders:rxd:{min_balance}"
    cached = get_cached(cache_key)
    if cached:
        return cached
    
    # Count holders with balance >= min_balance
    count = db.query(Holder).filter(Holder.rxd_balance >= min_balance).count()
    
    result = {
        "asset": "RXD",
        "min_balance": min_balance,
        "holder_count": count
    }
    
    # Cache the result
    cache_result(cache_key, result, ttl=300)  # Cache for 5 minutes
    
    return result

@router.get("/count/token/{ref}")
async def get_token_holder_count(
    ref: str,
    db: Session = Depends(get_db)
):
    """
    Get the count of unique holders for a specific token.
    
    Args:
        ref: Token reference
        
    Returns:
        Count of addresses holding the token
    """
    # Try to get from cache first
    cache_key = f"holders:token:{ref}"
    cached = get_cached(cache_key)
    if cached:
        return cached
    
    # Count UTXOs with this token reference that are unspent
    count = db.query(func.count(func.distinct(Holder.address))).filter(
        func.jsonb_exists(Holder.token_balances, ref)
    ).scalar()
    
    result = {
        "asset": ref,
        "holder_count": count or 0
    }
    
    # Cache the result
    cache_result(cache_key, result, ttl=300)  # Cache for 5 minutes
    
    return result

@router.get("/richlist/rxd")
async def get_rxd_richlist(
    limit: int = Query(100, description="Number of top holders to return"),
    db: Session = Depends(get_db)
):
    """
    Get the top RXD holders by balance.
    
    Args:
        limit: Number of top holders to return
        
    Returns:
        List of top holders with their addresses and balances
    """
    # Query top holders ordered by balance
    holders = db.query(
        Holder.address,
        Holder.rxd_balance
    ).filter(
        Holder.rxd_balance > 0
    ).order_by(
        Holder.rxd_balance.desc()
    ).limit(limit).all()
    
    # Format results
    richlist = []
    for holder in holders:
        richlist.append({
            "address": holder.address,
            "balance": str(holder.rxd_balance)
        })
    
    return {
        "asset": "RXD",
        "richlist": richlist
    }

@router.get("/stats")
async def get_holder_stats(
    db: Session = Depends(get_db)
):
    """
    Get general statistics about holders.
    
    Returns:
        Various statistics about RXD and token holders
    """
    # Try to get from cache first
    cache_key = "holders:stats"
    cached = get_cached(cache_key)
    if cached:
        return cached
    
    # Total addresses
    total_addresses = db.query(func.count(Holder.address)).scalar() or 0
    
    # RXD holders
    rxd_holders = db.query(func.count(Holder.address)).filter(Holder.rxd_balance > 0).scalar() or 0
    
    # Token holders (addresses that hold at least one token)
    token_holders = db.query(func.count(Holder.address)).filter(
        func.jsonb_object_length(Holder.token_balances) > 0
    ).scalar() or 0
    
    # Addresses with both RXD and tokens
    mixed_holders = db.query(func.count(Holder.address)).filter(
        Holder.rxd_balance > 0,
        func.jsonb_object_length(Holder.token_balances) > 0
    ).scalar() or 0
    
    result = {
        "total_addresses": total_addresses,
        "rxd_holders": rxd_holders,
        "token_holders": token_holders,
        "mixed_holders": mixed_holders
    }
    
    # Cache the result
    cache_result(cache_key, result, ttl=600)  # Cache for 10 minutes
    
    return result
