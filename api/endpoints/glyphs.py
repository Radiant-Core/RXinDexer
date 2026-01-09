"""API endpoints for unified Glyph model (new glyphs table)."""
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, case, and_, cast, text, literal
from sqlalchemy.sql.sqltypes import String
from typing import List, Optional

from api.dependencies import get_db, get_current_authenticated_user
from api.schemas import (
    GlyphResponse,
    GlyphActionResponse,
    GlyphStatsResponse,
    FTTokenTableRowResponse,
    FTDuplicatesResponse,
)
from api.cache import cache, CACHE_TTL_SHORT, CACHE_TTL_MEDIUM
from database import queries
from database.models import Glyph, GlyphToken, TokenHolder, TokenBurn, AddressCluster

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


@router.get("", response_model=List[GlyphResponse], summary="List all glyphs", tags=["glyphs"])
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


@router.post("/refresh-materialized-views", tags=["glyphs"], summary="Refresh materialized views")
def refresh_materialized_views(db: Session = Depends(get_db)):
    """Manually refresh materialized views for latest data."""
    try:
        # All views now support CONCURRENT refresh
        db.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_token_holder_stats"))
        db.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_token_burn_stats"))
        db.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_glyph_token_stats"))
        db.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_ft_glyph_summary"))
        db.commit()
        
        # Clear relevant cache entries
        from api.cache import cache
        cache.delete("glyphs:fts:table:1:60::::holders:desc")
        cache.delete("glyphs:fts:table:1:60::true:holders:desc")
        
        return {"message": "Materialized views refreshed successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error refreshing materialized views: {str(e)}")


@router.get("/fts/table", response_model=List[FTTokenTableRowResponse], summary="List FT tokens in table format", tags=["glyphs"])
def list_ft_table(
    response: Response,
    page: int = Query(1, ge=1),
    limit: int = Query(60, ge=1, le=100),
    q: Optional[str] = Query(None, description="Search query (name, ticker, description, ref)"),
    has_image: Optional[bool] = Query(None, description="Filter by image presence"),
    holders_mode: str = Query("address", description="Holder counting mode: address | cluster"),
    sort: str = Query("holders", description="Sort by: name, ticker, holders, circulating, supply, burned, difficulty, premine, mined, height, created_at, updated_at"),
    order: str = Query("desc", description="Order: asc, desc"),
    db: Session = Depends(get_db),
):
    """List FT tokens as enriched table rows (using materialized views for speed)."""
    cache_key = f"glyphs:fts:table:{page}:{limit}:{q}:{has_image}:{holders_mode}:{sort}:{order}"
    cached = cache.get(cache_key)
    if cached is not None:
        response.headers["X-Page"] = str(page)
        response.headers["X-Limit"] = str(limit)
        return cached

    offset = (page - 1) * limit
    is_asc = (order or "desc").lower() == "asc"

    # Build the SQL query dynamically
    # Using DISTINCT ON to get the canonical (first) record for each token group
    sql_query = """
        SELECT DISTINCT ON (COALESCE(NULLIF(trim(display_name), ''), NULLIF(trim(display_ticker), ''), ref), NULLIF(trim(display_ticker), ''))
            id, ref, token_type, display_name as name, display_ticker as ticker,
            height, created_at, updated_at, has_image, holder_count, circulating_supply,
            max_supply, burned_supply, difficulty, premine,
            (circulating_supply + burned_supply) as minted_supply,
            CASE 
                WHEN max_supply IS NOT NULL AND max_supply > 0 
                THEN (premine * 100.0 / max_supply)
                ELSE NULL 
            END as premine_percent,
            (difficulty IS NOT NULL) as is_minable,
            CASE 
                WHEN max_supply IS NOT NULL AND max_supply > 0 
                THEN (GREATEST((circulating_supply + burned_supply) - premine, 0) * 100.0 / max_supply)
                ELSE NULL 
            END as mined_percent
        FROM mv_ft_glyph_summary
        WHERE 1=1
    """

    # Apply filters
    params = {}
    if has_image is True:
        sql_query += " AND has_image = true"
    elif has_image is False:
        sql_query += " AND has_image = false"

    if q:
        like_term = f"%{q.strip()}%"
        sql_query += f" AND (ref ILIKE :like_term OR display_name ILIKE :like_term OR display_ticker ILIKE :like_term)"
        params['like_term'] = like_term

    # Apply sorting
    sort_key = (sort or "holders").lower()
    sort_column_map = {
        "name": "display_name",
        "ticker": "display_ticker", 
        "holders": "holder_count",
        "circulating": "circulating_supply",
        "supply": "max_supply",
        "burned": "burned_supply",
        "difficulty": "difficulty",
        "premine": "premine_percent",
        "mined": "mined_percent",
        "height": "height",
        "created_at": "created_at",
        "updated_at": "updated_at",
    }

    sort_column = sort_column_map.get(sort_key, "holder_count")
    sort_direction = "ASC" if is_asc else "DESC"
    
    # DISTINCT ON requires ORDER BY to start with the DISTINCT expression
    # We need to order by the grouping columns first, then by our desired sort
    sql_query += f" ORDER BY COALESCE(NULLIF(trim(display_name), ''), NULLIF(trim(display_ticker), ''), ref), NULLIF(trim(display_ticker), ''),"
    sql_query += f" holder_count DESC, height ASC, id DESC"
    
    # Apply final sorting with a subquery
    inner_query = sql_query
    sql_query = f"SELECT * FROM ({inner_query}) t ORDER BY {sort_column} {sort_direction}"
    sql_query += " LIMIT :limit OFFSET :offset"
    
    params['limit'] = limit
    params['offset'] = offset

    # Execute the query
    rows = db.execute(text(sql_query), params).fetchall()

    # Convert to response format
    result = []
    for row in rows:
        result.append({
            "id": int(row.id),
            "ref": row.ref,
            "token_type": row.token_type,
            "name": row.name,
            "ticker": row.ticker,
            "height": int(row.height) if row.height is not None else None,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "has_image": bool(row.has_image),
            "holder_count": int(row.holder_count) if row.holder_count is not None else 0,
            "circulating_supply": _int_to_str(row.circulating_supply),
            "max_supply": _int_to_str(row.max_supply),
            "burned_supply": _int_to_str(row.burned_supply),
            "difficulty": int(row.difficulty) if row.difficulty is not None else None,
            "minted_supply": _int_to_str(row.minted_supply),
            "premine_percent": float(row.premine_percent) if row.premine_percent is not None else None,
            "is_minable": bool(row.is_minable),
            "mined_percent": float(row.mined_percent) if row.mined_percent is not None else None,
        })

    response.headers["X-Page"] = str(page)
    response.headers["X-Limit"] = str(limit)
    cache.set(cache_key, result, CACHE_TTL_MEDIUM)
    return result


