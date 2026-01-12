"""
Token Tracking Module

Handles holder tracking, supply calculation, burn detection, and swap tracking
for the enhanced token indexer.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from sqlalchemy import text
from sqlalchemy.orm import Session

try:
    from script_utils import (
        detect_token_from_script,
        detect_token_burn,
        detect_psrt_signature,
        parse_ft_script,
        parse_nft_script,
    )
except ImportError:
    from indexer.script_utils import (
        detect_token_from_script,
        detect_token_burn,
        detect_psrt_signature,
        parse_ft_script,
        parse_nft_script,
    )

logger = logging.getLogger(__name__)


# ============================================================================
# HOLDER TRACKING
# ============================================================================

def update_token_holders(db: Session, token_id: str, transfers: List[Dict]) -> int:
    """
    Update token holder balances based on transfers.
    
    Args:
        db: Database session
        token_id: Token reference ID
        transfers: List of {'address': str, 'amount': int, 'is_receive': bool}
        
    Returns:
        Number of holders updated
    """
    updated = 0
    
    for transfer in transfers:
        address = transfer.get('address')
        amount = transfer.get('amount', 0)
        is_receive = transfer.get('is_receive', True)
        
        if not address or amount <= 0:
            continue
        
        # Check if holder exists
        result = db.execute(text("""
            SELECT id, balance FROM token_holders 
            WHERE token_id = :token_id AND address = :address
        """), {'token_id': token_id, 'address': address})
        row = result.fetchone()
        
        if row:
            # Update existing holder
            new_balance = row.balance + amount if is_receive else row.balance - amount
            if new_balance <= 0:
                # Remove holder if balance is zero or negative
                db.execute(text("""
                    DELETE FROM token_holders WHERE id = :id
                """), {'id': row.id})
            else:
                db.execute(text("""
                    UPDATE token_holders 
                    SET balance = :balance, last_updated_at = NOW()
                    WHERE id = :id
                """), {'id': row.id, 'balance': new_balance})
            updated += 1
        elif is_receive:
            # Insert new holder
            db.execute(text("""
                INSERT INTO token_holders (token_id, address, balance, first_acquired_at, last_updated_at)
                VALUES (:token_id, :address, :balance, NOW(), NOW())
                ON CONFLICT (token_id, address) DO UPDATE SET
                    balance = token_holders.balance + :balance,
                    last_updated_at = NOW()
            """), {'token_id': token_id, 'address': address, 'balance': amount})
            updated += 1
    
    return updated


def calculate_holder_percentages(db: Session, token_id: str) -> int:
    """
    Recalculate holder percentages based on circulating supply.
    
    Args:
        db: Database session
        token_id: Token reference ID
        
    Returns:
        Number of holders updated
    """
    # Get circulating supply
    result = db.execute(text("""
        SELECT circulating_supply FROM glyph_tokens WHERE token_id = :token_id
    """), {'token_id': token_id})
    row = result.fetchone()
    
    if not row or not row.circulating_supply or row.circulating_supply <= 0:
        return 0
    
    circulating = row.circulating_supply
    
    # Update percentages
    result = db.execute(text("""
        UPDATE token_holders 
        SET percentage = (balance::float / :circulating) * 100
        WHERE token_id = :token_id
    """), {'token_id': token_id, 'circulating': circulating})
    
    return result.rowcount


def get_token_holders(db: Session, token_id: str, limit: int = 100, offset: int = 0) -> List[Dict]:
    """
    Get top holders for a token.
    
    Args:
        db: Database session
        token_id: Token reference ID
        limit: Max results
        offset: Pagination offset
        
    Returns:
        List of holder dicts
    """
    result = db.execute(text("""
        SELECT address, balance, percentage, first_acquired_at, last_updated_at
        FROM token_holders
        WHERE token_id = :token_id
        ORDER BY balance DESC
        LIMIT :limit OFFSET :offset
    """), {'token_id': token_id, 'limit': limit, 'offset': offset})
    
    return [dict(row._mapping) for row in result.fetchall()]


def count_token_holders(db: Session, token_id: str) -> int:
    """Count total holders for a token using address clustering."""
    # Try to use address clustering first (more accurate count)
    try:
        exists = db.execute(text("SELECT to_regclass('public.address_clusters')")).scalar()
        if exists:
            result = db.execute(text(
                """
                SELECT COUNT(DISTINCT COALESCE('CLUSTER:' || ac.cluster_id::text, th.address))
                FROM token_holders th
                LEFT JOIN address_clusters ac ON ac.address = th.address
                WHERE th.token_id = :token_id
                  AND th.balance > 0
                  AND th.address IS NOT NULL
                  AND length(btrim(th.address)) > 0
                """
            ), {'token_id': token_id})
            count = result.scalar()
            if count is not None:
                return int(count)
    except Exception:
        pass
    
    # Fallback to simple address counting if clustering not available
    result = db.execute(text("""
        SELECT COUNT(*) FROM token_holders 
        WHERE token_id = :token_id 
          AND balance > 0 
          AND address IS NOT NULL 
          AND length(btrim(address)) > 0
    """), {'token_id': token_id})
    return result.scalar() or 0


# ============================================================================
# SUPPLY TRACKING
# ============================================================================

def calculate_circulating_supply(db: Session, token_id: str) -> int:
    """
    Calculate circulating supply by summing all unspent UTXO values for a token.
    
    For FT tokens, the supply is the sum of all UTXO values with the token ref.
    
    Args:
        db: Database session
        token_id: Token reference ID
        
    Returns:
        Circulating supply
    """
    # Build the script pattern to match
    # FT script: 76a914<address>88acbdd0<ref>dec0e9aa76e378e4a269e69d
    # We need to find UTXOs where script_hex contains the ref
    
    result = db.execute(text("""
        SELECT COALESCE(SUM(value), 0) as total
        FROM utxos
        WHERE spent = false 
        AND script_hex LIKE :pattern
    """), {'pattern': f'%{token_id}%'})
    
    row = result.fetchone()
    return int(row.total) if row and row.total else 0


def calculate_supply_from_holders(db: Session, token_id: str) -> int:
    """
    Calculate circulating supply by summing all holder balances.
    This is faster than scanning UTXOs if holder tracking is up to date.
    
    Args:
        db: Database session
        token_id: Token reference ID
        
    Returns:
        Circulating supply
    """
    result = db.execute(text("""
        SELECT COALESCE(SUM(balance), 0) as total
        FROM token_holders
        WHERE token_id = :token_id
    """), {'token_id': token_id})
    
    row = result.fetchone()
    return int(row.total) if row and row.total else 0


def update_token_supply(db: Session, token_id: str, 
                        circulating: int = None, 
                        burned: int = None,
                        holder_count: int = None) -> bool:
    """
    Update token supply fields in glyph_tokens.
    
    Args:
        db: Database session
        token_id: Token reference ID
        circulating: New circulating supply (optional)
        burned: New burned supply (optional)
        holder_count: New holder count (optional)
        
    Returns:
        True if updated
    """
    updates = []
    params = {'token_id': token_id}
    
    if circulating is not None:
        updates.append("circulating_supply = :circulating")
        params['circulating'] = circulating
    
    if burned is not None:
        updates.append("burned_supply = :burned")
        params['burned'] = burned
    
    if holder_count is not None:
        updates.append("holder_count = :holder_count")
        params['holder_count'] = holder_count
    
    if updates:
        updates.append("supply_updated_at = NOW()")
        sql = f"UPDATE glyph_tokens SET {', '.join(updates)} WHERE token_id = :token_id"
        db.execute(text(sql), params)
        return True
    
    return False


def record_supply_snapshot(db: Session, token_id: str, block_height: int) -> bool:
    """
    Record a supply snapshot for historical tracking.
    
    Args:
        db: Database session
        token_id: Token reference ID
        block_height: Current block height
        
    Returns:
        True if recorded
    """
    # Get current supply data
    result = db.execute(text("""
        SELECT circulating_supply, burned_supply, holder_count
        FROM glyph_tokens WHERE token_id = :token_id
    """), {'token_id': token_id})
    row = result.fetchone()
    
    if not row:
        return False
    
    db.execute(text("""
        INSERT INTO token_supply_history 
        (token_id, circulating_supply, burned_supply, holder_count, block_height)
        VALUES (:token_id, :circulating, :burned, :holders, :height)
    """), {
        'token_id': token_id,
        'circulating': row.circulating_supply or 0,
        'burned': row.burned_supply or 0,
        'holders': row.holder_count or 0,
        'height': block_height
    })
    
    return True


# ============================================================================
# BURN TRACKING
# ============================================================================

def record_token_burn(db: Session, token_id: str, txid: str, amount: int,
                      burner_address: str = None, block_height: int = None) -> int:
    """
    Record a token burn event.
    
    Args:
        db: Database session
        token_id: Token reference ID
        txid: Burn transaction ID
        amount: Amount burned
        burner_address: Address that burned (optional)
        block_height: Block height (optional)
        
    Returns:
        ID of the burn record
    """
    result = db.execute(text("""
        INSERT INTO token_burns (token_id, txid, amount, burner_address, block_height)
        VALUES (:token_id, :txid, :amount, :burner, :height)
        RETURNING id
    """), {
        'token_id': token_id,
        'txid': txid,
        'amount': amount,
        'burner': burner_address,
        'height': block_height
    })
    
    row = result.fetchone()
    
    # Update total burned supply
    db.execute(text("""
        UPDATE glyph_tokens 
        SET burned_supply = COALESCE(burned_supply, 0) + :amount
        WHERE token_id = :token_id
    """), {'token_id': token_id, 'amount': amount})

    try:
        with db.begin_nested():
            db.execute(text("""
                UPDATE glyphs
                SET burned_supply = COALESCE(burned_supply, 0) + :amount
                WHERE ref = :token_id
            """), {'token_id': token_id, 'amount': amount})
    except Exception:
        pass
    
    return row.id if row else None


def get_token_burns(db: Session, token_id: str, limit: int = 100) -> List[Dict]:
    """Get burn history for a token."""
    result = db.execute(text("""
        SELECT txid, amount, burner_address, block_height, burned_at
        FROM token_burns
        WHERE token_id = :token_id
        ORDER BY burned_at DESC
        LIMIT :limit
    """), {'token_id': token_id, 'limit': limit})
    
    return [dict(row._mapping) for row in result.fetchall()]


# ============================================================================
# SWAP/TRADE TRACKING
# ============================================================================

def record_swap(db: Session, txid: str, 
                from_token_id: str = None, from_amount: int = 0, from_is_rxd: bool = False,
                to_token_id: str = None, to_amount: int = 0, to_is_rxd: bool = False,
                seller_address: str = None, buyer_address: str = None,
                status: str = 'pending', block_height: int = None,
                psrt_hex: str = None) -> int:
    """
    Record a swap/trade.
    
    Args:
        db: Database session
        txid: Transaction ID
        from_token_id: Token being sold (None for RXD)
        from_amount: Amount being sold
        from_is_rxd: True if selling RXD
        to_token_id: Token being bought (None for RXD)
        to_amount: Amount being bought
        to_is_rxd: True if buying RXD
        seller_address: Seller's address
        buyer_address: Buyer's address (None for pending)
        status: 'pending', 'completed', 'cancelled'
        block_height: Block height when completed
        psrt_hex: Raw PSRT hex for pending swaps
        
    Returns:
        ID of the swap record
    """
    # Calculate price if this is a token-to-RXD swap
    price_per_token = None
    if to_is_rxd and from_amount > 0:
        price_per_token = to_amount / from_amount
    elif from_is_rxd and to_amount > 0:
        price_per_token = from_amount / to_amount
    
    result = db.execute(text("""
        INSERT INTO token_swaps 
        (txid, psrt_hex, from_token_id, from_amount, from_is_rxd,
         to_token_id, to_amount, to_is_rxd, seller_address, buyer_address,
         status, price_per_token, block_height, completed_at)
        VALUES (:txid, :psrt, :from_token, :from_amt, :from_rxd,
                :to_token, :to_amt, :to_rxd, :seller, :buyer,
                :status, :price, :height, 
                CASE WHEN :status = 'completed' THEN NOW() ELSE NULL END)
        RETURNING id
    """), {
        'txid': txid,
        'psrt': psrt_hex,
        'from_token': from_token_id,
        'from_amt': from_amount,
        'from_rxd': from_is_rxd,
        'to_token': to_token_id,
        'to_amt': to_amount,
        'to_rxd': to_is_rxd,
        'seller': seller_address,
        'buyer': buyer_address,
        'status': status,
        'price': price_per_token,
        'height': block_height
    })
    
    row = result.fetchone()
    swap_id = row.id if row else None
    
    # If completed, record price history
    if status == 'completed' and price_per_token and swap_id:
        token_id = from_token_id or to_token_id
        if token_id:
            record_price(db, token_id, price_per_token, swap_id, txid, 
                        from_amount if from_token_id else to_amount, block_height)
    
    return swap_id


def complete_swap(db: Session, txid: str, buyer_address: str, block_height: int) -> bool:
    """
    Mark a pending swap as completed.
    
    Args:
        db: Database session
        txid: Transaction ID of the swap
        buyer_address: Buyer's address
        block_height: Block height when completed
        
    Returns:
        True if updated
    """
    result = db.execute(text("""
        UPDATE token_swaps 
        SET status = 'completed', buyer_address = :buyer, 
            block_height = :height, completed_at = NOW()
        WHERE txid = :txid AND status = 'pending'
        RETURNING id, from_token_id, to_token_id, from_amount, to_amount, price_per_token
    """), {'txid': txid, 'buyer': buyer_address, 'height': block_height})
    
    row = result.fetchone()
    if row and row.price_per_token:
        # Record price history
        token_id = row.from_token_id or row.to_token_id
        volume = row.from_amount if row.from_token_id else row.to_amount
        if token_id:
            record_price(db, token_id, row.price_per_token, row.id, txid, volume, block_height)
    
    return row is not None


def get_active_swaps(db: Session, token_id: str = None, limit: int = 100) -> List[Dict]:
    """Get active (pending) swap offers."""
    if token_id:
        result = db.execute(text("""
            SELECT * FROM token_swaps
            WHERE status = 'pending' 
            AND (from_token_id = :token_id OR to_token_id = :token_id)
            ORDER BY created_at DESC
            LIMIT :limit
        """), {'token_id': token_id, 'limit': limit})
    else:
        result = db.execute(text("""
            SELECT * FROM token_swaps
            WHERE status = 'pending'
            ORDER BY created_at DESC
            LIMIT :limit
        """), {'limit': limit})
    
    return [dict(row._mapping) for row in result.fetchall()]


def get_completed_trades(db: Session, token_id: str = None, limit: int = 100) -> List[Dict]:
    """Get completed trades."""
    if token_id:
        result = db.execute(text("""
            SELECT * FROM token_swaps
            WHERE status = 'completed'
            AND (from_token_id = :token_id OR to_token_id = :token_id)
            ORDER BY completed_at DESC
            LIMIT :limit
        """), {'token_id': token_id, 'limit': limit})
    else:
        result = db.execute(text("""
            SELECT * FROM token_swaps
            WHERE status = 'completed'
            ORDER BY completed_at DESC
            LIMIT :limit
        """), {'limit': limit})
    
    return [dict(row._mapping) for row in result.fetchall()]


# ============================================================================
# PRICE TRACKING
# ============================================================================

def record_price(db: Session, token_id: str, price_rxd: float, 
                 swap_id: int = None, txid: str = None,
                 volume: int = 0, block_height: int = None) -> int:
    """
    Record a price data point.
    
    Args:
        db: Database session
        token_id: Token reference ID
        price_rxd: Price in RXD per token unit
        swap_id: Related swap ID (optional)
        txid: Transaction ID (optional)
        volume: Trade volume
        block_height: Block height
        
    Returns:
        ID of the price record
    """
    result = db.execute(text("""
        INSERT INTO token_price_history 
        (token_id, price_rxd, swap_id, txid, volume, block_height)
        VALUES (:token_id, :price, :swap_id, :txid, :volume, :height)
        RETURNING id
    """), {
        'token_id': token_id,
        'price': price_rxd,
        'swap_id': swap_id,
        'txid': txid,
        'volume': volume,
        'height': block_height
    })
    
    row = result.fetchone()
    
    # Update daily volume
    update_daily_volume(db, token_id, volume, price_rxd)
    
    return row.id if row else None


def update_daily_volume(db: Session, token_id: str, volume: int, price: float):
    """Update daily volume aggregation."""
    today = datetime.utcnow().date()
    
    # Try to update existing record
    result = db.execute(text("""
        UPDATE token_volume_daily
        SET volume_tokens = volume_tokens + :volume,
            volume_rxd = volume_rxd + :rxd_volume,
            trade_count = trade_count + 1,
            high_price = GREATEST(high_price, :price),
            low_price = LEAST(low_price, :price),
            close_price = :price
        WHERE token_id = :token_id AND date = :date
        RETURNING id
    """), {
        'token_id': token_id,
        'volume': volume,
        'rxd_volume': int(volume * price),
        'price': price,
        'date': today
    })
    
    if result.rowcount == 0:
        # Insert new record
        db.execute(text("""
            INSERT INTO token_volume_daily
            (token_id, date, volume_tokens, volume_rxd, trade_count,
             open_price, high_price, low_price, close_price)
            VALUES (:token_id, :date, :volume, :rxd_volume, 1,
                    :price, :price, :price, :price)
            ON CONFLICT (token_id, date) DO UPDATE SET
                volume_tokens = token_volume_daily.volume_tokens + :volume,
                volume_rxd = token_volume_daily.volume_rxd + :rxd_volume,
                trade_count = token_volume_daily.trade_count + 1,
                high_price = GREATEST(token_volume_daily.high_price, :price),
                low_price = LEAST(token_volume_daily.low_price, :price),
                close_price = :price
        """), {
            'token_id': token_id,
            'date': today,
            'volume': volume,
            'rxd_volume': int(volume * price),
            'price': price
        })


def get_price_history(db: Session, token_id: str, limit: int = 100) -> List[Dict]:
    """Get price history for a token."""
    result = db.execute(text("""
        SELECT price_rxd, volume, block_height, recorded_at
        FROM token_price_history
        WHERE token_id = :token_id
        ORDER BY recorded_at DESC
        LIMIT :limit
    """), {'token_id': token_id, 'limit': limit})
    
    return [dict(row._mapping) for row in result.fetchall()]


def get_daily_ohlcv(db: Session, token_id: str, days: int = 30) -> List[Dict]:
    """Get daily OHLCV data for a token."""
    result = db.execute(text("""
        SELECT date, open_price, high_price, low_price, close_price,
               volume_tokens, volume_rxd, trade_count
        FROM token_volume_daily
        WHERE token_id = :token_id
        ORDER BY date DESC
        LIMIT :days
    """), {'token_id': token_id, 'days': days})
    
    return [dict(row._mapping) for row in result.fetchall()]


# ============================================================================
# MINT EVENT TRACKING (for DMINT tokens)
# ============================================================================

def record_mint_event(db: Session, token_id: str, txid: str, amount: int,
                      minter_address: str = None, block_height: int = None) -> int:
    """
    Record a mint event for DMINT tokens.
    
    Args:
        db: Database session
        token_id: Token reference ID
        txid: Mint transaction ID
        amount: Amount minted
        minter_address: Minter's address
        block_height: Block height
        
    Returns:
        ID of the mint record
    """
    result = db.execute(text("""
        INSERT INTO token_mint_events (token_id, txid, minter_address, amount, block_height)
        VALUES (:token_id, :txid, :minter, :amount, :height)
        RETURNING id
    """), {
        'token_id': token_id,
        'txid': txid,
        'minter': minter_address,
        'amount': amount,
        'height': block_height
    })
    
    row = result.fetchone()
    
    # Update circulating supply
    db.execute(text("""
        UPDATE glyph_tokens 
        SET circulating_supply = COALESCE(circulating_supply, 0) + :amount
        WHERE token_id = :token_id
    """), {'token_id': token_id, 'amount': amount})
    
    return row.id if row else None


def get_mint_events(db: Session, token_id: str, limit: int = 100) -> List[Dict]:
    """Get mint history for a DMINT token."""
    result = db.execute(text("""
        SELECT txid, minter_address, amount, block_height, minted_at
        FROM token_mint_events
        WHERE token_id = :token_id
        ORDER BY minted_at DESC
        LIMIT :limit
    """), {'token_id': token_id, 'limit': limit})
    
    return [dict(row._mapping) for row in result.fetchall()]


# ============================================================================
# BATCH OPERATIONS
# ============================================================================

def recalculate_all_supplies(db: Session, batch_size: int = 100) -> int:
    """
    Recalculate circulating supply for all tokens.
    Should be run periodically or after backfill.
    
    Args:
        db: Database session
        batch_size: Number of tokens to process per batch
        
    Returns:
        Number of tokens updated
    """
    updated = 0
    offset = 0
    
    while True:
        result = db.execute(text("""
            SELECT token_id FROM glyph_tokens
            ORDER BY token_id
            LIMIT :limit OFFSET :offset
        """), {'limit': batch_size, 'offset': offset})
        
        tokens = result.fetchall()
        if not tokens:
            break
        
        for row in tokens:
            token_id = row.token_id
            
            # Calculate from holders
            supply = calculate_supply_from_holders(db, token_id)
            holder_count = count_token_holders(db, token_id)
            
            # Update token
            update_token_supply(db, token_id, 
                              circulating=supply, 
                              holder_count=holder_count)
            
            # Update percentages
            calculate_holder_percentages(db, token_id)
            
            updated += 1
        
        offset += batch_size
        db.commit()
        logger.info(f"Recalculated supply for {updated} tokens...")
    
    return updated
