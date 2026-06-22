# RXinDexer — Ecosystem Server Provider Setup

This guide covers the complete configuration for a single RXinDexer/Radiant-Core node that serves **Photonic Wallet**, **Glyph Miner**, and **GlyphGalaxy** (and any other app in the Radiant token ecosystem). It is the missing one-page contract that ties together the RXinDexer feature toggles, Radiant Core flags, network endpoints, and the API surface each consumer expects.

---

## 1. What you are running

A public server provider runs two primary services:

| Service | Role | Default ports |
|---|---|---|
| `radiantd` (Radiant Core) | Full node, consensus, mempool, swap index, raw tx/ block data | RPC `7332`, P2P `7333` |
| `rxindexer` (ElectrumX + RXinDexer extensions) | UTXO/address index, Glyph/WAVE/realm/swap index, REST API, WebSocket push | TCP `50010`, WSS `50011`, SSL `50012`, WS `50013`, REST `8000`, RPC `8001` |

Browser wallets connect to `wss://<your-host>:50011`. REST consumers (market explorers, etc.) connect to `https://<your-host>:8000`. TCP/SSL Electrum clients can use `50010` / `50012`.

> In most production setups these aren't exposed directly — a reverse proxy (e.g. Caddy) terminates TLS on `:443` and forwards to the internal **plain `ws://…:50013`** and REST `:8000`, while `50010`–`50012` stay firewalled. Browsers then connect to `wss://<your-host>` (443). If you front WSS this way, set `TRUST_PROXY=1` (see §7).

---

## 2. Radiant Core (`radiantd`) required flags

These flags must be set on the node RXinDexer reads from. The values below mirror the production reference in `docker/full-stack/docker-compose.yaml`.

```bash
# RPC (must be reachable by RXinDexer)
-server=1
-rpcuser=<user>
-rpcpassword=<strong-password>
-rpcbind=0.0.0.0
-rpcallowip=0.0.0.0/0          # tighten in production if not containerized
-rpcallowhost=radiantd          # internal docker hostname
-rpcallowhost=swap.<yourdomain>  # if you host a swap RPC proxy

# Indexes required by RXinDexer
-txindex=1
-swapindex=1

# Reorg handling — the node must follow the most-work chain.
# The reference deployment disables the v3.x finalizer because of the
# 2026-06-15 chain-split issue. Match the indexer REORG_LIMIT to this.
-parkdeepreorg=0
-finalizeheaders=0
-maxreorgdepth=100
```

**Why each matters**
- `-txindex=1`: RXinDexer needs to fetch arbitrary transactions by txid for payload decoding and metadata lookups.
- `-swapindex=1`: Required for Photonic Wallet's "Public (Swap Index)" broadcast offers and for any DEX/royalty marketplace that reads on-chain swap data.
- `-parkdeepreorg=0 -finalizeheaders=0`: Allows the node to follow the heaviest chain during deep reorgs. If you leave finalization enabled, the node can strand on a minority fork while RXinDexer tracks the other fork, causing a consensus mismatch.

> **Note:** RXinDexer follows the chain by **polling** the node's RPC (a prefetcher with a ~5s loop); it does **not** consume ZMQ. You do not need `-zmqpubhashblock` for the indexer — setting it has no effect on RXinDexer.

---

## 3. RXinDexer feature toggles

All ecosystem features are enabled by default in RXinDexer. **Never set `MINIMAL_MODE=1`** for a public ecosystem server — it disables every optional indexer and the REST API.

> ⚠️ **Toggle parsing gotcha.** These boolean toggles treat **any non-empty value as true** — including `"0"`. So `MINIMAL_MODE=0`, `ANALYTICS_INDEX=0`, etc. do **not** disable the feature; they *enable* it. To turn a toggle **off, leave it unset (or empty) — never `=0`.** Only use `=1` to force-enable.

Set these in your `.env` or compose environment:

