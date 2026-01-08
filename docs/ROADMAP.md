# RXinDexer Roadmap

**Generated:** January 7, 2026  
**Current Status:** Production Ready (91% audit complete)

---

## Overview

This roadmap consolidates remaining items from the security audit and architectural recommendations for future growth. Items are organized by priority and effort level.

---

## ✅ Completed (22/23 Audit Items)

### Critical Security
- [x] JWT Authentication system
- [x] Rate limiting (token bucket, tiered)
- [x] CORS configuration
- [x] Hardcoded credentials removed

### High Priority Performance
- [x] UTXO value precision (Float → BigInteger)
- [x] Legacy tables consolidated (unified `glyphs` table)
- [x] Foreign key constraints (where applicable)
- [x] Pagination limits on all endpoints
- [x] Connection pooling (API + Indexer pools)
- [x] Composite and partial indexes

### Medium Priority
- [x] Redis caching support (with in-memory fallback)
- [x] JSON field size constraints
- [x] Environment validation (Pydantic)
- [x] CSP security headers
- [x] GZip compression

### Low Priority
- [x] Health check endpoints
- [x] API documentation (OpenAPI/Swagger)
- [x] Automated backup service
- [x] Resource limits on containers
- [x] Data retention policies (time-based partitioning with pg_partman)

---

## 🔲 Remaining Audit Items (1)

### 1. Base64 File Data in Database
**Priority:** Low  
**Effort:** High (1-2 weeks)  
**Impact:** Reduces database size by ~33% for embedded files

**Current State:**
- Token images stored as base64 in `glyphs.embed_data`
- Works fine for <10GB total file storage

**Future Solution:**
1. Add MinIO/S3 service to docker-compose
2. Create migration to move files to object storage
3. Update `embed_data` to store file references instead
4. Add file retrieval proxy endpoint

**When to Implement:** When file storage exceeds 10GB or backup times become problematic.

---

## 🚀 Future Enhancements

### Phase 1: Scalability (1-2 weeks)

#### 1.1 Async Database Operations ✅ COMPLETED
**Priority:** Medium  
**Effort:** High  
**Impact:** 2-3x API throughput improvement

**Implementation:**
- Added `asyncpg>=0.29.0` and `greenlet>=3.0.0` dependencies
- Created async engine and session in `database/session.py`:
  - `async_engine` with asyncpg driver
  - `AsyncSessionLocal` async session maker
  - `get_async_db()` FastAPI dependency
  - Async read replica support
- Updated key API endpoints to async:
  - `/blocks/recent` - async block listing
  - `/transactions/recent` - async transaction listing
  - `/health/detailed`, `/health/services`, `/db-health` - async health checks
- Sync endpoints remain available for backward compatibility

```python
# Usage in endpoints
from api.dependencies import get_async_db
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

@router.get("/items")
async def get_items(db: AsyncSession = Depends(get_async_db)):
    result = await db.execute(select(Item))
    return result.scalars().all()
```

**Benefits:**
- Non-blocking I/O for database queries
- Better handling of concurrent requests
- Required for WebSocket support

---

#### 1.2 Cursor-Based Pagination ✅ COMPLETED
**Priority:** Medium  
**Effort:** Low  
**Impact:** Faster pagination for large datasets

**Implementation:**
- Added `cursor` parameter to `/tokens`, `/blocks/recent`, `/transactions/recent` endpoints
- Created `api/pagination.py` with `encode_cursor()`, `decode_cursor()`, and helper functions
- Response includes `next_cursor`, `prev_cursor`, `has_next`, `has_prev` for navigation
- Backward compatible - offset/page parameters still work

```python
# Usage example
GET /tokens?cursor=eyJpZCI6MTAwLCJkIjoiYWZ0ZXIifQ&limit=50

# Response
{
    "items": [...],
    "limit": 50,
    "has_next": true,
    "has_prev": true,
    "next_cursor": "eyJpZCI6NTAsImQiOiJhZnRlciJ9",
    "prev_cursor": "eyJpZCI6MTAxLCJkIjoiYmVmb3JlIn0"
}
```

---

#### 1.3 Read Replica Support ✅ COMPLETED
**Priority:** Low  
**Effort:** Low  
**Impact:** Horizontal read scaling

