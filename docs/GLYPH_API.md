# RXinDexer Glyph v2 API Reference

## Overview

RXinDexer extends the ElectrumX protocol with comprehensive Glyph v2 token indexing and querying capabilities. All methods are available through the standard JSON-RPC interface.

## Core Glyph Methods

### glyph.get_token

Get token information by Glyph ID.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `glyph_id` | string | Token ID in format `txid:vout` |

**Returns:**
```json
{
  "glyph_id": "abc123...def:0",
  "txid": "abc123...def",
  "vout": 0,
  "value": 100000000,
  "version": 2,
  "is_reveal": true
}
```

---

### glyph.get_by_ref

Get all UTXOs containing a specific reference.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `ref` | string | 36-byte reference in hex (72 characters) |

**Returns:** Array of UTXOs

---

### glyph.validate_protocols

Validate a protocol combination per Glyph v2 rules.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `protocols` | array | List of protocol IDs |

**Returns:**
```json
{
  "valid": true,
  "protocol_names": ["Fungible Token", "Decentralized Minting"],
  "token_type": "dMint FT"
}
```

---

### glyph.get_protocol_info

Get information about all Glyph v2 protocols.

**Parameters:** None

**Returns:** Protocol definitions dict

---

### glyph.parse_envelope

Parse a Glyph envelope from script hex.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `script_hex` | string | Script in hexadecimal |

**Returns:** Parsed envelope dict

---

## Index Methods

### glyph.get_token_info

Get full token information from the Glyph index.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `ref` | string | Token ref in format `txid_vout` |

**Returns:**
```json
{
  "ref": "abc123...def_0",
  "name": "My Token",
  "ticker": "MTK",
  "protocols": [1, 4],
  "token_type": "dMint FT",
  "decimals": 8,
  "total_supply": 21000000,
  "minted": 1500000,
  "metadata_hash": "..."
}
```

---

### glyph.get_balance

Get token balance for a scripthash.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `scripthash` | string | Address scripthash (64 hex chars) |
| `ref` | string | Token ref in format `txid_vout` |

**Returns:**
```json
{
  "confirmed": 1000000000,
  "unconfirmed": 0
}
```

---

### glyph.list_tokens

List all tokens held by a scripthash.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `scripthash` | string | Address scripthash (64 hex chars) |
| `limit` | int | Maximum results (default 100) |

**Returns:**
```json
[
  {
    "ref": "abc123...def_0",
    "name": "Token A",
    "balance": 1000000000
  },
  {
    "ref": "xyz789...ghi_1",
    "name": "NFT #42",
    "balance": 1
  }
]
```

---

### glyph.get_history

Get transaction history for a token.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `ref` | string | Token ref in format `txid_vout` |
| `limit` | int | Maximum results (default 100) |
| `offset` | int | Pagination offset (default 0) |

**Returns:**
```json
[
  {
    "tx_hash": "...",
    "height": 123456,
    "type": "transfer",
    "amount": 1000000
  }
]
```

---

### glyph.search_tokens

Search tokens by name or ticker.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `query` | string | Search query string |
| `protocols` | array | Optional list of protocol IDs to filter |
| `limit` | int | Maximum results (default 50) |

**Returns:** Array of matching tokens

---

### glyph.get_tokens_by_type

Get tokens by type.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `token_type` | int | GlyphTokenType ID (1=FT, 2=NFT, etc.) |
| `limit` | int | Maximum results (default 100) |
| `offset` | int | Pagination offset (default 0) |

**Returns:** Array of tokens

---

### glyph.get_metadata

Get full CBOR metadata for a token.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `ref` | string | Token ref in format `txid_vout` |

**Returns:** Parsed metadata dict

---

## dMint Contract Methods

### dmint.get_contracts

Get list of mineable dMint contracts.

**Parameters (v2):**
| Name | Type | Description |
|------|------|-------------|
| `request` | object | Request envelope with `version=2`, `view="token_summary"`, optional `filters`, `sort`, and `pagination` |

**Request (v2) example:**
```json
{
  "version": 2,
  "view": "token_summary",
  "filters": {
    "status": "mineable",
    "algorithm_ids": [1, 2]
  },
  "sort": {
    "field": "deploy_height",
    "dir": "desc"
  },
  "pagination": {
    "limit": 1000
  }
}
```

