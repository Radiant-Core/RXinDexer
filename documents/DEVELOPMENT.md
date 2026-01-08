# RXinDexer Development Guide

## Architecture
- **Indexer**: `indexer/` - Python-based blockchain sync engine.
- **API**: `api/` - FastAPI application serving data.
- **Database**: `database/` - SQLAlchemy ORM models and migration scripts.
- **Tests**: `tests/` - Pytest suite (Unit & Integration).

## Workflow
1. **Setup**:
   ```bash
   # Install dependencies
   pip install -r api/requirements.txt
   pip install -r indexer/requirements.txt
   ```
2. **Run Local**:
   ```bash
   docker compose -f docker/docker-compose.yml up -d
   ```
3. **Testing**:
   ```bash
   # Run all tests
   python -m pytest tests/
   
   # Run unit tests only
   python -m pytest tests/unit/
   ```

## Code Quality
- **Structure**: Keep files focused. Split large files (>1000 lines) into modules.
- **Style**: Follow PEP 8. Use type hints.
- **Documentation**: Update `API.md` when modifying endpoints.

## API Development
- **Endpoints**: Located in `api/endpoints/`.
- **Schemas**: Pydantic models in `api/schemas.py`.
- **OpenAPI**: Auto-generated at `/docs`.

## Radiant Node

RXinDexer uses the official **radiant-core** node software:
- **Repository**: https://github.com/Radiant-Core/Radiant-Core
- **Dockerfile**: `docker/radiant-node.Dockerfile`

The node is built from source during `docker compose build`. To use a specific version:
```bash
# Build with a specific tag/branch
docker build -f docker/radiant-node.Dockerfile \
  --build-arg RADIANT_NODE_REF=v1.2.0 \
  -t radiant-node-local .
```

## Troubleshooting
- **Sync Issues**: Check `indexer/sync.py` logs.
- **DB Locks**: Check for long-running transactions if "Spent Check" stalls.
- **Memory**: If OOM occurs, adjust `postgres-tuning.conf` settings (currently optimized for 10GB limit).