**Implementation:**
- Added `READ_REPLICA_URL` environment variable support in `database/session.py`
- Created `read_engine` with separate connection pool for read replica
- Added `get_read_session()` context manager for read-only queries
- Added `is_read_replica_configured()` helper function
- Falls back gracefully to primary if replica not configured

```bash
# Usage - set READ_REPLICA_URL to enable
READ_REPLICA_URL=postgresql://user:pass@replica-host:5432/rxindexer

# In code
from database.session import get_read_session, is_read_replica_configured

with get_read_session() as db:
    # Uses replica if available, otherwise primary
    tokens = db.query(Glyph).all()
```

---

### Phase 2: Features (2-4 weeks)

#### 2.1 WebSocket Real-Time Updates ✅ COMPLETED
**Effort:** Medium  
**Use Cases:**
- Live block notifications
- Token price updates
- Transaction confirmations

**Implementation:**
- Created `api/websocket.py` - Connection manager for multi-channel WebSocket support
- Created `api/endpoints/websocket.py` - WebSocket endpoints
- Updated `api/background_tasks.py` - Block monitor broadcasts new blocks
- Added `websockets>=12.0` and `uvicorn[standard]` dependencies

**Endpoints:**
- `ws://host/ws/blocks` - Live block notifications
- `ws://host/ws/transactions` - Transaction notifications
- `ws://host/ws/tokens` - Token update notifications
- `ws://host/ws/mempool` - Mempool transaction notifications
- `ws://host/ws/subscribe?channels=blocks,transactions` - Multi-channel subscription
- `GET /ws/stats` - WebSocket connection statistics

**Message Format:**
```json
{
    "type": "new_block",
    "data": {
        "height": 123456,
        "hash": "abc123...",
        "tx_count": 5,
        "timestamp": 1234567890
    },
    "_channel": "blocks",
    "_timestamp": "2026-01-08T12:00:00"
}
```

**Client Commands:**
```json
{"type": "ping"}              // Keep-alive
{"type": "subscribe", "channel": "tokens"}
{"type": "unsubscribe", "channel": "blocks"}
{"type": "get_stats"}         // Get connection stats
```

---

#### 2.2 GraphQL API ✅ COMPLETED
**Effort:** Medium  
**Benefits:**
- Flexible querying for complex frontends
- Reduces over-fetching
- Single endpoint for related data

**Implementation:**
- Added `strawberry-graphql[fastapi]>=0.220.0` dependency
- Created `api/graphql/` module with types and schema
- Fully async resolvers using AsyncSession

**Endpoint:** `POST /graphql` (also available at `/graphql` with GraphiQL UI)

**Available Queries:**
```graphql
# Get single items
block(height: Int, hash: String): Block
transaction(txid: String!): Transaction
glyph(ref: String!): Glyph

# Paginated lists with filters
blocks(limit: Int, offset: Int, minHeight: Int, maxHeight: Int): BlockConnection
transactions(limit: Int, offset: Int, blockHeight: Int): TransactionConnection
glyphs(limit: Int, offset: Int, tokenType: String, name: String, ticker: String): GlyphConnection

# Convenience queries
nfts(limit: Int, offset: Int): GlyphConnection
fts(limit: Int, offset: Int): GlyphConnection
containers(limit: Int, offset: Int): GlyphConnection

# Search and stats
searchTokens(query: String!, limit: Int): [Glyph]
addressUtxos(address: String!, spent: Boolean, limit: Int): [UTXO]
tokenStats: TokenStats
blockchainStats: BlockchainStats
```

**Example Query:**
```graphql
query {
  glyphs(tokenType: "NFT", limit: 10) {
    items {
      ref
      name
      ticker
      author
    }
    pagination {
      total
      hasNext
    }
  }
  tokenStats {
    totalTokens
    totalNfts
    totalFts
  }
}
```

---

#### 2.3 Background Task Queue ✅ COMPLETED
**Effort:** Medium  
**Use Cases:**
- Token metadata resolution
- Holder count updates
- Balance refreshes
- Webhook notifications

**Implementation:**
- Added `arq>=0.25.0` dependency (lightweight async Redis queue)
- Created `api/tasks/` module with worker and job definitions
- Added ARQ worker service to docker-compose.yml
- Added Redis service for task queue backend

