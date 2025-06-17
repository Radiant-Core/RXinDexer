# /Users/radiant/Desktop/RXinDexer/src/api/address.py
# This file implements the API endpoints for address-related queries.
# It provides balance information and transaction history for wallet addresses.

import logging
from typing import Dict, List, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from decimal import Decimal

# Import security module for API key authentication
from src.api.security import get_api_key

from src.models import get_db, UTXO, Holder
from src.utils.pagination import PaginationParams, paginate_results
from src.utils.cache import get_cached, cache_result

# Import RPC client from the selector for development mode compatibility
from src.sync.rpc_selector import RadiantRPC

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/{address}/balance")
async def get_address_balance(
    address: str, 
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key)
):
    """
    Get the current balance for an address.
    
    Args:
        address: Wallet address
        
    Returns:
        Dictionary with RXD and token balances
    """
    # Try to get from cache first
    cache_key = f"balance:{address}"
    cached = get_cached(cache_key)
    if cached:
        return cached
    
    # Get the holder record if it exists
    holder = db.query(Holder).filter(Holder.address == address).first()
    
    if not holder:
        # Check if address has any UTXOs
        utxo_count = db.query(UTXO).filter(UTXO.address == address).count()
        if utxo_count == 0:
            raise HTTPException(status_code=404, detail="Address not found")
        
        # Address exists but no holder record yet
        return {
            "address": address,
            "rxd_balance": "0",
            "glyph_tokens": {}
        }
    
    # Construct response
    result = {
        "address": address,
        "rxd_balance": str(holder.rxd_balance),
        "glyph_tokens": holder.token_balances
    }
    
    # Cache the result
    cache_result(cache_key, result, ttl=300)  # Cache for 5 minutes
    
    return result

@router.get("/{address}/utxos")
async def get_address_utxos(
    address: str,
    unspent_only: bool = Query(True, description="Show only unspent UTXOs"),
    pagination: PaginationParams = Depends(),
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key)
):
    """
    Get UTXOs for an address.
    
    Args:
        address: Wallet address
        unspent_only: Whether to show only unspent UTXOs
        pagination: Pagination parameters
        
    Returns:
        List of UTXOs for the address
    """
    # Build query
    query = db.query(UTXO).filter(UTXO.address == address)
    
    if unspent_only:
        query = query.filter(UTXO.spent == False)
    
    # Check if address exists
    if query.count() == 0:
        raise HTTPException(status_code=404, detail="Address not found")
    
    # Apply pagination
    results, pagination_data = paginate_results(query, pagination)
    
    # Format UTXOs
    utxos = []
    for utxo in results:
        utxos.append({
            "txid": utxo.txid,
            "vout": utxo.vout,
            "amount": str(utxo.amount),
            "token_ref": utxo.token_ref,
            "spent": utxo.spent,
            "block_height": utxo.block_height
        })
    
    return {
        "address": address,
        "utxos": utxos,
        "pagination": pagination_data
    }

@router.get("/{address}/transactions")
async def get_address_transactions(
    address: str,
    pagination: PaginationParams = Depends(),
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key)
):
    """
    Get transaction history for an address.
    
    Args:
        address: Wallet address
        pagination: Pagination parameters
        
    Returns:
        List of transactions involving the address
    """
    # Get all UTXOs (spent and unspent) for the address
    query = db.query(UTXO).filter(UTXO.address == address)
    
    # Check if address exists
    if query.count() == 0:
        raise HTTPException(status_code=404, detail="Address not found")
    
    # Apply pagination
    results, pagination_data = paginate_results(query, pagination)
    
    # Group by transaction
    transactions = {}
    for utxo in results:
        txid = utxo.txid
        
        if txid not in transactions:
            transactions[txid] = {
                "txid": txid,
                "block_height": utxo.block_height,
                "utxos": []
            }
        
        transactions[txid]["utxos"].append({
            "vout": utxo.vout,
            "amount": str(utxo.amount),
            "token_ref": utxo.token_ref,
            "spent": utxo.spent
        })
    
    return {
        "address": address,
        "transactions": list(transactions.values()),
        "pagination": pagination_data
    }
