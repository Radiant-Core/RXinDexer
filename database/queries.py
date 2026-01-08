# Optimized queries for RXinDexer database
from typing import Optional
from .models import Block, Transaction, UTXO, UserProfile, Glyph, GlyphAction
from sqlalchemy.orm import Session
from sqlalchemy import func, case

def get_block_by_height(db: Session, height: int):
    return db.query(Block).filter(Block.height == height).first()

def get_transactions_by_block(db: Session, block_id: int):
    return db.query(Transaction).filter(Transaction.block_id == block_id).all()

def get_balance_by_address(db: Session, address: str):
    """
    Returns the RXD balance for the given address.
    Uses pre-computed wallet_balances table with fallback to UTXO aggregation.
    """
    from sqlalchemy import text
    
    # Fast path: use wallet_balances table
    try:
        result = db.execute(text(
            "SELECT balance FROM wallet_balances WHERE address = :addr"
        ), {"addr": address})
        row = result.fetchone()
        if row:
            return float(row[0])
    except Exception:
        pass
    
    # Fallback to UTXO aggregation (for addresses not yet in cache)
    from .models import UTXO
    result = db.query(func.sum(UTXO.value)).filter(
        UTXO.address == address,
        UTXO.spent == False
    ).scalar()
    return float(result) if result else 0.0

def get_unique_wallet_holder_count(db: Session):
    """
    Returns the number of unique wallet addresses with a nonzero RXD balance.
    Uses pre-computed wallet_balances table for speed.
    """
    from sqlalchemy import text
    
    # Fast path: use wallet_balances table
    try:
        result = db.execute(text("SELECT COUNT(*) FROM wallet_balances WHERE balance > 0"))
        count = result.scalar()
        if count and count > 0:
            return count
    except Exception:
        pass
    
    # Slow fallback with timeout
    try:
        db.execute(text("SET LOCAL statement_timeout = '10s'"))
        from .models import UTXO
        addresses = db.query(UTXO.address).filter(UTXO.spent == False).distinct().count()
        return addresses
    except Exception:
        return 0

def get_token_holder_count(db: Session, token_id: str):
    """
    Returns the number of unique holders of a given Glyph token (by token_id).
    Uses address clustering to count multiple addresses belonging to the same wallet as a single holder.
    """
    from sqlalchemy import text
    
    # Try to use address clustering first (more accurate count)
    try:
        exists = db.execute(text("SELECT to_regclass('public.address_clusters')")).scalar()
        if exists:
            result = db.execute(text(
                """
                SELECT COUNT(DISTINCT 
                    CASE 
                        WHEN ac.cluster_id IS NOT NULL THEN 'CLUSTER:' || ac.cluster_id::text
                        ELSE th.address 
                    END
                )
                FROM token_holders th
                LEFT JOIN address_clusters ac ON ac.address = th.address
                WHERE th.token_id = :token_id
                  AND th.balance > 0
                  AND th.address IS NOT NULL
                  AND length(btrim(th.address)) > 0
                """
            ), {"token_id": token_id})
            count = result.scalar()
            if count is not None:
                return int(count)
    except Exception:
        pass
    
    # Fallback to simple address counting if clustering not available
    from sqlalchemy import text, func
    result = db.execute(text(
        """
        SELECT COUNT(DISTINCT address)
        FROM token_holders 
        WHERE token_id = :token_id
          AND balance > 0
          AND address IS NOT NULL
          AND length(btrim(address)) > 0
        """
    ), {"token_id": token_id})
    return result.scalar() or 0


def get_top_wallets(db: Session, limit: int = 100):
    """
    Returns the top wallets by RXD balance from pre-computed wallet_balances table.
    Returns empty list if wallet_balances hasn't been populated yet.
    
    Note: Run `python indexer/refresh_balances.py` to populate the cache.
    """
    from sqlalchemy import text
    
    # Use pre-computed wallet_balances table (fast path)
    try:
        result = db.execute(text("""
            SELECT address, balance 
            FROM wallet_balances 
            ORDER BY balance DESC 
            LIMIT :limit
        """), {"limit": limit})
        rows = result.fetchall()
        
        if rows:
            return [{"address": r[0], "balance": float(r[1])} for r in rows]
    except Exception:
        pass  # Table might not exist yet
    
    # Return empty if cache not populated
    # Don't attempt slow UTXO aggregation - it will timeout with large datasets
    return []


