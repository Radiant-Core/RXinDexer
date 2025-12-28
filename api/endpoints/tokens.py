from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import text, cast, or_
from sqlalchemy.dialects.postgresql import JSONB
from typing import List, Optional
import re
import json
import base64
from decimal import Decimal
from functools import lru_cache

from api.dependencies import get_db
from api.schemas import (
    GlyphTokenResponse, NFTResponse, NFTCollectionResponse, 
    HolderCountResponse, TopGlyphUserResponse, TopGlyphContainerResponse,
    TokenFileResponse, ContainerResponse
)
from api.cache import cache, CACHE_TTL_SHORT, CACHE_TTL_MEDIUM, CACHE_TTL_LONG
from database.models import NFT, TokenFile, Container, GlyphToken, Glyph, Transaction, TransactionInput
from database import queries
from database.queries import (
    get_top_nft_collections, search_nfts, get_recent_glyph_tokens,
    get_glyph_protocol_stats, get_glyph_token_by_id, get_tokens_by_protocol,
    get_token_tx_history, get_token_holder_count, get_top_glyph_users,
    get_top_glyph_containers
)
from database.session import SessionLocal

router = APIRouter()


def _glyph_to_legacy_token_dict(g: Glyph) -> dict:
    p = getattr(g, 'p', None)
    is_dmint = isinstance(p, list) and (4 in p or '4' in p)
    token_type = getattr(g, 'token_type', None)
    return {
        "token_id": g.ref,
        "txid": None,
        "type": "dmint" if is_dmint else (token_type.lower() if token_type else None),
        "name": g.name or None,
        "description": g.description or None,
        "ticker": g.ticker,
        "token_type_name": g.type or None,
        "immutable": g.immutable,
        "attrs": g.attrs,
        "location": g.location,
        "author": g.author or None,
        "container": g.container or None,
        "protocols": p,
        "protocol_type": 4 if is_dmint else None,
        "icon_mime_type": g.embed_type,
        "icon_url": g.remote_url,
        "genesis_height": g.height,
        "latest_height": g.height,
        "created_at": g.created_at,
        "updated_at": g.updated_at,
    }


def _resolve_token_ref(db: Session, token_id: str) -> tuple[str, Glyph | None]:
    if not isinstance(token_id, str):
        return token_id, None

    # Be tolerant of copy/paste artifacts (whitespace/newlines) by removing
    # non-hex characters. Token refs are 72 hex chars.
    normalized = re.sub(r'[^0-9a-fA-F]', '', token_id)
    if normalized:
        token_id = normalized.lower()

    glyph = queries.get_glyph_by_ref(db, token_id)
    if glyph:
        return token_id, glyph

    if isinstance(token_id, str) and len(token_id) == 72:
        # Support users pasting refs as outpoint strings (txid big-endian + vout bytes).
        # The canonical glyph ref is txid little-endian + vout little-endian.
        try:
            txid_part = token_id[:64]
            vout_hex = token_id[64:72]
            txid_le = bytes.fromhex(txid_part)[::-1].hex()
            vout_bytes = bytes.fromhex(vout_hex)

            # Candidate 1: assume vout is already little-endian bytes
            cand1 = txid_le + vout_hex
            if cand1 != token_id:
                glyph = queries.get_glyph_by_ref(db, cand1)
                if glyph:
                    return cand1, glyph

            # Candidate 2: assume vout bytes were provided big-endian, reverse to little
            cand2 = txid_le + vout_bytes[::-1].hex()
            if cand2 != token_id:
                glyph = queries.get_glyph_by_ref(db, cand2)
                if glyph:
                    return cand2, glyph
        except Exception:
            pass

    return token_id, None


def _spent_backfill_is_complete(db: Session) -> bool:
    try:
        row = db.execute(
            text("SELECT is_complete, COALESCE(last_processed_id, 0) FROM backfill_status WHERE backfill_type = 'spent'")
        ).fetchone()
        if not row or not bool(row[0]):
            return False

        last_processed_id = int(row[1] or 0)
        max_input_id = int(db.execute(text("SELECT COALESCE(MAX(id), 0) FROM transaction_inputs")).scalar() or 0)
        return last_processed_id >= max_input_id
    except Exception:
        db.rollback()
        return False


def _resolve_reveal_txid(db: Session, token_ref: str, glyph: Glyph | None) -> str | None:
    try:
        rop = getattr(glyph, 'reveal_outpoint', None) if glyph is not None else None
        if isinstance(rop, str) and ':' in rop:
            return rop.split(':', 1)[0]
    except Exception:
        pass

    if not isinstance(token_ref, str) or len(token_ref) != 72:
        return None

    try:
        txid_part = token_ref[:64]
        vout_hex = token_ref[64:72]
        vout_bytes = bytes.fromhex(vout_hex)
        vout_candidates = list({
            int.from_bytes(vout_bytes, 'little'),
            int.from_bytes(vout_bytes, 'big'),
        })

        txid_candidates = [txid_part]
        try:
            txid_candidates.append(bytes.fromhex(txid_part)[::-1].hex())
        except Exception:
            pass

        for spent_txid in txid_candidates:
            for spent_vout in vout_candidates:
                row = (
                    db.query(Transaction.txid)
                    .join(TransactionInput, Transaction.id == TransactionInput.transaction_id)
                    .filter(
                        TransactionInput.spent_txid == spent_txid,
                        TransactionInput.spent_vout == spent_vout,
                        TransactionInput.script_sig.isnot(None),
                        TransactionInput.script_sig.ilike('%676c79%'),
                    )
                    .order_by(TransactionInput.id.asc())
                    .first()
                )
                if row and row[0]:
                    return row[0]
    except Exception:
        db.rollback()
        return None

    return None

@router.get("/tokens/search", response_model=List[GlyphTokenResponse], tags=["tokens"])
def search_tokens(owner: Optional[str] = None, type: Optional[str] = None, metadata: Optional[str] = None, limit: int = 100, db: Session = Depends(get_db)):
    """
    Search for glyph tokens by owner, type, or metadata.
    
    - **owner**: Filter by token owner address
    - **type**: Filter by token type (fungible, nft, dmint)
    - **metadata**: JSON string to filter by token metadata fields
    - **limit**: Maximum number of results to return
    """
    metadata_query = {}
    if metadata:
        try:
            metadata_query = json.loads(metadata)
        except:
            raise HTTPException(status_code=400, detail="Invalid metadata query format")
    
    tokens = queries.search_glyph_tokens(db, owner, type, metadata_query, limit)
    return tokens

