# RXinDexer API Reference

Base URL: `http://<host>:8000`

Interactive documentation available at `/docs` (Swagger UI) and `/redoc`.

## Endpoints

### Blocks & Transactions
- `GET /blocks/recent` - Get latest blocks.
- `GET /blocks/{hash_or_height}` - Get block details.
- `GET /transactions/recent` - Get latest transactions.
- `GET /transactions/{txid}` - Get transaction details.

### Wallets
- `GET /wallet/{address}` - Get wallet balance and details.
- `GET /wallets/top` - Top 100 RXD holders.
- `GET /address/{address}/transactions` - Get transaction history for an address.

### Glyph Tokens
- `GET /tokens/search` - Search tokens by owner, type, or metadata.
- `GET /tokens/{token_id}` - Get token details.
- `GET /tokens/{token_id}/history` - Get token history.
- `GET /glyph/users/top` - Top Glyph token holders.
- `GET /glyph/containers/top` - Top Glyph container users.

### NFTs
- `GET /nft/search` - Search NFTs.
- `GET /nft/collections/top` - Top NFT collections.

### Analytics
- `GET /holders/rxd` - Count of unique RXD holders.
- `GET /holders/token/{token_id}` - Count of unique token holders.

### System
- `GET /health/db` - Check database connectivity.
- `GET /status` - Check sync status and node health.

## Admin (Protected)
- `POST /admin/cache/clear` - Clear server-side caches.