**Returns (v2 token_summary):**
```json
{
  "version": 2,
  "view": "token_summary",
  "schema": "dmint.get_contracts.v2",
  "generated_at": "2026-03-23T00:00:00+00:00",
  "indexed_height": 123456,
  "cursor_next": null,
  "count": 1,
  "total_estimate": 1,
  "items": [
    {
      "token_ref": "abc123...def_0",
      "ticker": "MINE",
      "name": "Mining Token",
      "algorithm": { "id": 1, "name": "blake3" },
      "daa_mode": { "id": 0, "name": "fixed" },
      "contracts": { "total": 100, "mineable_remaining": null, "fully_mined": null },
      "supply": { "total": "21000000", "minted": "1500000", "remaining": "19500000", "unit": "photons" },
      "reward_per_mint": "50",
      "target": "12345678",
      "percent_mined": 7.14285714,
      "deploy_height": 123000,
      "active": true,
      "is_fully_mined": false,
      "icon": { "type": null, "url": null, "data_hex": null }
    }
  ]
}
```

**Legacy compatibility:**
- Passing string param `"simple"` returns `[[ref, outputs], ...]`.
- Passing string param `"extended"` returns legacy version 1 extended response.

---

### dmint.get_contract

Get details for a specific dMint contract.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `ref` | string | Contract reference (72 hex chars) |

**Returns:** Contract details dict

---

### dmint.get_by_algorithm

Get contracts filtered by mining algorithm.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `algorithm` | int | Algorithm ID (0=SHA256D, 1=BLAKE3, etc.) |

**Returns:** Array of contracts

---

### dmint.get_most_profitable

Get contracts sorted by estimated profitability.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `limit` | int | Maximum contracts (default 10) |

**Returns:** Sorted array of contracts

---

## Protocol IDs

| ID | Name | Description |
|----|------|-------------|
| 1 | GLYPH_FT | Fungible Token |
| 2 | GLYPH_NFT | Non-Fungible Token |
| 3 | GLYPH_DAT | Data Storage |
| 4 | GLYPH_DMINT | Decentralized Minting |
| 5 | GLYPH_MUT | Mutable State |
| 6 | GLYPH_BURN | Explicit Burn |
| 7 | GLYPH_CONTAINER | Container/Collection |
| 8 | GLYPH_ENCRYPTED | Encrypted Content |
| 9 | GLYPH_TIMELOCK | Timelocked Reveal |
| 10 | GLYPH_AUTHORITY | Issuer Authority |
| 11 | GLYPH_WAVE | WAVE Naming |

## dMint Algorithm IDs

| ID | Name | Description |
|----|------|-------------|
| 0 | SHA256D | Double SHA-256 |
| 1 | BLAKE3 | BLAKE3 hash |
| 2 | K12 | KangarooTwelve |
| 3 | ARGON2ID_LIGHT | Argon2id (light) |
| 4 | RANDOMX_LIGHT | RandomX (light) |

## Method Costs

| Method | Cost |
|--------|------|
| glyph.get_token | 1.0 |
| glyph.get_by_ref | 2.0 |
| glyph.validate_protocols | 0.1 |
| glyph.get_protocol_info | 0.1 |
| glyph.parse_envelope | 0.5 |
| glyph.get_token_info | 1.0 |
| glyph.get_balance | 1.0 |
| glyph.list_tokens | 2.0 |
| glyph.get_history | 2.0 |
| glyph.search_tokens | 3.0 |
| glyph.get_tokens_by_type | 2.0 |
| glyph.get_metadata | 1.5 |
| dmint.get_contracts | 1.0 |
| dmint.get_contract | 1.0 |
| dmint.get_by_algorithm | 1.5 |
| dmint.get_most_profitable | 2.0 |
| glyph.subscribe.balance | 0.5 |
| glyph.unsubscribe.balance | 0.1 |
| glyph.subscribe.token | 0.5 |
| glyph.unsubscribe.token | 0.1 |
| glyph.subscribe.transfers | 0.5 |
| swap.subscribe.orderbook | 0.5 |
| swap.unsubscribe.orderbook | 0.1 |
| swap.subscribe.fills | 0.5 |
| swap.subscribe.user_orders | 0.5 |
| wave.subscribe.name | 0.5 |
| dmint.subscribe.token | 0.5 |

