# RXinDexer

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-%3E%3D3.8-blue.svg)
![Version](https://img.shields.io/badge/version-2.0.0-green.svg)
![Database](https://img.shields.io/badge/database-RocksDB%20%7C%20LevelDB-orange.svg)

© 2024-2026 The Radiant Community Devs

**RXinDexer** is a next-generation indexer for the Radiant blockchain with comprehensive support for the Glyph token standard (v1 and v2), WAVE naming system, and Swap DEX. Built on the proven ElectrumX foundation, RXinDexer extends indexing capabilities to support the full Radiant ecosystem including 38+ token-specific API methods.

## Features

### Core Electrum Features
- **RocksDB Support**: Production-optimized database backend with ~52% lower steady-state RAM
- **High Performance**: Optimized for Radiant's UTXO model and transaction volume
- **Docker Ready**: Production-ready Docker images with resource limits
- **SSL/TLS**: Built-in support for encrypted connections

### Glyph Token Indexing
- **Glyph v1**: Full FT, NFT, and DAT token indexing
- **Glyph v2**: dMint, Mutable tokens, Containers, Authority tokens
- **Metadata Parsing**: CBOR metadata extraction and caching
- **Token Balances**: Fast balance queries by address and ref

### WAVE Naming System (REP-3011)
- **Prefix Tree Index**: Efficient name resolution via character-based tree
- **Zone Records**: Address, content hash, and custom record resolution
- **Subdomain Support**: Hierarchical name resolution

### Swap DEX
- **Order Book Tracking**: RSWP on-chain swap advertisement indexing
- **Real-time Updates**: WebSocket subscriptions for order book changes
- **Trade History**: Historical swap data with pagination

## Quick Start (Docker - Recommended)

The fastest way to deploy RXinDexer is using the full-stack Docker deployment which includes both Radiant Node and RXinDexer.

### Prerequisites

- Docker and Docker Compose installed
- At least 16GB RAM recommended (8GB minimum)
- 500GB+ SSD storage for blockchain data

### Full Stack Deployment (Radiant Node + RXinDexer)

This is the recommended method for most users:

```bash
# Clone the repository
git clone https://github.com/Radiant-Core/RXinDexer.git
cd RXinDexer/docker/full-stack

# Copy and configure environment
cp .env.example .env

# Edit .env with secure RPC credentials (change the password!)
nano .env

# Build and start everything
docker-compose up -d

# View logs
docker-compose logs -f rxindexer
```

This automatically:
- Builds and starts a Radiant full node (radiantd)
- Waits for node sync via healthcheck
- Starts RXinDexer with all token indexing features enabled
- Persists all data in Docker volumes

### Environment Configuration

Edit `.env` before starting:

```bash
# Required: Set a secure RPC password
RPC_USER=radiant
RPC_PASS=your_secure_password_here  # CHANGE THIS!

# Performance (adjust based on available RAM)
CACHE_MB=2000

# Token indexing (all enabled by default)
GLYPH_INDEX=1
WAVE_INDEX=1
SWAP_INDEX=1
GLYPH_SUBSCRIPTIONS=1
MEMPOOL_GLYPH_INDEX=1
MEMPOOL_SWAP_INDEX=1
```

### Verify Installation

After starting, test that token tracking is working:

```bash
# Check container status
docker-compose ps

# Test basic connectivity (should return server version)
echo '{"id":1,"method":"server.version","params":[]}' | nc localhost 50010

# Test token methods
echo '{"id":1,"method":"wave.stats","params":[]}' | nc localhost 50010
echo '{"id":1,"method":"swap.get_unconfirmed_orders","params":[]}' | nc localhost 50010
```

### Exposed Ports

| Port | Protocol | Description |
|------|----------|-------------|
| 50010 | TCP | Electrum protocol (unencrypted) |
| 50011 | WSS | WebSocket Secure (for Photonic wallet) |
| 50012 | SSL | Electrum protocol (encrypted) |
| 8000 | HTTP | REST API interface |
| 8001 | TCP | Admin RPC interface |
| 7332 | TCP | Radiant Node RPC |
| 7333 | TCP | Radiant Node P2P |

## Standalone Deployment (Existing Radiant Node)

If you already have a Radiant node running:

```bash
# Clone the repository
git clone https://github.com/Radiant-Core/RXinDexer.git
cd RXinDexer

# Copy and configure environment
cp config.env .env

# Edit with your Radiant node credentials
nano .env
```

Configure `.env`:

```bash
# Your Radiant Core RPC credentials
DAEMON_URL=http://YOUR_RPC_USER:YOUR_RPC_PASSWORD@localhost:7332/

# Network
COIN=Radiant
NET=mainnet

# Database (rocksdb recommended)
DB_ENGINE=rocksdb
DB_DIRECTORY=/path/to/electrumdb

# Services
SERVICES=tcp://0.0.0.0:50010,ssl://0.0.0.0:50012,wss://0.0.0.0:50011,rpc://127.0.0.1:8001
```

Generate SSL certificates and build:

```bash
# Generate SSL certificates
mkdir -p electrumdb
openssl req -x509 -nodes -days 365 -newkey rsa:4096 \
    -keyout electrumdb/server.key \
    -out electrumdb/server.crt \
    -subj "/CN=localhost"

# Build and run
docker-compose build
docker-compose up -d
```

## Manual Installation (Without Docker)

For non-Docker deployments:

```bash
# Install system dependencies (Ubuntu/Debian)
sudo apt update
sudo apt install -y python3 python3-pip python3-dev \
    libleveldb-dev librocksdb-dev libsnappy-dev \
    libbz2-dev libzstd-dev liblz4-dev zlib1g-dev

# Clone repository
git clone https://github.com/Radiant-Core/RXinDexer.git
cd RXinDexer

# Install Python dependencies
pip3 install -r requirements.txt

# Set environment variables
export COIN=Radiant
export NET=mainnet
export DB_ENGINE=rocksdb
export DB_DIRECTORY=/path/to/electrumdb
export DAEMON_URL=http://user:pass@localhost:7332/
export SERVICES=tcp://0.0.0.0:50010,ssl://0.0.0.0:50012,wss://0.0.0.0:50011,rpc://127.0.0.1:8001
export GLYPH_INDEX=1
export WAVE_INDEX=1
export SWAP_INDEX=1

# Run
python3 electrumx_server
```

## RXinDexer Token API (38 Methods)

RXinDexer extends ElectrumX with comprehensive token indexing APIs.

### Glyph Token API (20 methods)

| Method | Description |
|--------|-------------|
| `glyph.get_token` | Get token by reference |
| `glyph.get_token_info` | Get detailed token information |
| `glyph.get_balance` | Get token balance for address |
| `glyph.list_tokens` | List tokens with pagination |
| `glyph.search_tokens` | Search tokens by name/ticker |
| `glyph.get_history` | Get token transaction history |
| `glyph.get_metadata` | Get token CBOR metadata |
| `glyph.get_by_ref` | Get token by reference ID |
| `glyph.get_tokens_by_type` | Filter tokens by type |
| `glyph.validate_protocols` | Validate Glyph protocols |
| `glyph.parse_envelope` | Parse Glyph envelope |
| `glyph.get_protocol_info` | Get protocol information |
| `glyph.get_unconfirmed_balance` | Get unconfirmed token balance |
| `glyph.get_unconfirmed_txs` | Get unconfirmed token transactions |
| `glyph.get_token_unconfirmed` | Get unconfirmed token state |
| `glyph.subscribe.balance` | Subscribe to balance updates |
| `glyph.unsubscribe.balance` | Unsubscribe from balance updates |
| `glyph.subscribe.token` | Subscribe to token updates |
| `glyph.unsubscribe.token` | Unsubscribe from token updates |
| `glyph.subscribe.transfers` | Subscribe to transfer events |

### WAVE Naming API (6 methods)

| Method | Description |
|--------|-------------|
| `wave.resolve` | Resolve WAVE name to address |
| `wave.check_available` | Check if name is available |
| `wave.get_subdomains` | Get subdomains of a name |
| `wave.reverse_lookup` | Find names owned by address |
| `wave.stats` | Get WAVE indexing statistics |
| `wave.subscribe.name` | Subscribe to name changes |

### Swap DEX API (6 methods)

| Method | Description |
|--------|-------------|
| `swap.get_unconfirmed_orders` | Get pending swap orders |
| `swap.get_user_unconfirmed` | Get user's pending orders |
| `swap.subscribe.orderbook` | Subscribe to orderbook |
| `swap.unsubscribe.orderbook` | Unsubscribe from orderbook |
| `swap.subscribe.fills` | Subscribe to trade fills |
| `swap.subscribe.user_orders` | Subscribe to user's orders |

### dMint Mining API (5 methods)

| Method | Description |
|--------|-------------|
| `dmint.get_contracts` | List dMint contracts |
| `dmint.get_contract` | Get specific contract |
| `dmint.get_by_algorithm` | Filter by mining algorithm |
| `dmint.get_most_profitable` | Get most profitable contracts |
| `dmint.subscribe.token` | Subscribe to mining updates |

### Mempool API (1 method)

| Method | Description |
|--------|-------------|
| `mempool.glyph_stats` | Get mempool token statistics |

## Database Backends

### RocksDB (Recommended)

- **~52% lower steady-state RAM** (561MB vs 1.17GB observed)
- Better write amplification control
- More tuning options
- Production recommended

### LevelDB

- Legacy database from original ElectrumX
- Higher steady-state RAM usage
- Set `DB_ENGINE=leveldb` to use

### RocksDB Tuning

```bash
# Compression (lz4 recommended)
ROCKSDB_COMPRESSION=lz4

# Block cache - main read performance lever (MB)
ROCKSDB_BLOCK_CACHE_MB=256

# Write buffer size (bytes)
ROCKSDB_WRITE_BUFFER_SIZE=67108864

# Durability (true for production)
ROCKSDB_USE_FSYNC=true
```

## Production Recommendations

### Security

1. **Use Strong RPC Credentials**: Generate random passwords
2. **Enable Rate Limiting**: Set `COST_SOFT_LIMIT=1000` and `COST_HARD_LIMIT=10000`
3. **SSL Certificates**: Use CA-signed certificates for public servers
4. **Firewall**: Only expose necessary ports

### Performance

1. **Use RocksDB** for lower memory usage
2. **Tune Cache Size** based on available RAM (`CACHE_MB=10000` for 16GB+)
3. **Use SSD Storage** for the database directory

## Troubleshooting

### "Connection refused" to daemon

Ensure Radiant Core is running with RPC enabled in `radiant.conf`:

```
server=1
rpcuser=youruser
rpcpassword=yourpassword
rpcallowip=127.0.0.1
rpcport=7332
```

### High memory during sync

Normal during initial sync. Memory drops significantly after sync completes. With RocksDB, steady-state RAM is typically under 600MB.

### Slow initial sync

Initial sync can take 1-2 hours. To speed up:
- Increase `CACHE_MB`
- Use SSD storage
- Ensure Radiant Core is fully synced first

### Token methods return "not enabled"

Verify environment variables are set:
```bash
GLYPH_INDEX=1
WAVE_INDEX=1
SWAP_INDEX=1
```

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed information on:
- Glyph v1/v2 indexing implementation
- WAVE prefix tree structure
- Database schema extensions
- API endpoint specifications

## Contributing

1. Fork the repository
2. Create a feature branch
3. Run tests: `pytest tests/`
4. Submit a pull request

## License

MIT License. See `LICENCE` file for details.

## Links

- [Radiant Blockchain](https://radiantblockchain.org)
- [RXinDexer GitHub](https://github.com/Radiant-Core/RXinDexer)
- [ElectrumX Documentation](https://electrumx.readthedocs.io/)
