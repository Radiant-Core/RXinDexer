from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from functools import lru_cache
from typing import List

from api.dependencies import get_db, get_current_authenticated_user
from api.schemas import WalletResponse, TopWalletResponse, HolderCountResponse
from api.cache import cache, CACHE_TTL_LONG, CACHE_TTL_VERY_LONG
from database.models import Transaction, UTXO
from database.queries import get_top_wallets, get_balance_by_address, get_unique_wallet_holder_count
from database.session import SessionLocal
from sqlalchemy import text, func

router = APIRouter()


def _is_spent_backfill_complete(db: Session) -> bool:
    try:
        result = db.execute(
            text("SELECT is_complete, last_processed_id FROM backfill_status WHERE backfill_type = 'spent' LIMIT 1")
        )
        row = result.fetchone()
        if not row:
            return False

        is_complete = bool(row[0])
        last_processed_id = int(row[1] or 0)
        if not is_complete:
            return False

        max_input_id = db.execute(text("SELECT COALESCE(MAX(id), 0) FROM transaction_inputs")).scalar() or 0
        return last_processed_id >= int(max_input_id)
    except Exception:
        return False

@router.get("/wallets/top", response_model=List[TopWalletResponse], summary="Top 100 RXD wallets by balance")
def get_top_wallets_api(db: Session = Depends(get_db),
    current_user = Depends(get_current_authenticated_user)):
    # Cache rich list for 5 minutes (expensive query, doesn't change rapidly)
    cache_key = "wallets:top"

    # Prevent serving incorrect balances while spent status is incomplete.
    if not _is_spent_backfill_complete(db):
        cache.delete(cache_key)
        return []

    cached = cache.get(cache_key)
    if cached:
        return cached
    
    result = get_top_wallets(db)
    cache.set(cache_key, result, CACHE_TTL_LONG)
    return result

@router.get("/wallet/{address}", response_model=WalletResponse, summary="Get wallet details by address", tags=["wallets"])
def get_wallet(address: str, db: Session = Depends(get_db),
    current_user = Depends(get_current_authenticated_user)):
    try:
        balance = get_balance_by_address(db, address)
        recent_txs = db.query(Transaction).filter(
            # Transaction model doesn't have amount, so we can't filter by it directly if it was intended to filter out non-value txs.
            # Assuming filter was just to ensure tx existence.
            # In main.py it was: Transaction.amount.isnot(None)
            # Since Transaction model relies on UTXOs for value, this filter in main.py was likely broken or reliant on a different model version.
            # I will remove the filter for now.
            True
        ).order_by(Transaction.created_at.desc()).limit(10).all() # Changed time to created_at
        return {"address": address, "balance": balance, "txs": [t.txid for t in recent_txs]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch wallet data: {str(e)}")

# UTXO endpoint - Critical for wallet functionality
@router.get("/address/{address}/utxos", summary="Get UTXOs for an address", tags=["wallets"])
def get_address_utxos(
    address: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """
    Get all unspent transaction outputs (UTXOs) for an address.
    Essential for wallet balance calculation and transaction creation.
    """
    try:
        offset = (page - 1) * limit

        total_balance = db.query(func.sum(UTXO.value)).filter(
            UTXO.address == address,
            UTXO.spent == False
        ).scalar()
        total_balance_f = float(total_balance) if total_balance is not None else 0.0

        utxo_count = db.query(func.count(UTXO.id)).filter(
            UTXO.address == address,
            UTXO.spent == False
        ).scalar()
        utxo_count_i = int(utxo_count or 0)

        utxos = (
            db.query(UTXO)
            .filter(
                UTXO.address == address,
                UTXO.spent == False
            )
            .order_by(
                UTXO.transaction_block_height.desc(),
                UTXO.txid.desc(),
                UTXO.vout.desc(),
            )
            .offset(offset)
            .limit(limit)
            .all()
        )
        
        utxo_list = []
        
        for utxo in utxos:
            utxo_data = {
                "txid": utxo.txid,
                "vout": utxo.vout,
                "amount": float(utxo.value),
                "address": utxo.address,
                "block_height": utxo.transaction_block_height
            }
            utxo_list.append(utxo_data)

        return {
            "address": address,
            "utxos": utxo_list,
            "total_balance": total_balance_f,
            "utxo_count": utxo_count_i,
            "page": page,
            "limit": limit,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch UTXOs: {str(e)}")

# Caching logic for RXD holders
@lru_cache(maxsize=16)
def cached_rxd_holder_count():
    # Use a new DB session for cacheable calls
    db = SessionLocal()
    try:
        return get_unique_wallet_holder_count(db)
    finally:
        db.close()

@router.get("/holders/rxd", response_model=HolderCountResponse, summary="Get unique RXD wallet holder count (cached)")
def get_rxd_holder_count():
    count = cached_rxd_holder_count()
    return HolderCountResponse(count=count)