```bash
# Core indexing
GLYPH_INDEX=1          # FT, NFT, dMint, mutable, container tokens
WAVE_INDEX=1           # WAVE naming system
SWAP_INDEX=1           # On-chain swap DEX order indexing
REALM_INDEX=1          # Realm directory / on-chain realm NFTs
PREDICT_INDEX=1        # RMKT beacon prediction markets
ROYALTY_INDEX=1        # RRYL beacon royalty listings

# Real-time features
GLYPH_SUBSCRIPTIONS=1  # WebSocket push notifications
MEMPOOL_GLYPH_INDEX=1  # Unconfirmed token tx tracking
MEMPOOL_SWAP_INDEX=1   # Unconfirmed swap order tracking

# HTTP REST API
REST_API_ENABLED=1
REST_API_HOST=0.0.0.0
REST_API_PORT=8000
```

**Optional**
- `ROYALTY_INDEX=1` — discovery of royalty-aware marketplace listings (`royalty.get_listings`). Note: this requires a reindex/backfill if enabled after the initial sync, so decide at deploy time.
- `ANALYTICS_INDEX=1` — rich lists, supply stats, holder counts (used by explorers). ⚠️ **Leave this OFF unless you specifically need it.** On a large UTXO set its birth-height backfill runs a synchronous full-UTXO scan at startup that can block the event loop and crash-loop the indexer (this has caused multi-hour outages). The production reference deployment runs with it **disabled**. If you do enable it, validate on a staging copy first.

---

## 4. Network services (ports)

The `SERVICES` env variable controls what RXinDexer listens on. A public ecosystem server should expose at least:

```bash
SERVICES=tcp://0.0.0.0:50010,ssl://0.0.0.0:50012,wss://0.0.0.0:50011,ws://0.0.0.0:50013,rpc://127.0.0.1:8001
```

| Port | Protocol | Consumer |
|---|---|---|
| `50010` | TCP ElectrumX | Desktop Electrum clients, some miners |
| `50012` | SSL ElectrumX | Desktop Electrum clients (encrypted) |
| `50011` | WSS | **Photonic Wallet** in the browser (WebSocket TLS) |
| `50013` | WS | Internal/reverse-proxied WebSocket (e.g., Caddy → RXinDexer) |
| `8000` | HTTP REST | Market explorers, third-party apps |
| `8001` | RPC | Local admin/monitoring only |

For SSL/WSS you must provide:

```bash
SSL_CERTFILE=/data/electrumdb/server.crt
SSL_KEYFILE=/data/electrumdb/server.key
```

If you terminate TLS at a reverse proxy (Caddy/nginx) and forward plain WS to `50013`, set `TRUST_PROXY=1` so the per-IP rate limiter sees the real client IP (see section 8).

---

## 5. REST API configuration

REST is enabled with `REST_API_ENABLED=1`. In production, set both:

```bash
ALLOWED_ORIGINS=https://photonic-wallet.example.com,https://glyphgalaxy.example.com
REST_API_KEY=change_me_to_a_long_random_string
```

The REST API is required for:
- `GET /tokens/{ref}/holders` — reverse holder lookup
- `GET /glyphs/{ref}` — token metadata by ref
- `GET /wave/resolve/{name}` — WAVE name resolution
- `GET /dmint/contracts` — dMint contract listing
- `GET /swaps/orders` — swap order book
- `GET /health`, `/status` — monitoring

**CORS note:** `ALLOWED_ORIGINS` is required when `ELECTRUMX_ENV=prod`. Use `*` only in development.

---

## 6. Required API surface by app

The table below is the practical contract. If a method is missing or returns the wrong shape, the corresponding app feature breaks.

