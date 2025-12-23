# RXinDexer

**RXinDexer is dedicated to Razoo. Thank you for all you did for Radiant.**

RXinDexer is a production-ready indexer for the Radiant (RXD) blockchain, providing fast API access to transaction history, balances, and Glyph token metadata.

## Project Status: Production Ready ✅

| Component | Status |
|-----------|--------|
| Core Infrastructure | ✅ Complete |
| Blockchain Indexer | ✅ Complete |
| REST API | ✅ Complete |
| Glyph Token Support | ✅ Complete |
| Wallet Balance Cache | ✅ Complete |

## What You Get

RXinDexer runs as a small set of Docker services:

| Service | Purpose | Default Ports |
|--------|---------|---------------|
| `radiant-node` | Radiant full node (RPC + REST enabled) | `7332`, `7333` |
| `rxindexer-db` | PostgreSQL database | `5432` |
| `rxindexer-indexer` | Block sync + parse + token/backfill workers | (internal) |
| `rxindexer-api` | FastAPI API server | `8000` |
| `rxindexer-balance-refresh` | Periodic wallet balance cache refresh | (internal) |

## Quick Start

```bash
cd docker
docker compose up -d
```

Once running:
- **API Docs**: http://localhost:8000/docs
- **Health Check**: http://localhost:8000/health
- **Explorer**: Optional and external to this repository (run separately)

## Developer Quick Start

### Run everything in Docker (recommended)

```bash
cd docker
docker compose up -d
```

### Run API on host (while DB + node run in Docker)

1. Start dependencies:
```bash
docker compose -f docker/docker-compose.yml up -d db radiant-node
```

2. Create a local env file based on `config/.env.example`, and set DB vars:
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`

3. Install Python deps and run FastAPI:
```bash
pip install -r api/requirements.txt
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

For deeper development notes (testing, architecture, conventions):
- [Development Guide](DEVELOPMENT.md)

## API Overview (Users + Developers)

- **Base URL**: `http://<host>:8000`
- **Swagger UI**: `/docs`
- **Redoc**: `/redoc`
- **Compatibility**: every endpoint is exposed twice (e.g. `/tokens/...` and `/api/tokens/...`).

### Common Endpoint Groups

- **Health/Status**
  - `GET /health`
  - `GET /health/db`
  - `GET /status`
- **Blocks/Transactions**
  - `GET /blocks/recent`
  - `GET /block/{height}`
  - `GET /transactions/recent`
  - `GET /transaction/{txid}`
  - `GET /address/{address}/transactions`
- **Wallets (balances + UTXOs)**
  - `GET /wallet/{address}`
  - `GET /address/{address}/utxos`
  - `GET /wallets/top`
- **Glyphs + Tokens**
  - Unified glyphs table: `GET /glyphs` and related `/glyphs/*` endpoints
  - Token analytics: `GET /tokens/{token_id}/holders`, `/supply`, `/trades`, `/burns`, `/price`, `/ohlcv`
- **Market**
  - `GET /market/rxd` (CoinGecko)
  - `GET /market/swaps`, `GET /market/trades`, `GET /market/volume`

Full endpoint list (authoritative):
- [API Reference](API.md)

## Documentation

| Document | Description |
|----------|-------------|
| [Deployment Guide](DEPLOYMENT.md) | Production setup, monitoring, maintenance |
| [API Reference](API.md) | REST API endpoints |
| [Development Guide](DEVELOPMENT.md) | Architecture and contribution guidelines |
| [Project Progress](PROJECT_PROGRESS.md) | Status and changelog |
| [Token Indexer Roadmap](TOKEN_INDEXER_ROADMAP.md) | Implemented features and current state |
| [Optimization Ideas](OPTIMIZATION_IDEAS.md) | Reference notes for future performance tuning |
| [Node Enhancement Suggestions](NODE_ENHANCEMENT_SUGGESTIONS.md) | Optional Radiant node enhancements (swap tracking, etc.) |

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Radiant Node   │────▶│    Indexer      │────▶│   PostgreSQL    │
│  (blockchain)   │     │  (sync/parse)   │     │   (database)    │
└─────────────────┘     └─────────────────┘     └────────┬────────┘
                                                         │
                                                         ▼
                                                ┌─────────────────┐
                                                │    FastAPI      │
                                                │   (REST API)    │
                                                └─────────────────┘
```

## Configuration

### Docker (default)
Docker Compose wires service-to-service networking automatically (the API/indexer point at `radiant-node` and `db`).

### Running Outside Docker (developer workflow)
If you run the API or indexer on the host, use these environment variables:

- `RADIANT_NODE_HOST`
- `RADIANT_NODE_RPCUSER`
- `RADIANT_NODE_RPCPASSWORD`
- `RADIANT_NODE_RPCPORT`
- `RADIANT_NODE_RESTPORT`

See: [`config/.env.example`](../config/.env.example)

Database connection environment variables are also required for non-docker runs:

- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_HOST`
- `POSTGRES_PORT`

## Features

- **Partitioned Tables**: Blocks, transactions, and UTXOs are partitioned for scalability
- **Glyph Token Support**: Full indexing of Glyph protocol tokens with Photonic Wallet compatibility
- **Wallet Balance Cache**: Pre-computed balances for instant rich list queries
- **Connection Pooling**: Production-ready database connection management
- **Automated Maintenance**: Weekly cleanup of Docker artifacts

## Development Notes

- **Migrations**: Alembic migrations run automatically on container startup; schema is authoritative in `alembic/versions/`.
- **Indexer catchup vs correctness**: During heavy catchup, spent checks may be delayed and backfilled later (see [Deployment Guide](DEPLOYMENT.md)).
- **Explorer**: This repository ships the backend only. Any explorer UI is intentionally external.

## Requirements

- Docker & Docker Compose v2+
- 8+ GB RAM (16 GB recommended)
- 250+ GB SSD storage
- macOS: OrbStack recommended over Docker Desktop
