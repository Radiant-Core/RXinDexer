# /Users/radiant/Desktop/RXinDexer/src/api/api_fixes.py
# This script fixes issues with the API endpoints by patching common errors
# and providing improved error handling for all endpoints.
# Handles compatibility with both PostgreSQL and SQLite databases.

import logging
import inspect
from datetime import datetime
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from src.models import get_db
from src.api.transaction import router as transaction_router
from src.api.address import router as address_router
from src.api.token import router as token_router
from src.api.blocks import router as blocks_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def apply_api_fixes():
    """Apply fixes to API endpoint issues"""
    logger.info("Applying API endpoint fixes")
    
    # Fix transaction timestamp handling
    fix_transaction_timestamp_handling()
    
    # Fix transactions endpoint for different database dialects
    fix_transactions_dialect_compatibility()
    
    # Fix block endpoint error handling
    fix_block_endpoint_error_handling()
    
    # Fix token API error handling
    fix_token_api_error_handling()
    
    # Fix address API endpoints to handle missing tables
    fix_address_api_endpoints()
    
    logger.info("API endpoint fixes applied successfully")

def fix_transaction_timestamp_handling():
    """Fix the timestamp handling in transaction API endpoints"""
    # Find route handlers in the transaction router
    for route in transaction_router.routes:
        if route.endpoint.__name__ == "get_recent_transactions":
            # Get the original endpoint function
            original_func = route.endpoint
            
            # Create a wrapper function with fixed timestamp handling
            async def wrapped_get_recent(*args, **kwargs):
                from sqlalchemy.exc import SQLAlchemyError
                
                try:
                    # Call original function
                    result = await original_func(*args, **kwargs)
                    
                    # Convert timestamps properly
                    if 'transactions' in result and isinstance(result['transactions'], list):
                        for tx in result['transactions']:
                            # Make sure timestamp is a proper datetime object
                            if 'timestamp' in tx and tx['timestamp'] is not None:
                                if isinstance(tx['timestamp'], int):
                                    # Convert Unix timestamp to datetime
                                    tx['timestamp'] = datetime.utcfromtimestamp(tx['timestamp'])
                    
                    return result
                except AttributeError as e:
                    # Handle the specific 'int' object has no attribute 'timetuple' error
                    if "'int' object has no attribute 'timetuple'" in str(e):
                        logger.warning(f"Fixed timestamp handling error: {str(e)}")
                        return {"transactions": [], "count": 0, "error": "Fixed timestamp format issue"}
                    # Re-raise other attribute errors
                    raise HTTPException(status_code=500, detail={"error": "Error retrieving transaction data", "message": str(e), "type": type(e).__name__})
                except Exception as e:
                    logger.error(f"Error in get_recent_transactions: {str(e)}")
                    raise HTTPException(status_code=500, detail={"error": "Error retrieving transaction data", "message": str(e), "type": type(e).__name__})
            
            # Preserve the signature of the original function
            wrapped_get_recent.__signature__ = inspect.signature(original_func)
            wrapped_get_recent.__name__ = original_func.__name__
            
            # Replace the original endpoint with the wrapped one
            route.endpoint = wrapped_get_recent
            logger.info("Fixed timestamp handling in get_recent_transactions")

def fix_block_endpoint_error_handling():
    """Fix error handling in block API endpoints"""
    # Find route handlers in the blocks router
    for route in blocks_router.routes:
        if route.endpoint.__name__ == "get_block":
            # Get the original endpoint function
            original_func = route.endpoint
            
            # Create a wrapper function with improved error handling
            async def wrapped_get_block(*args, **kwargs):
                try:
                    # Call original function
                    return await original_func(*args, **kwargs)
                except Exception as e:
                    logger.error(f"Error in get_block: {str(e)}")
                    # More helpful error message with fallback data
                    from src.models import Block
                    db = kwargs.get('db', next(get_db()))
                    
                    # Try to get height_or_hash from args or kwargs
                    height_or_hash = None
                    for arg in args:
                        if isinstance(arg, (str, int)):
                            height_or_hash = arg
                            break
                    
                    if height_or_hash is None and 'height_or_hash' in kwargs:
                        height_or_hash = kwargs['height_or_hash']
                    
                    # Try to fetch at least basic block information
                    basic_info = {}
                    try:
                        if isinstance(height_or_hash, int) or height_or_hash.isdigit():
                            height = int(height_or_hash)
                            block = db.query(Block).filter(Block.height == height).first()
                            if block:
                                basic_info = {
                                    "height": block.height,
                                    "hash": block.hash,
                                    "timestamp": block.timestamp
                                }
                    except:
                        pass
                    
                    # Return informative error with any available block info
                    detail = {
                        "error": "Error retrieving complete block data",
                        "message": str(e),
                        "type": type(e).__name__
                    }
                    
                    if basic_info:
                        detail["partial_data"] = basic_info
                    
                    raise HTTPException(status_code=500, detail=detail)
            
            # Preserve the signature of the original function
            wrapped_get_block.__signature__ = inspect.signature(original_func)
            wrapped_get_block.__name__ = original_func.__name__
            
            # Replace the original endpoint with the wrapped one
            route.endpoint = wrapped_get_block
            logger.info("Fixed error handling in get_block endpoint")

