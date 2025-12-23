# RXinDexer API Reference

Base URL: `http://<host>:8000`

Interactive documentation:
- `GET /docs` (Swagger UI)
- `GET /redoc`

Compatibility:
- All endpoints are exposed **twice**: once at their normal path (e.g. `/tokens/...`) and again under the **`/api` prefix** (e.g. `/api/tokens/...`).

## Core

### Health & Status
- `GET /health` - Basic health check.
- `GET /health/detailed` - Detailed health check.
- `GET /health/db` - Database health check.
- `GET /db-health` - Database health check (legacy path).
- `GET /status` - Node status (RPC `getblockchaininfo`).

### Blocks
- `GET /blocks/recent` - Recent blocks (DB).
- `GET /block/{height}` - Block details (node RPC).

### Transactions
- `GET /transactions/recent` - Recent transactions.
- `GET /transaction/{txid}` - Transaction details (inputs/outputs).
- `GET /address/{address}/transactions` - Address transaction history.
- `GET /transactions/stats` - Transaction statistics.
- `GET /transactions/stats/timeseries` - Timeseries statistics.

### Wallets
- `GET /wallet/{address}` - Wallet balance and recent txids.
- `GET /wallets/top` - Rich list (top 100 wallets).
- `GET /address/{address}/utxos` - Address UTXOs.
- `GET /holders/rxd` - Unique RXD holder count (cached).

### Mempool
- `GET /mempool/info` - Mempool summary.
- `GET /mempool/txs` - Mempool transactions (paged).
- `GET /mempool/blocks` - Mempool block projection.

### Stats
- `GET /stats/overview` - Explorer overview statistics.

## Glyphs & Tokens

### Unified Glyphs (`/glyphs/*`)
- `GET /glyphs` - List glyphs (filter/search/sort).
- `GET /glyphs/fts/table` - FT table view.
- `GET /glyphs/recent` - Recent glyphs.
- `GET /glyphs/stats` - Glyph stats.
- `GET /glyphs/search` - Search glyphs.
- `GET /glyphs/containers` - Container glyphs.
- `GET /glyphs/users` - User glyphs.
- `GET /glyphs/{ref}` - Glyph details.
- `GET /glyphs/{ref}/actions` - Glyph actions.
- `GET /glyphs/by-author/{author_ref}` - Glyphs by author.
- `GET /glyphs/in-container/{container_ref}` - Glyphs in container.

### Legacy/Compatibility Token Endpoints
- `GET /tokens` - List tokens.
- `GET /tokens/search` - Search tokens.
- `GET /tokens/recent` - Recent tokens.
- `GET /tokens/stats` - Token stats.
- `GET /tokens/{token_id}` - Token details.
- `GET /tokens/protocol/{protocol_id}` - Tokens by protocol.
- `GET /tokens/{token_id}/history` - Token history.

### Enhanced Token Analytics
- `GET /tokens/{token_id}/holders` - Holder list.
- `GET /tokens/{token_id}/supply` - Supply breakdown.
- `GET /tokens/{token_id}/trades` - Trade history.
- `GET /tokens/{token_id}/burns` - Burn history.
- `GET /tokens/{token_id}/price` - Price history.
- `GET /tokens/{token_id}/ohlcv` - Daily OHLCV.
- `GET /tokens/{token_id}/mints` - DMINT mint events.
- `GET /tokens/{token_id}/contracts` - DMINT minting contracts.

### Token Files / Images
- `GET /tokens/{token_id}/files` - Files for token.
- `GET /tokens/{token_id}/files/{file_key}` - Specific file by key.
- `GET /tokens/{token_id}/image` - Primary token image (binary).

### Containers
- `GET /containers` - List containers.
- `GET /containers/{container_id}` - Container details.
- `GET /containers/{container_id}/tokens` - Tokens in a container.

### NFT Endpoints
- `GET /nft/collections/top` - Top NFT collections.
- `GET /nft/search` - Search NFTs.
- `GET /nfts/recent` - Recent NFTs.
- `GET /nfts/users` - NFTs of token_type_name = `user`.
- `GET /nfts/containers` - NFTs of token_type_name = `container`.
- `GET /nfts/{token_id}` - NFT details.

### Glyph Analytics
- `GET /glyph/users/top` - Top 100 Glyph users.
- `GET /glyph/containers/top` - Top 100 Glyph containers.
- `GET /holders/token/{token_id}` - Unique token holder count (cached).

## Market

- `GET /market/rxd` - RXD market data (CoinGecko).
- `GET /market/swaps` - Active swap offers.
- `GET /market/trades` - Recent completed trades.
- `GET /market/volume` - Trading volume stats.

## Users

- `GET /users/{address}` - User profile.

## Admin

- `POST /admin/cache/clear` - Clear server-side caches.
