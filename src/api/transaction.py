# /Users/radiant/Desktop/RXinDexer/src/api/transaction.py
# This file implements the API endpoints for transaction-related queries.
# It provides transaction details and lookup functionality.

import logging
from typing import Dict, List, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.models import get_db, UTXO, GlyphToken
from src.sync.rpc_selector import RadiantRPC  # Import from selector instead of direct import
from src.utils.pagination import PaginationParams, paginate_results

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/{txid}")
async def get_transaction(
    txid: str,
    db: Session = Depends(get_db)
):
    """
    Get details about a specific transaction.
    
    Args:
        txid: Transaction ID
        
    Returns:
        Transaction details including inputs and outputs
    """
    # Query UTXOs created by this transaction
    outputs = db.query(UTXO).filter(UTXO.txid == txid).all()
    
    if not outputs:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    # Query UTXOs spent by this transaction
    inputs = db.query(UTXO).filter(UTXO.spent_txid == txid).all()
    
    # Get block height
    block_height = outputs[0].block_height if outputs else None
    
    # Check for Glyph tokens in this transaction
    tokens = []
    for output in outputs:
        if output.token_ref:
            token = db.query(GlyphToken).filter(GlyphToken.ref == output.token_ref).first()
            if token:
                tokens.append({
                    "ref": token.ref,
                    "type": token.type,
                    "vout": output.vout
                })
    
    # Format transaction details
    tx_details = {
        "txid": txid,
        "block_height": block_height,
        "inputs": [
            {
                "txid": utxo.txid,
                "vout": utxo.vout,
                "address": utxo.address,
                "amount": str(utxo.amount),
                "token_ref": utxo.token_ref
            } for utxo in inputs
        ],
        "outputs": [
            {
                "vout": utxo.vout,
                "address": utxo.address,
                "amount": str(utxo.amount),
                "token_ref": utxo.token_ref,
                "spent": utxo.spent
            } for utxo in outputs
        ],
        "tokens": tokens
    }
    
    return tx_details

@router.get("/block/{height}")
async def get_block_transactions(
    height: int,
    pagination: PaginationParams = Depends(),
    db: Session = Depends(get_db)
):
    """
    Get transactions in a specific block.
    
    Args:
        height: Block height
        pagination: Pagination parameters
        
    Returns:
        List of transactions in the block
    """
    # Check if block exists
    block_exists = db.query(UTXO).filter(UTXO.block_height == height).first()
    
    if not block_exists:
        raise HTTPException(status_code=404, detail="Block not found")
    
    # Get unique transaction IDs in this block
    query = db.query(UTXO.txid).filter(UTXO.block_height == height).distinct()
    
    # Apply pagination
    results, pagination_data = paginate_results(query, pagination)
    
    # Format results
    transactions = []
    for result in results:
        txid = result[0]
        
        # Get outputs for this transaction
        outputs = db.query(UTXO).filter(UTXO.txid == txid).all()
        
        # Check for Glyph tokens
        has_tokens = any(output.token_ref for output in outputs)
        
        transactions.append({
            "txid": txid,
            "has_tokens": has_tokens
        })
    
    return {
        "block_height": height,
        "transactions": transactions,
        "pagination": pagination_data
    }

@router.get("/search/{query}")
async def search_transaction(
    query: str,
    db: Session = Depends(get_db)
):
    """
    Search for a transaction by ID, address, or token reference.
    
    Args:
        query: Search query (txid, address, or token ref)
        
    Returns:
        Search results matching the query
    """
    results = {
        "type": None,
        "results": []
    }
    
    # Check if query is a transaction ID
    tx = db.query(UTXO).filter(UTXO.txid == query).first()
    if tx:
        results["type"] = "transaction"
        results["results"] = [{
            "txid": query,
            "block_height": tx.block_height
        }]
        return results
    
    # Check if query is an address
    address_txs = db.query(UTXO.txid).filter(UTXO.address == query).distinct().limit(10).all()
    if address_txs:
        results["type"] = "address"
        results["results"] = [{"txid": tx[0]} for tx in address_txs]
        return results
    
    # Check if query is a token reference
    token = db.query(GlyphToken).filter(GlyphToken.ref == query).first()
    if token:
        results["type"] = "token"
        results["results"] = [{
            "ref": token.ref,
            "type": token.type,
            "genesis_txid": token.genesis_txid
        }]
        return results
    
    # No results found
    raise HTTPException(status_code=404, detail="No results found for query")
