"""API endpoints for unified Glyph model (new glyphs table)."""
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, case
from typing import List, Optional

from api.dependencies import get_db
from api.schemas import (
    GlyphResponse,
    GlyphActionResponse,
    GlyphStatsResponse,
    FTTokenTableRowResponse,
    FTDuplicatesResponse,
)
from api.cache import cache, CACHE_TTL_SHORT, CACHE_TTL_MEDIUM
from database import queries
from database.models import Glyph, GlyphToken, TokenHolder, TokenBurn

router = APIRouter(prefix="/glyphs", tags=["glyphs"])


def _int_to_str(value) -> str | None:
    if value is None:
        return None
    try:
        return str(int(value))
    except Exception:
        try:
            return str(value)
        except Exception:
            return None


@router.get("", response_model=List[GlyphResponse])
def list_glyphs(
    response: Response,
    page: int = Query(1, ge=1),
    token_type: Optional[str] = Query(None, description="Filter by token type: NFT, FT, DAT, CONTAINER, USER"),
    limit: int = Query(100, ge=1, le=500),
    q: Optional[str] = Query(None, description="Search query (name, ticker, description, ref)"),
    author: Optional[str] = Query(None, description="Filter by author ref"),
    container: Optional[str] = Query(None, description="Filter by container ref"),
    sort: str = Query("created_at", description="Sort by: created_at, updated_at, height, name"),
    order: str = Query("desc", description="Order: asc, desc"),
    spent: Optional[bool] = Query(None, description="Filter by spent status"),
    is_container: Optional[bool] = Query(None, description="Filter containers only"),
    has_image: Optional[bool] = Query(None, description="Filter by image presence"),
    db: Session = Depends(get_db),
):
    """List glyphs with filtering and sorting."""
    offset = (page - 1) * limit
    glyphs = queries.get_glyphs(
        db,
        limit=limit,
        offset=offset,
        query=q,
        token_type=token_type,
        author=author,
        container=container,
        sort=sort,
        order=order,
        spent=spent,
        is_container=is_container,
        has_image=has_image,
    )

    response.headers["X-Page"] = str(page)
    response.headers["X-Limit"] = str(limit)
    return glyphs