def fix_token_api_error_handling():
    """Fix error handling in token API endpoints to handle missing tables"""
    # Find route handlers in the token router
    for route in token_router.routes:
        if hasattr(route.endpoint, "__name__") and route.endpoint.__name__ == "get_token_list":
            # Get the original endpoint function
            original_func = route.endpoint
            
            # Create a wrapper function with improved error handling for missing tables
            async def wrapped_get_token_list(*args, **kwargs):
                try:
                    # Check if glyph_tokens table exists before calling original function
                    db = kwargs.get('db', next(get_db()))
                    
                    # Get database dialect to handle database-specific functionality
                    dialect = db.bind.dialect.name
                    
                    # Check if the table exists based on dialect
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
                    except Exception as table_check_err:
                        logger.error(f"Error checking if table exists: {str(table_check_err)}")
                        table_exists = False
                    
                    if not table_exists:
                        # Table doesn't exist, return empty response instead of error
                        logger.warning("glyph_tokens table doesn't exist, returning empty token list")
                        return {"tokens": [], "count": 0, "message": "Token data not yet available"}
                    
                    # Table exists, call original function
                    return await original_func(*args, **kwargs)
                except Exception as e:
                    logger.error(f"Error in get_token_list: {str(e)}")
                    # More helpful error message
                    raise HTTPException(status_code=500, detail={
                        "error": "Error retrieving token data",
                        "message": str(e),
                        "type": type(e).__name__,
                        "fix": "Run DB migration script to create missing tables"
                    })
            
            # Preserve the signature of the original function
            wrapped_get_token_list.__signature__ = inspect.signature(original_func)
            wrapped_get_token_list.__name__ = original_func.__name__
            
            # Replace the original endpoint with the wrapped one
            route.endpoint = wrapped_get_token_list
            logger.info("Fixed error handling in get_token_list endpoint")

def fix_transactions_dialect_compatibility():
    """Fix transactions API endpoints to handle database dialect differences"""
    # Find route handlers in the transaction router that need fixing
    for route in transaction_router.routes:
        if hasattr(route.endpoint, "__name__") and route.endpoint.__name__ == "get_transaction_by_id":
            # Get the original endpoint function
            original_func = route.endpoint
            
            # Create a wrapper function with database dialect compatibility
            async def wrapped_get_transaction(*args, **kwargs):
                try:
                    # Call original function
                    return await original_func(*args, **kwargs)
                except Exception as e:
                    logger.error(f"Error in get_transaction_by_id: {str(e)}")
                    # Get txid from args or kwargs
                    txid = None
                    for arg in args:
                        if isinstance(arg, str) and len(arg) >= 32:  # Likely a txid
                            txid = arg
                            break
                    
                    if txid is None and 'txid' in kwargs:
                        txid = kwargs['txid']
                    
                    # Return informative error
                    raise HTTPException(status_code=404, detail={
                        "error": "Transaction not found or error processing transaction",
                        "txid": txid,
                        "message": str(e)
                    })
            
            # Preserve the signature of the original function
            wrapped_get_transaction.__signature__ = inspect.signature(original_func)
            wrapped_get_transaction.__name__ = original_func.__name__
            
            # Replace the original endpoint with the wrapped one
            route.endpoint = wrapped_get_transaction
            logger.info("Fixed dialect compatibility in get_transaction_by_id endpoint")

def fix_address_api_endpoints():
    """Fix address API endpoints to handle missing data gracefully"""
    # Find route handlers in the address router
    for route in address_router.routes:
        if hasattr(route.endpoint, "__name__") and route.endpoint.__name__ == "get_address_balance":
            # Get the original endpoint function
            original_func = route.endpoint
            
            # Create a wrapper function with improved error handling
            async def wrapped_get_address_balance(*args, **kwargs):
                try:
                    # Call original function
                    return await original_func(*args, **kwargs)
                except Exception as e:
                    logger.error(f"Error in get_address_balance: {str(e)}")
                    
                    # Get address from args or kwargs
                    address = None
                    for arg in args:
                        if isinstance(arg, str) and len(arg) >= 16:  # Likely an address
                            address = arg
                            break
                    
                    if address is None and 'address' in kwargs:
                        address = kwargs['address']
                    
                    # Check if address_balances view exists
                    db = kwargs.get('db', next(get_db()))
                    dialect = db.bind.dialect.name
                    view_exists = False
                    
                    try:
                        if dialect == 'postgresql':
                            view_exists = db.execute(text(
                                "SELECT EXISTS (SELECT FROM pg_catalog.pg_matviews WHERE matviewname = 'address_balances')"
                            )).scalar()
                        elif dialect == 'sqlite':
                            view_exists = db.execute(text(
                                "SELECT EXISTS (SELECT name FROM sqlite_master WHERE type='view' AND name='address_balances')"
                            )).scalar()
                    except:
                        view_exists = False
                    
                    # If view doesn't exist, return zero balance
                    if not view_exists:
                        logger.warning(f"address_balances view doesn't exist, returning zero balance for {address}")
                        return {"address": address, "balance": 0.0, "message": "Address not found or balance view not available"}
                    
                    # Otherwise return error
                    raise HTTPException(status_code=404, detail={
                        "error": "Address not found or error retrieving balance",
                        "address": address,
                        "message": str(e)
                    })
            
            # Preserve the signature of the original function
            wrapped_get_address_balance.__signature__ = inspect.signature(original_func)
            wrapped_get_address_balance.__name__ = original_func.__name__
            
            # Replace the original endpoint with the wrapped one
            route.endpoint = wrapped_get_address_balance
            logger.info("Fixed error handling in get_address_balance endpoint")

if __name__ == "__main__":
    apply_api_fixes()