**Task Jobs Available:**
- `update_holder_counts` - Update token holder statistics
- `refresh_token_metadata` - Refresh token remote metadata
- `refresh_balances` - Update wallet balance cache
- `send_webhook` - Send webhook notifications
- `cleanup_old_data` - Clean up old database records

**API Endpoints:**
- `GET /tasks/status` - Get task queue status
- `POST /tasks/holder-counts` - Trigger holder count update (auth required)
- `POST /tasks/refresh-balances` - Trigger balance refresh (auth required)
- `POST /tasks/refresh-metadata?token_ref=...` - Refresh token metadata (auth required)
- `POST /tasks/cleanup?days=30` - Trigger cleanup (auth required)

**Running the Worker:**
```bash
# Docker (automatic)
docker-compose up -d worker

# Manual
arq api.tasks.worker.WorkerSettings
```

**Enqueueing Tasks Programmatically:**
```python
from api.tasks import enqueue_task

# Enqueue a task
job_id = await enqueue_task("update_holder_counts", token_ref="abc123...")

# Enqueue with delay
job_id = await enqueue_task("cleanup_old_data", days=30, _defer_by=3600)
```

---

### Phase 3: Enterprise (1-2 months)

#### 3.1 Multi-Tenancy
- API key management
- Per-tenant rate limits
- Usage tracking and billing

#### 3.2 Audit Logging
- Track all write operations
- Compliance reporting
- Change history

#### 3.3 High Availability
- Database failover
- Load balancer health checks
- Zero-downtime deployments

---

## 📊 Metrics & Monitoring

### Current
- Prometheus metrics endpoint
- Grafana dashboards (monitoring/grafana/)
- Health check endpoints

### Recommended Additions
- [x] Query latency histograms by endpoint (already in `rxindexer_api_latency_seconds`)
- [x] Cache hit/miss ratios (`rxindexer_cache_hits_total`, `rxindexer_cache_misses_total`, `rxindexer_cache_hit_ratio`)
- [x] Database connection pool utilization (`rxindexer_db_pool_size`, `rxindexer_db_pool_checkedout`, `rxindexer_db_pool_overflow`)
- [x] Indexer sync lag alerting (Prometheus alerts: `SyncLagHigh`, `SyncLagCritical`, `SyncLagIncreasing`, `SyncStalled`)

---

## 🔧 Quick Reference

### Radiant Node Source
The project builds the Radiant node from source using **radiant-core**:
- **Repository**: https://github.com/Radiant-Core/Radiant-Core
- **Dockerfile**: `docker/radiant-node.Dockerfile`
- **Build args**: `RADIANT_NODE_REPO` (default: Radiant-Core), `RADIANT_NODE_REF` (default: master)

### Environment Variables (New)
| Variable | Description | Default |
|----------|-------------|---------|
| `REDIS_URL` | Redis connection URL for caching | (in-memory fallback) |
| `READ_REPLICA_URL` | Read replica for scaling | (uses primary) |

### Async Database Support
The API now uses async database operations for improved throughput:
- **Async endpoints**: `/blocks/recent`, `/transactions/recent`, `/health/*`
- **Dependencies**: `asyncpg>=0.29.0`, `greenlet>=3.0.0`, `sqlalchemy[asyncio]>=2.0.0`
- **Backward compatible**: Sync `get_db()` still works for gradual migration

### Commands
```bash
# Run with Redis caching
REDIS_URL=redis://localhost:6379/0 docker-compose up -d

# Check cache backend
curl http://localhost:8000/health | jq .cache_backend

# Run backup
docker-compose --profile backup run backup
```

---

## Timeline Estimate

| Phase | Items | Effort | Priority |
|-------|-------|--------|----------|
| Now | Production ready | ✅ Done | - |
| Now | Data retention, cursor pagination | ✅ Done | - |
| Now | Async DB operations | ✅ Done | - |
| Now | WebSocket real-time updates | ✅ Done | - |
| Now | GraphQL API | ✅ Done | - |
| Now | Background task queue | ✅ Done | - |
| Future | Multi-tenancy, HA | 4-8 weeks | Low |

---

*Last updated: January 8, 2026*
