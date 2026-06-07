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
  /dmint/contracts/{ref}/daa   — DAA configuration for a contract
  /dmint/algorithms            — Supported mining algorithms
  /dmint/by-algorithm/{algo}   — Filter by algorithm
  /dmint/profitable            — Sorted by profitability
  /dmint/stats                 — Aggregate dMint statistics

V2 Hard Fork Endpoints:
  /v2/activation-status        — Fork activation height and opcode status

WAVE Endpoints:
  /wave/resolve/{name}         — Resolve WAVE name (canonical/first registration)
  /wave/available/{name}       — Check availability
  /wave/registrations/{name}   — Get all registrations including duplicates
  /wave/names                  — List all registered WAVE names
  /wave/{name}/subdomains      — List subdomains
  /wave/reverse/{scripthash}   — Reverse lookup by owner
  /wave/stats                  — WAVE indexing stats

Swap Endpoints:
  /swaps/orders                — Active swap orders
  /swaps/orders/{order_id}     — Single order detail
  /swaps/history               — Trade history
"""

from typing import Optional, Dict, Any, List, Set
import asyncio
import base64
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass, field

from electrumx.server import metrics as _metrics
from electrumx.server.rate_limiter import (
    DEFAULT_TRUSTED_PROXIES as _DEFAULT_TRUSTED_PROXIES,
    IPRateLimiter as _IPRateLimiter,
    peer_in_networks as _peer_in_networks,
)

from fastapi import FastAPI, HTTPException, Query, Path, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import Response as _Response
from pydantic import BaseModel
import time as _time

_logger = logging.getLogger(__name__)


def _internal_error(exc: Exception, context: str = "") -> HTTPException:
    """Return a generic 500 HTTPException while logging the real cause.

    M2: handlers must not leak DB/daemon internals (exception strings, stack
    traces, file paths) to clients. The raw exception — with a server-side
    traceback and the handler context — is logged at ERROR level for operators,
    and the client receives only ``detail="Internal error"``.
    """
    _logger.error(
        "Unhandled error in REST handler%s: %s",
        f" ({context})" if context else "",
        exc,
        exc_info=True,
    )
    return HTTPException(status_code=500, detail="Internal error")

class _TTLCache:
    """TTL cache with bounded max_size (R12: prevents unbounded memory growth)."""
    def __init__(self, max_size: int = None):
        self._store: Dict[str, tuple] = {}
        self._max_size = max_size or int(os.getenv('REST_CACHE_MAX_ENTRIES', '500'))

    def get(self, key, ttl=30):
        entry = self._store.get(key)
        if entry and _time.monotonic() - entry[1] < ttl:
            return entry[0]
        return None

    def put(self, key, value):
        if key not in self._store and len(self._store) >= self._max_size:
            # Evict oldest entry (insertion-order guaranteed in Python 3.7+)
            oldest_key = next(iter(self._store))
            del self._store[oldest_key]
        self._store[key] = (value, _time.monotonic())

_cache = _TTLCache()

# M1 (DoS): hard cap on the rich-list pagination offset. Without this, an
# attacker could rotate `offset` (previously only ge=0) to bust the per-(limit,
# offset) TTL cache and force a fresh full-keyspace scan every request on a
# public, no-API-key path. Kept in sync with analytics_index.TOP_ADDRESSES_MAX_OFFSET
# (same env var, same default) so the REST bound and the index pool size agree.
_TOP_ADDRESSES_MAX_OFFSET = int(os.getenv('ANALYTICS_TOP_MAX_OFFSET', '10000'))

# App instance
app = FastAPI(
    title="RXinDexer REST API",
    description="REST API for Radiant blockchain indexer with Glyph v2 token, dMint, WAVE, and Swap support",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# REST security posture: fail closed by network EXPOSURE, not by a free-text env
# name.  A REST server bound to a non-loopback interface is internet-facing and
# MUST require an API key + explicit CORS origins.  Previously this keyed off
# ELECTRUMX_ENV defaulting to 'dev', so a production node that forgot to set
# ELECTRUMX_ENV=prod ran fully unauthenticated with wildcard CORS (fail OPEN).
# Keying on the bind host makes the safe path the default; ELECTRUMX_ENV=dev is
# the explicit escape hatch for local/regtest setups that bind 0.0.0.0 on purpose.
_env_name = os.getenv('ELECTRUMX_ENV', os.getenv('ENV', '')).strip().lower()
_is_dev = _env_name == 'dev'

_rest_host = os.getenv('REST_API_HOST', '127.0.0.1').strip()
_public_bind = _rest_host not in ('127.0.0.1', 'localhost', '::1', '0', '')
_enforce_security = _public_bind and not _is_dev

_allowed_origins_raw = os.getenv('ALLOWED_ORIGINS', '').strip()
_allowed_origins = [o.strip() for o in _allowed_origins_raw.split(',') if o.strip()]

# REST_REQUIRE_API_KEY (legacy alias REST_REQUIRE_API_KEY_IN_PROD) — default ON.
_require_rest_api_key = os.getenv(
    'REST_REQUIRE_API_KEY',
    os.getenv('REST_REQUIRE_API_KEY_IN_PROD', '1')
).strip() not in ('0', 'false', 'no')

if _enforce_security:
    if not _allowed_origins:
        raise RuntimeError(
            f'ALLOWED_ORIGINS must be set for a public REST bind '
            f'(REST_API_HOST={_rest_host}). Set explicit origins, or '
            f'ELECTRUMX_ENV=dev for local use.'
        )
    if _require_rest_api_key and not os.getenv('REST_API_KEY', '').strip():
        raise RuntimeError(
            f'REST_API_KEY must be set for a public REST bind '
            f'(REST_API_HOST={_rest_host}). Set a key, or REST_REQUIRE_API_KEY=0 '
            f'to explicitly opt out.'
        )

# CORS middleware — never wildcard on a public, non-dev bind (R10: fail-closed).
if _allowed_origins:
    _cors_origins = _allowed_origins
elif _enforce_security:
    _cors_origins = []          # fail closed: no cross-origin access until configured
else:
    _cors_origins = ['*']       # loopback or explicit dev only
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=['GET', 'POST'],
    allow_headers=['*'],
)

# Response compression — dMint/glyph JSON (and any inline hex icon data) is highly
# compressible; gzip typically shrinks the contracts list by several-fold at the
# wire. minimum_size skips the overhead on tiny responses.
app.add_middleware(GZipMiddleware, minimum_size=1024)


# --- Ref / scripthash helpers (defined before any endpoint that references them
#     as a default param value, since decorators evaluate at import time) -------

# Accepts a ref in either canonical form (72-hex internal, or display txid_vout)
# and returns the 36 raw key bytes. Length bounds cover both: 72-hex == 72 chars,
# display == 64 + '_' + 1..10 decimal digits.
_REF_PATH = Path(..., min_length=66, max_length=80,
                 description="Token ref — 72-hex (internal) or txid_vout form")


def _parse_ref(ref: str) -> bytes:
    """Parse a path ref in either supported form, or raise HTTP 400."""
    from electrumx.server.glyph_index import parse_ref_any
    try:
        return parse_ref_any(ref)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ref format")


def _resolve_ref(ref: str) -> bytes:
    """Resolve a path ref to the canonical 36 raw key bytes for index lookups.

    Accepts both supported forms (``txid_vout`` and 72-hex) and, for the
    72-hex case, transparently retries with the txid portion reversed if the
    primary form does not match a stored token. This patches the
    backward-compat hazard where ``/dmint/contracts.token_ref`` emits the
    72-hex form with the **BE-display** txid order, while the rest of the
    72-hex API expects **internal-LE** order — naive clients chaining the two
    used to 404 silently.

    If no candidate matches a token, returns the primary parsed bytes so the
    caller's "not found" / "empty list" path runs as it did before.
    """
    from electrumx.server.glyph_index import parse_ref_candidates
    try:
        candidates = parse_ref_candidates(ref)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    # Single candidate (e.g. txid_vout form) — no probe needed.
    if len(candidates) == 1:
        return candidates[0]
    # Probe the token index to pick the candidate that actually exists.
    try:
        for cand in candidates:
            if _glyph_index.get_token(cand) is not None:
                return cand
    except Exception:
        # Probe failure should not break the request — fall through to primary.
        pass
    return candidates[0]


def _resolve_dmint_ref(ref: str) -> str:
    """Resolve a path ref to the 72-hex form expected by the dmint contract store.

    The dmint store stores refs with the txid in **BE-display** order (the form
    that the ``token_ref`` field of ``/dmint/contracts`` emits). Clients that
    fetch a glyph ref from the wider API (LE-internal 72-hex) and then chain
    to ``/dmint/contracts/{ref}/...`` would otherwise 404 on a byte-order
    mismatch. Probe both byte orders and return whichever resolves to a real
    contract; on miss, return the primary form so existing 404 paths still
    fire as before.
    """
    from electrumx.server.glyph_index import parse_ref_candidates
    try:
        candidates = parse_ref_candidates(ref)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    if _dmint_contracts is None or len(candidates) == 1:
        return candidates[0].hex()
    try:
        for cand in candidates:
            if _dmint_contracts.get_contract(cand.hex()) is not None:
                return cand.hex()
    except Exception:
        # Probe failure should not break the request — fall through to primary.
        pass
    return candidates[0].hex()


def _resolve_scripthash(ident: str) -> bytes:
    """Resolve an ownership identifier to a 32-byte Electrum scripthash.

    Accepts either a 64-hex Electrum scripthash (as a wallet computes and as
    ``blockchain.scripthash.*`` uses) or a base58 address. Raises ValueError on
    anything else.
    """
    from electrumx.lib.hash import sha256
    s = ident.strip()
    if len(s) == 64:
        try:
            return bytes.fromhex(s)
        except ValueError:
            pass
    coin = getattr(getattr(_glyph_index, 'env', None), 'coin', None)
    if coin is None:
        raise ValueError('coin unavailable')
    # base58 address -> Electrum scripthash (sha256(scriptPubKey) reversed)
    return sha256(coin.pay_to_address_script(s))[::-1]


def _require_api_key(x_api_key: Optional[str] = Header(default=None, alias='X-API-Key')):
    required_key = os.getenv('REST_API_KEY', '').strip()
    if not required_key:
        return
    # R7: timing-safe comparison to prevent timing oracle attacks
    if not x_api_key or not hmac.compare_digest(x_api_key, required_key):
        raise HTTPException(status_code=401, detail='Unauthorized')


@dataclass
class _TokenBucket:
    tokens: float
    last_ts: float


_rate_buckets: Dict[str, _TokenBucket] = {}
_rate_request_count: int = 0
_rate_last_cleanup_ts: float = 0.0
_RATE_CLEANUP_INTERVAL: int = 1000
_TRUST_PROXY: bool = os.getenv('TRUST_PROXY', '0').strip() not in ('0', 'false', 'no', '')
_TRUST_PROXY_HOPS: int = max(1, int(os.getenv('TRUST_PROXY_HOPS', '1')))

# Trusted-proxy allowlist for X-Forwarded-For (mirrors the ElectrumX limiter
# hardening, commit c4637e6). REST :8000 is published on 0.0.0.0 in prod, so
# without a peer gate a client connecting DIRECTLY could set
# `X-Forwarded-For: <victim>` to evade its own rate limit or poison a victim
# IP's REST bucket. We honour the forwarded chain only when the direct socket
# peer (request.client.host) is itself a configured reverse proxy. Empty/unset
# => safe default (loopback + RFC1918, the docker bridge Caddy connects from);
# prod narrows it to TRUSTED_PROXIES=172.18.0.0/16 (the full-stack_default
# bridge subnet where Caddy lives). Reuses the same env + parsing/matching
# helpers as the per-IP limiter so both gate XFF identically.
_TRUSTED_PROXIES = _IPRateLimiter._parse_networks(
    os.getenv('TRUSTED_PROXIES', '').strip() or _DEFAULT_TRUSTED_PROXIES
)


def _get_client_ip(request: Request) -> str:
    """R8 + XFF-spoof hardening: resolve the real client IP, honouring
    X-Forwarded-For ONLY when the direct socket peer is a trusted proxy.

    A client connecting directly to the published REST port could otherwise
    spoof X-Forwarded-For to control its rate-limit key. We trust the forwarded
    chain only when ``request.client.host`` is inside ``_TRUSTED_PROXIES``;
    otherwise we fall back to the raw socket peer, so a direct client can only
    accrue against its own IP and never poison a victim's. TRUST_PROXY=0
    behaviour is unchanged (forwarded headers never consulted).
    """
    peer = request.client.host if request.client else None
    if _TRUST_PROXY and _peer_in_networks(peer, _TRUSTED_PROXIES):
        forwarded = request.headers.get('x-forwarded-for', '').strip()
        if forwarded:
            parts = [p.strip() for p in forwarded.split(',') if p.strip()]
            if parts:
                # Take the Nth-from-right entry where N = TRUST_PROXY_HOPS
                idx = max(0, len(parts) - _TRUST_PROXY_HOPS)
                return parts[idx]
        real_ip = request.headers.get('x-real-ip', '').strip()
        if real_ip:
            return real_ip
    return peer if peer else 'unknown'


def _rate_limit(request: Request):
    global _rate_request_count, _rate_last_cleanup_ts
    limit_per_minute = int(os.getenv('REST_RATE_LIMIT_PER_MIN', '600'))
    burst = int(os.getenv('REST_RATE_LIMIT_BURST', str(limit_per_minute)))
    if limit_per_minute <= 0:
        return

    client_host = _get_client_ip(request)  # R8
    now = time.time()

    # Purge stale buckets: every 1000 requests OR every 60 seconds (R17)
    _rate_request_count += 1
    if _rate_request_count >= _RATE_CLEANUP_INTERVAL or now - _rate_last_cleanup_ts > 60.0:
        _rate_request_count = 0
        _rate_last_cleanup_ts = now
        stale_cutoff = now - 120.0  # 2 minutes
        stale_keys = [k for k, b in _rate_buckets.items() if b.last_ts < stale_cutoff]
        for k in stale_keys:
            del _rate_buckets[k]

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
async def _observability_middleware(request: Request, call_next):
    """R18/R19: record per-request latency and count in Prometheus metrics."""
    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - t0
    endpoint = request.url.path
    _metrics.rest_requests_total.labels(
        method=request.method, endpoint=endpoint, status=str(response.status_code)
    ).inc()
    _metrics.rest_request_seconds.labels(endpoint=endpoint).observe(elapsed)
    return response


@app.middleware("http")
async def _security_middleware(request: Request, call_next):
    # NOTE: in Starlette, @app.middleware("http") decorators execute in REVERSE
    # registration order (last-registered = outermost). _security_middleware is
    # registered after _observability_middleware so it is the OUTER wrapper.
    # HTTPException raised here bypasses FastAPI's ExceptionMiddleware (which lives
    # below user middlewares in the Starlette stack) and would reach
    # ServerErrorMiddleware, converting it to a 500. We catch it and return a
    # JSONResponse directly.
    path = request.url.path
    # Public read-only endpoints — no API key required.
    # Include both trailing-slash and bare forms so FastAPI's redirect logic
    # (which issues a 307 for /tokens → /tokens/) doesn't cause a spurious auth check.
    public_paths = (
        '/health', '/status',
        '/analytics', '/analytics/',
        '/blocks', '/blocks/', '/block/',
        '/glyphs', '/glyphs/', '/glyph/',
        '/tokens', '/tokens/',
        '/transaction', '/transaction/',
        '/dmint', '/dmint/',
        '/v2', '/v2/',
        '/wave', '/wave/',
        '/swap', '/swap/', '/swaps', '/swaps/',
        '/mempool', '/mempool/',
        '/docs', '/openapi',
    )

    # Protect only write/broadcast operations (regardless of method)
    protected_operations = ('/broadcast', '/submit', '/key-reveal')

    try:
        if request.method != 'GET' or any(path.startswith(p) for p in protected_operations):
            _require_api_key(request.headers.get('x-api-key'))
            _rate_limit(request)
        elif any(path.startswith(p) for p in public_paths):
            _rate_limit(request)
        else:
            _require_api_key(request.headers.get('x-api-key'))
            _rate_limit(request)
    except HTTPException as exc:
        from fastapi.responses import JSONResponse as _JSONResponse
        return _JSONResponse(
            status_code=exc.status_code,
            content={'detail': exc.detail},
            headers=dict(exc.headers or {}),
        )
    return await call_next(request)

@app.get('/metrics', include_in_schema=False)
async def prometheus_metrics():
    """R18: Prometheus /metrics endpoint."""
    content = _metrics.generate_metrics_text()
    return _Response(content=content, media_type=_metrics.METRICS_CONTENT_TYPE)


# Global references to indexes (set by the server on startup)
_glyph_index = None
_wave_index = None
_swap_index = None
_analytics_index = None
_dmint_contracts = None
_mempool = None
_db = None
_daemon = None
_start_time = time.time()


def set_indexer(glyph_index, db, daemon, wave_index=None, swap_index=None,
                analytics_index=None, dmint_contracts=None, mempool=None):
    """Set the indexer references from the main server."""
    global _glyph_index, _db, _daemon, _wave_index, _swap_index, _analytics_index, _dmint_contracts, _mempool
    _glyph_index = glyph_index
    _db = db
    _daemon = daemon
    _wave_index = wave_index
    _swap_index = swap_index
    _analytics_index = analytics_index
    _dmint_contracts = dmint_contracts
    _mempool = mempool


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
        _logger.error("health/ready DB error: %s", e, exc_info=True)
        raise HTTPException(status_code=503, detail="Database error")
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
        _logger.error("health/db DB error: %s", e, exc_info=True)
        raise HTTPException(status_code=503, detail="Database error")


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
    status["analytics_indexing"] = _analytics_index is not None
    status["dmint_contracts"] = _dmint_contracts is not None

    return status


def _ensure_analytics_index():
    if not _analytics_index:
        raise HTTPException(status_code=503, detail="Analytics index not available")


@app.get("/analytics/stats", tags=["Analytics"])
def get_analytics_stats():
    _ensure_analytics_index()
    c = _cache.get('a_stats', 120)
    if c: return c
    try:
        r = _analytics_index.get_stats()
    except Exception as e:
        raise _internal_error(e, "/analytics/stats")
    _cache.put('a_stats', r)
    return r


@app.get("/analytics/balance-distribution", tags=["Analytics"])
def get_balance_distribution():
    _ensure_analytics_index()
    c = _cache.get('a_bdist', 30)
    if c: return c
    try:
        r = _analytics_index.get_balance_distribution()
    except Exception as e:
        raise _internal_error(e, "/analytics/balance-distribution")
    _cache.put('a_bdist', r)
    return r


@app.get("/analytics/supply-aging", tags=["Analytics"])
def get_supply_aging():
    _ensure_analytics_index()
    c = _cache.get('a_aging', 30)
    if c: return c
    try:
        r = _analytics_index.get_supply_aging()
    except Exception as e:
        raise _internal_error(e, "/analytics/supply-aging")
    _cache.put('a_aging', r)
    return r


@app.get("/analytics/top-addresses", tags=["Analytics"])
def get_top_addresses(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=_TOP_ADDRESSES_MAX_OFFSET),
):
    _ensure_analytics_index()
    ck = f'a_top_{limit}_{offset}'
    c = _cache.get(ck, 120)
    if c: return c
    try:
        r = _analytics_index.get_top_addresses(limit=limit, offset=offset)
    except Exception as e:
        raise _internal_error(e, "/analytics/top-addresses")
    _cache.put(ck, r)
    return r


@app.get("/analytics/movement", tags=["Analytics"])
def get_movement(days: int = Query(default=30, ge=1, le=3650)):
    _ensure_analytics_index()
    ck = f'a_move_{days}'
    c = _cache.get(ck, 300)
    if c: return c
    try:
        r = _analytics_index.get_movement(days=days)
    except Exception as e:
        raise _internal_error(e, "/analytics/movement")
    _cache.put(ck, r)
    return r


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
            header_data, count = await _db.read_headers(h, 1)
            if header_data and count > 0:
                blocks.append({
                    "height": h,
                    "header_hex": header_data.hex(),
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
        header_data, count = await _db.read_headers(height, 1)
        return {
            "height": height,
            "header_hex": header_data.hex() if header_data and count > 0 else None,
        }
    except Exception as e:
        raise _internal_error(e)


@app.get("/dmint/contracts/{ref}/icon-debug", tags=["dMint"])
async def get_dmint_contract_icon_debug(ref: str = _REF_PATH):
    """Debug helper: inspect icon fields for a specific dMint contract."""
    _ensure_dmint()

    try:
        ref = _resolve_dmint_ref(ref)  # accept BE-display or LE-internal; pick the matching form
        contract = _dmint_contracts.get_contract(ref)
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")

        return {
            "ref": ref,
            "icon_type": contract.get("icon_type"),
            "icon_url": contract.get("icon_url"),
            "icon_ref": contract.get("icon_ref"),
            "icon_data": contract.get("icon_data"),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise _internal_error(e)


@app.get("/dmint/contracts/{ref}/icon", tags=["dMint"])
async def get_dmint_contract_icon(ref: str = _REF_PATH):
    """Serve a contract's embedded icon as raw image bytes.

    Lets clients lazily fetch icons per-token (and lets the browser HTTP-cache
    them) instead of inlining every icon as hex in the contracts list response.
    Returns 404 when the contract has no embedded icon (use icon_url instead).
    """
    _ensure_dmint()

    try:
        ref = _resolve_dmint_ref(ref)  # accept BE-display or LE-internal
        contract = _dmint_contracts.get_contract(ref)
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")

        icon_data = contract.get("icon_data")
        if not icon_data:
            raise HTTPException(status_code=404, detail="Contract has no embedded icon")

        try:
            raw = bytes.fromhex(icon_data)
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="Icon data is not valid hex")

        media_type = contract.get("icon_type") or "application/octet-stream"
        # Icons are immutable per ref — cache hard so a token's icon is fetched once.
        return _Response(
            content=raw,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=604800, immutable"},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _internal_error(e)


@app.get("/tx/{txid}", tags=["Transactions"])
@app.get("/transaction/{txid}", tags=["Transactions"])
async def get_transaction(txid: str = Path(..., min_length=64, max_length=64)):
    """Get transaction by txid."""
    if not _daemon:
        raise HTTPException(status_code=503, detail="Daemon not available")

    try:
        raw_tx = await _daemon.getrawtransaction(txid, True)
        return raw_tx
    except Exception as e:
        # The daemon raises both for genuinely-missing txs and for internal RPC
        # failures; either way, never leak the raw exception string to clients.
        _logger.warning("getrawtransaction failed for %s: %s", txid, e, exc_info=True)
        raise HTTPException(status_code=404, detail="Transaction not found")


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
        raise _internal_error(e)


@app.get("/glyphs/search", tags=["Glyphs"])
async def search_glyphs(
    q: str = Query(..., min_length=1, max_length=100, description="Search query (name or ticker)"),
    protocols: Optional[str] = Query(default=None, max_length=256, description="Comma-separated protocol IDs to filter"),
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
        raise _internal_error(e)


@app.get("/glyphs/stats", tags=["Glyphs"])
def get_glyph_stats():
    """Get Glyph token indexing statistics (counts by type and version)."""
    _ensure_glyph_index()
    c = _cache.get('g_stats', 120)
    if c: return c
    r = _glyph_index.get_stats()
    _cache.put('g_stats', r)
    return r


@app.get("/glyphs/by-type/{type_id}", tags=["Glyphs"])
async def get_glyphs_by_type(
    type_id: int = Path(..., ge=0, le=7, description="Token type ID (1=FT, 2=NFT, 3=DAT, 4=DMINT, 5=WAVE, 6=Container, 7=Authority)"),
    limit: int = Query(default=100, le=500),
    cursor: Optional[str] = Query(default=None, description="Opaque pagination cursor from previous response next_cursor"),
):
    """Get tokens filtered by type."""
    _ensure_glyph_index()

    try:
        result = _glyph_index.get_tokens_by_type(type_id, limit=limit, cursor=cursor)
        return {"type_id": type_id, **result}
    except Exception as e:
        raise _internal_error(e)


@app.get("/glyphs/{ref}", tags=["Glyphs"])
async def get_glyph(ref: str = _REF_PATH):
    """Get Glyph token by reference (72 hex chars = 36 bytes)."""
    _ensure_glyph_index()

    try:
        ref_bytes = _resolve_ref(ref)
        token = _glyph_index.get_token(ref_bytes)
        if not token:
            raise HTTPException(status_code=404, detail="Token not found")

        return _glyph_index._token_to_dict(token)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except HTTPException:
        raise
    except Exception as e:
        raise _internal_error(e)


@app.get("/tokens/{ref}/holders", tags=["Token Analytics"])
async def get_token_holders(
    ref: str = _REF_PATH,
    limit: int = Query(default=100, le=500),
    cursor: Optional[str] = Query(default=None, description="Opaque pagination cursor from previous response next_cursor"),
):
    """Get token holders with their balances."""
    _ensure_glyph_index()

    try:
        ref_bytes = _resolve_ref(ref)
        return _glyph_index.get_token_holders(ref_bytes, limit=limit, cursor=cursor)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except Exception as e:
        raise _internal_error(e)


@app.get("/addresses/{ident}/glyphs", tags=["Ownership"])
async def get_address_glyphs(
    ident: str = Path(..., min_length=1, max_length=128,
                      description="Electrum scripthash (64 hex) or base58 address"),
    limit: int = Query(default=100, le=500),
    cursor: Optional[str] = Query(default=None, description="Opaque pagination cursor"),
):
    """Forward ownership: Glyph tokens (FT + NFT) held by an address.

    This is the REST equivalent of the ElectrumX ``glyph.list_tokens`` method the
    game/wallet uses. ``ident`` may be a 64-hex Electrum scripthash (what a wallet
    computes from its address) or a base58 address. Refs are returned in canonical
    ``txid_vout`` form.
    """
    _ensure_glyph_index()
    try:
        sh = _resolve_scripthash(ident)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid scripthash or address")
    try:
        return _glyph_index.get_balances_for_scripthash(sh, limit=limit, cursor=cursor)
    except Exception as e:
        raise _internal_error(e)


@app.get("/tokens/{ref}/supply", tags=["Token Analytics"])
async def get_token_supply(ref: str = _REF_PATH):
    """Get detailed token supply information."""
    _ensure_glyph_index()

    try:
        ref_bytes = _resolve_ref(ref)
        result = _glyph_index.get_token_supply(ref_bytes)
        if not result:
            raise HTTPException(status_code=404, detail="Token not found")
        return result
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except HTTPException:
        raise
    except Exception as e:
        raise _internal_error(e)


@app.get("/tokens/{ref}/burns", tags=["Token Analytics"])
async def get_token_burns(
    ref: str = _REF_PATH,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0)
):
    """Get token burn history."""
    _ensure_glyph_index()

    try:
        ref_bytes = _resolve_ref(ref)
        return _glyph_index.get_token_burns(ref_bytes, limit=limit, offset=offset)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except Exception as e:
        raise _internal_error(e)


@app.get("/tokens/{ref}/trades", tags=["Token Analytics"])
async def get_token_trades(
    ref: str = _REF_PATH,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0)
):
    """Get token trade/transfer history."""
    _ensure_glyph_index()

    try:
        ref_bytes = _resolve_ref(ref)
        return _glyph_index.get_token_trades(ref_bytes, limit=limit, offset=offset)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except Exception as e:
        raise _internal_error(e)


@app.get("/tokens/{ref}/top-holders", tags=["Token Analytics"])
async def get_top_token_holders(
    ref: str = _REF_PATH,
    limit: int = Query(default=100, le=500)
):
    """Get top token holders (rich list) for a specific token."""
    _ensure_glyph_index()

    try:
        ref_bytes = _resolve_ref(ref)
        return _glyph_index.get_top_holders(ref_bytes, limit=limit)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except Exception as e:
        raise _internal_error(e)


@app.get("/tokens/{ref}/history", tags=["Token Analytics"])
async def get_token_history(
    ref: str = _REF_PATH,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    cursor: Optional[str] = Query(default=None),
):
    """Get full event history (deploy, mint, transfer, burn, update) for a token.

    When ``cursor`` is supplied (even as an empty string) the response shape
    switches to ``{entries, next_cursor, has_more}`` for stable pagination
    across mempool churn. Omit ``cursor`` to keep the legacy list shape.
    See docs/pagination-cursors.md.
    """
    _ensure_glyph_index()

    try:
        ref_bytes = _resolve_ref(ref)
        if cursor is None:
            return _glyph_index.get_token_history(ref_bytes, limit=limit, offset=offset)
        return _glyph_index.get_token_history(
            ref_bytes, limit=limit, cursor=cursor or None, _use_cursor=True
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except Exception as e:
        raise _internal_error(e)


def _sanitize_cbor(obj):
    """Recursively convert CBOR-decoded objects to JSON-serializable types.

    Handles CBORTag (unknown tags → str repr), bytes (→ hex str), and
    nested dicts/lists.
    """
    import cbor2
    if isinstance(obj, dict):
        return {str(k): _sanitize_cbor(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_cbor(i) for i in obj]
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex()
    if isinstance(obj, cbor2.CBORTag):
        return {"_cbor_tag": obj.tag, "value": _sanitize_cbor(obj.value)}
    return obj


@app.get("/tokens/{ref}/metadata", tags=["Token Analytics"])
async def get_token_metadata(ref: str = _REF_PATH):
    """Get parsed CBOR metadata for a token."""
    _ensure_glyph_index()

    try:
        ref_bytes = _resolve_ref(ref)
        token = _glyph_index.get_token(ref_bytes)
        if not token:
            raise HTTPException(status_code=404, detail="Token not found")

        if token.metadata_hash:
            metadata = _glyph_index.get_metadata(token.metadata_hash)
            if metadata:
                return {"ref": ref, "metadata": _sanitize_cbor(metadata)}
        return {"ref": ref, "metadata": None}
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except HTTPException:
        raise
    except Exception as e:
        raise _internal_error(e)


# =============================================================================
# ENCRYPTED TOKENS (Phase 6 / REP-3008 + REP-3009)
# =============================================================================

@app.get("/glyphs/encrypted", tags=["Encrypted"])
async def list_encrypted_tokens(
    limit: int = Query(default=100, le=500),
    cursor: Optional[str] = Query(default=None, description="Opaque pagination cursor from previous response next_cursor"),
    timelocked_only: bool = Query(default=False, description="Only return timelocked tokens"),
):
    """
    List encrypted Glyph tokens.

    Privacy-preserving: only returns ciphertext hashes and metadata
    commitments — never plaintext content or CEKs.
    """
    _ensure_glyph_index()
    try:
        result = _glyph_index.list_encrypted_tokens(
            limit=limit, cursor=cursor, timelocked_only=timelocked_only
        )
        return {**result, "timelocked_only": timelocked_only}
    except Exception as e:
        raise _internal_error(e)


@app.get("/glyphs/{ref}/key-reveal", tags=["Encrypted"])
async def get_key_reveal(ref: str = _REF_PATH):
    """
    Get the CEK reveal record for a timelocked token.

    Returns the revealed CEK hex once a reveal transaction has been
    confirmed. Returns ``{"revealed": false}`` if no reveal is indexed yet.
    """
    _ensure_glyph_index()
    try:
        ref_bytes = _resolve_ref(ref)
        result = _glyph_index.get_key_reveal(ref_bytes)
        if result is None:
            return {"revealed": False}
        return {"revealed": True, **result}
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format (expected 72 hex chars)")
    except Exception as e:
        raise _internal_error(e)


@app.post("/glyphs/{ref}/key-reveal", tags=["Encrypted"])
async def record_key_reveal(
    ref: str = _REF_PATH,
    reveal_tx: str = Query(..., min_length=64, max_length=64, description="Reveal tx txid (64 hex)"),
    revealed_key: str = Query(..., min_length=64, max_length=64, description="CEK hex (64 hex = 32 bytes)"),
    reveal_height: int = Query(..., ge=0, description="Block height of reveal confirmation"),
):
    """
    Record a CEK reveal for a timelocked token.

    The indexer verifies the SHA256 of the submitted CEK against the
    on-chain commitment before accepting the reveal.
    """
    _ensure_glyph_index()
    try:
        import hashlib
        import time as _time

        ref_bytes = _resolve_ref(ref)
        cek_bytes = bytes.fromhex(revealed_key)

        # Verify CEK hash matches on-chain commitment
        token = _glyph_index.get_token(ref_bytes)
        if token and token.timelock_cek_hash:
            expected = token.timelock_cek_hash
            if expected.startswith("sha256:"):
                expected = expected[len("sha256:"):]
            if hashlib.sha256(cek_bytes).hexdigest() != expected:
                raise HTTPException(status_code=422, detail="CEK hash mismatch")

        _glyph_index.record_key_reveal(
            ref_bytes,
            bytes.fromhex(reveal_tx),
            revealed_key,
            reveal_height,
            int(_time.time()),
        )
        return {"ok": True}
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid hex parameter")
    except Exception as e:
        raise _internal_error(e)


# =============================================================================
# DMINT v2 CONTRACTS
# =============================================================================

def _ensure_dmint():
    if not _dmint_contracts:
        raise HTTPException(status_code=503, detail="dMint contracts manager not available")


@app.get("/dmint/contracts", tags=["dMint"])
async def get_dmint_contracts(
    request: Request,
    version: int = Query(default=2, ge=1, le=2),
    view: str = Query(default="token_summary"),
    status: str = Query(default="mineable", description="mineable | finished | all"),
    algorithm_ids: Optional[str] = Query(default=None, max_length=256, description="Comma-separated algorithm IDs"),
    sort_field: str = Query(default="deploy_height"),
    sort_dir: str = Query(default="desc"),
    limit: int = Query(default=1000, ge=1, le=5000),
    cursor: Optional[str] = Query(default=None),
    include_icon_data: bool = Query(default=False, description="Include embedded icon data_hex (large)"),
    format: Optional[str] = Query(default=None, description="Legacy only: 'simple' | 'extended'"),
    active_only: bool = Query(default=True, description="Legacy only"),
):
    """Get list of mineable dMint contracts."""
    _ensure_dmint()

    try:
        if format in ('simple', 'extended'):
            if format == 'simple':
                return _dmint_contracts.get_contracts_simple()
            return _dmint_contracts.get_contracts_extended(active_only=active_only)

        use_v2 = version == 2
        if use_v2:
            parsed_algorithm_ids = []
            if algorithm_ids:
                parsed_algorithm_ids = [
                    int(part.strip())
                    for part in algorithm_ids.split(',')
                    if part.strip()
                ]

            params = {
                "version": 2,
                "view": view,
                "filters": {
                    "status": status,
                    "algorithm_ids": parsed_algorithm_ids,
                },
                "sort": {
                    "field": sort_field,
                    "dir": sort_dir,
                },
                "pagination": {
                    "limit": limit,
                    "cursor": cursor,
                },
            }
            result = _dmint_contracts.get_contracts_v2(params)
            if not include_icon_data and isinstance(result, dict):
                # Strip heavy inline icon hex from the list. For embedded icons
                # (no remote url), redirect the client to the lazy /icon route so
                # the icon still renders but is fetched/cached per-token on demand.
                for item in result.get('items', []):
                    icon = item.get('icon')
                    if not isinstance(icon, dict):
                        continue
                    if icon.get('data_hex') and not icon.get('url'):
                        ref = item.get('token_ref')
                        if ref:
                            icon['url'] = f"/dmint/contracts/{ref}/icon"
                    icon['data_hex'] = None
            return result

        return _dmint_contracts.get_contracts_extended(active_only=active_only)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise _internal_error(e)


@app.get("/dmint/contracts/{ref}", tags=["dMint"])
async def get_dmint_contract(ref: str = _REF_PATH):
    """Get details for a specific dMint contract."""
    _ensure_dmint()

    try:
        ref = _resolve_dmint_ref(ref)  # accept BE-display or LE-internal; pick the matching form
        contract = _dmint_contracts.get_contract(ref)
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")
        return contract
    except HTTPException:
        raise
    except Exception as e:
        raise _internal_error(e)


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
        raise _internal_error(e)


@app.get("/dmint/profitable", tags=["dMint"])
async def get_dmint_profitable(limit: int = Query(default=10, le=100)):
    """Get dMint contracts sorted by estimated profitability (reward/difficulty)."""
    _ensure_dmint()

    try:
        return _dmint_contracts.get_most_profitable(limit=limit)
    except Exception as e:
        raise _internal_error(e)


@app.get("/dmint/stats", tags=["dMint"])
async def get_dmint_stats():
    """Get aggregate dMint statistics: total contracts, breakdown by algorithm and DAA mode."""
    _ensure_dmint()

    try:
        contracts = _dmint_contracts.contracts
        active = [c for c in contracts if c.get('active', True)]
        inactive = [c for c in contracts if not c.get('active', True)]

        algo_names = {0: 'SHA256D', 1: 'BLAKE3', 2: 'K12', 3: 'Argon2id-Light', 4: 'RandomX-Light'}
        by_algorithm = {}
        for c in active:
            algo_id = c.get('algorithm', 0)
            name = algo_names.get(algo_id, f'unknown({algo_id})')
            by_algorithm[name] = by_algorithm.get(name, 0) + 1

        daa_names = {0: 'fixed', 1: 'epoch', 2: 'asert', 3: 'lwma', 4: 'schedule'}
        by_daa = {}
        for c in active:
            daa_id = c.get('daa_mode', 0)
            name = daa_names.get(daa_id, f'unknown({daa_id})')
            by_daa[name] = by_daa.get(name, 0) + 1

        total_reward = sum(c.get('reward', 0) for c in active)

        return {
            'total_contracts': len(contracts),
            'active': len(active),
            'completed': len(inactive),
            'by_algorithm': by_algorithm,
            'by_daa_mode': by_daa,
            'total_active_reward': total_reward,
            'updated_height': _dmint_contracts.last_updated_height,
        }
    except Exception as e:
        raise _internal_error(e)


@app.get("/dmint/contracts/{ref}/daa", tags=["dMint"])
async def get_dmint_contract_daa(
    ref: str = _REF_PATH
):
    """Get DAA (Difficulty Adjustment Algorithm) configuration for a specific dMint contract."""
    _ensure_dmint()

    try:
        ref = _resolve_dmint_ref(ref)  # accept BE-display or LE-internal; pick the matching form
        contract = _dmint_contracts.get_contract(ref)
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")

        daa_names = {0: 'fixed', 1: 'epoch', 2: 'asert', 3: 'lwma', 4: 'schedule'}
        algo_names = {0: 'SHA256D', 1: 'BLAKE3', 2: 'K12', 3: 'Argon2id-Light', 4: 'RandomX-Light'}
        daa_id = contract.get('daa_mode', 0)
        algo_id = contract.get('algorithm', 0)

        result = {
            'ref': ref,
            'algorithm': {'id': algo_id, 'name': algo_names.get(algo_id, 'unknown')},
            'daa_mode': {'id': daa_id, 'name': daa_names.get(daa_id, 'unknown')},
            'current_difficulty': contract.get('difficulty', 0),
            'reward': contract.get('reward', 0),
        }

        # Include mode-specific parameters if available
        daa_params = contract.get('daa_params', {})
        if daa_params:
            result['daa_params'] = daa_params
        elif daa_id == 2:  # ASERT defaults
            result['daa_params'] = {
                'target_block_time': contract.get('target_block_time', 60),
                'half_life': contract.get('half_life', 1000),
            }
        elif daa_id == 3:  # LWMA defaults
            result['daa_params'] = {
                'target_block_time': contract.get('target_block_time', 60),
                'window_size': contract.get('window_size', 144),
            }
        elif daa_id == 1:  # Epoch defaults
            result['daa_params'] = {
                'target_block_time': contract.get('target_block_time', 60),
                'epoch_length': contract.get('epoch_length', 2016),
                'max_adjustment': contract.get('max_adjustment', 4),
            }

        return result
    except HTTPException:
        raise
    except Exception as e:
        raise _internal_error(e)


@app.get("/dmint/contracts/{ref}/mints", tags=["dMint"])
async def get_dmint_mint_history(
    ref: str = _REF_PATH,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Get mint history for a dMint token, including minted amounts per event."""
    _ensure_glyph_index()

    try:
        ref_bytes = _resolve_ref(ref)
        return _glyph_index.get_mint_history(ref_bytes, limit=limit, offset=offset)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except Exception as e:
        raise _internal_error(e)


