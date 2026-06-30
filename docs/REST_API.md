# RXinDexer REST API v2.0.0

This document provides a comprehensive guide to the REST API for `RXinDexer`, a high-performance indexer for the Radiant blockchain. The API exposes rich data including Glyph v2 tokens, dMint contracts, WAVE names, and core blockchain information.

**Base URL**: The API is typically served on port `8000`. Your base URL will be `http://<your-indexer-host>:8000`.

**Authentication**: In a production environment, requests require an API key passed in the `X-API-Key` header.

## Health & Status

Endpoints to monitor the status and health of the indexer.

### Get Health

-   **Endpoint**: `GET /health`
-   **Description**: Checks the overall health of the API and its database connection.

**Example Request:**
```bash
curl http://localhost:8000/health
```

**Example Response:**
```json
{
  "status": "healthy",
  "uptime_seconds": 3600.5,
  "database": "connected",
  "sync_height": 415000
}
```

---

## Blocks & Transactions

Endpoints for querying core blockchain data.

### Get Recent Blocks

-   **Endpoint**: `GET /blocks/recent`
-   **Description**: Retrieves a list of the most recent blocks indexed.
-   **Query Parameters**:
    -   `limit` (integer, default: 10, max: 100): The number of blocks to return.

**Example Request:**
```bash
curl http://localhost:8000/blocks/recent?limit=2
```

### Get Transaction

-   **Endpoint**: `GET /transaction/{txid}`
-   **Description**: Fetches a raw transaction by its transaction ID.
-   **Path Parameters**:
    -   `txid` (string): The 64-character transaction hash.

**Example Request:**
```bash
curl http://localhost:8000/transaction/a1b2...c3d4
```

---

## Glyphs / Tokens

Endpoints for querying Glyph v2 token data.

### List All Glyphs

-   **Endpoint**: `GET /glyphs`
-   **Description**: Paginates through all indexed Glyph tokens.
-   **Query Parameters**:
    -   `limit` (integer, default: 100, max: 500)
    -   `offset` (integer, default: 0)
    -   `token_type` (integer, optional): Filter by a specific protocol ID (e.g., `1` for FT, `2` for NFT).

**Example Request:**
```bash
curl http://localhost:8000/glyphs?token_type=2&limit=5
```

### Get Glyph Details

-   **Endpoint**: `GET /glyphs/{ref}`
-   **Description**: Retrieves detailed information for a single Glyph token.
-   **Path Parameters**:
    -   `ref` (string): The 72-character token reference (36-byte hex).

**Example Request:**
```bash
curl http://localhost:8000/glyphs/a1b2...c3d4
```

### Search Glyphs

-   **Endpoint**: `GET /glyphs/search`
-   **Description**: Searches for tokens by their name or ticker.
-   **Query Parameters**:
    -   `q` (string, required): The search query.
    -   `limit` (integer, default: 50, max: 200)

**Example Request:**
```bash
curl http://localhost:8000/glyphs/search?q=MyToken
```

### Get Token Holders

-   **Endpoint**: `GET /tokens/{ref}/holders`
-   **Description**: Retrieves a list of addresses that hold a specific token and their balances.

**Example Request:**
```bash
curl http://localhost:8000/tokens/a1b2...c3d4/holders
```

---

## dMint (Decentralized Minting)

Endpoints for querying dMint PoW contracts.

### List dMint Contracts

-   **Endpoint**: `GET /dmint/contracts`
-   **Description**: Retrieves all active, mineable dMint contracts.

**Example Request:**
```bash
curl http://localhost:8000/dmint/contracts
```

### Get dMint Algorithms

-   **Endpoint**: `GET /dmint/algorithms`
-   **Description**: Lists the supported PoW mining algorithms and DAA modes.

**Example Request:**
```bash
curl http://localhost:8000/dmint/algorithms
```

---

## V2 Hard Fork Status

### Get Activation Status

-   **Endpoint**: `GET /v2/activation-status`
-   **Description**: Provides the status of the Radiant V2 hard fork, including activation height and the current block height.

**Example Request:**
```bash
curl http://localhost:8000/v2/activation-status
```

---

## WAVE Naming System

Endpoints for the WAVE decentralized naming protocol — human-readable names
(e.g. `alice.rxd`) backed by mutable Glyph NFTs. Names are **first-registration-wins**:
the earliest registration of a name is *canonical* and is the only one returned by
resolution; later duplicate registrations are tracked but never resolved.

