from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List
from datetime import datetime

from api.dependencies import get_db
from api.schemas import BlockResponse
from api.utils import rpc_call
from api.cache import cache, CACHE_TTL_SHORT, CACHE_TTL_MEDIUM
from database.queries import get_recent_blocks
from database.models import Transaction

router = APIRouter()

@router.get("/block/{height}", response_model=BlockResponse)
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

@router.get("/blocks/recent", response_model=List[BlockResponse], summary="Recent blocks")
def get_recent_blocks_api(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    # Cache recent blocks for 10 seconds (changes frequently)
    cache_key = f"blocks:recent:{limit}:{offset}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    try:
        blocks = get_recent_blocks(db, limit=limit, offset=offset)

        # Compute tx counts in one query (avoids loading relationships).
        block_ids = [b.id for b in blocks]
        counts = {}
        if block_ids:
            rows = (
                db.query(Transaction.block_id, func.count(Transaction.id))
                .filter(Transaction.block_id.in_(block_ids))
                .group_by(Transaction.block_id)
                .all()
            )
            counts = {block_id: int(cnt) for block_id, cnt in rows}

        def _to_unix_seconds(value):
            if value is None:
                return 0
            if isinstance(value, datetime):
                return int(value.timestamp())
            try:
                return int(value)
            except Exception:
                return 0

        result = [BlockResponse(
            hash=block.hash,
            height=block.height,
            time=_to_unix_seconds(getattr(block, 'timestamp', None)),
            # Avoid touching relationship attributes here; depending on session settings
            # this can trigger expensive lazy-loads for every block.
            tx=[],
            tx_count=counts.get(block.id, 0)
        ) for block in blocks]

        cache.set(cache_key, result, 30)  # Cache recent blocks for 30 seconds
        return result
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        result = []
        cache.set(cache_key, result, 5)
        return result
