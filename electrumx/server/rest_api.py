"""
FastAPI REST API for RXinDexer

This module provides a REST API layer on top of the ElectrumX-based RXinDexer,
exposing token analytics, market data, and blockchain queries via HTTP endpoints.

Based on the RXinDexer PostgreSQL implementation API design.
"""

from typing import Optional, Dict, Any, List
import os
import time
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Query, Path, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# App instance
app = FastAPI(
    title="RXinDexer REST API",
    description="REST API for Radiant blockchain indexer with Glyph token support",
    version="1.0.0",
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

# Global reference to the indexer (set by the server on startup)
_glyph_index = None
_db = None
_daemon = None
_start_time = time.time()


def set_indexer(glyph_index, db, daemon):
    """Set the indexer reference from the main server."""
    global _glyph_index, _db, _daemon
    _glyph_index = glyph_index
    _db = db
    _daemon = daemon


# =============================================================================
# RESPONSE MODELS
# =============================================================================

class HealthResponse(BaseModel):
    status: str
    uptime_seconds: float
    database: str
    sync_height: Optional[int] = None


class TokenResponse(BaseModel):
    ref: str
    name: Optional[str]
    ticker: Optional[str]
    type: str
    glyph_version: int
    total_supply: int
    current_supply: int


class HolderResponse(BaseModel):
    scripthash: str
    balance: int
    percentage: Optional[float] = None


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
    """Get detailed indexer status."""
    status = {
        "api_version": "1.0.0",
        "uptime_seconds": round(time.time() - _start_time, 2),
    }
    
    if _db:
        status["sync_height"] = _db.db_height
        status["db_engine"] = getattr(_db, 'db_engine', 'unknown')
    
    if _glyph_index:
        status["glyph_indexing"] = True
        status["tokens_cached"] = len(getattr(_glyph_index, 'token_cache', {}))
    
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

@app.get("/glyphs", tags=["Glyphs"])
async def get_all_glyphs(
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    token_type: Optional[int] = Query(default=None, description="Filter by token type ID")
):
    """Get all indexed Glyph tokens with pagination."""
    if not _glyph_index:
        raise HTTPException(status_code=503, detail="Glyph index not available")
    
    try:
        result = await _glyph_index.get_all_tokens_summary(
            limit=limit,
            offset=offset,
            token_type=token_type,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/glyphs/{ref}", tags=["Glyphs"])
async def get_glyph(ref: str = Path(..., min_length=72, max_length=72)):
    """Get Glyph token by reference (72 hex chars = 36 bytes)."""
    if not _glyph_index:
        raise HTTPException(status_code=503, detail="Glyph index not available")
    
    try:
        ref_bytes = bytes.fromhex(ref)
        token = await _glyph_index.get_token(ref_bytes)
        if not token:
            raise HTTPException(status_code=404, detail="Token not found")
        
        return _glyph_index._format_token_for_api(token)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tokens/{ref}/holders", tags=["Token Analytics"])
async def get_token_holders(
    ref: str = Path(..., min_length=72, max_length=72),
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0)
):
    """Get token holders with their balances."""
    if not _glyph_index:
        raise HTTPException(status_code=503, detail="Glyph index not available")
    
    try:
        ref_bytes = bytes.fromhex(ref)
        result = await _glyph_index.get_token_holders(ref_bytes, limit=limit, offset=offset)
        return result
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tokens/{ref}/supply", tags=["Token Analytics"])
async def get_token_supply(ref: str = Path(..., min_length=72, max_length=72)):
    """Get detailed token supply information."""
    if not _glyph_index:
        raise HTTPException(status_code=503, detail="Glyph index not available")
    
    try:
        ref_bytes = bytes.fromhex(ref)
        result = await _glyph_index.get_token_supply(ref_bytes)
        if not result:
            raise HTTPException(status_code=404, detail="Token not found")
        return result
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tokens/{ref}/burns", tags=["Token Analytics"])
async def get_token_burns(
    ref: str = Path(..., min_length=72, max_length=72),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0)
):
    """Get token burn history."""
    if not _glyph_index:
        raise HTTPException(status_code=503, detail="Glyph index not available")
    
    try:
        ref_bytes = bytes.fromhex(ref)
        result = await _glyph_index.get_token_burns(ref_bytes, limit=limit, offset=offset)
        return result
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
    if not _glyph_index:
        raise HTTPException(status_code=503, detail="Glyph index not available")
    
    try:
        ref_bytes = bytes.fromhex(ref)
        result = await _glyph_index.get_token_trades(ref_bytes, limit=limit, offset=offset)
        return result
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
    if not _glyph_index:
        raise HTTPException(status_code=503, detail="Glyph index not available")
    
    try:
        ref_bytes = bytes.fromhex(ref)
        result = await _glyph_index.get_top_holders(ref_bytes, limit=limit)
        return result
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# WALLETS / ADDRESSES
# =============================================================================