## Error Handling

Methods return `null` for not found items or an error dict:

```json
{
  "error": "Glyph indexing not enabled"
}
```

## WebSocket Subscriptions

RXinDexer pushes real-time notifications over the same `ws://` / `wss://` Electrum
endpoints used for ordinary JSON-RPC calls. Subscriptions are scoped to the
session that created them and are released automatically when the connection
closes.

### Enabling

Subscriptions are gated by `GLYPH_SUBSCRIPTIONS` (default `1`). Set
`GLYPH_SUBSCRIPTIONS=0` to disable; with subscriptions off, every
`*.subscribe.*` RPC returns `{"error": "Subscriptions not enabled"}`.

### Rate limits

Per-client limits (configurable via env vars, defaults shown):

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_SUBS_PER_CLIENT` | `10000` | Maximum live subscriptions per session |
| `SUB_RATE_LIMIT` | `100` | New-subscription rate per client (subs/sec) |
| `SUB_BURST_LIMIT` | `500` | Burst allowance (token-bucket capacity) |

Exceeding the rate triggers temporary blocking; exceeding the count returns an
error from the relevant subscribe RPC.

### Wire format

All subscribe RPCs take **positional** string parameters (hex scripthash, ref,
WAVE name) and return a boolean (`true` on accept) or an `{"error": "..."}`
object.

Notifications are JSON-RPC notifications (no `id`) whose `params` is a
**single-element positional array containing one object**:

```json
{"jsonrpc": "2.0", "method": "glyph.balance",
 "params": [{"scripthash": "...", "ref": "...", "balance": 1000, "delta": 50}]}
