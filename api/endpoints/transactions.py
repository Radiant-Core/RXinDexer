from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, distinct, func
from typing import List
from datetime import datetime, timedelta

from api.dependencies import get_db
from api.schemas import TransactionResponse
from api.cache import cache, CACHE_TTL_SHORT, CACHE_TTL_MEDIUM
from api.utils import rpc_call
from database.models import Block, Transaction, UTXO
from database.queries import get_recent_transactions

router = APIRouter()

def _to_iso_time(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        return datetime.utcfromtimestamp(int(value)).isoformat()
    except Exception:
        try:
            return str(value)
        except Exception:
            return None

@router.get("/transactions/recent", response_model=List[TransactionResponse], summary="Recent transactions")
def get_recent_transactions_api(
    limit: int = Query(10, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    # Cache recent transactions for 10 seconds
    cache_key = f"transactions:recent:{limit}:{offset}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    try:
        txs = get_recent_transactions(db, limit=limit, offset=offset)

        # Aggregate output sums in one query to avoid loading tx.utxos per tx.
        txids = [tx.txid for tx in txs]
        sums = {}
        if txids:
            rows = (
                db.query(UTXO.txid, func.sum(UTXO.value))
                .filter(UTXO.txid.in_(txids))
                .group_by(UTXO.txid)
                .all()
            )
            sums = {txid: float(total or 0) for txid, total in rows}

        result = [TransactionResponse(
            txid=tx.txid,
            block_id=tx.block_id,
            amount=sums.get(tx.txid),
            time=_to_iso_time(getattr(tx, 'created_at', None))
        ) for tx in txs]

        cache.set(cache_key, result, 30)  # Cache recent transactions for 30 seconds
        return result
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        result = []
        cache.set(cache_key, result, 5)
        return result


@router.get("/transactions/stats/timeseries", tags=["transactions"], summary="Transaction count timeseries")
def get_transaction_stats_timeseries(
    period: str = Query("24h"),
    db: Session = Depends(get_db),
):
    cache_key = f"transactions:stats:timeseries:{period}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    now = datetime.utcnow()

    if period == "1h":
        start = now - timedelta(hours=1)
        step = timedelta(minutes=5)
        trunc = "minute"
    elif period == "7d":
        start = now - timedelta(days=7)
        step = timedelta(days=1)
        trunc = "day"
    else:
        period = "24h"
        start = now - timedelta(hours=24)
        step = timedelta(hours=1)
        trunc = "hour"

    def floor_bucket(dt: datetime) -> datetime:
        if trunc == "day":
            return dt.replace(hour=0, minute=0, second=0, microsecond=0)
        if trunc == "hour":
            return dt.replace(minute=0, second=0, microsecond=0)
        if trunc == "minute":
            dt2 = dt.replace(second=0, microsecond=0)
            minute = (dt2.minute // 5) * 5
            return dt2.replace(minute=minute)
        return dt

    series = []
    try:
        bucket_expr = func.date_trunc(trunc, Transaction.created_at)
        rows = (
            db.query(
                bucket_expr.label("bucket"),
                func.count(Transaction.id).label("count"),
            )
            .filter(Transaction.created_at >= start)
            .group_by(bucket_expr)
            .order_by(bucket_expr)
            .all()
        )

        bucket_counts = {}
        for bucket_dt, count in rows:
            if not bucket_dt:
                continue
            bucket = floor_bucket(bucket_dt)
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + int(count or 0)

        cur = floor_bucket(start)
        end = floor_bucket(now)
        while cur <= end:
            series.append({"t": int(cur.replace(tzinfo=None).timestamp()), "count": int(bucket_counts.get(cur, 0))})
            cur = cur + step
    except Exception:
        db.rollback()
        series = []

    result = {
        "period": period,
        "updated_at": int(now.timestamp()),
        "series": series,
    }

    cache.set(cache_key, result, CACHE_TTL_SHORT)
    return result


@router.get("/transactions/stats", tags=["transactions"], summary="Transaction statistics")
def get_transaction_stats(db: Session = Depends(get_db)):
    cache_key = "transactions:stats"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    now = datetime.utcnow()
    t_1h = now - timedelta(hours=1)
    t_24h = now - timedelta(hours=24)
    t_7d = now - timedelta(days=7)

    tx_1h = int(db.query(func.count(Transaction.id)).filter(Transaction.created_at >= t_1h).scalar() or 0)
    tx_24h = int(db.query(func.count(Transaction.id)).filter(Transaction.created_at >= t_24h).scalar() or 0)
    tx_7d = int(db.query(func.count(Transaction.id)).filter(Transaction.created_at >= t_7d).scalar() or 0)

    blocks_24h = int(db.query(func.count(Block.id)).filter(Block.timestamp >= t_24h).scalar() or 0)
    avg_tx_per_block_24h = (float(tx_24h) / float(blocks_24h)) if blocks_24h > 0 else None

    mempool_tx_count = None
    try:
        mempool_info = rpc_call("getmempoolinfo")
        mempool_tx_count = int(mempool_info.get("size") or 0)
    except Exception:
        mempool_tx_count = None

    result = {
        "txs_1h": tx_1h,
        "txs_24h": tx_24h,
        "txs_7d": tx_7d,
        "tps_1h": (float(tx_1h) / 3600.0),
        "blocks_24h": blocks_24h,
        "avg_txs_per_block_24h": avg_tx_per_block_24h,
        "mempool_tx_count": mempool_tx_count,
        "updated_at": int(now.timestamp()),
    }

    cache.set(cache_key, result, CACHE_TTL_SHORT)
    return result

# Transaction details endpoint - Critical for explorers and wallets
@router.get("/transaction/{txid}")
def get_transaction_details(txid: str, db: Session = Depends(get_db)):
    """
    Get detailed transaction information including inputs and outputs.
    Essential for transaction tracking and wallet functionality.
    """
    try:
        # Get transaction
        tx = db.query(Transaction).filter(Transaction.txid == txid).first()
        if not tx:
            raise HTTPException(status_code=404, detail="Transaction not found")
        
        # Get outputs (UTXOs created by this transaction)
        outputs = db.query(UTXO).filter(UTXO.txid == txid).order_by(UTXO.vout).all()
        
        # Get inputs (UTXOs spent by this transaction)
        inputs = db.query(UTXO).filter(UTXO.spent_in_txid == txid).all()
        
        output_list = []
        total_output = 0.0
        for utxo in outputs:
            output_data = {
                "vout": utxo.vout,
                "address": utxo.address,
                "amount": float(utxo.value),
                "spent": utxo.spent
            }
            output_list.append(output_data)
            total_output += float(utxo.value)
        
        input_list = []
        total_input = 0.0
        for utxo in inputs:
            input_data = {
                "txid": utxo.txid,
                "vout": utxo.vout,
                "address": utxo.address,
                "amount": float(utxo.value)
            }
            input_list.append(input_data)
            total_input += float(utxo.value)
        
        # Check if Transaction model has 'time' and 'block_height'
        # In models.py: created_at is there. block_height is there. 
        # But main.py used tx.time.isoformat(). Let's check models.py again.
        
        return {
            "txid": tx.txid,
            "block_height": tx.block_height,
            "time": tx.created_at.isoformat() if tx.created_at else None, # Changed to created_at
            "inputs": input_list,
            "outputs": output_list,
            "total_input": total_input,
            "total_output": total_output,
            "fee": total_input - total_output if total_input > 0 else 0
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch transaction details: {str(e)}")

# Address transaction history - Critical for wallets and explorers
@router.get("/address/{address}/transactions")
def get_address_transactions(
    address: str, 
    page: int = 1, 
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """
    Get transaction history for an address with pagination.
    Essential for wallet transaction history display.
    """
    try:
        if limit > 1000:
            limit = 1000  # Prevent excessive queries
        
        offset = (page - 1) * limit
        
        # Correct logic to find all transactions involving an address:
        # 1. Transactions that SENT to this address (Outputs) -> UTXO.txid where UTXO.address == address
        # 2. Transactions where this address SPENT money (Inputs) -> UTXO.spent_in_txid where UTXO.address == address AND UTXO.spent = True
        
        from sqlalchemy import union, select, desc
        
        # Query 1: Received
        q1 = db.query(UTXO.txid.label('txid')).filter(UTXO.address == address)
        
        # Query 2: Sent (Inputs)
        q2 = db.query(UTXO.spent_in_txid.label('txid')).filter(UTXO.address == address, UTXO.spent == True)
        
        # Union of distinct TXIDs
        involved_txids_query = q1.union(q2).subquery()
        
        # Get total count (expensive but needed for pagination)
        # For performance on large history, you might want to make this optional or cached
        total_count = db.query(involved_txids_query).count()
        
        # Get transactions with details
        # We need to join with the involved_txids subquery to filter
        transactions = db.query(Transaction).join(
            involved_txids_query, Transaction.txid == involved_txids_query.c.txid
        ).order_by(Transaction.created_at.desc()).offset(offset).limit(limit).all()
        
        tx_list = []
        for tx in transactions:
            total_amount = sum(u.value for u in tx.utxos)
            tx_data = {
                "txid": tx.txid,
                "block_height": tx.block_height,
                "time": _to_iso_time(getattr(tx, 'created_at', None)),
                "amount": float(total_amount or 0),
            }
            tx_list.append(tx_data)

        if not tx_list:
            received_total = db.query(func.count(func.distinct(UTXO.txid))).filter(UTXO.address == address).scalar()
            total_count = int(received_total or 0)

            rows = (
                db.query(
                    UTXO.txid.label('txid'),
                    func.max(UTXO.transaction_block_height).label('block_height'),
                    func.max(UTXO.date).label('time'),
                    func.sum(UTXO.value).label('amount'),
                )
                .filter(UTXO.address == address)
                .group_by(UTXO.txid)
                .order_by(func.max(UTXO.transaction_block_height).desc())
                .offset(offset)
                .limit(limit)
                .all()
            )

            tx_list = [
                {
                    "txid": r.txid,
                    "block_height": int(r.block_height or 0),
                    "time": _to_iso_time(r.time),
                    "amount": float(r.amount or 0),
                }
                for r in rows
                if r.txid
            ]
        
        return {
            "address": address,
            "transactions": tx_list,
            "page": page,
            "limit": limit,
            "total_count": total_count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch address transactions: {str(e)}")