@app.get("/dmint/tokens", tags=["dMint"])
async def get_dmint_tokens(
    limit: int = Query(default=100, le=500),
    cursor: Optional[str] = Query(default=None, description="Opaque pagination cursor from previous response next_cursor"),
    active_only: bool = Query(default=True),
):
    """Get all dMint tokens with full mining details (algorithm, difficulty, reward, supply)."""
    _ensure_glyph_index()

    try:
        return _glyph_index.get_dmint_tokens(limit=limit, cursor=cursor, active_only=active_only)
    except Exception as e:
        raise _internal_error(e)


# =============================================================================
# V2 HARD FORK STATUS
# =============================================================================

# Activation heights per network (from Radiant Core chainparams.cpp)
_V2_ACTIVATION_HEIGHTS = {
    'mainnet': 410_000,
    'testnet3': 410_000,
    'scalenet': 1_000,
    'regtest': 200,
}

_V2_OPCODES = [
    {'name': 'OP_BLAKE3', 'hex': '0xee', 'purpose': 'Blake3 hash for dMint PoW'},
    {'name': 'OP_K12', 'hex': '0xef', 'purpose': 'KangarooTwelve hash for dMint PoW'},
    {'name': 'OP_LSHIFT', 'hex': '0x98', 'purpose': 'Bitwise left shift (DAA arithmetic)'},
    {'name': 'OP_RSHIFT', 'hex': '0x99', 'purpose': 'Bitwise right shift (DAA arithmetic)'},
    {'name': 'OP_2MUL', 'hex': '0x8d', 'purpose': 'Multiply by 2 (DAA arithmetic)'},
    {'name': 'OP_2DIV', 'hex': '0x8e', 'purpose': 'Divide by 2 (DAA arithmetic)'},
]


