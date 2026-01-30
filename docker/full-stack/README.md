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

# 2. Start
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
| rxindexer | 8000 | RPC interface |

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

Docker volumes store all data:
- `radiant-node-data` - Blockchain (~50GB+)
- `rxindexer-db-data` - Index database
- `rxindexer-contracts-data` - dMint contracts

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
# Remove and rebuild index (keeps blockchain)
docker-compose down
docker volume rm rxindexer-db-data
docker-compose up -d
```