def get_top_glyph_users(db: Session, limit: int = 100):
    """
    Returns the top users by number of owned Glyph tokens (by address).
    """
    results = (
        db.query(
            Glyph.owner,
            func.count(Glyph.id).label('token_count')
        )
        .filter(Glyph.owner.isnot(None))
        .group_by(Glyph.owner)
        .order_by(func.count(Glyph.id).desc())
        .limit(limit)
        .all()
    )
    return [{"address": r[0], "token_count": int(r[1])} for r in results if r[0]]


def get_top_glyph_containers(db: Session, limit: int = 100):
    """
    Returns the top containers by number of tokens (from UserProfile.containers JSON).
    """
    profiles = db.query(UserProfile).all()
    container_counts = {}
    import json
    for profile in profiles:
        containers = profile.containers or []
        if isinstance(containers, str):
            try:
                containers = json.loads(containers)
            except Exception:
                containers = []
        for container in containers:
            container_counts[container] = container_counts.get(container, 0) + 1
    sorted_containers = sorted(container_counts.items(), key=lambda x: x[1], reverse=True)[:limit]
    return [{"container": c[0], "user_count": c[1]} for c in sorted_containers]


def get_recent_transactions(db: Session, limit: int = 100, offset: int = 0):
    """
    Returns the most recent transactions, ordered by time descending.
    """
    return (
        db.query(Transaction)
        .order_by(Transaction.created_at.desc(), Transaction.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def get_top_nft_collections(db: Session, limit: int = 100):
    """
    Returns the top NFT collections by number of NFTs.
    Uses unified glyphs table with container field.
    """
    results = (
        db.query(Glyph.container, func.count(Glyph.id).label('nft_count'))
        .filter(Glyph.token_type == 'NFT')
        .filter(Glyph.container != '')
        .group_by(Glyph.container)
        .order_by(func.count(Glyph.id).desc())
        .limit(limit)
        .all()
    )
    return [{"collection": r[0], "nft_count": int(r[1])} for r in results if r[0]]


def get_user_profile(db: Session, address: str):
    return db.query(UserProfile).filter(UserProfile.address == address).first()


def search_nfts(db: Session, owner: str = None, collection: str = None, metadata_query: dict = None, limit: int = 100):
    """Search NFTs using unified glyphs table."""
    q = db.query(Glyph).filter(Glyph.token_type == 'NFT')
    if owner:
        q = q.filter(Glyph.owner == owner)
    if collection:
        q = q.filter(Glyph.container == collection)
    if metadata_query:
        for k, v in metadata_query.items():
            q = q.filter(Glyph.attrs[k].astext == v)
    return q.order_by(Glyph.id.desc()).limit(limit).all()


def search_glyph_tokens(db: Session, owner: str = None, token_type: str = None, metadata_query: dict = None, limit: int = 100):
    """Search tokens using unified glyphs table."""
    q = db.query(Glyph)
    if owner:
        q = q.filter(Glyph.owner == owner)
    if token_type:
        q = q.filter(Glyph.token_type == token_type.upper())
    if metadata_query:
        for k, v in metadata_query.items():
            q = q.filter(Glyph.attrs[k].astext == v)
    return q.order_by(Glyph.id.desc()).limit(limit).all()


def get_glyph_token_by_id(db: Session, token_id: str):
    """Get detailed information about a single glyph token by its ref."""
    return db.query(Glyph).filter(Glyph.ref == token_id).first()


def get_recent_glyph_tokens(db: Session, limit: int = 20, token_type: str = None):
    """Get the most recently created glyph tokens, optionally filtered by type."""
    q = db.query(Glyph)
    if token_type:
        q = q.filter(Glyph.token_type == token_type.upper())
    return q.order_by(Glyph.id.desc()).limit(limit).all()


def list_glyph_tokens(
    db: Session,
    limit: int = 100,
    offset: int = 0,
    token_type: str = None,
    sort: str = "created_at",
    order: str = "desc",
    mintable: Optional[bool] = None,
):
    """List glyphs with filtering and sorting using unified table."""
    from sqlalchemy import case

    q = db.query(Glyph)
    if token_type:
        q = q.filter(Glyph.token_type == token_type.upper())

    mintable_expr = (
        (Glyph.deploy_method == 'dmint') |
        ((Glyph.max_supply.isnot(None)) &
         ((Glyph.current_supply.is_(None)) | (Glyph.current_supply < Glyph.max_supply)))
    )

    if mintable is True:
        q = q.filter(mintable_expr)
    elif mintable is False:
        q = q.filter(~mintable_expr)

    sort_key = (sort or "created_at").lower()
    is_asc = (order or "desc").lower() == "asc"

    if sort_key == "created_at":
        primary_sort = Glyph.id.asc() if is_asc else Glyph.id.desc()
        q = q.order_by(primary_sort)
    elif sort_key == "genesis_height":
        primary_sort = Glyph.genesis_height.asc() if is_asc else Glyph.genesis_height.desc()
        q = q.order_by(primary_sort, Glyph.id.desc())
    elif sort_key == "holder_count":
        primary_sort = Glyph.holder_count.asc() if is_asc else Glyph.holder_count.desc()
        q = q.order_by(primary_sort, Glyph.id.desc())
    elif sort_key == "circulating_supply":
        primary_sort = Glyph.circulating_supply.asc() if is_asc else Glyph.circulating_supply.desc()
        q = q.order_by(primary_sort, Glyph.id.desc())
    elif sort_key == "max_supply":
        primary_sort = Glyph.max_supply.asc() if is_asc else Glyph.max_supply.desc()
        q = q.order_by(primary_sort, Glyph.id.desc())
    elif sort_key == "current_supply":
        primary_sort = Glyph.current_supply.asc() if is_asc else Glyph.current_supply.desc()
        q = q.order_by(primary_sort, Glyph.id.desc())
    elif sort_key == "mintable":
        mintable_sort = case((mintable_expr, 1), else_=0)
        primary_sort = mintable_sort.asc() if is_asc else mintable_sort.desc()
        q = q.order_by(primary_sort, Glyph.id.desc())
    else:
        primary_sort = Glyph.id.asc() if is_asc else Glyph.id.desc()
        q = q.order_by(primary_sort)

    return q.offset(offset).limit(limit).all()


def get_token_tx_history(db: Session, token_id: str, limit: int = 50):
    """Get transaction history for a specific token using glyph_actions."""
    actions = db.query(GlyphAction).filter(GlyphAction.ref == token_id).order_by(GlyphAction.height.desc()).limit(limit).all()
    if not actions:
        # Fallback to glyph info
        glyph = db.query(Glyph).filter(Glyph.ref == token_id).first()
        if glyph:
            return [{
                "txid": glyph.current_txid,
                "type": "genesis",
                "height": glyph.genesis_height,
                "timestamp": glyph.created_at
            }]
        return []
    
    return [{
        "txid": a.txid,
        "type": a.type,
        "height": a.height,
        "timestamp": a.timestamp
    } for a in actions]


def get_glyph_protocol_stats(db: Session):
    """Get statistics about Glyph token usage by protocol."""
    from sqlalchemy import func, distinct

    # Count tokens by type
    type_counts = db.query(
        Glyph.token_type,
        func.count(Glyph.id).label('count')
    ).group_by(Glyph.token_type).all()
    
    # Count unique token holders
    holder_count = db.query(func.count(distinct(Glyph.owner))).filter(Glyph.owner.isnot(None)).scalar()
    
    # Total token count
    total_tokens = db.query(func.count(Glyph.id)).scalar()
    
    return {
        "total_tokens": total_tokens,
        "unique_holders": holder_count,
        "tokens_by_type": [{"type": t[0], "count": t[1]} for t in type_counts if t[0]],
        "protocol_usage": []
    }


def get_tokens_by_protocol(db: Session, protocol_id: int, limit: int = 100):
    """Get tokens that use a specific protocol ID using unified glyphs table."""
    from sqlalchemy import text
    
    # Use parameterized query for safety
    sql = text("""
        SELECT * FROM glyphs 
        WHERE p @> :protocol_json::jsonb
        ORDER BY created_at DESC 
        LIMIT :limit
    """)
    result = db.execute(sql, {"protocol_json": f'[{protocol_id}]', "limit": limit})
    
    return [dict(row._mapping) for row in result]


def get_recent_blocks(db: Session, limit: int = 100, offset: int = 0):
    return (
        db.query(Block)
        .order_by(Block.height.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


# ============================================================================
# UNIFIED GLYPH MODEL QUERIES (new glyphs table)
# ============================================================================

def get_glyphs(
    db: Session,
    limit: int = 100,
    offset: int = 0,
    query: Optional[str] = None,
    token_type: str = None,
    author: Optional[str] = None,
    container: Optional[str] = None,
    sort: str = "created_at",
    order: str = "desc",
    spent: bool = None,
    is_container: bool = None,
    has_image: Optional[bool] = None,
):
    """List glyphs with filtering and sorting."""
    q = db.query(Glyph)
    
    if token_type:
        q = q.filter(Glyph.token_type == token_type.upper())
    if query:
        search_pattern = f"%{query}%"
        q = q.filter(
            (Glyph.name.ilike(search_pattern))
            | (Glyph.ticker.ilike(search_pattern))
            | (Glyph.description.ilike(search_pattern))
            | (Glyph.ref.ilike(search_pattern))
        )
    if author is not None:
        q = q.filter(Glyph.author == author)
    if container is not None:
        q = q.filter(Glyph.container == container)
    if spent is not None:
        q = q.filter(Glyph.spent == spent)
    if is_container is not None:
        q = q.filter(Glyph.is_container == is_container)
    if has_image is True:
        q = q.filter((Glyph.embed_data.isnot(None) & (Glyph.embed_data != '')) | (Glyph.remote_url.isnot(None) & (Glyph.remote_url != '')))
    elif has_image is False:
        q = q.filter(((Glyph.embed_data.is_(None)) | (Glyph.embed_data == '')) & ((Glyph.remote_url.is_(None)) | (Glyph.remote_url == '')))
    
    sort_key = (sort or "created_at").lower()
    is_asc = (order or "desc").lower() == "asc"
    
    if sort_key == "created_at":
        primary_sort = Glyph.id.asc() if is_asc else Glyph.id.desc()
    elif sort_key == "updated_at":
        primary_sort = Glyph.updated_at.asc() if is_asc else Glyph.updated_at.desc()
    elif sort_key == "height":
        primary_sort = Glyph.height.asc() if is_asc else Glyph.height.desc()
    elif sort_key == "name":
        primary_sort = Glyph.name.asc() if is_asc else Glyph.name.desc()
    else:
        primary_sort = Glyph.id.asc() if is_asc else Glyph.id.desc()

    return q.order_by(primary_sort).offset(offset).limit(limit).all()


def get_recent_glyphs(db: Session, limit: int = 20, token_type: str = None):
    """Get the most recently created glyphs."""
    q = db.query(Glyph)
    if token_type:
        q = q.filter(Glyph.token_type == token_type.upper())
    return q.order_by(Glyph.id.desc()).limit(limit).all()


def get_glyph_by_ref(db: Session, ref: str):
    """Get a glyph by its ref (primary identifier)."""
    return db.query(Glyph).filter(Glyph.ref == ref).first()


def search_glyphs(db: Session, query: str = None, token_type: str = None, author: str = None, container: str = None, limit: int = 100):
    """Search glyphs by name, ticker, author, or container."""
    q = db.query(Glyph)
    
    if query:
        search_pattern = f"%{query}%"
        q = q.filter(
            (Glyph.name.ilike(search_pattern)) |
            (Glyph.ticker.ilike(search_pattern)) |
            (Glyph.description.ilike(search_pattern))
        )
    if token_type:
        q = q.filter(Glyph.token_type == token_type.upper())
    if author:
        q = q.filter(Glyph.author == author)
    if container:
        q = q.filter(Glyph.container == container)
    
    return q.order_by(Glyph.id.desc()).limit(limit).all()


def get_glyph_stats(db: Session):
    """Get statistics about glyphs."""
    from sqlalchemy import distinct
    
    # Count by token_type
    type_counts = db.query(
        Glyph.token_type,
        func.count(Glyph.id).label('count')
    ).group_by(Glyph.token_type).all()
    
    # Total count
    total = db.query(func.count(Glyph.id)).scalar() or 0
    
    # Container count
    containers = db.query(func.count(Glyph.id)).filter(Glyph.is_container == True).scalar() or 0
    
    # Unique authors
    authors = db.query(func.count(distinct(Glyph.author))).filter(Glyph.author != '').scalar() or 0
    
    return {
        "total": total,
        "containers": containers,
        "unique_authors": authors,
        "by_type": {t[0]: t[1] for t in type_counts if t[0]}
    }


def get_glyph_actions(db: Session, ref: str, limit: int = 50):
    """Get action history for a glyph."""
    return db.query(GlyphAction).filter(GlyphAction.ref == ref).order_by(GlyphAction.height.desc()).limit(limit).all()


def get_glyphs_by_author(db: Session, author_ref: str, limit: int = 100):
    """Get all glyphs created by a specific author."""
    return db.query(Glyph).filter(Glyph.author == author_ref).order_by(Glyph.id.desc()).limit(limit).all()


def get_glyphs_in_container(db: Session, container_ref: str, limit: int = 100):
    """Get all glyphs in a specific container."""
    return db.query(Glyph).filter(Glyph.container == container_ref).order_by(Glyph.id.desc()).limit(limit).all()


def get_containers(db: Session, limit: int = 100):
    """Get all container glyphs."""
    return db.query(Glyph).filter(Glyph.is_container == True).order_by(Glyph.id.desc()).limit(limit).all()


def get_users(db: Session, limit: int = 100):
    """Get all user-type glyphs."""
    return db.query(Glyph).filter(Glyph.token_type == 'USER').order_by(Glyph.id.desc()).limit(limit).all()