> **Integrating a dapp?** A zero-dependency JavaScript resolver and a full guide live
> at <https://radiantcore.org/docs/wave-names.html>
> (source: <https://radiantcore.org/docs/wave-resolver.js>).

All `/wave/*` routes are **public** (no API key) and **GET-only**.

### Resolve a WAVE name

-   **Endpoint**: `GET /wave/resolve/{name}`
-   **Description**: Resolves a name to its canonical registration. Returns the current
    `target` payment address, the `ref` (`"txid_vout"`), the `owner` scripthash, and a
    `zone` record (address, display, avatar, url, TXT, …).
-   **Query**: `include_duplicates=true` to also list non-canonical registrations.

```bash
curl https://radiantcore.org/api/wave/resolve/alice
```
- Registered → `{ "name": "alice", "ref": "...", "target": "1Rxd...", "zone": {…}, "owner": "…", "available": false, "canonical": true }`
- Unregistered → `{ "name": "nobody", "available": true, "resolved": false }`

### Check availability

-   **Endpoint**: `GET /wave/available/{name}`
-   **Description**: Whether a name is free to register.

```bash
curl https://radiantcore.org/api/wave/available/myhandle
```
→ `{ "available": true, "name": "myhandle" }` or `{ "available": false, "ref": "…", "name": "alice" }`

### List names (paginated)

-   **Endpoint**: `GET /wave/names`
-   **Description**: Lists all canonical names, newest-cursor paginated.
-   **Query**: `limit` (default 500, max 2000); `cursor` (opaque token — pass the
    previous response's `next_cursor`); `include_duplicates=true` optional.

```bash
curl "https://radiantcore.org/api/wave/names?limit=1000"
# → { "names": [ { "name", "full_name", "target", "ref", "height", … } ], "total": N, "next_cursor": "…" }
```
Keep calling with the returned `cursor` until `next_cursor` is `null` (last page).

### Other WAVE endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /wave/registrations/{name}` | Canonical registration + all duplicates. |
| `GET /wave/reverse/{scripthash}` | All names owned by a scripthash. |
| `GET /wave/stats` | Index health and totals. |

---

## Public Access: CORS & Rate Limits

These apply to **every** public REST route, including `/wave/*`.

### Rate limiting

A per-IP token bucket guards the REST API:

| Setting | Default | Env var |
| --- | --- | --- |
| Sustained rate | **600 req/min per IP** (10/s) | `REST_RATE_LIMIT_PER_MIN` |
| Burst | **600** | `REST_RATE_LIMIT_BURST` |

Exceeding it returns **HTTP 429** `{"detail": "Rate limit exceeded"}`. The default is
comfortable for normal dapp use — a name lookup per payment is far below 10/s, so only
abusive clients hit it. Behind a reverse proxy, set `TRUST_PROXY=1` and `TRUSTED_PROXIES`
so the limiter keys on the real client IP; otherwise every user collapses into the
proxy's single IP bucket and shares one limit.

### CORS (browser dapps)

Public read endpoints are meant to be callable from any origin. CORS is configured by
FastAPI's `CORSMiddleware` via the **`ALLOWED_ORIGINS`** env var (comma-separated; use
`*` for a fully public read API).

**Single source of truth — do not also inject `Access-Control-Allow-Origin` at the
reverse proxy (Caddy/nginx).** Two `Access-Control-Allow-Origin` headers on one response
is invalid, and every browser rejects it — even though `curl`/Node (which don't enforce
CORS) succeed, masking the bug. If the live deployment currently sets the header in both
places, remove it from the proxy and let FastAPI emit the only copy.

Verify exactly one header comes back:
```bash
curl -sD - -o /dev/null -H 'Origin: https://example.com' \
  https://radiantcore.org/api/wave/resolve/alice | grep -ci '^access-control-allow-origin'
# must print 1
```
Preflight `OPTIONS` requests are answered by `CORSMiddleware` (the API-key/rate-limit
check skips `OPTIONS`), so a preflight returns `200`/`204`, never `401`.

---
## WebSocket Subscriptions

RXinDexer provides a WebSocket endpoint for real-time updates on mempool activity.

- **Endpoint**: `ws://<your-indexer-host>:8000/ws`

**Subscription Actions:**

To receive updates, send a JSON message after connecting:

- **Subscribe by Token Reference**:
  ```json
  {"action": "subscribe", "refs": ["aabbcc..."]}
  ```

- **Subscribe by Address (Scripthash)**:
  ```json
  {"action": "subscribe", "scripthashes": ["ddeeff..."]}
  ```

- **Subscribe to All Tokens**:
  ```json
  {"action": "subscribe", "all_tokens": true}
  ```

**Update Message Format:**

When a relevant transaction appears in the mempool, you will receive a message like this:
```json
{
  "event": "update",
  "touched_refs": ["aabbcc..."],
  "touched_scripthashes": ["ddeeff..."]
}
```