```

This matches the Electrum convention used by `blockchain.scripthash.subscribe`,
so any client that already spreads subscription params (`(...params) => ...`)
receives the object as a single argument without per-method branching.

### Ref string format

Token refs accept either form:

- **Underscore form**: `"<txid_hex_be>_<vout>"` (the form RXinDexer emits in
  notifications and most responses), e.g. `"abc123…def_0"`.
- **72-hex form**: 36 raw bytes hex-encoded as `txid_le || vout_le32`, e.g.
  `"<64 hex>00000000"`.

### Subscribe RPCs

| Method | Params (positional) | Returns | Notification |
|--------|---------------------|---------|--------------|
| `glyph.subscribe.balance` | `scripthash_hex`, `ref` | `bool` | `glyph.balance` |
| `glyph.unsubscribe.balance` | `scripthash_hex`, `ref` | `bool` | — |
| `glyph.subscribe.token` | `ref` | `bool` | `glyph.token` |
| `glyph.unsubscribe.token` | `ref` | `bool` | — |
| `glyph.subscribe.transfers` | `ref` | `bool` | `glyph.transfer` |
| `swap.subscribe.orderbook` | `base_ref`, `quote_ref` | `bool` | `swap.orderbook` |
| `swap.unsubscribe.orderbook` | `base_ref`, `quote_ref` | `bool` | — |
| `swap.subscribe.fills` | `base_ref`, `quote_ref` | `bool` | `swap.fill` |
| `swap.subscribe.user_orders` | `scripthash_hex` | `bool` | `swap.user_order` |
| `wave.subscribe.name` | `name` (string, case-insensitive) | `bool` | `wave.name` |
| `dmint.subscribe.token` | `ref` | `bool` | `dmint.update` |

Subscriptions are de-duplicated per `(session, key)`: re-subscribing with the
same arguments is a no-op that still returns `true`. There is no "subscribe to
everything" RPC — callers must subscribe to each token/pair/name explicitly.

### Notification methods

All `params` are wrapped in a single-element positional array, as described
above. The object's field schemas:

#### `glyph.balance`

Pushed whenever a subscribed `(scripthash, token_ref)` pair's confirmed balance
changes.

| Field | Type | Description |
|-------|------|-------------|
| `scripthash` | hex string | 32-byte scripthash (64 chars) |
| `ref` | string | Token ref in `txid_vout` form |
| `balance` | int | New confirmed balance |
| `delta` | int | Signed change since previous notification |

#### `glyph.token`

Pushed when token state changes (supply, metadata, freeze flags, etc.).

| Field | Type | Description |
|-------|------|-------------|
| `ref` | string | Token ref |
| `data` | object | Token state snapshot (shape mirrors `glyph.get_token_info`) |

#### `glyph.transfer`

Pushed for every confirmed transfer of the subscribed token.

| Field | Type | Description |
|-------|------|-------------|
| `ref` | string | Token ref |
| `txid` | hex string | Transaction id (big-endian display form) |
| `from` | hex string \| null | Sender scripthash, `null` for mint |
| `to` | hex string \| null | Recipient scripthash, `null` for burn |
| `amount` | int | Transfer amount |
| `height` | int | Block height of inclusion |

#### `swap.orderbook`

Pushed when an order is added, modified, or removed from the orderbook for the
subscribed trading pair.

| Field | Type | Description |
|-------|------|-------------|
| `base_ref` | string | Base token ref |
| `quote_ref` | string | Quote token ref |
| `change` | string | `"add"`, `"update"`, or `"remove"` |
| `order` | object | Order data (shape mirrors `swap.get_orders` entries) |

#### `swap.fill`

Pushed when a trade fills against an order in the subscribed pair.

| Field | Type | Description |
|-------|------|-------------|
| `base_ref` | string | Base token ref |
| `quote_ref` | string | Quote token ref |
| `fill` | object | Fill record (shape mirrors `swap.get_history` entries) |

#### `swap.user_order`

Pushed when an order owned by the subscribed scripthash changes state.

| Field | Type | Description |
|-------|------|-------------|
| `scripthash` | hex string | Owner scripthash |
| `change` | string | `"new"`, `"filled"`, `"partial"`, or `"cancelled"` |
| `order` | object | Order data |

#### `wave.name`

Pushed when the subscribed WAVE name's ownership changes (mint, transfer, or
release).

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | WAVE name (original case as registered) |
| `owner` | hex string \| null | New owner scripthash, `null` if released |
| `txid` | hex string | Transaction id of the ownership change |
| `height` | int | Block height of inclusion |

#### `dmint.update`

Pushed when the subscribed dMint contract's mining stats change.

| Field | Type | Description |
|-------|------|-------------|
| `ref` | string | dMint contract ref |
| `data` | object | Mining stats (shape mirrors `dmint.get_contract`) |

### Lifecycle

1. **Connect** over `ws://` or `wss://` to a Radiant-aware ElectrumX endpoint.
2. **Subscribe** by calling the relevant `*.subscribe.*` RPC. The RPC returns
   `true` on accept (or an `{"error": "..."}` object).
3. **Receive notifications** as JSON-RPC notifications (no `id` field). Match on
   the `method` field; read `params[0]` as the payload object.
4. **Unsubscribe** explicitly with the matching `*.unsubscribe.*` RPC where one
   exists, or simply close the connection — all subscriptions for a session are
   released automatically when the WebSocket disconnects.

### Example: track a token balance

```javascript
// Subscribe
ws.send(JSON.stringify({
  jsonrpc: "2.0", id: 1,
  method: "glyph.subscribe.balance",
  params: ["aa…32-byte-scripthash-hex…bb", "abc…txid_hex…def_0"],
}));

// Incoming push (no `id`):
// {"jsonrpc": "2.0", "method": "glyph.balance",
//  "params": [{"scripthash": "aa…bb", "ref": "abc…def_0",
//              "balance": 1500, "delta": 500}]}

ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.method === "glyph.balance") {
    const { scripthash, ref, balance, delta } = msg.params[0];
    // update UI
  }
};

// Unsubscribe later
ws.send(JSON.stringify({
  jsonrpc: "2.0", id: 2,
  method: "glyph.unsubscribe.balance",
  params: ["aa…bb", "abc…def_0"],
}));
```

### Operational notes

- **No replay on subscribe**: subscribing only delivers *future* events. If you
  need current state, call the corresponding `glyph.get_*` / `swap.get_*` REST
  or RPC method first, then subscribe.