| App | Feature | Required RXinDexer API |
|---|---|---|
| **Photonic Wallet** | wallet balance, UTXOs | `blockchain.scripthash.listunspent`, `blockchain.transaction.get`, `blockchain.transaction.broadcast` |
| **Photonic Wallet** | token inventory | `glyph.list_tokens` (by full Electrum scripthash) |
| **Photonic Wallet** | token metadata / thumbnails | `glyph.get_metadata`, `glyph.get_by_ref` (must return `deploy_txid`) |
| **Photonic Wallet** | WAVE names | `wave.resolve`, `wave.check_available` |
| **Photonic Wallet** | public swap offers | external Radiant Core swap RPC proxy (see section 9) |
| **Glyph Miner** | dMint contract discovery | `dmint.get_contracts`, `dmint.get_contract`, `dmint.get_by_algorithm`, `dmint.get_most_profitable` |
| **Glyph Miner** | contract subscription | `dmint.subscribe.token` |
| **GlyphGalaxy** | login / plot ownership | `glyph.list_tokens` + `glyph.get_metadata` |
| **GlyphGalaxy** | WAVE gamertags | `wave.resolve`, `wave.check_available` |
| **GlyphGalaxy** | realm directory | `realm.list`, `realm.get_by_id`, `realm.search` |
| **GlyphGalaxy** | marketplace item cards | `glyph.get_by_ref`, `GET /tokens/{ref}/holders` (must return resolvable `address`) |
| **GlyphGalaxy** | broadcast / settlement | `blockchain.transaction.broadcast` |

### Field-shape requirements for GlyphGalaxy

These are not obvious from the RXinDexer docs alone:

1. `glyph.list_tokens` must be keyed by the **full 32-byte Electrum scripthash** (64 hex chars), not the old 11-byte `hashX`.
2. `glyph.get_by_ref` must return a `deploy_txid` field so the SDK can read the reveal tx that carries the CBOR envelope.
3. `GET /tokens/{ref}/holders` and `glyph.get_by_ref` should return a resolvable `address` (or `scriptPubKey`) for the current holder, not just a scripthash. Otherwise marketplace "Owned by" degrades to a hash.

---

## 7. Rate limits and proxy trust

The cost-based rate limiter is enabled by default (`RATE_LIMIT_ENABLED=1`). Public servers behind a reverse proxy **must** set:

```bash
TRUST_PROXY=1
TRUST_PROXY_HOPS=1
TRUSTED_PROXIES=172.18.0.0/16   # replace with your proxy subnet(s)
```

Without this, every browser wallet appears to share the proxy's IP and the whole pool gets throttled or disconnected.

Suggested production defaults (from `docker/full-stack/docker-compose.yaml`):

```bash
MAX_SESSIONS=10000
MAX_SEND=10000000
MAX_RECV=10000000
COST_SOFT_LIMIT=10000
COST_HARD_LIMIT=100000
INITIAL_CONCURRENT=50
REQUEST_SLEEP=500
REQUEST_TIMEOUT=60
SESSION_TIMEOUT=600
```

---

## 8. Swap RPC proxy for Photonic Wallet

Photonic Wallet's "Public (Swap Index)" feature does **not** use RXinDexer directly. It talks to a **CORS-enabled Radiant Core JSON-RPC proxy** with these read-only swap RPCs whitelisted:

```bash
# On radiantd
-swapindex=1
-rpcwhitelist=swapreader:getswapindexinfo,getopenorders,getopenordersbywant,getswaphistory,getswaphistorybywant,getswapcount,getswapcountbywant
```

Then expose that RPC through a reverse proxy that injects Basic auth and adds CORS headers. The full recipe is in `Photonic-Wallet/docs/deployment-guide.md` (section "Hosted Swap RPC Proxy").

If you only want to serve wallet balance/token features, the swap proxy is optional. If you want public swap offers to work, it is required.

---

## 9. Full production `.env` template