@router.get("/tokens/recent", response_model=List[GlyphTokenResponse], tags=["tokens"])
def get_recent_tokens(type: Optional[str] = None, limit: int = 20, db: Session = Depends(get_db)):
    """
    Get the most recently created glyph tokens.
    
    - **type**: Optional filter by token type (fungible, nft, dmint)
    - **limit**: Maximum number of results to return
    """
    # Cache recent tokens for 10 seconds
    cache_key = f"tokens:recent:{type}:{limit}"
    cached_result = cache.get(cache_key)
    if cached_result:
        return cached_result
    
    tokens = get_recent_glyph_tokens(db, limit, type)
    if tokens:
        cache.set(cache_key, tokens, CACHE_TTL_SHORT)
        return tokens

    # Fallback to unified glyphs table (legacy glyph_tokens may no longer be populated).
    try:
        tl = str(type).lower() if type is not None else ""
        if tl in {"ft", "nft", "dat", "container", "user"}:
            glyphs = (
                db.query(Glyph)
                .filter(Glyph.token_type == tl.upper())
                .order_by(Glyph.id.desc())
                .limit(limit)
                .all()
            )
            result = [_glyph_to_legacy_token_dict(g) for g in glyphs]
            cache.set(cache_key, result, CACHE_TTL_SHORT)
            return result

        if tl == "dmint":
            # DMINT is a protocol (4), not a glyph token_type. Detect via Glyph.p.
            # Legacy DMINT contracts are typically FTs with protocols [1,4].
            scan_limit = min(max(limit * 50, limit), 5000)
            rows = (
                db.query(Glyph)
                .filter(Glyph.token_type == "FT")
                .order_by(Glyph.id.desc())
                .limit(scan_limit)
                .all()
            )
            dmints = [g for g in rows if isinstance(getattr(g, 'p', None), list) and (4 in g.p or '4' in g.p)]
            result = [_glyph_to_legacy_token_dict(g) for g in dmints[:limit]]
            cache.set(cache_key, result, CACHE_TTL_SHORT)
            return result

        glyphs = db.query(Glyph).order_by(Glyph.id.desc()).limit(limit).all()
        result = [_glyph_to_legacy_token_dict(g) for g in glyphs]
        cache.set(cache_key, result, CACHE_TTL_SHORT)
        return result
    except Exception:
        db.rollback()
        cache.set(cache_key, [], 5)
        return []


@router.get("/tokens", response_model=List[GlyphTokenResponse], tags=["tokens"])
def list_tokens(
    type: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=500),
    offset: Optional[int] = Query(None, ge=0),
    sort: str = "created_at",
    order: str = "desc",
    mintable: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    """List glyph tokens with server-side filtering and sorting.

    - **type**: Optional filter by token type (fungible, nft, dmint)
    - **limit**: Max results
    - **sort**: created_at | genesis_height | holder_count | circulating_supply | max_supply | current_supply | mintable
    - **order**: asc | desc
    - **mintable**: true/false to filter mintable tokens (dmint OR supply not maxed)
    """
    effective_offset = offset if offset is not None else (page - 1) * limit
    tokens = queries.list_glyph_tokens(
        db,
        limit=limit,
        offset=effective_offset,
        token_type=type,
        sort=sort,
        order=order,
        mintable=mintable,
    )
    if tokens:
        return tokens

    # Fallback to unified glyphs table (legacy glyph_tokens may no longer be populated).
    try:
        tl = str(type).lower() if type is not None else ""
        sort_key = (sort or "created_at").lower()
        is_asc = (order or "desc").lower() == "asc"

        if tl in {"ft", "nft", "dat", "container", "user"}:
            q = db.query(Glyph).filter(Glyph.token_type == tl.upper())
        elif tl == "dmint":
            q = db.query(Glyph).filter(Glyph.token_type == "FT")
        else:
            q = db.query(Glyph)

        if sort_key in {"genesis_height", "height"}:
            q = q.order_by(Glyph.height.asc() if is_asc else Glyph.height.desc(), Glyph.id.desc())
        elif sort_key == "name":
            q = q.order_by(Glyph.name.asc() if is_asc else Glyph.name.desc(), Glyph.id.desc())
        else:
            q = q.order_by(Glyph.id.asc() if is_asc else Glyph.id.desc())

        # For DMINT we filter in Python since it's protocol-based, not token_type.
        if tl == "dmint":
            scan_limit = min(max((effective_offset + limit) * 50, limit), 5000)
            rows = q.limit(scan_limit).all()
            dmints = [g for g in rows if isinstance(getattr(g, 'p', None), list) and (4 in g.p or '4' in g.p)]
            page_rows = dmints[effective_offset:effective_offset + limit]
            return [_glyph_to_legacy_token_dict(g) for g in page_rows]

        glyphs = q.offset(effective_offset).limit(limit).all()
        return [_glyph_to_legacy_token_dict(g) for g in glyphs]
    except Exception:
        db.rollback()
        return []


@router.get("/tokens/stats", tags=["tokens"])
def get_token_stats(db: Session = Depends(get_db)):
    """
    Get statistics about glyph token usage.
    
    Returns counts of tokens by type, unique holders, and protocol usage.
    """
    cache_key = "tokens:stats"
    cached_result = cache.get(cache_key)
    if cached_result:
        return cached_result
    stats = get_glyph_protocol_stats(db)
    cache.set(cache_key, stats, CACHE_TTL_LONG)  # Cache token stats for 5 minutes
    return stats