@router.get("/fts/duplicates/{ref}", response_model=FTDuplicatesResponse, summary="Get FT token duplicates", tags=["glyphs"])
def get_ft_duplicates(ref: str, holders_mode: str = Query("address", description="Holder counting mode: address | cluster"), db: Session = Depends(get_db)):
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

    holders_mode_key = (holders_mode or "address").lower()
    if holders_mode_key == "cluster":
        try:
            exists = db.execute(text("SELECT to_regclass('public.address_clusters')")).scalar()
            if not exists:
                holders_mode_key = "address"
        except Exception:
            holders_mode_key = "address"
    cluster_key_expr = func.coalesce(
        func.concat('CLUSTER:', cast(AddressCluster.cluster_id, String)),
        TokenHolder.address,
    )

    if holders_mode_key == "cluster":
        holders_subq = (
            db.query(
                TokenHolder.token_id.label("token_id"),
                func.count(func.distinct(cluster_key_expr))
                .filter(
                    TokenHolder.balance > 0,
                    TokenHolder.address.isnot(None),
                    func.length(func.trim(TokenHolder.address)) > 0,
                )
                .label("holder_count"),
            )
            .outerjoin(AddressCluster, AddressCluster.address == TokenHolder.address)
            .group_by(TokenHolder.token_id)
            .subquery()
        )
    else:
        holders_subq = (
            db.query(
                TokenHolder.token_id.label("token_id"),
                func.count(func.distinct(TokenHolder.address))
                .filter(
                    TokenHolder.balance > 0,
                    TokenHolder.address.isnot(None),
                    func.length(func.trim(TokenHolder.address)) > 0,
                )
                .label("holder_count"),
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
        holders = int(r.holder_count) if r.holder_count is not None else 0
        height = int(r.height) if r.height is not None else 2147483647
        return (-holders, height, r.ref)

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


@router.get("/recent", response_model=List[GlyphResponse], summary="Get recent glyphs", tags=["glyphs"])
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


@router.get("/stats", response_model=GlyphStatsResponse, summary="Get glyph statistics", tags=["glyphs"])
def get_glyph_stats(db: Session = Depends(get_db)):
    """Get statistics about glyphs."""
    cache_key = "glyphs:stats"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    stats = queries.get_glyph_stats(db)
    cache.set(cache_key, stats, CACHE_TTL_MEDIUM)
    return stats


@router.get("/search", response_model=List[GlyphResponse], summary="Search glyphs", tags=["glyphs"])
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


@router.get("/containers", response_model=List[GlyphResponse], summary="Get container glyphs", tags=["glyphs"])
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


@router.get("/users", response_model=List[GlyphResponse], summary="Get user glyphs", tags=["glyphs"])
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


@router.get("/{ref}", response_model=GlyphResponse, summary="Get glyph by ref", tags=["glyphs"])
def get_glyph(ref: str, db: Session = Depends(get_db)):
    """Get a glyph by its ref."""
    glyph = queries.get_glyph_by_ref(db, ref)
    if not glyph:
        raise HTTPException(status_code=404, detail=f"Glyph {ref} not found")
    return glyph


@router.get("/{ref}/actions", response_model=List[GlyphActionResponse], summary="Get glyph actions", tags=["glyphs"])
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


@router.get("/by-author/{author_ref}", response_model=List[GlyphResponse], summary="Get glyphs by author", tags=["glyphs"])
def get_glyphs_by_author(
    author_ref: str,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Get all glyphs created by a specific author."""
    return queries.get_glyphs_by_author(db, author_ref, limit=limit)


@router.get("/in-container/{container_ref}", response_model=List[GlyphResponse], summary="Get glyphs in container", tags=["glyphs"])
def get_glyphs_in_container(
    container_ref: str,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Get all glyphs in a specific container."""
    return queries.get_glyphs_in_container(db, container_ref, limit=limit)
