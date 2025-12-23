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

## Quick Start

```bash
cd docker
docker compose up -d
```

Once running:
- **API Docs**: http://localhost:8000/docs
- **Health Check**: http://localhost:8000/health
- **Explorer**: Optional and external to this repository (run separately)

## Documentation

| Document | Description |
|----------|-------------|
| [Deployment Guide](DEPLOYMENT.md) | Production setup, monitoring, maintenance |
| [API Reference](API.md) | REST API endpoints |
| [Development Guide](DEVELOPMENT.md) | Architecture and contribution guidelines |
| [Project Progress](PROJECT_PROGRESS.md) | Status and changelog |

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

## Features

- **Partitioned Tables**: Blocks, transactions, and UTXOs are partitioned for scalability
- **Glyph Token Support**: Full indexing of Glyph protocol tokens with Photonic Wallet compatibility
- **Wallet Balance Cache**: Pre-computed balances for instant rich list queries
- **Connection Pooling**: Production-ready database connection management
- **Automated Maintenance**: Weekly cleanup of Docker artifacts

## Requirements

- Docker & Docker Compose v2+
- 8+ GB RAM (16 GB recommended)
- 200+ GB SSD storage
- macOS: OrbStack recommended over Docker Desktop
