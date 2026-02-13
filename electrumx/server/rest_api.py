"""
FastAPI REST API for RXinDexer

This module provides a REST API layer on top of the ElectrumX-based RXinDexer,
exposing Glyph v2 token analytics, dMint contracts, WAVE naming, swap/DEX data,
and blockchain queries via HTTP endpoints.

Glyph v2 Endpoints:
  /glyphs                     — List/search all tokens
  /glyphs/{ref}               — Token detail
  /glyphs/search              — Search tokens by name/ticker
  /glyphs/stats               — Indexing statistics
  /tokens/{ref}/holders       — Token holder list
  /tokens/{ref}/supply        — Supply breakdown
  /tokens/{ref}/burns         — Burn history
  /tokens/{ref}/trades        — Transfer history
  /tokens/{ref}/top-holders   — Rich list
  /tokens/{ref}/history       — Full event history
  /tokens/{ref}/metadata      — Raw CBOR metadata

dMint v2 Endpoints:
  /dmint/contracts             — All active dMint contracts
  /dmint/contracts/{ref}       — Single contract detail
  /dmint/algorithms            — Supported mining algorithms
  /dmint/by-algorithm/{algo}   — Filter by algorithm
  /dmint/profitable            — Sorted by profitability

WAVE Endpoints:
  /wave/resolve/{name}         — Resolve WAVE name
  /wave/available/{name}       — Check availability
  /wave/{name}/subdomains      — List subdomains
  /wave/reverse/{scripthash}   — Reverse lookup by owner
  /wave/stats                  — WAVE indexing stats

Swap Endpoints:
  /swaps/orders                — Active swap orders
  /swaps/orders/{order_id}     — Single order detail
  /swaps/history               — Trade history
"""