- **Mempool vs. confirmed**: notifications fire from the block processor, so
  they reflect confirmed (in-block) changes. Mempool changes are not pushed.
- **Reorg handling**: a reorg replays the affected blocks, which may re-emit
  `glyph.transfer` / `swap.fill` for the same `txid`. Clients should treat
  notifications as idempotent keyed on `(method, txid, height)` or equivalent.
- **Backpressure**: if a session's send buffer fills, individual notifications
  may be dropped silently (logged at debug level on the server). Clients that
  need guaranteed delivery should periodically reconcile via the `get_*` RPCs.

---

## REST API (HTTP)

RXinDexer also provides a FastAPI-based REST API for HTTP access. Enable it with `REST_API_ENABLED=1`.

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `REST_API_ENABLED` | `0` | Enable REST API |
| `REST_API_HOST` | `0.0.0.0` | Listen address |
| `REST_API_PORT` | `8000` | Listen port |
| `REST_API_KEY` | *(none)* | API key (required in prod) |
| `REST_RATE_LIMIT_PER_MIN` | `600` | Rate limit per client |
| `ALLOWED_ORIGINS` | *(none)* | CORS origins (required in prod) |

### Health & Status

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check (status, uptime, sync height) |
| GET | `/health/live` | Liveness probe |
| GET | `/health/ready` | Readiness probe |
| GET | `/health/db` | Database health |
| GET | `/status` | Full indexer status (all subsystems) |

### Glyph v2 Token Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/glyphs` | List all tokens (paginated, filterable by `token_type`) |
| GET | `/glyphs/search?q=` | Search tokens by name/ticker |
| GET | `/glyphs/stats` | Token counts by type and version |
| GET | `/glyphs/by-type/{type_id}` | Filter tokens by type ID |
| GET | `/glyphs/{ref}` | Get single token by 72-hex ref |

### Token Analytics Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/tokens/{ref}/holders` | Token holder list |
| GET | `/tokens/{ref}/supply` | Supply breakdown (total, circulating, burned) |
| GET | `/tokens/{ref}/burns` | Burn event history |
| GET | `/tokens/{ref}/trades` | Transfer event history |
| GET | `/tokens/{ref}/top-holders` | Rich list (sorted by balance) |
| GET | `/tokens/{ref}/history` | Full event history (deploy, mint, transfer, burn) |
| GET | `/tokens/{ref}/metadata` | Parsed CBOR metadata |

### dMint v2 Contract Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/dmint/contracts` | Contracts endpoint (v2 via `version=2`; legacy via `format=simple\|extended`) |
| GET | `/dmint/contracts/{ref}` | Single contract detail |
| GET | `/dmint/algorithms` | Supported algorithm and DAA mode definitions |
| GET | `/dmint/by-algorithm/{id}` | Contracts filtered by algorithm (0=SHA256D, 1=BLAKE3, 2=K12) |
| GET | `/dmint/profitable` | Contracts sorted by profitability |

### WAVE Naming System Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/wave/resolve/{name}` | Resolve WAVE name to zone records |
| GET | `/wave/available/{name}` | Check name availability |
| GET | `/wave/{name}/subdomains` | List subdomains |
| GET | `/wave/reverse/{scripthash}` | Reverse lookup by owner |
| GET | `/wave/stats` | WAVE indexing statistics |

### Swap / DEX Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/swaps/orders` | Active orders (filter by `base_ref`, `quote_ref`) |
| GET | `/swaps/orders/{order_id}` | Single order detail |
| GET | `/swaps/history` | Trade/fill history |
| GET | `/swaps/stats` | Swap indexing statistics |

### Blocks & Transactions

| Method | Path | Description |
|--------|------|-------------|
| GET | `/blocks/recent` | Recent blocks |
| GET | `/block/{height}` | Block by height |
| GET | `/transaction/{txid}` | Transaction by ID |

### Authentication

All endpoints (except `/health*`) require an `X-API-Key` header when `REST_API_KEY` is set.

### Interactive Docs

- Swagger UI: `http://host:port/docs`
- ReDoc: `http://host:port/redoc`

---

*Reference: [Glyph v2 Token Standard](https://github.com/Radiant-Core/Glyph-Token-Standards)*