@router.get("/fts/table", response_model=List[FTTokenTableRowResponse])
def list_ft_table(
    response: Response,
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=500),
    q: Optional[str] = Query(None, description="Search query (name, ticker, description, ref)"),
    sort: str = Query("holders", description="Sort by: name, ticker, holders, circulating, supply, burned, difficulty, premine, mined, height, created_at, updated_at"),
    order: str = Query("desc", description="Order: asc, desc"),
    db: Session = Depends(get_db),
):
    """List FT tokens as enriched table rows (no N+1), with server-side sorting."""
    cache_key = f"glyphs:fts:table:{page}:{limit}:{q}:{sort}:{order}"
    cached = cache.get(cache_key)
    if cached is not None:
        response.headers["X-Page"] = str(page)
        response.headers["X-Limit"] = str(limit)
        return cached

    offset = (page - 1) * limit
    is_asc = (order or "desc").lower() == "asc"

    has_image_expr = or_(Glyph.embed_data.isnot(None), Glyph.remote_url.isnot(None)).label("has_image")

    holders_subq = (
        db.query(
            TokenHolder.token_id.label("token_id"),
            func.count(TokenHolder.id).label("holder_count"),
            func.sum(TokenHolder.balance).label("circulating_supply"),
        )
        .group_by(TokenHolder.token_id)
        .subquery()
    )

    burns_subq = (
        db.query(
            TokenBurn.token_id.label("token_id"),
            func.sum(TokenBurn.amount).label("burned_supply"),
        )
        .group_by(TokenBurn.token_id)
        .subquery()
    )

    legacy_subq = (
        db.query(
            GlyphToken.token_id.label("token_id"),
            func.max(GlyphToken.max_supply).label("max_supply"),
            func.max(GlyphToken.difficulty).label("difficulty"),
            func.max(GlyphToken.premine).label("premine"),
        )
        .group_by(GlyphToken.token_id)
        .subquery()
    )

    holder_count_expr = func.coalesce(holders_subq.c.holder_count, 0).label("holder_count")
    circulating_expr = func.coalesce(holders_subq.c.circulating_supply, 0).label("circulating_supply")
    burned_expr = func.coalesce(burns_subq.c.burned_supply, 0).label("burned_supply")
    minted_supply_expr = (circulating_expr + burned_expr).label("minted_supply")

    premine_expr = func.coalesce(legacy_subq.c.premine, 0)
    is_minable_expr = (legacy_subq.c.difficulty.isnot(None)).label("is_minable")

    premine_percent_expr = (
        (premine_expr * 100.0) / func.nullif(legacy_subq.c.max_supply, 0)
    ).label("premine_percent")

    mined_amount_expr = func.greatest(minted_supply_expr - premine_expr, 0)
    mined_percent_expr = (
        case(
            (
                (legacy_subq.c.max_supply.isnot(None)) & (legacy_subq.c.max_supply > 0),
                (mined_amount_expr * 100.0) / func.nullif(legacy_subq.c.max_supply, 0),
            ),
            else_=None,
        )
    ).label("mined_percent")

    name_key_expr = func.coalesce(
        func.nullif(func.trim(Glyph.name), ""),
        func.nullif(func.trim(Glyph.ticker), ""),
        Glyph.ref,
    )
    ticker_key_expr = func.nullif(func.trim(Glyph.ticker), "")

    display_name_expr = func.coalesce(
        func.nullif(func.trim(Glyph.name), ""),
        func.nullif(func.trim(Glyph.ticker), ""),
        Glyph.ref,
    ).label("name")

    # Canonical token selection:
    # - One row per (name, ticker)
    # - Canonical = oldest height (lowest), tie-break by highest holders
    canonical_rank_expr = func.row_number().over(
        partition_by=(name_key_expr, ticker_key_expr),
        order_by=[
            func.coalesce(Glyph.height, 2147483647).asc(),
            func.coalesce(holders_subq.c.holder_count, 0).desc(),
            Glyph.id.asc(),
        ],
    ).label("canonical_rank")

    base = (
        db.query(
            Glyph.id.label("id"),
            Glyph.ref.label("ref"),
            Glyph.token_type.label("token_type"),
            display_name_expr,
            ticker_key_expr.label("ticker"),
            Glyph.height.label("height"),
            Glyph.created_at.label("created_at"),
            Glyph.updated_at.label("updated_at"),
            has_image_expr,
            holder_count_expr,
            circulating_expr,
            legacy_subq.c.max_supply,
            burned_expr,
            legacy_subq.c.difficulty,
            minted_supply_expr,
            premine_percent_expr,
            is_minable_expr,
            mined_percent_expr,
            canonical_rank_expr,
        )
        .outerjoin(holders_subq, holders_subq.c.token_id == Glyph.ref)
        .outerjoin(burns_subq, burns_subq.c.token_id == Glyph.ref)
        .outerjoin(legacy_subq, legacy_subq.c.token_id == Glyph.ref)
        .filter(Glyph.token_type == "FT")
    )

    if q:
        like = f"%{q.strip()}%"
        base = base.filter(
            (Glyph.ref.ilike(like))
            | (Glyph.name.ilike(like))
            | (Glyph.ticker.ilike(like))
            | (Glyph.description.ilike(like))
        )

    canonical_subq = base.subquery()

    sort_key = (sort or "holders").lower()
    sort_map = {
        "name": canonical_subq.c.name,
        "ticker": canonical_subq.c.ticker,
        "holders": canonical_subq.c.holder_count,
        "circulating": canonical_subq.c.circulating_supply,
        "supply": canonical_subq.c.max_supply,
        "burned": canonical_subq.c.burned_supply,
        "difficulty": canonical_subq.c.difficulty,
        "premine": canonical_subq.c.premine_percent,
        "mined": canonical_subq.c.mined_percent,
        "height": canonical_subq.c.height,
        "created_at": canonical_subq.c.created_at,
        "updated_at": canonical_subq.c.updated_at,
    }

    primary = sort_map.get(sort_key, canonical_subq.c.holder_count)
    primary = primary.asc() if is_asc else primary.desc()

    rows = (
        db.query(
            canonical_subq.c.id,
            canonical_subq.c.ref,
            canonical_subq.c.token_type,
            canonical_subq.c.name,
            canonical_subq.c.ticker,
            canonical_subq.c.height,
            canonical_subq.c.created_at,
            canonical_subq.c.updated_at,
            canonical_subq.c.has_image,
            canonical_subq.c.holder_count,
            canonical_subq.c.circulating_supply,
            canonical_subq.c.max_supply,
            canonical_subq.c.burned_supply,
            canonical_subq.c.difficulty,
            canonical_subq.c.minted_supply,
            canonical_subq.c.premine_percent,
            canonical_subq.c.is_minable,
            canonical_subq.c.mined_percent,
        )
        .filter(canonical_subq.c.canonical_rank == 1)
        .order_by(primary, canonical_subq.c.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    result = []
    for r in rows:
        result.append(
            {
                "id": int(r.id),
                "ref": r.ref,
                "token_type": r.token_type,
                "name": r.name,
                "ticker": r.ticker,
                "height": int(r.height) if r.height is not None else None,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
                "has_image": bool(r.has_image),
                "holder_count": int(r.holder_count) if r.holder_count is not None else 0,
                "circulating_supply": _int_to_str(r.circulating_supply),
                "max_supply": _int_to_str(r.max_supply),
                "burned_supply": _int_to_str(r.burned_supply),
                "difficulty": int(r.difficulty) if r.difficulty is not None else None,
                "minted_supply": _int_to_str(r.minted_supply),
                "premine_percent": float(r.premine_percent) if r.premine_percent is not None else None,
                "is_minable": bool(r.is_minable),
                "mined_percent": float(r.mined_percent) if r.mined_percent is not None else None,
            }
        )

    response.headers["X-Page"] = str(page)
    response.headers["X-Limit"] = str(limit)
    cache.set(cache_key, result, CACHE_TTL_MEDIUM)
    return result


@router.get("/fts/duplicates/{ref}", response_model=FTDuplicatesResponse)
def get_ft_duplicates(ref: str, db: Session = Depends(get_db)):
    glyph = queries.get_glyph_by_ref(db, ref)
    if not glyph or glyph.token_type != "FT":
        raise HTTPException(status_code=404, detail=f"FT glyph {ref} not found")

    name_key = (getattr(glyph, "name", None) or "").strip() or (getattr(glyph, "ticker", None) or "").strip()
    ticker_key = (getattr(glyph, "ticker", None) or "").strip() or None

    if not name_key:
        return {
            "canonical": {
                "ref": glyph.ref,
                "name": glyph.ref,
                "ticker": None,
                "height": int(glyph.height) if glyph.height is not None else None,
                "holder_count": 0,
                "has_image": bool(glyph.embed_data or glyph.remote_url),
            },
            "duplicates": [],
            "is_canonical": True,
        }

    holders_subq = (
        db.query(
            TokenHolder.token_id.label("token_id"),
            func.count(TokenHolder.id).label("holder_count"),
        )
        .group_by(TokenHolder.token_id)
        .subquery()
    )

    has_image_expr = or_(Glyph.embed_data.isnot(None), Glyph.remote_url.isnot(None)).label("has_image")
    holder_count_expr = func.coalesce(holders_subq.c.holder_count, 0).label("holder_count")

    name_key_expr = func.coalesce(
        func.nullif(func.trim(Glyph.name), ""),
        func.nullif(func.trim(Glyph.ticker), ""),
        Glyph.ref,
    )
    ticker_key_expr = func.nullif(func.trim(Glyph.ticker), "")

    display_name_expr = func.coalesce(
        func.nullif(func.trim(Glyph.name), ""),
        func.nullif(func.trim(Glyph.ticker), ""),
        Glyph.ref,
    ).label("name")

    rows = (
        db.query(
            Glyph.ref.label("ref"),
            display_name_expr,
            ticker_key_expr.label("ticker"),
            Glyph.height.label("height"),
            has_image_expr,
            holder_count_expr,
        )
        .outerjoin(holders_subq, holders_subq.c.token_id == Glyph.ref)
        .filter(Glyph.token_type == "FT")
        .filter(name_key_expr == name_key)
        .filter(ticker_key_expr.is_(None) if ticker_key is None else (ticker_key_expr == ticker_key))
        .all()
    )

    if not rows:
        raise HTTPException(status_code=404, detail="No rows found for duplicate group")

    def _rank_key(r):
        height = int(r.height) if r.height is not None else 2147483647
        holders = int(r.holder_count) if r.holder_count is not None else 0
        return (height, -holders, r.ref)

    rows_sorted = sorted(rows, key=_rank_key)
    canonical = rows_sorted[0]
    duplicates = [r for r in rows_sorted[1:] if r.ref != canonical.ref]

    return {
        "canonical": {
            "ref": canonical.ref,
            "name": canonical.name,
            "ticker": canonical.ticker,
            "height": int(canonical.height) if canonical.height is not None else None,
            "holder_count": int(canonical.holder_count) if canonical.holder_count is not None else 0,
            "has_image": bool(canonical.has_image),
        },
        "duplicates": [
            {
                "ref": r.ref,
                "name": r.name,
                "ticker": r.ticker,
                "height": int(r.height) if r.height is not None else None,
                "holder_count": int(r.holder_count) if r.holder_count is not None else 0,
                "has_image": bool(r.has_image),
            }
            for r in duplicates
        ],
        "is_canonical": ref == canonical.ref,
    }


@router.get("/recent", response_model=List[GlyphResponse])
def get_recent_glyphs(
    token_type: Optional[str] = Query(None, description="Filter by token type"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Get the most recently created glyphs."""
    cache_key = f"glyphs:recent:{token_type}:{limit}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    glyphs = queries.get_recent_glyphs(db, limit=limit, token_type=token_type)
    cache.set(cache_key, glyphs, CACHE_TTL_SHORT)
    return glyphs


@router.get("/stats", response_model=GlyphStatsResponse)
def get_glyph_stats(db: Session = Depends(get_db)):
    """Get statistics about glyphs."""
    cache_key = "glyphs:stats"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    stats = queries.get_glyph_stats(db)
    cache.set(cache_key, stats, CACHE_TTL_MEDIUM)
    return stats


@router.get("/search", response_model=List[GlyphResponse])
def search_glyphs(
    q: Optional[str] = Query(None, description="Search query (name, ticker, description)"),
    token_type: Optional[str] = Query(None, description="Filter by token type"),
    author: Optional[str] = Query(None, description="Filter by author ref"),
    container: Optional[str] = Query(None, description="Filter by container ref"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Search glyphs by name, ticker, author, or container."""
    glyphs = queries.search_glyphs(
        db,
        query=q,
        token_type=token_type,
        author=author,
        container=container,
        limit=limit,
    )
    return glyphs


@router.get("/containers", response_model=List[GlyphResponse])
def get_containers(
    response: Response,
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=500),
    q: Optional[str] = Query(None, description="Search query (name, ticker, description, ref)"),
    sort: str = Query("created_at", description="Sort by: created_at, updated_at, height, name"),
    order: str = Query("desc", description="Order: asc, desc"),
    spent: Optional[bool] = Query(None, description="Filter by spent status"),
    has_image: Optional[bool] = Query(None, description="Filter by image presence"),
    db: Session = Depends(get_db),
):
    """Get all container glyphs."""
    offset = (page - 1) * limit
    glyphs = queries.get_glyphs(
        db,
        limit=limit,
        offset=offset,
        query=q,
        token_type="CONTAINER",
        sort=sort,
        order=order,
        spent=spent,
        is_container=True,
        has_image=has_image,
    )
    response.headers["X-Page"] = str(page)
    response.headers["X-Limit"] = str(limit)
    return glyphs


@router.get("/users", response_model=List[GlyphResponse])
def get_users(
    response: Response,
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=500),
    q: Optional[str] = Query(None, description="Search query (name, ticker, description, ref)"),
    sort: str = Query("created_at", description="Sort by: created_at, updated_at, height, name"),
    order: str = Query("desc", description="Order: asc, desc"),
    spent: Optional[bool] = Query(None, description="Filter by spent status"),
    has_image: Optional[bool] = Query(None, description="Filter by image presence"),
    db: Session = Depends(get_db),
):
    """Get all user-type glyphs."""
    offset = (page - 1) * limit
    glyphs = queries.get_glyphs(
        db,
        limit=limit,
        offset=offset,
        query=q,
        token_type="USER",
        sort=sort,
        order=order,
        spent=spent,
        has_image=has_image,
    )
    response.headers["X-Page"] = str(page)
    response.headers["X-Limit"] = str(limit)
    return glyphs


@router.get("/{ref}", response_model=GlyphResponse)
def get_glyph(ref: str, db: Session = Depends(get_db)):
    """Get a glyph by its ref."""
    glyph = queries.get_glyph_by_ref(db, ref)
    if not glyph:
        raise HTTPException(status_code=404, detail=f"Glyph {ref} not found")
    return glyph


@router.get("/{ref}/actions", response_model=List[GlyphActionResponse])
def get_glyph_actions(
    ref: str,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Get action history for a glyph."""
    glyph = queries.get_glyph_by_ref(db, ref)
    if not glyph:
        raise HTTPException(status_code=404, detail=f"Glyph {ref} not found")
    
    actions = queries.get_glyph_actions(db, ref, limit=limit)
    return actions


@router.get("/by-author/{author_ref}", response_model=List[GlyphResponse])
def get_glyphs_by_author(
    author_ref: str,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Get all glyphs created by a specific author."""
    return queries.get_glyphs_by_author(db, author_ref, limit=limit)


@router.get("/in-container/{container_ref}", response_model=List[GlyphResponse])
def get_glyphs_in_container(
    container_ref: str,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Get all glyphs in a specific container."""
    return queries.get_glyphs_in_container(db, container_ref, limit=limit)