@app.get("/wallet/{address}", tags=["Wallets"])
async def get_wallet(address: str):
    """Get wallet balance and token holdings for an address."""
    if not _db:
        raise HTTPException(status_code=503, detail="Database not available")
    
    # This would need integration with the address balance tracking
    return {
        "address": address,
        "message": "Wallet endpoint - requires address index integration",
    }


@app.get("/address/{address}/utxos", tags=["Wallets"])
async def get_address_utxos(
    address: str,
    limit: int = Query(default=100, le=500)
):
    """Get UTXOs for an address."""
    if not _db:
        raise HTTPException(status_code=503, detail="Database not available")
    
    # This would need integration with UTXO tracking
    return {
        "address": address,
        "utxos": [],
        "message": "UTXO endpoint - requires UTXO index integration",
    }


@app.get("/wallets/top", tags=["Wallets"])
async def get_top_wallets(limit: int = Query(default=100, le=500)):
    """Get top RXD wallets by balance (rich list)."""
    if not _db:
        raise HTTPException(status_code=503, detail="Database not available")
    
    # This would need integration with balance caching
    return {
        "wallets": [],
        "message": "Top wallets endpoint - requires balance cache integration",
    }


# =============================================================================
# MARKET DATA
# =============================================================================

@app.get("/market/rxd", tags=["Market"])
async def get_rxd_market():
    """Get RXD market data (CoinGecko style)."""
    # This would integrate with external price feeds
    return {
        "symbol": "RXD",
        "name": "Radiant",
        "price_usd": None,
        "market_cap_usd": None,
        "volume_24h_usd": None,
        "message": "Market data requires external price feed integration",
    }


@app.get("/tokens/{ref}/price", tags=["Market"])
async def get_token_price(ref: str = Path(..., min_length=72, max_length=72)):
    """Get token price data."""
    return {
        "ref": ref,
        "price_rxd": None,
        "price_usd": None,
        "message": "Token price requires swap/trade analysis integration",
    }


@app.get("/tokens/{ref}/ohlcv", tags=["Market"])
async def get_token_ohlcv(
    ref: str = Path(..., min_length=72, max_length=72),
    interval: str = Query(default="1d", description="Candle interval: 1h, 4h, 1d, 1w"),
    limit: int = Query(default=100, le=500)
):
    """Get OHLCV candle data for a token."""
    return {
        "ref": ref,
        "interval": interval,
        "candles": [],
        "message": "OHLCV requires trade history aggregation",
    }


@app.get("/market/swaps", tags=["Market"])
async def get_market_swaps(limit: int = Query(default=50, le=200)):
    """Get recent swap advertisements."""
    # This would integrate with swap indexing
    return {
        "swaps": [],
        "message": "Swap data requires swap index integration",
    }


@app.get("/market/trades", tags=["Market"])
async def get_market_trades(limit: int = Query(default=50, le=200)):
    """Get recent trades."""
    return {
        "trades": [],
        "message": "Trade data requires trade tracking integration",
    }


@app.get("/market/volume", tags=["Market"])
async def get_market_volume():
    """Get 24h trading volume."""
    return {
        "volume_24h_rxd": None,
        "volume_24h_usd": None,
        "trade_count_24h": None,
        "message": "Volume requires trade aggregation",
    }


# =============================================================================
# STARTUP
# =============================================================================

def create_app():
    """Factory function to create the FastAPI app."""
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