@app.get("/v2/activation-status", tags=["V2 Fork"])
async def get_v2_activation_status():
    """Get V2 hard fork activation status including current height vs activation height."""
    current_height = None
    if _db:
        try:
            current_height = _db.db_height
        except Exception:
            pass

    network = os.getenv('NET', os.getenv('COIN', 'mainnet')).strip().lower()
    if 'test' in network:
        network_key = 'testnet3'
    elif 'scale' in network:
        network_key = 'scalenet'
    elif 'reg' in network:
        network_key = 'regtest'
    else:
        network_key = 'mainnet'

    activation_height = _V2_ACTIVATION_HEIGHTS.get(network_key, 410_000)
    activated = current_height is not None and current_height >= activation_height

    return {
        'network': network_key,
        'activation_height': activation_height,
        'current_height': current_height,
        'activated': activated,
        'blocks_remaining': max(0, activation_height - (current_height or 0)) if current_height is not None else None,
        'opcodes': _V2_OPCODES,
        'gating_flag': 'SCRIPT_ENHANCED_REFERENCES',
    }


# =============================================================================
# WAVE NAMING SYSTEM
# =============================================================================

def _ensure_wave():
    if not _wave_index:
        raise HTTPException(status_code=503, detail="WAVE index not available")


@app.get("/wave/resolve/{name}", tags=["WAVE"])
async def wave_resolve(
    name: str = Path(..., min_length=1, max_length=63),
    include_duplicates: bool = Query(default=False, description="Include duplicate registrations in response"),
):
    """Resolve a WAVE name to its zone records and owner.
    
    Always returns the CANONICAL (first) registration for the name.
    Later registrations are tracked as duplicates but not used for resolution.
    
    Set include_duplicates=true to see all duplicate registrations.
    """
    _ensure_wave()

    # Strip domain suffix (e.g. "alice.rxd" → "alice") for user convenience
    dot_idx = name.rfind('.')
    if dot_idx > 0:
        name = name[:dot_idx]

    try:
        result = _wave_index.resolve(name, include_duplicates=include_duplicates)
        if not result:
            return {"name": name, "available": True, "resolved": False}
        return result
    except Exception as e:
        raise _internal_error(e)


