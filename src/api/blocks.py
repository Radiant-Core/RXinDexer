# /Users/radiant/Desktop/RXinDexer/src/api/blocks.py
# This file implements API endpoints for block-related information.
# It provides access to block data, latest blocks, and block statistics.

import logging
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc

# Import security module for API key authentication
from src.api.security import get_api_key

from src.models import get_db, Block
from src.sync.rpc_selector import RadiantRPC  # Import from selector instead of direct import
from src.utils.pagination import PaginationParams, paginate_results

# Create router with explicit API key dependency for ALL endpoints
router = APIRouter(
    dependencies=[Depends(get_api_key)]
)
logger = logging.getLogger(__name__)

@router.get("/latest")
async def get_latest_block(
    db: Session = Depends(get_db)
):
    """
    Get the latest block information
    
    Returns:
        Latest block data including height, hash, and timestamp
    """
    try:
        # First try to get from RPC for most up-to-date info
        try:
            rpc = RadiantRPC()
            block_count = rpc.client.getblockcount()
            block_hash = rpc.client.getblockhash(block_count)
            block_data = rpc.client.getblock(block_hash)
            
            # Update our database with this info
            try:
                db.execute(
                    text("""
                    INSERT INTO blocks (hash, height, timestamp, created_at)
                    VALUES (:hash, :height, to_timestamp(:timestamp), NOW())
                    ON CONFLICT (hash) DO UPDATE
                    SET height = EXCLUDED.height,
                        timestamp = EXCLUDED.timestamp
                    """),
                    {
                        "hash": block_hash,
                        "height": block_count,
                        "timestamp": block_data["time"]
                    }
                )
                db.commit()
            except Exception as db_error:
                db.rollback()
                logger.warning(f"Failed to update block in database: {str(db_error)}")
            
            return {
                "height": block_count,
                "hash": block_hash,
                "timestamp": block_data["time"],
                "transactions_count": len(block_data["tx"]),
                "size": block_data["size"],
                "source": "rpc"
            }
        except Exception as rpc_error:
            logger.warning(f"Failed to get latest block from RPC: {str(rpc_error)}")
            
        # Fall back to database if RPC fails - use raw SQL to avoid model issues
        result = db.execute(
            text("""
            SELECT height, hash, EXTRACT(EPOCH FROM timestamp) as timestamp
            FROM blocks
            ORDER BY height DESC
            LIMIT 1
            """)
        ).fetchone()
        
        if not result:
            return {
                "error": "No block data available",
                "source": "none"
            }
        
        # Convert to dict for consistent response format
        return {
            "height": result[0],
            "hash": result[1],
            "timestamp": result[2],
            "source": "database"
        }
    except Exception as e:
        logger.error(f"Error getting latest block: {str(e)}")
        raise HTTPException(status_code=500, detail="Error retrieving latest block data")

@router.get("/{height_or_hash}")
async def get_block(
    height_or_hash: str,
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key)
):
    """
    Get details about a specific block by height or hash
    
    Args:
        height_or_hash: Block height (number) or block hash (string)
        
    Returns:
        Block details
    """
    try:
        # Check if height_or_hash is a number (height) or string (hash)
        try:
            height = int(height_or_hash)
            block = db.query(Block).filter(Block.height == height).first()
        except ValueError:
            # Not an integer, so must be a hash
            block = db.query(Block).filter(Block.hash == height_or_hash).first()
        
        if not block:
            # Try to get from RPC if not in database
            rpc = RadiantRPC()
            try:
                # Try as hash first
                block_data = rpc.client.getblock(height_or_hash)
            except:
                try:
                    # Try as height
                    height = int(height_or_hash)
                    block_hash = rpc.client.getblockhash(height)
                    block_data = rpc.client.getblock(block_hash)
                except:
                    raise HTTPException(status_code=404, detail="Block not found")
            
            return {
                "height": block_data["height"],
                "hash": block_data["hash"],
                "timestamp": block_data["time"],
                "size": block_data["size"],
                "transactions_count": len(block_data["tx"]),
                "source": "rpc"
            }
        
        # Return formatted block data from database
        return {
            "height": block.height,
            "hash": block.hash,
            "timestamp": block.timestamp,
            "size": block.size,
            "transactions_count": block.transactions_count,
            "median_fee": block.median_fee,
            "source": "database"
        }
        
    except Exception as e:
        logger.error(f"Error getting block {height_or_hash}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error retrieving block data")

@router.get("/range/{start_height}/{end_height}")
async def get_blocks_range(
    start_height: int,
    end_height: int,
    pagination: PaginationParams = Depends(),
    db: Session = Depends(get_db),
    api_key: str = Depends(get_api_key)
):
    """
    Get multiple blocks in a specified height range
    
    Args:
        start_height: Starting block height
        end_height: Ending block height
        pagination: Pagination parameters
        
    Returns:
        List of blocks in the specified range
    """
    try:
        if end_height < start_height:
            raise HTTPException(status_code=400, detail="End height must be greater than or equal to start height")
        
        # Limit range size to prevent performance issues
        if end_height - start_height > 1000:
            end_height = start_height + 1000
        
        # Query blocks in the range
        query = db.query(Block).filter(
            Block.height >= start_height,
            Block.height <= end_height
        ).order_by(desc(Block.height))
        
        # Apply pagination
        paginated_results = paginate_results(query, pagination)
        
        # Format results
        blocks_list = []
        for block in paginated_results["items"]:
            blocks_list.append({
                "height": block.height,
                "hash": block.hash,
                "timestamp": block.timestamp,
                "transactions_count": block.transactions_count,
                "size": block.size
            })
        
        return {
            "blocks": blocks_list,
            "pagination": paginated_results["pagination"]
        }
        
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error getting blocks range: {str(e)}")
        raise HTTPException(status_code=500, detail="Error retrieving blocks range")