@router.get("/tokens/{token_id}", response_model=GlyphTokenResponse, tags=["tokens"])
def get_token(token_id: str, db: Session = Depends(get_db)):
    """
    Get detailed information about a specific glyph token.
    
    - **token_id**: The unique identifier of the token
    """
    token = get_glyph_token_by_id(db, token_id)
    if token:
        # Some legacy rows can have null genesis_height even though it is derivable.
        # Ensure we always provide a correct genesis_height so the explorer can
        # compute the on-chain genesis timestamp (initial contract creation time).
        genesis_height = getattr(token, 'genesis_height', None)
        created_at = getattr(token, 'created_at', None)

        reveal_txid = getattr(token, 'reveal_txid', None) or getattr(token, 'txid', None)
        if (genesis_height is None or created_at is None) and reveal_txid:
            try:
                tx_row = (
                    db.query(Transaction)
                    .filter(Transaction.txid == reveal_txid)
                    .order_by(Transaction.id.desc())
                    .first()
                )
                if tx_row:
                    if genesis_height is None:
                        genesis_height = getattr(tx_row, 'block_height', None) or genesis_height
                    if created_at is None:
                        created_at = getattr(tx_row, 'created_at', None) or created_at
            except Exception:
                pass

        if genesis_height is None:
            try:
                resolved_ref, glyph = _resolve_token_ref(db, token_id)
                if glyph and getattr(glyph, 'height', None) is not None:
                    genesis_height = getattr(glyph, 'height', None)
                    if created_at is None:
                        created_at = getattr(glyph, 'created_at', None)
            except Exception:
                pass

        # If we can resolve a unified glyph row, always prefer its height as the
        # canonical initial contract creation height. This avoids returning a
        # stale/incorrect legacy genesis_height that can drift from the unified model.
        try:
            resolved_ref, glyph = _resolve_token_ref(db, token_id)
            if glyph and getattr(glyph, 'height', None) is not None:
                genesis_height = getattr(glyph, 'height', None)
                if created_at is None:
                    created_at = getattr(glyph, 'created_at', None)
        except Exception:
            pass

        try:
            if genesis_height is not None:
                setattr(token, 'genesis_height', genesis_height)
            if created_at is not None:
                setattr(token, 'created_at', created_at)
        except Exception:
            pass

        return token

    resolved_ref, glyph = _resolve_token_ref(db, token_id)
    if not glyph:
        raise HTTPException(status_code=404, detail=f"Token {token_id} not found")

    reveal_txid = _resolve_reveal_txid(db, resolved_ref, glyph)

    genesis_height = getattr(glyph, 'height', None)
    created_at = getattr(glyph, 'created_at', None)
    if reveal_txid:
        try:
            tx_row = (
                db.query(Transaction)
                .filter(Transaction.txid == reveal_txid)
                .order_by(Transaction.id.desc())
                .first()
            )
            if tx_row:
                genesis_height = getattr(tx_row, 'block_height', None) or genesis_height
                created_at = getattr(tx_row, 'created_at', None) or created_at
        except Exception:
            pass

    return {
        "token_id": glyph.ref,
        "txid": None,
        "type": "dmint" if (isinstance(getattr(glyph, 'p', None), list) and (4 in glyph.p or '4' in glyph.p)) else (glyph.token_type.lower() if glyph.token_type else None),
        "name": glyph.name or None,
        "description": glyph.description or None,
        "ticker": glyph.ticker,
        "token_type_name": glyph.type or None,
        "immutable": glyph.immutable,
        "attrs": glyph.attrs,
        "location": glyph.location,
        "author": glyph.author or None,
        "container": glyph.container or None,
        "protocols": glyph.p,
        "protocol_type": 4 if (isinstance(getattr(glyph, 'p', None), list) and (4 in glyph.p or '4' in glyph.p)) else None,
        "icon_mime_type": glyph.embed_type,
        "icon_url": glyph.remote_url,
        "genesis_height": genesis_height,
        "latest_height": glyph.height,
        "created_at": created_at,
        "updated_at": glyph.updated_at,
    }


@router.get("/tokens/protocol/{protocol_id}", response_model=List[GlyphTokenResponse], tags=["tokens"])
def get_tokens_by_protocol(protocol_id: int, limit: int = 100, db: Session = Depends(get_db)):
    """
    Get tokens that use a specific protocol ID.
    
    - **protocol_id**: Protocol identifier number
    - **limit**: Maximum number of results to return
    """
    tokens = get_tokens_by_protocol(db, protocol_id, limit)
    return tokens


@router.get("/tokens/{token_id}/history", tags=["tokens"])
def get_token_history(token_id: str, limit: int = 50, db: Session = Depends(get_db)):
    """
    Get transaction history for a specific token.
    
    - **token_id**: The unique identifier of the token
    - **limit**: Maximum number of history entries to return
    """
    token = get_glyph_token_by_id(db, token_id)
    if not token:
        raise HTTPException(status_code=404, detail=f"Token {token_id} not found")
    
    history = get_token_tx_history(db, token_id, limit)
    return history

# NFT Endpoints

@router.get("/nft/collections/top", response_model=List[NFTCollectionResponse], summary="Top NFT collections by NFT count")
def get_top_nft_collections_api(db: Session = Depends(get_db)):
    return get_top_nft_collections(db)

@router.get("/nft/search", response_model=List[NFTResponse], summary="Search NFTs")
def search_nfts_api(
    owner: Optional[str] = None,
    collection: Optional[str] = None,
    token_type_name: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    metadata_key: Optional[str] = None,
    metadata_value: Optional[str] = None
):
    """Search NFTs with optional filters.
    
    - **owner**: Filter by owner address
    - **collection**: Filter by collection/container
    - **token_type_name**: Filter by type: 'user', 'container', or null for objects
    - **metadata_key/metadata_value**: Filter by metadata field
    """
    metadata_query = {metadata_key: metadata_value} if metadata_key and metadata_value else None
    nfts = search_nfts(db, owner=owner, collection=collection, metadata_query=metadata_query, limit=limit)
    
    # Filter by token_type_name if provided
    if token_type_name:
        nfts = [n for n in nfts if getattr(n, 'token_type_name', None) == token_type_name]
    
    return [NFTResponse.model_validate(nft) for nft in nfts]

