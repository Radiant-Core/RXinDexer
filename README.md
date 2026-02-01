# RXinDexer

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-%3E%3D3.8-blue.svg)
![Version](https://img.shields.io/badge/version-2.0.0--dev-orange.svg)
![Database](https://img.shields.io/badge/database-RocksDB%20%7C%20LevelDB-orange.svg)

© 2024-2026 The Radiant Community Devs

**RXinDexer** is a next-generation indexer for the Radiant blockchain with comprehensive support for the Glyph token standard (v1 and v2) and WAVE naming system. Built on the proven ElectrumX foundation, RXinDexer extends indexing capabilities to support the full Radiant ecosystem.

## Features

### Core Electrum Features
- **RocksDB Support**: Production-optimized database backend with lower steady-state RAM
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

### Swap & DeFi
- **Swap Index**: RSWP on-chain swap advertisement tracking
- **Order Book**: Real-time open order queries
- **Swap History**: Historical swap data with pagination

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed information on:
- Glyph v1/v2 indexing implementation
- WAVE prefix tree structure
- Database schema extensions
- API endpoint specifications

## Quick Start (Docker)

The fastest way to deploy RXinDexer is using Docker.

### Prerequisites

- Docker and Docker Compose installed
- A running Radiant Core node with RPC access
- At least 16GB RAM recommended for initial sync

### 1. Clone and Configure

```bash
git clone https://github.com/Radiant-Core/ElectrumX.git
cd ElectrumX

# Copy the example config
cp config.env .env

# Edit with your Radiant node credentials
vi .env
```

### 2. Configure Environment

Edit `.env` with your settings:

```bash
# Required: Your Radiant Core RPC credentials
DAEMON_URL=http://YOUR_RPC_USER:YOUR_RPC_PASSWORD@localhost:7332/

# Network
COIN=Radiant
NET=mainnet

# Database (rocksdb recommended for production)
DB_ENGINE=rocksdb
DB_DIRECTORY=/root/electrumdb

# Services to expose (includes WSS for Photonic wallet)
SERVICES=tcp://0.0.0.0:50010,ssl://0.0.0.0:50012,wss://0.0.0.0:50011,rpc://0.0.0.0:8000
```

### 3. Generate SSL Certificates

**IMPORTANT**: See [SSL_CERTIFICATES.md](SSL_CERTIFICATES.md) for detailed security guidelines and certificate management.

For production, use proper CA-signed certificates. For testing:

```bash
mkdir -p electrumdb
openssl req -x509 -nodes -days 365 -newkey rsa:4096 \
    -keyout electrumdb/server.key \
    -out electrumdb/server.crt \
    -subj "/CN=your.domain.com"
```

⚠️ **Security Note**: The `.gitignore` file prevents accidental commits of certificate files. Never commit private keys to version control!

### 4. Build and Run

```bash
# Build the image
docker-compose build

# Start in background
docker-compose up -d

# View logs
docker logs -f electrumx_server

# Graceful shutdown
docker-compose down
```

### Full Stack Deployment (Node + RXinDexer)

For a complete one-command deployment including both Radiant Node and RXinDexer:

```bash
cd docker/full-stack
cp .env.example .env
# Edit .env with secure RPC credentials
docker-compose up -d
```

This automatically:

- Builds and starts a Radiant full node (radiantd)
- Waits for node sync via healthcheck
- Starts RXinDexer with all indexing features enabled
- Persists all data in Docker volumes

See `docker/full-stack/README.md` for full documentation including all 38 API methods.

## Manual Installation

For non-Docker deployments:

```bash
# Install system dependencies (Ubuntu/Debian)
sudo apt update
sudo apt install -y python3 python3-pip python3-dev \
    libleveldb-dev librocksdb-dev libsnappy-dev \
    libbz2-dev libzstd-dev liblz4-dev zlib1g-dev

# Clone repository
git clone https://github.com/Radiant-Core/ElectrumX.git
cd ElectrumX

# Install Python dependencies
pip3 install -r requirements.txt

# Set environment variables (or use a .env file)
export COIN=Radiant
export NET=mainnet
export DB_ENGINE=rocksdb
export DB_DIRECTORY=/path/to/electrumdb
export DAEMON_URL=http://user:pass@localhost:7332/
export SERVICES=tcp://0.0.0.0:50010,ssl://0.0.0.0:50012,wss://0.0.0.0:50011,rpc://0.0.0.0:8000

# Run
python3 electrumx_server
```

## Database Backends

### RocksDB (Default)

- **~52% lower steady-state RAM** (561MB vs 1.17GB observed)
- Better write amplification control
- More tuning options
- Production recommended

### LevelDB

- Legacy database used by ElectrumX
- Higher steady-state RAM usage


Set `DB_ENGINE=leveldb` to use LevelDB instead.

### RocksDB Tuning

Key environment variables for RocksDB performance:

```bash
# Compression (lz4 recommended)
ROCKSDB_COMPRESSION=lz4

# Block cache - main read performance lever (MB)
ROCKSDB_BLOCK_CACHE_MB=256

# Write buffer size (bytes)
ROCKSDB_WRITE_BUFFER_SIZE=67108864

# Background jobs
ROCKSDB_MAX_BACKGROUND_COMPACTIONS=4
ROCKSDB_MAX_BACKGROUND_FLUSHES=2

# Durability (true for production serving)
ROCKSDB_USE_FSYNC=true
```

See `docs/environment.rst` for all available options.

## Production Recommendations

### Security

1. **Enable Rate Limiting**: Never set `COST_SOFT_LIMIT=0` or `COST_HARD_LIMIT=0` in production

   ```bash
   COST_SOFT_LIMIT=1000
   COST_HARD_LIMIT=10000
   ```

2. **Use Strong RPC Credentials**: Generate random passwords for `DAEMON_URL`

3. **SSL Certificates**: Use CA-signed certificates for public servers

4. **Run as Non-Root**: Set `ALLOW_ROOT=false` when possible

5. **Firewall**: Only expose necessary ports:
   - `50010` - TCP (unencrypted)
   - `50011` - WSS (WebSocket Secure, for Photonic wallet)
   - `50012` - SSL/TLS

### Performance

1. **Use RocksDB** for lower steady-state memory

2. **Tune Cache Size** based on available RAM:

   ```bash
   CACHE_MB=10000  # For 16GB+ RAM systems
   ```

3. **Set Resource Limits** in Docker:

   ```yaml
   deploy:
     resources:
       limits:
         memory: 12G
   ```

4. **Use SSD Storage** for the database directory

### Monitoring

Monitor these metrics in production:

- RSS memory usage
- Database size (`du -sh /path/to/electrumdb`)
- Sync status via RPC
- Connection count

## RPC Commands

ElectrumX exposes an RPC interface (default port 8000):

```bash
# Inside Docker container
docker exec electrumx_server python3 electrumx_rpc getinfo

# Or using the script directly
./electrumx_rpc getinfo
./electrumx_rpc sessions
./electrumx_rpc peers
```

Common RPC commands:

- `getinfo` - Server status and sync progress
- `sessions` - Connected client sessions
- `peers` - Known peer servers
- `stop` - Graceful shutdown

## RXinDexer API (38 Methods)

RXinDexer extends ElectrumX with comprehensive token indexing APIs:

### Glyph Token API (20 methods)
```bash
glyph.get_token          glyph.list_tokens        glyph.search_tokens
glyph.get_balance        glyph.get_history        glyph.get_metadata
glyph.get_by_ref         glyph.get_token_info     glyph.get_tokens_by_type
glyph.validate_protocols glyph.parse_envelope     glyph.get_protocol_info
glyph.get_unconfirmed_balance                     glyph.get_unconfirmed_txs
glyph.subscribe.balance  glyph.subscribe.token    glyph.subscribe.transfers
glyph.unsubscribe.balance glyph.unsubscribe.token glyph.get_token_unconfirmed
```

### WAVE Naming API (6 methods)
```bash
wave.resolve             wave.check_available     wave.get_subdomains
wave.reverse_lookup      wave.stats               wave.subscribe.name
```

### Swap DEX API (6 methods)
```bash
swap.get_unconfirmed_orders    swap.subscribe.orderbook
swap.get_user_unconfirmed      swap.subscribe.fills
swap.subscribe.user_orders     swap.unsubscribe.orderbook
```

### dMint Mining API (5 methods)
```bash
dmint.get_contracts      dmint.get_contract       dmint.get_by_algorithm
dmint.get_most_profitable                         dmint.subscribe.token
```

### Mempool API (1 method)
```bash
mempool.glyph_stats
```

### Configuration

All indexing features are enabled by default. Disable selectively:

```bash
# Disable specific indexers
GLYPH_INDEX=0
WAVE_INDEX=0
SWAP_INDEX=0

# Disable mempool tracking
MEMPOOL_GLYPH_INDEX=0
MEMPOOL_SWAP_INDEX=0

# Disable WebSocket subscriptions
GLYPH_SUBSCRIPTIONS=0
```

## Troubleshooting

### "Connection refused" to daemon

Ensure Radiant Core is running with RPC enabled:

```bash
# In radiant.conf
server=1
rpcuser=youruser
rpcpassword=yourpassword
rpcallowip=127.0.0.1
rpcport=7332
```

### High memory usage during sync

This is normal. Memory usage drops significantly after initial sync completes.
With RocksDB, steady-state RAM is typically under 500MB.

### Slow initial sync

Initial sync can take 1-2 hours depending on hardware. To speed up:

- Increase `CACHE_MB`
- Use SSD storage
- Ensure Radiant Core is fully synced first

### "Module not found: rocksdb"

Install the Python RocksDB bindings:

```bash
pip3 install Cython python-rocksdb
```

### Docker permission errors

Ensure the `electrumdb` directory is writable:

```bash
mkdir -p electrumdb
chmod 755 electrumdb
```

## Documentation

- Full environment variables: `docs/environment.rst`
- Architecture: `docs/architecture.rst`
- Performance notes: `docs/PERFORMANCE-NOTES`
- API protocol: `docs/protocol-*.rst`

## Known Issues

### websockets library compatibility

The websockets library v11.0+ has a breaking API change. ElectrumX requires `websockets>=10.0,<11.0`. This is already pinned in `requirements.txt`.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Run tests: `pytest tests/`
4. Submit a pull request

## License

MIT License. See `LICENCE` file for details.

## Links

- [Radiant Blockchain](https://radiantblockchain.org)
- [Original ElectrumX](https://github.com/kyuupichan/electrumx)
- [ElectrumX Documentation](https://electrumx.readthedocs.io/)