from typing import Optional, Dict, Any, List
import os
import time
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Query, Path, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# App instance
app = FastAPI(
    title="RXinDexer REST API",
    description="REST API for Radiant blockchain indexer with Glyph v2 token, dMint, WAVE, and Swap support",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

_env_name = os.getenv('ELECTRUMX_ENV', os.getenv('ENV', 'dev')).strip().lower()
_is_prod = _env_name == 'prod'

_allowed_origins_raw = os.getenv('ALLOWED_ORIGINS', '').strip()
_allowed_origins = [o.strip() for o in _allowed_origins_raw.split(',') if o.strip()]
if _is_prod and not _allowed_origins:
    raise RuntimeError('ALLOWED_ORIGINS must be set in production (ELECTRUMX_ENV=prod)')

_require_rest_api_key_prod = os.getenv('REST_REQUIRE_API_KEY_IN_PROD', '1').strip() not in ('0', 'false', 'no')
if _is_prod and _require_rest_api_key_prod and not os.getenv('REST_API_KEY', '').strip():
    raise RuntimeError('REST_API_KEY must be set in production (or set REST_REQUIRE_API_KEY_IN_PROD=0)')

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins if _allowed_origins else [],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _require_api_key(x_api_key: Optional[str] = Header(default=None, alias='X-API-Key')):
    required_key = os.getenv('REST_API_KEY', '').strip()
    if not required_key:
        return
    if not x_api_key or x_api_key != required_key:
        raise HTTPException(status_code=401, detail='Unauthorized')


@dataclass
class _TokenBucket:
    tokens: float
    last_ts: float


_rate_buckets: Dict[str, _TokenBucket] = {}


def _rate_limit(request: Request):
    limit_per_minute = int(os.getenv('REST_RATE_LIMIT_PER_MIN', '600'))
    burst = int(os.getenv('REST_RATE_LIMIT_BURST', str(limit_per_minute)))
    if limit_per_minute <= 0:
        return

    client_host = request.client.host if request.client else 'unknown'
    now = time.time()
    bucket = _rate_buckets.get(client_host)
    if bucket is None:
        bucket = _TokenBucket(tokens=float(burst), last_ts=now)
        _rate_buckets[client_host] = bucket

    elapsed = max(0.0, now - bucket.last_ts)
    refill_per_sec = float(limit_per_minute) / 60.0
    bucket.tokens = min(float(burst), bucket.tokens + elapsed * refill_per_sec)
    bucket.last_ts = now

    if bucket.tokens < 1.0:
        raise HTTPException(status_code=429, detail='Rate limit exceeded')
    bucket.tokens -= 1.0


@app.middleware("http")
async def _security_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith('/health'):
        return await call_next(request)

    _require_api_key(request.headers.get('x-api-key'))
    _rate_limit(request)
    return await call_next(request)

# Global references to indexes (set by the server on startup)
_glyph_index = None
_wave_index = None
_swap_index = None
_dmint_contracts = None
_db = None
_daemon = None
_start_time = time.time()


def set_indexer(glyph_index, db, daemon, wave_index=None, swap_index=None,
                dmint_contracts=None):
    """Set the indexer references from the main server."""
    global _glyph_index, _db, _daemon, _wave_index, _swap_index, _dmint_contracts
    _glyph_index = glyph_index
    _db = db
    _daemon = daemon
    _wave_index = wave_index
    _swap_index = swap_index
    _dmint_contracts = dmint_contracts


# =============================================================================
# RESPONSE MODELS
# =============================================================================

class HealthResponse(BaseModel):
    status: str
    uptime_seconds: float
    database: str
    sync_height: Optional[int] = None


# =============================================================================
# HEALTH & STATUS ENDPOINTS
# =============================================================================

@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """Check API health and database connectivity."""
    uptime = time.time() - _start_time

    db_status = "connected" if _db else "disconnected"
    sync_height = None

    if _db:
        try:
            sync_height = _db.db_height
        except Exception:
            db_status = "error"

    return HealthResponse(
        status="healthy" if _db else "degraded",
        uptime_seconds=round(uptime, 2),
        database=db_status,
        sync_height=sync_height,
    )


@app.get("/health/live", tags=["Health"])
async def health_live():
    return {"status": "alive"}


@app.get("/health/ready", tags=["Health"])
async def health_ready():
    if not _db:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        height = _db.db_height
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database error: {str(e)}")
    if height is None or height < 1:
        raise HTTPException(status_code=503, detail="Not ready")
    return {"status": "ready", "height": height}


@app.get("/health/db", tags=["Health"])
async def health_db():
    """Check database health specifically."""
    if not _db:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        height = _db.db_height
        return {
            "status": "connected",
            "height": height,
            "db_engine": getattr(_db, 'db_engine', 'unknown'),
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database error: {str(e)}")


@app.get("/status", tags=["Health"])
async def get_status():
    """Get detailed indexer status including all subsystem states."""
    status = {
        "api_version": "2.0.0",
        "uptime_seconds": round(time.time() - _start_time, 2),
    }

    if _db:
        status["sync_height"] = _db.db_height
        status["db_engine"] = getattr(_db, 'db_engine', 'unknown')

    if _glyph_index:
        status["glyph_indexing"] = True
        status["tokens_cached"] = len(getattr(_glyph_index, 'token_cache', {}))

    status["wave_indexing"] = _wave_index is not None
    status["swap_indexing"] = _swap_index is not None
    status["dmint_contracts"] = _dmint_contracts is not None

    return status


# =============================================================================
# BLOCKS & TRANSACTIONS
# =============================================================================

@app.get("/blocks/recent", tags=["Blocks"])
async def get_recent_blocks(limit: int = Query(default=10, le=100)):
    """Get recent blocks."""
    if not _db:
        raise HTTPException(status_code=503, detail="Database not available")

    height = _db.db_height
    blocks = []

    for h in range(height, max(0, height - limit), -1):
        try:
            header = await _db.read_headers(h, 1)
            if header:
                blocks.append({
                    "height": h,
                    "header_hex": header.hex() if header else None,
                })
        except Exception:
            continue

    return {"blocks": blocks, "current_height": height}


@app.get("/block/{height}", tags=["Blocks"])
async def get_block(height: int = Path(..., ge=0)):
    """Get block by height."""
    if not _db:
        raise HTTPException(status_code=503, detail="Database not available")

    if height > _db.db_height:
        raise HTTPException(status_code=404, detail="Block not found")

    try:
        header = await _db.read_headers(height, 1)
        return {
            "height": height,
            "header_hex": header.hex() if header else None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/transaction/{txid}", tags=["Transactions"])
async def get_transaction(txid: str = Path(..., min_length=64, max_length=64)):
    """Get transaction by txid."""
    if not _daemon:
        raise HTTPException(status_code=503, detail="Daemon not available")

    try:
        raw_tx = await _daemon.getrawtransaction(txid, True)
        return raw_tx
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Transaction not found: {str(e)}")


# =============================================================================
# GLYPHS / TOKENS
# =============================================================================

def _ensure_glyph_index():
    if not _glyph_index:
        raise HTTPException(status_code=503, detail="Glyph index not available")


@app.get("/glyphs", tags=["Glyphs"])
async def get_all_glyphs(
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    token_type: Optional[int] = Query(default=None, description="Filter by token type ID (1=FT, 2=NFT, 3=DAT, 4=DMINT)")
):
    """Get all indexed Glyph tokens with pagination."""
    _ensure_glyph_index()

    try:
        result = _glyph_index.get_all_tokens_summary(
            limit=limit, offset=offset, token_type=token_type,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/glyphs/search", tags=["Glyphs"])
async def search_glyphs(
    q: str = Query(..., min_length=1, max_length=100, description="Search query (name or ticker)"),
    protocols: Optional[str] = Query(default=None, description="Comma-separated protocol IDs to filter"),
    limit: int = Query(default=50, le=200),
):
    """Search tokens by name or ticker."""
    _ensure_glyph_index()

    try:
        protocol_list = None
        if protocols:
            protocol_list = [int(p.strip()) for p in protocols.split(',') if p.strip()]
        result = _glyph_index.search_tokens(q, protocols=protocol_list, limit=limit)
        return {"query": q, "results": result, "count": len(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/glyphs/stats", tags=["Glyphs"])
async def get_glyph_stats():
    """Get Glyph token indexing statistics (counts by type and version)."""
    _ensure_glyph_index()

    try:
        return _glyph_index.get_stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/glyphs/by-type/{type_id}", tags=["Glyphs"])
async def get_glyphs_by_type(
    type_id: int = Path(..., ge=0, le=7, description="Token type ID (1=FT, 2=NFT, 3=DAT, 4=DMINT, 5=WAVE, 6=Container, 7=Authority)"),
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Get tokens filtered by type."""
    _ensure_glyph_index()

    try:
        result = _glyph_index.get_tokens_by_type(type_id, limit=limit, offset=offset)
        return {"type_id": type_id, "tokens": result, "count": len(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/glyphs/{ref}", tags=["Glyphs"])
async def get_glyph(ref: str = Path(..., min_length=72, max_length=72)):
    """Get Glyph token by reference (72 hex chars = 36 bytes)."""
    _ensure_glyph_index()

    try:
        ref_bytes = bytes.fromhex(ref)
        token = _glyph_index.get_token(ref_bytes)
        if not token:
            raise HTTPException(status_code=404, detail="Token not found")

        return _glyph_index._token_to_dict(token)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tokens/{ref}/holders", tags=["Token Analytics"])
async def get_token_holders(
    ref: str = Path(..., min_length=72, max_length=72),
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0)
):
    """Get token holders with their balances."""
    _ensure_glyph_index()

    try:
        ref_bytes = bytes.fromhex(ref)
        return _glyph_index.get_token_holders(ref_bytes, limit=limit, offset=offset)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tokens/{ref}/supply", tags=["Token Analytics"])
async def get_token_supply(ref: str = Path(..., min_length=72, max_length=72)):
    """Get detailed token supply information."""
    _ensure_glyph_index()

    try:
        ref_bytes = bytes.fromhex(ref)
        result = _glyph_index.get_token_supply(ref_bytes)
        if not result:
            raise HTTPException(status_code=404, detail="Token not found")
        return result
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tokens/{ref}/burns", tags=["Token Analytics"])
async def get_token_burns(
    ref: str = Path(..., min_length=72, max_length=72),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0)
):
    """Get token burn history."""
    _ensure_glyph_index()

    try:
        ref_bytes = bytes.fromhex(ref)
        return _glyph_index.get_token_burns(ref_bytes, limit=limit, offset=offset)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tokens/{ref}/trades", tags=["Token Analytics"])
async def get_token_trades(
    ref: str = Path(..., min_length=72, max_length=72),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0)
):
    """Get token trade/transfer history."""
    _ensure_glyph_index()

    try:
        ref_bytes = bytes.fromhex(ref)
        return _glyph_index.get_token_trades(ref_bytes, limit=limit, offset=offset)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tokens/{ref}/top-holders", tags=["Token Analytics"])
async def get_top_token_holders(
    ref: str = Path(..., min_length=72, max_length=72),
    limit: int = Query(default=100, le=500)
):
    """Get top token holders (rich list) for a specific token."""
    _ensure_glyph_index()

    try:
        ref_bytes = bytes.fromhex(ref)
        return _glyph_index.get_top_holders(ref_bytes, limit=limit)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tokens/{ref}/history", tags=["Token Analytics"])
async def get_token_history(
    ref: str = Path(..., min_length=72, max_length=72),
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0)
):
    """Get full event history (deploy, mint, transfer, burn, update) for a token."""
    _ensure_glyph_index()

    try:
        ref_bytes = bytes.fromhex(ref)
        return _glyph_index.get_token_history(ref_bytes, limit=limit, offset=offset)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tokens/{ref}/metadata", tags=["Token Analytics"])
async def get_token_metadata(ref: str = Path(..., min_length=72, max_length=72)):
    """Get parsed CBOR metadata for a token."""
    _ensure_glyph_index()

    try:
        ref_bytes = bytes.fromhex(ref)
        token = _glyph_index.get_token(ref_bytes)
        if not token:
            raise HTTPException(status_code=404, detail="Token not found")

        if token.metadata_hash:
            metadata = _glyph_index.get_metadata(token.metadata_hash)
            if metadata:
                return {"ref": ref, "metadata": metadata}
        return {"ref": ref, "metadata": None}
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# DMINT v2 CONTRACTS
# =============================================================================

def _ensure_dmint():
    if not _dmint_contracts:
        raise HTTPException(status_code=503, detail="dMint contracts manager not available")


@app.get("/dmint/contracts", tags=["dMint"])
async def get_dmint_contracts(
    format: str = Query(default="extended", description="'simple' for [[ref,outputs],...] or 'extended' for full details"),
    active_only: bool = Query(default=True),
):
    """Get list of mineable dMint contracts."""
    _ensure_dmint()

    try:
        if format == 'simple':
            return _dmint_contracts.get_contracts_simple()
        return _dmint_contracts.get_contracts_extended(active_only=active_only)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dmint/contracts/{ref}", tags=["dMint"])
async def get_dmint_contract(ref: str = Path(..., min_length=72, max_length=72)):
    """Get details for a specific dMint contract."""
    _ensure_dmint()

    try:
        contract = _dmint_contracts.get_contract(ref)
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")
        return contract
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dmint/algorithms", tags=["dMint"])
async def get_dmint_algorithms():
    """Get supported mining algorithm definitions (per Glyph v2 spec Section 11.2)."""
    return {
        "algorithms": [
            {"id": 0, "name": "SHA256D", "description": "Double SHA-256 (v1 compatible)", "opcode": "OP_HASH256"},
            {"id": 1, "name": "BLAKE3", "description": "BLAKE3 hash (V2 hard fork)", "opcode": "OP_BLAKE3"},
            {"id": 2, "name": "KangarooTwelve", "description": "K12 hash (V2 hard fork)", "opcode": "OP_K12"},
            {"id": 3, "name": "Argon2id-Light", "description": "Memory-hard (deferred)", "opcode": None},
            {"id": 4, "name": "RandomX-Light", "description": "CPU-friendly (deferred)", "opcode": None},
        ],
        "daa_modes": [
            {"id": 0, "name": "Fixed", "description": "No difficulty adjustment"},
            {"id": 1, "name": "Epoch", "description": "Bitcoin-style epoch-based DAA"},
            {"id": 2, "name": "ASERT", "description": "ASERT-lite via OP_LSHIFT/OP_RSHIFT"},
            {"id": 3, "name": "LWMA", "description": "Linearly Weighted Moving Average"},
            {"id": 4, "name": "Schedule", "description": "Predetermined difficulty schedule"},
        ],
    }


@app.get("/dmint/by-algorithm/{algorithm}", tags=["dMint"])
async def get_dmint_by_algorithm(
    algorithm: int = Path(..., ge=0, le=4, description="Algorithm ID (0=SHA256D, 1=BLAKE3, 2=K12)")
):
    """Get dMint contracts filtered by mining algorithm."""
    _ensure_dmint()

    try:
        return _dmint_contracts.get_contracts_by_algorithm(algorithm)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dmint/profitable", tags=["dMint"])
async def get_dmint_profitable(limit: int = Query(default=10, le=100)):
    """Get dMint contracts sorted by estimated profitability (reward/difficulty)."""
    _ensure_dmint()

    try:
        return _dmint_contracts.get_most_profitable(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# WAVE NAMING SYSTEM
# =============================================================================

def _ensure_wave():
    if not _wave_index:
        raise HTTPException(status_code=503, detail="WAVE index not available")


@app.get("/wave/resolve/{name}", tags=["WAVE"])
async def wave_resolve(name: str = Path(..., min_length=1, max_length=63)):
    """Resolve a WAVE name to its zone records and owner."""
    _ensure_wave()

    try:
        result = _wave_index.resolve(name)
        if not result:
            return {"name": name, "available": True, "resolved": False}
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/wave/available/{name}", tags=["WAVE"])
async def wave_check_available(name: str = Path(..., min_length=1, max_length=63)):
    """Check if a WAVE name is available for registration."""
    _ensure_wave()

    try:
        return _wave_index.check_available(name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/wave/{name}/subdomains", tags=["WAVE"])
async def wave_get_subdomains(
    name: str = Path(..., min_length=1, max_length=63),
    limit: int = Query(default=100, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """Get subdomains of a parent WAVE name."""
    _ensure_wave()

    try:
        return _wave_index.get_subdomains(name, limit=limit, offset=offset)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/wave/reverse/{scripthash}", tags=["WAVE"])
async def wave_reverse_lookup(
    scripthash: str = Path(..., min_length=64, max_length=64),
    limit: int = Query(default=100, le=1000),
):
    """Find WAVE names owned by an address (scripthash)."""
    _ensure_wave()

    try:
        scripthash_bytes = bytes.fromhex(scripthash)
        return _wave_index.reverse_lookup(scripthash_bytes, limit=limit)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid scripthash format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/wave/stats", tags=["WAVE"])
async def wave_stats():
    """Get WAVE naming system indexing statistics."""
    _ensure_wave()

    try:
        return _wave_index.stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# SWAPS / DEX
# =============================================================================

def _ensure_swap():
    if not _swap_index:
        raise HTTPException(status_code=503, detail="Swap index not available")


@app.get("/swaps/orders", tags=["Swaps"])
async def get_swap_orders(
    base_ref: Optional[str] = Query(default=None, description="Base token ref (72 hex)"),
    quote_ref: Optional[str] = Query(default=None, description="Quote token ref (72 hex)"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Get active swap orders, optionally filtered by trading pair."""
    _ensure_swap()

    try:
        base_bytes = bytes.fromhex(base_ref) if base_ref else None
        quote_bytes = bytes.fromhex(quote_ref) if quote_ref else None
        return _swap_index.get_open_orders(
            base_ref=base_bytes, quote_ref=quote_bytes,
            limit=limit, offset=offset,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/swaps/orders/{order_id}", tags=["Swaps"])
async def get_swap_order(order_id: str = Path(..., min_length=72, max_length=72)):
    """Get a single swap order by ID."""
    _ensure_swap()

    try:
        order_bytes = bytes.fromhex(order_id)
        result = _swap_index.get_order(order_bytes)
        if not result:
            raise HTTPException(status_code=404, detail="Order not found")
        return result
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order_id format")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/swaps/history", tags=["Swaps"])
async def get_swap_history(
    base_ref: Optional[str] = Query(default=None, description="Base token ref (72 hex)"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Get swap trade/fill history."""
    _ensure_swap()

    try:
        base_bytes = bytes.fromhex(base_ref) if base_ref else None
        return _swap_index.get_trade_history(
            base_ref=base_bytes, limit=limit, offset=offset,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/swaps/stats", tags=["Swaps"])
async def get_swap_stats():
    """Get swap/DEX indexing statistics."""
    _ensure_swap()

    try:
        return _swap_index.stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# STARTUP
# =============================================================================

def create_app():
    """Factory function to create the FastAPI app."""
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
