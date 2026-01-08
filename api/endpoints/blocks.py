from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from typing import List, Optional
from datetime import datetime

from api.dependencies import get_db, get_async_db, get_current_authenticated_user
from api.schemas import BlockResponse
from api.utils import rpc_call
from api.cache import cache, CACHE_TTL_SHORT, CACHE_TTL_MEDIUM
from database.queries import get_recent_blocks
from database.models import Transaction, Block

router = APIRouter()

@router.get("/block/{height}", response_model=BlockResponse, summary="Get block by height", tags=["blocks"])
def get_block(height: int):
    # Cache individual blocks for 1 minute (they don't change)
    cache_key = f"block:{height}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    try:
        hash = rpc_call("getblockhash", [height])
        block = rpc_call("getblock", [hash])
        result = BlockResponse(hash=block['hash'], height=block['height'], time=block['time'], tx=block['tx'])
        cache.set(cache_key, result, CACHE_TTL_MEDIUM)
        return result
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Block not found: {e}")

@router.get("/blocks/recent", summary="Recent blocks", tags=["blocks"])
async def get_recent_blocks_api(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    cursor: Optional[str] = Query(None, description="Cursor for cursor-based pagination (use instead of offset)"),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get recent blocks with pagination (async).
    
    - **limit**: Maximum blocks to return
    - **cursor**: Cursor for efficient pagination (preferred over offset)
    - **offset**: Legacy offset-based pagination
    
    When using cursor, returns paginated response with next_cursor/prev_cursor.
    """
    from api.pagination import decode_cursor, encode_cursor
    
    cursor_info = decode_cursor(cursor) if cursor else None
    
    # Cache key includes cursor for cursor-based requests
    cache_key = f"blocks:recent:{limit}:{offset}:{cursor or ''}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    try:
        # Build async query with cursor support
        stmt = select(Block)
        
        if cursor_info:
            if cursor_info.direction == "after":
                stmt = stmt.where(Block.id < cursor_info.id)
            else:
                stmt = stmt.where(Block.id > cursor_info.id)
        elif offset > 0:
            stmt = stmt.offset(offset)
        
        stmt = stmt.order_by(Block.id.desc())
        
        # Fetch one extra to detect has_next
        fetch_limit = limit + 1 if cursor_info else limit
        stmt = stmt.limit(fetch_limit)
        
        result = await db.execute(stmt)
        blocks = result.scalars().all()
        
        has_next = len(blocks) > limit if cursor_info else False
        if has_next:
            blocks = blocks[:limit]

        # Compute tx counts in one async query
        block_ids = [b.id for b in blocks]
        counts = {}
        if block_ids:
            count_stmt = (
                select(Transaction.block_id, func.count(Transaction.id))
                .where(Transaction.block_id.in_(block_ids))
                .group_by(Transaction.block_id)
            )
            count_result = await db.execute(count_stmt)
            counts = {block_id: int(cnt) for block_id, cnt in count_result.all()}

        def _to_unix_seconds(value):
            if value is None:
                return 0
            if isinstance(value, datetime):
                return int(value.timestamp())
            try:
                return int(value)
            except Exception:
                return 0

        items = [BlockResponse(
            hash=block.hash,
            height=block.height,
            time=_to_unix_seconds(getattr(block, 'timestamp', None)),
            tx=[],
            tx_count=counts.get(block.id, 0)
        ) for block in blocks]

        # Return cursor-based response if cursor was used
        if cursor_info or cursor is not None:
            result = {
                "items": items,
                "limit": limit,
                "has_next": has_next,
                "has_prev": bool(cursor_info),
                "next_cursor": encode_cursor(blocks[-1].id, "after") if has_next and blocks else None,
                "prev_cursor": encode_cursor(blocks[0].id, "before") if blocks else None,
            }
        else:
            result = items

        cache.set(cache_key, result, 30)
        return result
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        result = []
        cache.set(cache_key, result, 5)
        return result