@app.get("/wave/available/{name}", tags=["WAVE"])
async def wave_check_available(name: str = Path(..., min_length=1, max_length=63)):
    """Check if a WAVE name is available for registration."""
    _ensure_wave()

    try:
        return _wave_index.check_available(name)
    except Exception as e:
        raise _internal_error(e)


@app.get("/wave/registrations/{name}", tags=["WAVE"])
async def wave_get_all_registrations(name: str = Path(..., min_length=1, max_length=63)):
    """Get all registrations for a WAVE name including duplicates.
    
    Returns the canonical (first) registration plus all duplicate registrations.
    This is useful for auditing name ownership disputes.
    """
    _ensure_wave()

    try:
        result = _wave_index.get_all_registrations(name)
        if not result or not result.get('registered', False):
            return {"name": name, "registered": False, "available": True}
        return result
    except Exception as e:
        raise _internal_error(e)


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
        raise _internal_error(e)


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
        raise _internal_error(e)


@app.get("/wave/stats", tags=["WAVE"])
async def wave_stats():
    """Get WAVE naming system indexing statistics."""
    _ensure_wave()

    try:
        return _wave_index.stats()
    except Exception as e:
        raise _internal_error(e)


@app.get("/wave/names", tags=["WAVE"])
async def wave_list_names(
    limit: int = Query(default=500, le=2000),
    cursor: Optional[str] = Query(default=None, description="Pagination cursor"),
    include_duplicates: bool = Query(default=False, description="Include duplicate count for each name"),
):
    """List all registered WAVE names with their targets.
    
    Returns a compact list sourced from the Glyph token index (type 5 = WAVE).
    Supports cursor-based pagination for large result sets.
    
    Note: The canonical (first) registration is always returned for each name.
    Later registrations of the same name are tracked as duplicates but not used for resolution.
    """
    _ensure_wave()
    if not _glyph_index:
        raise HTTPException(status_code=503, detail="Glyph index not available")

    try:
        # Decode cursor for glyph BY_TYPE pagination
        result = _glyph_index.get_tokens_by_type(5, limit=limit * 3, cursor=cursor)
        tokens = result.get('tokens', [])

        # Deduplicate: for each name, keep only the canonical (first-registration) token.
        # The wave index stores the canonical ref per name; any token whose glyph ref
        # does NOT match the wave canonical ref is a duplicate.
        seen_names: set = set()
        names = []
        for token in tokens:
            attrs = token.get('attrs') or {}
            name = attrs.get('name', '')
            domain = attrs.get('domain', 'rxd')
            if not name:
                continue

            full_name = f"{name}.{domain}"
            token_ref_str = token.get('ref', '')

            # Skip if we've already emitted this name
            if full_name in seen_names:
                continue

            # Check wave index: is this token the canonical (first) registration?
            # The wave index stores claim_ref = tx_hash+vout(0) for canonical entries.
            # The glyph token's deploy_txid equals the claim_ref txid (same tx).
            canonical_ref = _wave_index._resolve_name_to_ref(name)
            if canonical_ref is None:
                # Not in wave index — skip
                continue

            # canonical_ref[:32] is the tx_hash (little-endian), deploy_txid is hex (reversed)
            canonical_txid_hex = canonical_ref[:32][::-1].hex()
            token_deploy_txid = token.get('deploy_txid', '')
            if canonical_txid_hex != token_deploy_txid:
                # This is a duplicate registration — skip
                continue

            seen_names.add(full_name)
            name_entry = {
                'name': name,
                'domain': domain,
                'full_name': full_name,
                'target': attrs.get('target', ''),
                'ref': token_ref_str,
                'height': token.get('deploy_height', 0),
                'spent': token.get('is_spent', False),
                'canonical': True,
            }
            if include_duplicates:
                name_entry['has_duplicates'] = _wave_index._has_duplicates(name)
            names.append(name_entry)
            if len(names) >= limit:
                break

        return {
            'names': names,
            'total': len(names),
            'next_cursor': result.get('next_cursor') if len(names) >= limit else None,
        }
    except Exception as e:
        raise _internal_error(e)


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
        if base_bytes and quote_bytes:
            return _swap_index.get_orderbook(
                base_bytes, quote_bytes, limit=limit,
            )
        return _swap_index.get_open_orders(
            base_ref=base_bytes, limit=limit, offset=offset,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except Exception as e:
        raise _internal_error(e)


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
        raise _internal_error(e)


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
        if not base_bytes:
            return {'trades': [], 'error': 'base_ref is required'}
        return _swap_index.get_swap_history(
            base_bytes, limit=limit, offset=offset,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ref format")
    except Exception as e:
        raise _internal_error(e)


@app.get("/swaps/stats", tags=["Swaps"])
async def get_swap_stats():
    """Get swap/DEX indexing statistics."""
    _ensure_swap()

    try:
        return {
            'enabled': getattr(_swap_index, 'enabled', False),
            'order_cache_size': len(getattr(_swap_index, 'order_cache', {})),
        }
    except Exception as e:
        raise _internal_error(e)


# =============================================================================
# MEMPOOL (Unconfirmed Glyph/Swap Data)
# =============================================================================

def _get_mempool_glyph():
    """Get the mempool glyph index if available."""
    if not _mempool:
        return None
    return getattr(_mempool, 'glyph_mempool', None)


@app.get("/mempool/glyphs/balance/{scripthash}/{ref}", tags=["Mempool"])
async def mempool_glyph_balance(
    scripthash: str = Path(..., min_length=64, max_length=64),
    ref: str = _REF_PATH,
):
    """Get unconfirmed balance delta for a token at an address."""
    mp = _get_mempool_glyph()
    if not mp:
        raise HTTPException(status_code=503, detail="Mempool Glyph indexing not available")

    try:
        sh_bytes = bytes.fromhex(scripthash)
        ref_bytes = _resolve_ref(ref)
        delta = mp.get_unconfirmed_glyph_balance(sh_bytes, ref_bytes)
        return {"scripthash": scripthash, "ref": ref, "unconfirmed_delta": delta}
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid hex format")
    except Exception as e:
        raise _internal_error(e)


@app.get("/mempool/glyphs/txs/{scripthash}", tags=["Mempool"])
async def mempool_glyph_txs(
    scripthash: str = Path(..., min_length=64, max_length=64),
):
    """Get unconfirmed Glyph transactions for an address."""
    mp = _get_mempool_glyph()
    if not mp:
        raise HTTPException(status_code=503, detail="Mempool Glyph indexing not available")

    try:
        sh_bytes = bytes.fromhex(scripthash)
        txs = mp.get_unconfirmed_glyph_txs(sh_bytes)
        return {"scripthash": scripthash, "txs": txs, "count": len(txs)}
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid hex format")
    except Exception as e:
        raise _internal_error(e)


@app.get("/mempool/glyphs/token/{ref}", tags=["Mempool"])
async def mempool_token_txs(
    ref: str = _REF_PATH,
):
    """Get unconfirmed transactions for a specific token."""
    mp = _get_mempool_glyph()
    if not mp:
        raise HTTPException(status_code=503, detail="Mempool Glyph indexing not available")

    try:
        ref_bytes = _resolve_ref(ref)
        txs = mp.get_unconfirmed_token_txs(ref_bytes)
        return {"ref": ref, "txs": txs, "count": len(txs)}
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid hex format")
    except Exception as e:
        raise _internal_error(e)


@app.get("/mempool/swaps/orders", tags=["Mempool"])
async def mempool_swap_orders(
    base_ref: Optional[str] = Query(default=None, description="Base token ref (72 hex)"),
    quote_ref: Optional[str] = Query(default=None, description="Quote token ref (72 hex)"),
):
    """Get unconfirmed swap orders from mempool."""
    mp = _get_mempool_glyph()
    if not mp:
        raise HTTPException(status_code=503, detail="Mempool Glyph indexing not available")

    try:
        base_bytes = bytes.fromhex(base_ref) if base_ref else None
        quote_bytes = bytes.fromhex(quote_ref) if quote_ref else None
        orders = mp.get_unconfirmed_swap_orders(base_bytes, quote_bytes)
        return {"orders": orders, "count": len(orders)}
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid hex format")
    except Exception as e:
        raise _internal_error(e)


@app.get("/mempool/swaps/user/{scripthash}", tags=["Mempool"])
async def mempool_user_swap_orders(
    scripthash: str = Path(..., min_length=64, max_length=64),
):
    """Get unconfirmed swap orders for a specific user."""
    mp = _get_mempool_glyph()
    if not mp:
        raise HTTPException(status_code=503, detail="Mempool Glyph indexing not available")

    try:
        sh_bytes = bytes.fromhex(scripthash)
        orders = mp.get_user_unconfirmed_orders(sh_bytes)
        return {"scripthash": scripthash, "orders": orders, "count": len(orders)}
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid hex format")
    except Exception as e:
        raise _internal_error(e)


@app.get("/mempool/info", tags=["Mempool"])
async def mempool_node_info():
    """Get standard mempool info from the Radiant node (tx count, size, fees)."""
    if not _daemon:
        raise HTTPException(status_code=503, detail="Daemon not available")

    try:
        info = await _daemon._send_single('getmempoolinfo')
        return info
    except Exception as e:
        raise _internal_error(e, "/mempool/info")


@app.get("/mempool/stats", tags=["Mempool"])
async def mempool_glyph_stats():
    """Get mempool Glyph/Swap indexing statistics."""
    mp = _get_mempool_glyph()
    if not mp:
        return {"enabled": False}

    stats = mp.stats()
    stats["enabled"] = True
    return stats


# =============================================================================
# WEBSOCKET SUBSCRIPTIONS
# =============================================================================

@dataclass
class _WsClient:
    ws: Any
    subscribed_refs: Set[str] = field(default_factory=set)
    subscribed_scripthashes: Set[str] = field(default_factory=set)
    subscribe_all_tokens: bool = False
    subscribe_all_swaps: bool = False


_ws_clients: Dict[int, _WsClient] = {}
_ws_broadcast_task = None


async def _ws_broadcast_loop():
    """Poll mempool touched sets and broadcast to subscribed WebSocket clients."""
    while True:
        await asyncio.sleep(2.0)
        mp = _get_mempool_glyph()
        if not mp or not _ws_clients:
            continue

        try:
            touched_refs, touched_shs = mp.get_touched_and_clear()
        except Exception:
            continue

        if not touched_refs and not touched_shs:
            continue

        ref_hexes = {r.hex() for r in touched_refs}
        sh_hexes = {s.hex() for s in touched_shs}

        dead = []
        for cid, client in _ws_clients.items():
            matched_refs = ref_hexes & client.subscribed_refs if client.subscribed_refs else set()
            matched_shs = sh_hexes & client.subscribed_scripthashes if client.subscribed_scripthashes else set()
            send_token = client.subscribe_all_tokens and ref_hexes
            send_swap = client.subscribe_all_swaps and ref_hexes

            if matched_refs or matched_shs or send_token or send_swap:
                msg = {
                    'event': 'update',
                    'touched_refs': list(matched_refs or (ref_hexes if send_token or send_swap else set())),
                    'touched_scripthashes': list(matched_shs),
                }
                try:
                    await client.ws.send_json(msg)
                except Exception:
                    dead.append(cid)

        for cid in dead:
            _ws_clients.pop(cid, None)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket endpoint for real-time token/swap event subscriptions.

    Clients send JSON messages to subscribe:
      {"action": "subscribe", "refs": ["aabb...00"]}
      {"action": "subscribe", "scripthashes": ["ccdd...00"]}
      {"action": "subscribe", "all_tokens": true}
      {"action": "subscribe", "all_swaps": true}
      {"action": "unsubscribe", "refs": ["aabb...00"]}
      {"action": "ping"}
    """
    global _ws_broadcast_task
    await ws.accept()

    cid = id(ws)
    client = _WsClient(ws=ws)
    _ws_clients[cid] = client

    # Start broadcast loop if not running
    if _ws_broadcast_task is None or _ws_broadcast_task.done():
        _ws_broadcast_task = asyncio.get_event_loop().create_task(_ws_broadcast_loop())

    try:
        await ws.send_json({'event': 'connected', 'message': 'RXinDexer WebSocket ready'})
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                await ws.send_json({'event': 'error', 'message': 'Invalid JSON'})
                continue

            action = msg.get('action', '')

            if action == 'ping':
                await ws.send_json({'event': 'pong'})
            elif action == 'subscribe':
                for ref in msg.get('refs', []):
                    client.subscribed_refs.add(ref)
                for sh in msg.get('scripthashes', []):
                    client.subscribed_scripthashes.add(sh)
                if msg.get('all_tokens'):
                    client.subscribe_all_tokens = True
                if msg.get('all_swaps'):
                    client.subscribe_all_swaps = True
                await ws.send_json({
                    'event': 'subscribed',
                    'refs': len(client.subscribed_refs),
                    'scripthashes': len(client.subscribed_scripthashes),
                    'all_tokens': client.subscribe_all_tokens,
                    'all_swaps': client.subscribe_all_swaps,
                })
            elif action == 'unsubscribe':
                for ref in msg.get('refs', []):
                    client.subscribed_refs.discard(ref)
                for sh in msg.get('scripthashes', []):
                    client.subscribed_scripthashes.discard(sh)
                if msg.get('all_tokens') is False:
                    client.subscribe_all_tokens = False
                if msg.get('all_swaps') is False:
                    client.subscribe_all_swaps = False
                await ws.send_json({'event': 'unsubscribed'})
            else:
                await ws.send_json({'event': 'error', 'message': f'Unknown action: {action}'})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _ws_clients.pop(cid, None)


# =============================================================================
# STARTUP
# =============================================================================

def create_app():
    """Factory function to create the FastAPI app."""
    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
