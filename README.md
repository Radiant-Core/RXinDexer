# /Users/radiant/Desktop/RXinDexer/README.md
# This file provides project documentation and setup instructions for RXinDexer.
# This is NOT an implementation file but a documentation resource for developers.

# RXinDexer

A lightweight, scalable indexer for the Radiant (RXD) blockchain with support for Glyph tokens, NFTs, user profiles, and containers.

## Overview

RXinDexer provides:
- Indexing of RXD transactions and balances using Radiant's UTXO model
- Tracking of Glyph tokens (fungible, non-fungible, dmint) via CBOR-encoded payloads
- Comprehensive NFT metadata indexing and collection tracking
- User profile and container relationship management
- Counting unique wallet holders for RXD and Glyph tokens
- High-performance REST APIs with robust caching for querying:
  - Balances and transaction history
  - Token metadata and holder counts
  - NFT collections and transfers
  - User profiles and containers

## Technical Stack

- Radiant Node: Version 1.2.0
- Database: PostgreSQL 16
- Caching: Redis 7 (with in-memory fallback for development)
- Language: Python 3.11
- Key Libraries:
  - python-bitcoinrpc (v1.0)
  - cbor2 (v5.4.6)
  - fastapi (v0.115.0)
  - sqlalchemy (v2.0.35)
  - redis-py (v5.0.8)
  - pydantic (v2.5.2)
- Tools: Docker (v27.0), pytest (v8.3.3)

## Getting Started

### Prerequisites

- Python 3.11+
- PostgreSQL 16
- Redis 7
- Radiant Node 1.2.0

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/Radiant-Core/RXinDexer.git
   cd RXinDexer
   ```

2. Set up a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Configure environment:
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

5. Set up database:
   ```bash
   python -m src.db.init_db
   ```

### Running

1. Start the API server:
   ```bash
   uvicorn src.main:app --reload
   ```

2. Run the indexer:
   ```bash
   python -m src.indexer
   ```

## Docker Deployment

RXinDexer includes a complete Docker environment with all necessary components:

### Quick Start

```bash
# Build and start all services
docker-compose up -d

# View logs from the indexer service
docker-compose logs --tail=100 rxindexer-indexer

# View logs from the API service
docker-compose logs rxindexer-api

# Rebuild a specific service after code changes
docker-compose stop rxindexer-api
docker-compose build rxindexer-api
docker-compose up -d rxindexer-api
```

### Full Rebuild & Fresh Start

```bash
# Stop all containers and remove volumes (for a clean restart)
docker-compose down -v

# Rebuild all images
docker-compose build

# Start all services
docker-compose up -d
```

### Consolidated Sync Script

The new consolidated sync script (`sync/rxindex_sync.py`) combines all indexing functionality into a single, efficient process. This script handles:

- Database initialization and schema verification
- Block synchronization with configurable batch sizes
- Parallel block processing for improved performance
- UTXO tracking and token balance updates
- Automatic recovery from node disconnections
- Detailed logging of the sync process

```bash
# Run the consolidated sync script directly
python sync/rxindex_sync.py --initialize --continuous --interval 60

# Options:
# --initialize: Set up database tables if they don't exist
# --continuous: Keep running and checking for new blocks
# --interval N: Wait N seconds between sync checks
# --batch-size N: Process N blocks at a time
# --max-workers N: Use N parallel workers for block processing
```

In Docker, the sync script is run automatically by the rxindexer-indexer service with optimal settings.

### Docker Components

1. **rxindexer-api**: FastAPI service exposing the API endpoints (port 8000)
2. **rxindexer-indexer**: Background indexer with consolidated sync script (uses rxindex_sync.py)
3. **rxindexer-db**: PostgreSQL database for storing indexed data (port 5432)
4. **rxindexer-redis**: Redis instance for caching (port 6379)
5. **rxindexer-radiant**: Radiant blockchain node (port 7332)

### Consolidated Sync Script

The project uses a single consolidated sync script (`sync/rxindex_sync.py`) that combines the best features from all previous sync implementations:

- **Robust Error Handling**: Gracefully handles connection issues and schema differences
- **Parallel Processing**: Uses worker threads for faster block synchronization
- **Schema Compatibility**: Automatically adapts to existing database schema
- **Performance Optimizations**: Bloom filters for fast transaction lookups
- **Transaction Safety**: Isolated connections to prevent transaction conflicts
- **Timestamp Standardization**: Consistent timestamp handling across all tables

### Environment Variables

Adjust these in the `.env` file or directly in `docker-compose.yml`:

```
DATABASE_URL=postgresql://postgres:postgres@db:5432/rxindexer
RADIANT_RPC_URL=http://radiant:7332
RADIANT_RPC_USER=rxin
RADIANT_RPC_PASSWORD=securepassword
REDIS_HOST=redis
REDIS_PORT=6379
LOG_LEVEL=INFO
```

## API Documentation

Once running, API documentation is available at:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### Available Endpoints

#### NFT Endpoints (v1)
- `GET /api/v1/nfts` - List all NFTs with pagination and filtering
- `GET /api/v1/nfts/{token_id}` - Get detailed information about a specific NFT
- `GET /api/v1/nfts/collections` - List all NFT collections
- `GET /api/v1/nfts/collection/{collection_id}` - Get a specific collection with its NFTs
- `GET /api/v1/nfts/transfers` - List NFT transfer history with filtering

#### User and Container Endpoints (v1)
- `GET /api/v1/users` - List all user profiles with pagination
- `GET /api/v1/users/{user_id}` - Get detailed information about a specific user
- `GET /api/v1/containers` - List all containers with pagination
- `GET /api/v1/containers/{container_id}` - Get a specific container with its contents
- `GET /api/v1/users/{user_id}/containers` - List containers owned by a specific user

#### Core Blockchain Endpoints
- `GET /api/address/{address}` - Get address balance and transaction count
- `GET /api/token/{token_ref}` - Get token metadata and balance
- `GET /api/holder/{address}` - Get tokens held by an address
- `GET /api/transaction/{txid}` - Get transaction details

## Caching System

RXinDexer implements a robust caching system for API performance optimization:

### Cache Implementation
- **Redis Cache**: Primary caching mechanism in production and Docker environments
- **In-Memory Fallback**: Automatic fallback for development without Redis
- **Cache Decorator**: Simple application to any API endpoint using `@cache_decorator(ttl=CACHE_TTL)`

### Cache Configuration
- Default TTL: 60 seconds (configurable per endpoint)
- Redis connection: Configured via environment variables (`REDIS_HOST`, `REDIS_PORT`)
- Cache keys: Generated based on function name and arguments

### Example Usage
```python
from src.utils.cache import cache_decorator

@router.get("/some/endpoint")
@cache_decorator(ttl=300)  # Cache for 5 minutes
async def get_some_data():
    # Expensive database operation here
    return result
```

## Development

### Project Structure

```
RXinDexer/
├── src/                  # Source code
│   ├── sync/             # Blockchain sync module
│   ├── parser/           # Transaction & token parser
│   ├── api/              # FastAPI endpoints
│   ├── models/           # SQLAlchemy models
│   └── utils/            # Utility functions
├── tests/                # Test suite
├── docker/               # Docker configuration
├── alembic/              # Database migrations
└── docs/                 # Documentation
```

### Testing

```bash
pytest
```

## License

[MIT License](LICENSE)

## References

- [Glyph Protocol Tech Guide](https://github.com/Radiant-Core/Glyph-Protocol-Tech-Guide)
- [Radiant Node](https://github.com/Radiant-Core/Radiant-Node)
