# RXinDexer Full Stack Deployment

One-command deployment for running a complete Radiant infrastructure with the full node (radiantd) and RXinDexer (advanced ElectrumX with Glyph/WAVE/Swap indexing).

## What is RXinDexer?

RXinDexer is an enhanced ElectrumX server that indexes Radiant's advanced token features:

| Feature | Description |
|---------|-------------|
| **Glyph Tokens** | FT, NFT, dMint, Mutable, Containers |
| **WAVE Names** | Decentralized naming system |
| **Swap Orders** | DEX order book indexing |
| **Mempool** | Unconfirmed transaction tracking |
| **WebSockets** | Real-time push notifications |

**This deployment is fully self-contained** - both services are built from source:
- **Radiant Node**: [Radiant-Core/Radiant-Core](https://github.com/Radiant-Core/Radiant-Core)
- **RXinDexer**: [Radiant-Core/RXinDexer](https://github.com/Radiant-Core/RXinDexer)

## Quick Start

```bash
# 1. Configure
cp .env.example .env
nano .env  # Set RPC_PASS

# 2. Start (use --no-cache to avoid build context issues)
docker-compose build --no-cache
docker-compose up -d

# 3. Monitor
docker-compose logs -f
```

## Services & Ports

| Service | Port | Description |
|---------|------|-------------|
| radiantd | 7332 | Node RPC |
| radiantd | 7333 | P2P network |
| rxindexer | 50010 | TCP (Electrum protocol) |
| rxindexer | 50011 | WSS (WebSocket subscriptions) |
| rxindexer | 50012 | SSL (Electrum protocol) |
| rxindexer | 8000 | REST API (HTTP) |
| rxindexer | 8001 | Admin RPC (localhost only) |

## API Methods (38 total)

### Glyph Token API (20 methods)
```
glyph.get_token          glyph.list_tokens        glyph.search_tokens
glyph.get_balance        glyph.get_history        glyph.get_metadata
glyph.subscribe.balance  glyph.subscribe.token    glyph.subscribe.transfers
```

### WAVE Naming API (6 methods)
```
wave.resolve             wave.check_available     wave.get_subdomains
wave.reverse_lookup      wave.stats               wave.subscribe.name
```

### Swap DEX API (6 methods)
```
swap.get_unconfirmed_orders    swap.subscribe.orderbook
swap.get_user_unconfirmed      swap.subscribe.fills
swap.subscribe.user_orders     swap.unsubscribe.orderbook
```

### dMint Mining API (5 methods)
```
dmint.get_contracts      dmint.get_contract       dmint.get_by_algorithm
dmint.get_most_profitable                         dmint.subscribe.token
```

### Mempool API (1 method)
```
mempool.glyph_stats
```

## Configuration

All RXinDexer features are enabled by default. Disable any feature in `.env`:

```bash
# Disable WAVE indexing
WAVE_INDEX=0

# Disable mempool tracking
MEMPOOL_GLYPH_INDEX=0
MEMPOOL_SWAP_INDEX=0
```

See `.env.example` for all options.

## Build & Sync Times

**First-time build** (compiles from source):
- radiantd: ~10-20 minutes
- rxindexer: ~2-5 minutes

**Initial sync** (after build):
1. **radiantd** syncs blockchain first (1-4 hours)
2. **rxindexer** waits via healthcheck until ready
3. **rxindexer** indexes blockchain + tokens (1-3 hours)

Monitor progress:
```bash
# Check radiantd sync
docker exec radiantd radiant-cli -rpcuser=radiant -rpcpassword=YOUR_PASS getblockchaininfo

# Check rxindexer status
docker logs -f rxindexer
```

## Data Persistence

Data is stored as follows:
- `radiant-node-data` (named volume) - Blockchain (~50GB+)
- `./electrumdb` (**host bind-mount**) - RXinDexer RocksDB index (~15GB+) **and**
  the SSL cert/key. This lives on the host at `docker/full-stack/electrumdb`.
- `rxindexer-contracts-data` (named volume) - dMint contracts

> **Why the index is a bind-mount (and must stay one).** The RXinDexer index is
> deliberately a host bind-mount, not a named volume. A named volume here was a
> foot-gun: a `docker compose up` that didn't see the production override would
> mount a *fresh empty* named volume, and RXinDexer would resync the chain from
> genesis (~24h outage) instead of opening the live index. Binding `./electrumdb`
> in the committed base makes the default config point at the same on-disk index
> in every environment. Ensure the dir is owned by uid:gid `1000:1000`:
> `sudo chown -R 1000:1000 docker/full-stack/electrumdb`.

### Optional per-host override (TLS certs)

The committed base ships generic self-signed cert defaults (`server.crt` /
`server.key`, auto-generated on first boot by `entrypoint.sh`). To use CA-signed
certs (e.g. Let's Encrypt) without editing the committed base, copy the template
and point `SSL_CERTFILE` / `SSL_KEYFILE` at your cert inside `./electrumdb`:

```bash
cp docker-compose.override.yaml.example docker-compose.override.yaml
# edit cert filenames; docker compose auto-merges this file
```

The override is **optional** and gitignored. It does **not** affect the index
bind-mount — forgetting it never triggers a resync.

## Graceful Shutdown

RXinDexer requires graceful shutdown to avoid database corruption:
```bash
docker-compose down
# Or:
docker kill --signal="TERM" rxindexer
```

## Production Recommendations

1. **Use a reverse proxy** (nginx/traefik) for SSL termination
2. **Set secure RPC credentials** in `.env`
3. **Increase CACHE_MB** for faster sync (requires RAM)
4. **Use SSD storage** for all volumes
5. **Monitor with** `docker stats` for resource usage

## Troubleshooting

**RXinDexer won't start:**
```bash
# Check if radiantd is healthy
docker exec radiantd radiant-cli -rpcuser=radiant -rpcpassword=YOUR_PASS getblockchaininfo
```

**Connection refused on ports:**
```bash
# Verify services are running
docker-compose ps
```

**Database corruption after crash:**
```bash
# Remove and rebuild index (keeps blockchain). This DELETES the live ~15GB
# index and forces a full resync — only do this if the index is unrecoverable.
docker-compose down
sudo rm -rf ./electrumdb/{hist,meta,utxo}   # index subdirs; keeps SSL certs
docker-compose up -d
```