@router.get("/nfts/recent", response_model=List[NFTResponse], summary="Recent NFTs")
def get_recent_nfts_api(
    token_type_name: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """Get recent NFTs with optional type filter.
    
    - **token_type_name**: Filter by type: 'user', 'container', or null for objects
    """
    try:
        query = db.query(NFT)
        if token_type_name:
            query = query.filter(NFT.token_type_name == token_type_name)
        nfts = query.order_by(NFT.created_at.desc()).limit(limit).all()
    except Exception:
        nfts = []
    return [NFTResponse.model_validate(nft) for nft in nfts]

@router.get("/nfts/users", response_model=List[NFTResponse], summary="Get User NFTs")
def get_user_nfts_api(limit: int = 100, db: Session = Depends(get_db)):
    """Get NFTs with token_type_name = 'user'."""
    try:
        nfts = db.query(NFT).filter(NFT.token_type_name == 'user').order_by(NFT.created_at.desc()).limit(limit).all()
    except Exception:
        nfts = []
    return [NFTResponse.model_validate(nft) for nft in nfts]

@router.get("/nfts/containers", response_model=List[NFTResponse], summary="Get Container NFTs")
def get_container_nfts_api(limit: int = 100, db: Session = Depends(get_db)):
    """Get NFTs with token_type_name = 'container'."""
    try:
        nfts = db.query(NFT).filter(NFT.token_type_name == 'container').order_by(NFT.created_at.desc()).limit(limit).all()
    except Exception:
        nfts = []
    return [NFTResponse.model_validate(nft) for nft in nfts]

@router.get("/nfts/{token_id}", response_model=NFTResponse, summary="Get NFT by ID")
def get_nft_by_id_api(token_id: str, db: Session = Depends(get_db)):
    """Get detailed information about a specific NFT."""
    nft = db.query(NFT).filter(NFT.token_id == token_id).first()
    if not nft:
        raise HTTPException(status_code=404, detail=f"NFT {token_id} not found")
    return NFTResponse.model_validate(nft)

# Glyph Analytics

@router.get("/glyph/users/top", response_model=List[TopGlyphUserResponse], summary="Top 100 Glyph users by token count")
def get_top_glyph_users_api(db: Session = Depends(get_db)):
    return get_top_glyph_users(db)

@router.get("/glyph/containers/top", response_model=List[TopGlyphContainerResponse], summary="Top 100 Glyph containers by user count")
def get_top_glyph_containers_api(db: Session = Depends(get_db)):
    return get_top_glyph_containers(db)

# Caching logic for holders
@lru_cache(maxsize=128)
def cached_token_holder_count(token_id: str):
    db = SessionLocal()
    try:
        return get_token_holder_count(db, token_id)
    finally:
        db.close()

@router.get("/holders/token/{token_id}", response_model=HolderCountResponse, summary="Get unique Glyph token holder count (cached)")
def get_token_holder_count_api(token_id: str):
    count = cached_token_holder_count(token_id)
    return HolderCountResponse(count=count)

# Token Files Endpoints

@router.get("/tokens/{token_id}/files", response_model=List[TokenFileResponse], tags=["tokens"], summary="Get files/images for a token")
def get_token_files(token_id: str, db: Session = Depends(get_db)):
    """Get all files (images, etc.) associated with a token."""
    files = db.query(TokenFile).filter(TokenFile.token_id == token_id).all()
    return files

@router.get("/tokens/{token_id}/files/{file_key}", tags=["tokens"], summary="Get specific file by key")
def get_token_file_by_key(token_id: str, file_key: str, db: Session = Depends(get_db)):
    """Get a specific file by its key (e.g., 'icon', 'image')."""
    file = db.query(TokenFile).filter(
        TokenFile.token_id == token_id,
        TokenFile.file_key == file_key
    ).first()
    if not file:
        raise HTTPException(status_code=404, detail=f"File '{file_key}' not found for token {token_id}")
    return TokenFileResponse.model_validate(file)

@router.get("/tokens/{token_id}/image", tags=["tokens"], summary="Get token image as binary")
def get_token_image(token_id: str, db: Session = Depends(get_db)):
    """
    Get the primary image for a token as binary data.
    Returns the image with proper Content-Type header for direct display.
    """
    file = None

    def _is_image_mime(mime: str | None) -> bool:
        return bool(mime) and mime.startswith('image/')

    # Try common image keys
    for key in ['icon', 'image', 'main', 'img']:
        candidate = db.query(TokenFile).filter(
            TokenFile.token_id == token_id,
            TokenFile.file_key == key
        ).first()
        if not candidate:
            continue
        if _is_image_mime(getattr(candidate, 'mime_type', None)):
            file = candidate
            break
    
    # If no specific key found, get any image file
    if not file:
        file = db.query(TokenFile).filter(
            TokenFile.token_id == token_id,
            TokenFile.mime_type.like('image/%')
        ).first()
    
    if not file:
        token = (
            db.query(GlyphToken)
            .filter(GlyphToken.token_id == token_id)
            .order_by(GlyphToken.updated_at.desc().nullslast(), GlyphToken.id.desc())
            .first()
        )
        if token:
            if getattr(token, 'icon_url', None):
                return RedirectResponse(url=token.icon_url)
            if getattr(token, 'icon_data', None):
                try:
                    image_bytes = base64.b64decode(token.icon_data)
                    return Response(
                        content=image_bytes,
                        media_type=getattr(token, 'icon_mime_type', None) or "application/octet-stream",
                    )
                except Exception as e:
                    raise HTTPException(status_code=500, detail=f"Failed to decode image: {str(e)}")

        nft = db.query(NFT).filter(NFT.token_id == token_id).first()
        if nft:
            if getattr(nft, 'icon_url', None):
                return RedirectResponse(url=nft.icon_url)
            if getattr(nft, 'icon_data', None):
                try:
                    image_bytes = base64.b64decode(nft.icon_data)
                    return Response(
                        content=image_bytes,
                        media_type=getattr(nft, 'icon_mime_type', None) or "application/octet-stream",
                    )
                except Exception as e:
                    raise HTTPException(status_code=500, detail=f"Failed to decode image: {str(e)}")

        raise HTTPException(status_code=404, detail=f"No image found for token {token_id}")
    
    # If remote URL, redirect
    if file.remote_url:
        if not _is_image_mime(getattr(file, 'mime_type', None)):
            raise HTTPException(status_code=404, detail=f"No image found for token {token_id}")
        return RedirectResponse(url=file.remote_url)
    
    # If embedded, decode and return binary
    if file.file_data:
        if not _is_image_mime(getattr(file, 'mime_type', None)):
            raise HTTPException(status_code=404, detail=f"No image found for token {token_id}")
        try:
            image_bytes = base64.b64decode(file.file_data)
            return Response(
                content=image_bytes,
                media_type=file.mime_type or "application/octet-stream"
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to decode image: {str(e)}")
    
    raise HTTPException(status_code=404, detail="No image data available")

# Container Endpoints

@router.get("/containers", response_model=List[ContainerResponse], tags=["containers"], summary="List all containers")
def list_containers(limit: int = 100, db: Session = Depends(get_db)):
    """Get all containers/collections, ordered by token count."""
    containers = db.query(Container).order_by(Container.token_count.desc()).limit(limit).all()
    return containers

@router.get("/containers/{container_id}", response_model=ContainerResponse, tags=["containers"], summary="Get container details")
def get_container(container_id: str, db: Session = Depends(get_db)):
    """Get details for a specific container/collection."""
    container = db.query(Container).filter(Container.container_id == container_id).first()
    if not container:
        raise HTTPException(status_code=404, detail=f"Container {container_id} not found")
    return container

@router.get("/containers/{container_id}/tokens", response_model=List[GlyphTokenResponse], tags=["containers"], summary="Get tokens in container")
def get_container_tokens(container_id: str, limit: int = 100, db: Session = Depends(get_db)):
    """Get all tokens belonging to a container/collection."""
    from database.models import GlyphToken
    tokens = db.query(GlyphToken).filter(GlyphToken.container == container_id).limit(limit).all()
    return tokens


# ============================================================================
# ENHANCED TOKEN ENDPOINTS - Holders, Supply, Trades
# ============================================================================

@router.get("/tokens/{token_id}/holders", tags=["tokens"], summary="Get token holders")
def get_token_holders_api(
    token_id: str, 
    limit: int = 100, 
    offset: int = 0,
    holders_mode: str = Query("address", description="Holder listing/counting mode: address | cluster"),
    db: Session = Depends(get_db)
):
    """
    Get holders of a specific token with their balances.
    
    - **token_id**: Token reference ID
    - **limit**: Max results (default 100)
    - **offset**: Pagination offset
    """
    from sqlalchemy import text
    
    resolved_ref, _ = _resolve_token_ref(db, token_id)

    mode = (holders_mode or "address").lower()

    if mode == "cluster":
        try:
            exists = db.execute(text("SELECT to_regclass('public.address_clusters')")).scalar()
            if not exists:
                mode = "address"
        except Exception:
            mode = "address"

    if mode == "cluster":
        # Cluster view: group addresses into wallet-like clusters (common-input heuristic).
        # If an address is not clustered yet, fall back to treating it as its own cluster.
        result = db.execute(
            text(
                """
                WITH clustered AS (
                    SELECT
                        COALESCE('CLUSTER:' || ac.cluster_id::text, th.address) AS cluster_key,
                        th.balance AS balance
                    FROM token_holders th
                    LEFT JOIN address_clusters ac ON ac.address = th.address
                    WHERE th.token_id = :token_id
                      AND th.balance > 0
                      AND th.address IS NOT NULL
                      AND length(btrim(th.address)) > 0
                ),
                totals AS (
                    SELECT COALESCE(SUM(balance), 0) AS total_balance FROM clustered
                )
                SELECT
                    c.cluster_key AS address,
                    SUM(c.balance)::bigint AS balance,
                    CASE
                        WHEN t.total_balance > 0 THEN (SUM(c.balance)::float / t.total_balance::float) * 100
                        ELSE 0
                    END AS percentage,
                    NULL::timestamptz AS first_acquired_at,
                    NULL::timestamptz AS last_updated_at
                FROM clustered c
                CROSS JOIN totals t
                GROUP BY c.cluster_key, t.total_balance
                ORDER BY SUM(c.balance) DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {'token_id': resolved_ref, 'limit': limit, 'offset': offset},
        )

        holders = [dict(row._mapping) for row in result.fetchall()]

        count_result = db.execute(
            text(
                """
                SELECT COUNT(DISTINCT COALESCE('CLUSTER:' || ac.cluster_id::text, th.address))
                FROM token_holders th
                LEFT JOIN address_clusters ac ON ac.address = th.address
                WHERE th.token_id = :token_id
                  AND th.balance > 0
                  AND th.address IS NOT NULL
                  AND length(btrim(th.address)) > 0
                """
            ),
            {'token_id': resolved_ref},
        )
        total = count_result.scalar() or 0
    else:
        # Address view (current behavior)
        result = db.execute(text("""
            SELECT address, balance, percentage, first_acquired_at, last_updated_at
            FROM token_holders
            WHERE token_id = :token_id
              AND balance > 0
              AND address IS NOT NULL
              AND length(btrim(address)) > 0
            ORDER BY balance DESC
            LIMIT :limit OFFSET :offset
        """), {'token_id': resolved_ref, 'limit': limit, 'offset': offset})
        
        holders = [dict(row._mapping) for row in result.fetchall()]
        
        count_result = db.execute(text("""
            SELECT COUNT(DISTINCT address)
            FROM token_holders
            WHERE token_id = :token_id
              AND balance > 0
              AND address IS NOT NULL
              AND length(btrim(address)) > 0
        """), {'token_id': resolved_ref})
        total = count_result.scalar() or 0
    
    return {
        "token_id": resolved_ref,
        "holders": holders,
        "total": total,
        "limit": limit,
        "offset": offset
    }


@router.get("/tokens/{token_id}/supply", tags=["tokens"], summary="Get token supply breakdown")
def get_token_supply_api(token_id: str, holders_mode: str = Query("address", description="Holder counting mode: address | cluster"), db: Session = Depends(get_db)):
    """
    Get supply breakdown for a token.
    
    Returns circulating supply, burned supply, max supply, and holder count.
    """
    from sqlalchemy import text
    from database.models import GlyphToken
    
    resolved_ref, glyph = _resolve_token_ref(db, token_id)
    token = db.query(GlyphToken).filter(GlyphToken.token_id == resolved_ref).first()
    if not token:
        protocols = getattr(glyph, 'p', None)
        is_dmint = isinstance(protocols, list) and (4 in protocols or '4' in protocols)

        reveal_txid = _resolve_reveal_txid(db, resolved_ref, glyph)

        def _parse_pushdata(buf: bytes, i: int):
            if i >= len(buf):
                raise ValueError('push out of range')
            op = buf[i]
            if op == 0x00:
                return b'', i + 1
            if 1 <= op <= 75:
                l = op
                start = i + 1
                end = start + l
                return buf[start:end], end
            if op == 76:
                l = buf[i + 1]
                start = i + 2
                end = start + l
                return buf[start:end], end
            if op == 77:
                l = int.from_bytes(buf[i + 1:i + 3], 'little')
                start = i + 3
                end = start + l
                return buf[start:end], end
            if op == 78:
                l = int.from_bytes(buf[i + 1:i + 5], 'little')
                start = i + 5
                end = start + l
                return buf[start:end], end
            raise ValueError(f'unsupported push opcode: {op}')

        def _decode_script_num(b: bytes) -> int:
            if not b:
                return 0
            bb = bytearray(b)
            negative = False
            if bb[-1] & 0x80:
                negative = True
                bb[-1] &= 0x7F
            n = int.from_bytes(bb, 'little', signed=False)
            return -n if negative else n

        def _read_script_num(buf: bytes, i: int) -> tuple[int, int]:
            if i >= len(buf):
                raise ValueError('num out of range')
            op = buf[i]
            if op == 0x00:
                return 0, i + 1
            if op == 0x4f:
                return -1, i + 1
            if 0x51 <= op <= 0x60:
                return (op - 0x50), i + 1
            data, ni = _parse_pushdata(buf, i)
            return _decode_script_num(data), ni

        def _value_to_photons(v) -> int:
            try:
                if v is None:
                    return 0
                # utxos.value is numeric(20,8) in RXD-like units
                return int(Decimal(str(v)) * Decimal('100000000'))
            except Exception:
                return 0

        def _parse_dmint_contract_script(script_hex: str):
            if not isinstance(script_hex, str):
                return None
            try:
                buf = bytes.fromhex(script_hex)
            except Exception:
                return None

            try:
                height_bytes, i = _parse_pushdata(buf, 0)
                height = int.from_bytes(height_bytes[:4].ljust(4, b'\x00'), 'little', signed=False) if height_bytes is not None else 0
                if i >= len(buf) or buf[i] != 0xD8:
                    return None
                i += 1
                contract_ref = buf[i:i + 36].hex()
                i += 36
                if i >= len(buf) or buf[i] != 0xD0:
                    return None
                i += 1
                token_ref = buf[i:i + 36].hex()
                i += 36

                max_height, i = _read_script_num(buf, i)
                reward, i = _read_script_num(buf, i)
                target, i = _read_script_num(buf, i)
                max_target = (1 << 63) - 1
                difficulty = int(max_target // target) if target else None

                if token_ref != resolved_ref:
                    return None

                return {
                    'height': height,
                    'contract_ref': contract_ref,
                    'token_ref': token_ref,
                    'max_height': max_height,
                    'reward': reward,
                    'target': target,
                    'difficulty': difficulty,
                }
            except Exception:
                return None

        max_supply = None
        premine = None
        difficulty = None
        max_height = None
        reward = None
        num_contracts = None

        if reveal_txid and (is_dmint or glyph is None):
            try:
                reveal_utxos = (
                    db.execute(
                        text(
                            """
                            SELECT vout, value, script_hex
                            FROM utxos
                            WHERE txid = :txid
                              AND script_hex IS NOT NULL
                            ORDER BY vout ASC
                            """
                        ),
                        {'txid': reveal_txid},
                    )
                    .fetchall()
                )

                contract_infos = []
                for row in reveal_utxos:
                    info = _parse_dmint_contract_script(row.script_hex)
                    if info:
                        contract_infos.append(info)

                # If we couldn't infer DMINT from glyph protocols, infer it from
                # presence of DMINT contract scripts in the reveal transaction.
                if contract_infos:
                    is_dmint = True

                num_contracts = len(contract_infos)
                if contract_infos:
                    difficulty = contract_infos[0].get('difficulty')
                    max_height = contract_infos[0].get('max_height')
                    reward = contract_infos[0].get('reward')

                ft_pat = re.compile(r'^76a914[0-9a-f]{40}88acbdd0' + re.escape(resolved_ref) + r'dec0e9aa76e378e4a269e69d$', re.IGNORECASE)
                premine_val = 0
                for row in reveal_utxos:
                    if not isinstance(row.script_hex, str):
                        continue
                    if ft_pat.match(row.script_hex.strip()):
                        try:
                            premine_val += _value_to_photons(row.value)
                        except Exception:
                            pass
                premine = premine_val if premine_val > 0 else None

                if num_contracts and max_height is not None and reward is not None:
                    max_supply = int(num_contracts) * int(max_height) * int(reward) + int(premine or 0)
            except Exception:
                db.rollback()

        if not glyph and not is_dmint and not token:
            # No legacy token row, no glyph row, and no DMINT contracts found.
            raise HTTPException(status_code=404, detail=f"Token {token_id} not found")

        burned_supply = 0
        try:
            burned_supply = (
                db.execute(
                    text("SELECT COALESCE(SUM(amount), 0) FROM token_burns WHERE token_id = :token_id"),
                    {'token_id': resolved_ref},
                ).scalar()
                or 0
            )
        except Exception:
            burned_supply = 0

        circulating_supply = 0
        holder_count = 0
        try:
            circulating_supply = (
                db.execute(
                    text(
                        "SELECT COALESCE(SUM(balance), 0) "
                        "FROM token_holders "
                        "WHERE token_id = :token_id AND balance > 0 AND address IS NOT NULL AND length(btrim(address)) > 0"
                    ),
                    {'token_id': resolved_ref},
                ).scalar()
                or 0
            )

            mode = (holders_mode or "address").lower()
            if mode == "cluster":
                try:
                    exists = db.execute(text("SELECT to_regclass('public.address_clusters')")).scalar()
                    if not exists:
                        mode = "address"
                except Exception:
                    mode = "address"
            if mode == "cluster":
                holder_count = (
                    db.execute(
                        text(
                            "SELECT COUNT(DISTINCT COALESCE('CLUSTER:' || ac.cluster_id::text, th.address)) "
                            "FROM token_holders th "
                            "LEFT JOIN address_clusters ac ON ac.address = th.address "
                            "WHERE th.token_id = :token_id AND th.balance > 0 AND th.address IS NOT NULL AND length(btrim(th.address)) > 0"
                        ),
                        {'token_id': resolved_ref},
                    ).scalar()
                    or 0
                )
            else:
                holder_count = (
                    db.execute(
                        text(
                            "SELECT COUNT(DISTINCT address) "
                            "FROM token_holders "
                            "WHERE token_id = :token_id AND balance > 0 AND address IS NOT NULL AND length(btrim(address)) > 0"
                        ),
                        {'token_id': resolved_ref},
                    ).scalar()
                    or 0
                )
        except Exception:
            circulating_supply = 0
            holder_count = 0

        if circulating_supply == 0:
            # Avoid expensive full-table script scans on large UTXO sets.
            # If we don't have holder-derived balances, fall back to the indexed
            # utxos.glyph_ref column (populated by the indexer) rather than
            # scanning script_hex.
            if not holder_count:
                # During catchup we may intentionally skip spent checks. In that mode,
                # utxos.spent=false is not reliable, so avoid reporting misleading
                # circulating supply / minted percent.
                if not _spent_backfill_is_complete(db):
                    circulating_supply = None
                else:
                    try:
                        row = db.execute(
                            text(
                                """
                                SELECT COUNT(*) AS cnt, COALESCE(SUM(value), 0) AS total
                                FROM utxos
                                WHERE spent = false
                                  AND glyph_ref = :glyph_ref
                                """
                            ),
                            {'glyph_ref': resolved_ref},
                        ).fetchone()

                        if row and int(getattr(row, 'cnt', 0) or 0) > 0:
                            circulating_supply = _value_to_photons(getattr(row, 'total', 0))
                        else:
                            circulating_supply = None
                    except Exception:
                        db.rollback()
                        circulating_supply = None

        # If we fell back to summing utxos.value, convert it to photons.
        try:
            if isinstance(circulating_supply, (float, Decimal, str)):
                circulating_supply = _value_to_photons(circulating_supply)
        except Exception:
            pass

        minted_supply = None
        percent_minted = None
        burned_percent = None
        try:
            if circulating_supply is None:
                minted_supply = None
                percent_minted = None
                burned_percent = None
            else:
                minted_supply = int((circulating_supply or 0) + (burned_supply or 0))
                if max_supply and max_supply > 0:
                    percent_minted = (minted_supply / float(max_supply)) * 100.0
                    burned_percent = (float(burned_supply or 0) / float(max_supply)) * 100.0
        except Exception:
            minted_supply = None
            percent_minted = None
            burned_percent = None

        return {
            "token_id": resolved_ref,
            "name": getattr(glyph, 'name', None),
            "ticker": getattr(glyph, 'ticker', None),
            "type": "dmint" if is_dmint else (getattr(glyph, 'token_type', '') or '').lower(),
            "max_supply": max_supply,
            "circulating_supply": int(circulating_supply) if circulating_supply is not None else None,
            "burned_supply": int(burned_supply) if burned_supply is not None else 0,
            "minted_supply": minted_supply,
            "percent_minted": percent_minted,
            "burned_percent": burned_percent,
            "premine": premine,
            "holder_count": int(holder_count) if holder_count is not None else 0,
            "supply_updated_at": None,
            "difficulty": difficulty,
            "max_height": max_height,
            "reward": reward,
            "num_contracts": num_contracts,
        }
    
    return {
        "token_id": resolved_ref,
        "name": token.name,
        "ticker": token.ticker,
        "type": token.type,
        "max_supply": token.max_supply,
        "circulating_supply": token.circulating_supply,
        "burned_supply": token.burned_supply or 0,
        "premine": token.premine,
        "holder_count": token.holder_count or 0,
        "supply_updated_at": token.supply_updated_at.isoformat() if token.supply_updated_at else None,
        # DMINT specific
        "difficulty": token.difficulty,
        "max_height": token.max_height,
        "reward": token.reward,
    }


@router.get("/tokens/{token_id}/contracts", tags=["tokens"], summary="Get DMINT minting contracts")
def get_token_contracts_api(token_id: str, limit: int = 500, db: Session = Depends(get_db)):
    resolved_ref, glyph = _resolve_token_ref(db, token_id)
    protocols = getattr(glyph, 'p', None)
    is_dmint = isinstance(protocols, list) and (4 in protocols or '4' in protocols)
    reveal_txid = _resolve_reveal_txid(db, resolved_ref, glyph)

    if not reveal_txid:
        if not glyph:
            raise HTTPException(status_code=404, detail=f"Token {token_id} not found")
        return {"token_id": resolved_ref, "contracts": [], "count": 0}

    def _parse_pushdata(buf: bytes, i: int):
        if i >= len(buf):
            raise ValueError('push out of range')
        op = buf[i]
        if op == 0x00:
            return b'', i + 1
        if 1 <= op <= 75:
            l = op
            start = i + 1
            end = start + l
            return buf[start:end], end
        if op == 76:
            l = buf[i + 1]
            start = i + 2
            end = start + l
            return buf[start:end], end
        if op == 77:
            l = int.from_bytes(buf[i + 1:i + 3], 'little')
            start = i + 3
            end = start + l
            return buf[start:end], end
        if op == 78:
            l = int.from_bytes(buf[i + 1:i + 5], 'little')
            start = i + 5
            end = start + l
            return buf[start:end], end
        raise ValueError(f'unsupported push opcode: {op}')

    def _decode_script_num(b: bytes) -> int:
        if not b:
            return 0
        bb = bytearray(b)
        negative = False
        if bb[-1] & 0x80:
            negative = True
            bb[-1] &= 0x7F
        n = int.from_bytes(bb, 'little', signed=False)
        return -n if negative else n

    def _read_script_num(buf: bytes, i: int) -> tuple[int, int]:
        if i >= len(buf):
            raise ValueError('num out of range')
        op = buf[i]
        if op == 0x00:
            return 0, i + 1
        if op == 0x4f:
            return -1, i + 1
        if 0x51 <= op <= 0x60:
            return (op - 0x50), i + 1
        data, ni = _parse_pushdata(buf, i)
        return _decode_script_num(data), ni

    def _parse_dmint_contract_script(script_hex: str):
        if not isinstance(script_hex, str):
            return None
        try:
            buf = bytes.fromhex(script_hex)
        except Exception:
            return None

        try:
            height_bytes, i = _parse_pushdata(buf, 0)
            height = int.from_bytes(height_bytes[:4].ljust(4, b'\x00'), 'little', signed=False) if height_bytes is not None else 0
            if i >= len(buf) or buf[i] != 0xD8:
                return None
            i += 1
            contract_ref = buf[i:i + 36].hex()
            i += 36
            if i >= len(buf) or buf[i] != 0xD0:
                return None
            i += 1
            token_ref = buf[i:i + 36].hex()
            i += 36

            max_height, i = _read_script_num(buf, i)
            reward, i = _read_script_num(buf, i)
            target, i = _read_script_num(buf, i)
            max_target = (1 << 63) - 1
            difficulty = int(max_target // target) if target else None

            if token_ref != resolved_ref:
                return None

            return {
                'height': height,
                'contract_ref': contract_ref,
                'token_ref': token_ref,
                'max_height': max_height,
                'reward': reward,
                'target': target,
                'difficulty': difficulty,
            }
        except Exception:
            return None

    try:
        reveal_utxos = (
            db.execute(
                text(
                    """
                    SELECT txid, vout, script_hex, transaction_block_height
                    FROM utxos
                    WHERE txid = :txid
                      AND script_hex IS NOT NULL
                    ORDER BY vout ASC
                    """
                ),
                {'txid': reveal_txid},
            ).fetchall()
        )
    except Exception:
        db.rollback()
        return {"token_id": resolved_ref, "contracts": [], "count": 0}

    contracts = []
    for row in reveal_utxos:
        info = _parse_dmint_contract_script(row.script_hex)
        if not info:
            continue

        # We can infer DMINT from the presence of matching contract scripts.
        is_dmint = True

        contract_ref = info['contract_ref']
        location_ref = None
        current_height = info.get('height')

        if not location_ref:
            try:
                from indexer.script_utils import construct_ref
                location_ref = construct_ref(reveal_txid, int(row.vout))
            except Exception:
                location_ref = f"{reveal_txid}:{row.vout}"

        contracts.append(
            {
                'location': location_ref,
                'contract_id': contract_ref,
                'height': current_height,
                'max_height': info.get('max_height'),
                'reward': info.get('reward'),
                'difficulty': info.get('difficulty'),
            }
        )
        if len(contracts) >= limit:
            break

    return {"token_id": resolved_ref, "contracts": contracts, "count": len(contracts)}


@router.get("/tokens/{token_id}/trades", tags=["tokens"], summary="Get token trade history")
def get_token_trades_api(
    token_id: str, 
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """
    Get trade history for a token.
    
    Returns completed swaps involving this token.
    """
    from sqlalchemy import text
    
    result = db.execute(text("""
        SELECT txid, from_token_id, from_amount, from_is_rxd,
               to_token_id, to_amount, to_is_rxd,
               seller_address, buyer_address, price_per_token,
               block_height, completed_at
        FROM token_swaps
        WHERE status = 'completed'
        AND (from_token_id = :token_id OR to_token_id = :token_id)
        ORDER BY completed_at DESC
        LIMIT :limit
    """), {'token_id': token_id, 'limit': limit})
    
    trades = [dict(row._mapping) for row in result.fetchall()]
    
    return {
        "token_id": token_id,
        "trades": trades,
        "count": len(trades)
    }


@router.get("/tokens/{token_id}/burns", tags=["tokens"], summary="Get token burn history")
def get_token_burns_api(token_id: str, limit: int = 50, db: Session = Depends(get_db)):
    """Get burn (melt) history for a token."""
    from sqlalchemy import text
    
    result = db.execute(text("""
        SELECT txid, amount, burner_address, block_height, burned_at
        FROM token_burns
        WHERE token_id = :token_id
        ORDER BY burned_at DESC
        LIMIT :limit
    """), {'token_id': token_id, 'limit': limit})
    
    burns = [dict(row._mapping) for row in result.fetchall()]
    
    return {
        "token_id": token_id,
        "burns": burns,
        "total_burned": sum(b['amount'] for b in burns)
    }


@router.get("/tokens/{token_id}/price", tags=["tokens"], summary="Get token price history")
def get_token_price_api(token_id: str, limit: int = 100, db: Session = Depends(get_db)):
    """Get price history for a token."""
    from sqlalchemy import text
    
    result = db.execute(text("""
        SELECT price_rxd, volume, block_height, recorded_at
        FROM token_price_history
        WHERE token_id = :token_id
        ORDER BY recorded_at DESC
        LIMIT :limit
    """), {'token_id': token_id, 'limit': limit})
    
    prices = [dict(row._mapping) for row in result.fetchall()]
    
    # Get latest price
    latest_price = prices[0]['price_rxd'] if prices else None
    
    return {
        "token_id": token_id,
        "latest_price_rxd": latest_price,
        "price_history": prices
    }


@router.get("/tokens/{token_id}/ohlcv", tags=["tokens"], summary="Get daily OHLCV data")
def get_token_ohlcv_api(token_id: str, days: int = 30, db: Session = Depends(get_db)):
    """Get daily OHLCV (Open, High, Low, Close, Volume) data for charts."""
    from sqlalchemy import text
    
    result = db.execute(text("""
        SELECT date, open_price, high_price, low_price, close_price,
               volume_tokens, volume_rxd, trade_count
        FROM token_volume_daily
        WHERE token_id = :token_id
        ORDER BY date DESC
        LIMIT :days
    """), {'token_id': token_id, 'days': days})
    
    ohlcv = [dict(row._mapping) for row in result.fetchall()]
    
    return {
        "token_id": token_id,
        "ohlcv": ohlcv
    }


# ============================================================================
# MARKET ENDPOINTS - Swaps and Trades
# ============================================================================

@router.get("/market/swaps", tags=["market"], summary="Get active swap offers")
def get_active_swaps_api(
    token_id: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """
    Get active (pending) swap offers.
    
    - **token_id**: Optional filter by token
    - **limit**: Max results
    """
    from sqlalchemy import text
    
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
    
    swaps = [dict(row._mapping) for row in result.fetchall()]
    
    return {
        "swaps": swaps,
        "count": len(swaps)
    }


@router.get("/market/trades", tags=["market"], summary="Get recent completed trades")
def get_recent_trades_api(
    token_id: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """
    Get recently completed trades.
    
    - **token_id**: Optional filter by token
    - **limit**: Max results
    """
    from sqlalchemy import text
    
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
    
    trades = [dict(row._mapping) for row in result.fetchall()]
    
    return {
        "trades": trades,
        "count": len(trades)
    }


@router.get("/market/volume", tags=["market"], summary="Get trading volume stats")
def get_market_volume_api(days: int = 7, db: Session = Depends(get_db)):
    """
    Get aggregated trading volume statistics.
    
    - **days**: Number of days to aggregate (default 7)
    """
    from sqlalchemy import text
    
    result = db.execute(text("""
        SELECT 
            SUM(volume_tokens) as total_volume_tokens,
            SUM(volume_rxd) as total_volume_rxd,
            SUM(trade_count) as total_trades,
            COUNT(DISTINCT token_id) as active_tokens
        FROM token_volume_daily
        WHERE date >= NOW() - INTERVAL ':days days'
    """.replace(':days', str(days))), {})
    
    row = result.fetchone()
    
    # Get top traded tokens
    top_result = db.execute(text("""
        SELECT token_id, SUM(volume_tokens) as volume, SUM(trade_count) as trades
        FROM token_volume_daily
        WHERE date >= NOW() - INTERVAL ':days days'
        GROUP BY token_id
        ORDER BY volume DESC
        LIMIT 10
    """.replace(':days', str(days))), {})
    
    top_tokens = [dict(r._mapping) for r in top_result.fetchall()]
    
    return {
        "period_days": days,
        "total_volume_tokens": row.total_volume_tokens if row else 0,
        "total_volume_rxd": row.total_volume_rxd if row else 0,
        "total_trades": row.total_trades if row else 0,
        "active_tokens": row.active_tokens if row else 0,
        "top_traded_tokens": top_tokens
    }


# ============================================================================
# MINT EVENTS (for DMINT tokens)
# ============================================================================

@router.get("/tokens/{token_id}/mints", tags=["tokens"], summary="Get mint events for DMINT token")
def get_token_mints_api(token_id: str, limit: int = 50, db: Session = Depends(get_db)):
    """Get mint history for a DMINT token."""
    from sqlalchemy import text
    
    result = db.execute(text("""
        SELECT txid, minter_address, amount, block_height, minted_at
        FROM token_mint_events
        WHERE token_id = :token_id
        ORDER BY minted_at DESC
        LIMIT :limit
    """), {'token_id': token_id, 'limit': limit})
    
    mints = [dict(row._mapping) for row in result.fetchall()]
    
    return {
        "token_id": token_id,
        "mints": mints,
        "total_minted": sum(m['amount'] for m in mints)
    }