```bash
# === Radiant Core RPC ===
RPC_USER=radiant
RPC_PASS=your_secure_random_password

# === RXinDexer core ===
DAEMON_URL=http://${RPC_USER}:${RPC_PASS}@radiantd:7332/
COIN=Radiant
NET=mainnet
DB_ENGINE=rocksdb
DB_DIRECTORY=/data/electrumdb

# === Network services ===
SERVICES=tcp://0.0.0.0:50010,ssl://0.0.0.0:50012,wss://0.0.0.0:50011,ws://0.0.0.0:50013,rpc://127.0.0.1:8001
SSL_CERTFILE=/data/electrumdb/server.crt
SSL_KEYFILE=/data/electrumdb/server.key

# === REST API ===
REST_API_ENABLED=1
REST_API_HOST=0.0.0.0
REST_API_PORT=8000
ALLOWED_ORIGINS=https://yourdomain.com
REST_API_KEY=change_me

# === Performance ===
CACHE_MB=2000
MAX_SESSIONS=10000
MAX_SEND=10000000
MAX_RECV=10000000
REQUEST_TIMEOUT=60
REORG_LIMIT=100

# === Rate limits ===
COST_SOFT_LIMIT=10000
COST_HARD_LIMIT=100000
INITIAL_CONCURRENT=50
REQUEST_SLEEP=500

# === Proxy trust (set if behind Caddy/nginx) ===
TRUST_PROXY=1
TRUST_PROXY_HOPS=1
TRUSTED_PROXIES=172.18.0.0/16

# === RXinDexer ecosystem features ===
GLYPH_INDEX=1
WAVE_INDEX=1
WAVE_HOT_NAMES=10000
WAVE_GENESIS_REF=115e62d96f44402c448bf76d4ca403188733b902ab0b7703d9f36333178afda4_0
SWAP_INDEX=1
REALM_INDEX=1
PREDICT_INDEX=1
ROYALTY_INDEX=1
# ANALYTICS_INDEX — leave UNSET (off). See §3: its startup backfill can
# crash-loop the indexer on a large UTXO set. Enable only after staging it.
GLYPH_SUBSCRIPTIONS=1
MEMPOOL_GLYPH_INDEX=1
MEMPOOL_SWAP_INDEX=1

# === Logging ===
LOG_LEVEL=INFO
```

---

## 10. Deployment checklist

- [ ] `radiantd` is synced and has `-txindex=1 -swapindex=1`
- [ ] `radiantd` has `-parkdeepreorg=0 -finalizeheaders=0 -maxreorgdepth=100`
- [ ] `rxindexer` has `REORG_LIMIT=100` (matches node setting)
- [ ] `MINIMAL_MODE` is **unset/empty** (do NOT set `=0` — any non-empty value, including `0`, enables it)
- [ ] `GLYPH_INDEX`, `WAVE_INDEX`, `SWAP_INDEX`, `REALM_INDEX`, `GLYPH_SUBSCRIPTIONS` are all `1`
- [ ] `REST_API_ENABLED=1` with `ALLOWED_ORIGINS` and `REST_API_KEY` set
- [ ] WSS port `50011` is exposed and reachable from browsers
- [ ] REST port `8000` is exposed (or proxied) with TLS
- [ ] SSL cert/key are valid for `wss://` and `https://` hostnames
- [ ] If behind a reverse proxy, `TRUST_PROXY=1` and `TRUSTED_PROXIES` are set
- [ ] Swap RPC proxy is configured if you want Photonic Wallet public offers
- [ ] `WAVE_GENESIS_REF` is set correctly for the active network
- [ ] `./electrumdb` bind-mount is owned by `1000:1000`

---

## 11. Quick verification

From a client:

```bash
# WebSocket handshake
curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Key: $(openssl rand -base64 16)" \
  -H "Sec-WebSocket-Version: 13" \
  https://your-host:50011

# REST health
curl https://your-host:8000/health

# REST holder lookup (replace with a real ref)
curl -H "X-API-Key: $REST_API_KEY" \
  https://your-host:8000/tokens/<ref>/holders

# RPC token list (requires a local RPC connection)
curl -X POST http://localhost:8001 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"glyph.list_tokens","params":["<scripthash>"]}'
```

---

## 12. References

- RXinDexer environment variables: `docs/environment.rst`
- RXinDexer Glyph API: `docs/GLYPH_API.md`
- RXinDexer REST API: `docs/REST_API.md`
- Full-stack Docker deploy: `docker/full-stack/README.md` and `docker/full-stack/docker-compose.yaml`
- Photonic Wallet swap proxy recipe: [Photonic-Wallet](https://github.com/Radiant-Core/Photonic-Wallet) `docs/deployment-guide.md`

---

*Last updated: 2026-06-20*
