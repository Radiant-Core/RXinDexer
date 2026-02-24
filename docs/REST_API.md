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

Endpoints for the WAVE decentralized naming protocol.

### Resolve WAVE Name

-   **Endpoint**: `GET /wave/resolve/{name}`
-   **Description**: Resolves a WAVE name to its zone records (address, avatar, etc.).

**Example Request:**
```bash
curl http://localhost:8000/wave/resolve/alice
```

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
