#!/bin/bash
# /Users/radiant/Desktop/RXinDexer/clean_and_rebuild.sh
# Script to completely clean and rebuild the RXinDexer stack

echo "=== Starting clean and rebuild process ==="

# Stop all containers
echo "Stopping all containers..."
docker stop rxindexer-indexer rxindexer-radiant rxindexer-db rxindexer-api 2>/dev/null || true

# Remove all containers
echo "Removing all containers..."
docker rm rxindexer-indexer rxindexer-radiant rxindexer-db rxindexer-api 2>/dev/null || true

# Apply our fixes to the source code
echo "Applying code fixes..."

# 1. Fix block parser timestamp bug
sed -i '' 's/timestamp\": tx.get(\"time\", 0) or block.get(\"time\", 0)/timestamp\": tx.get(\"time\", 0)/g' src/parser/block_parser.py || true

# 2. Fix parallel processor to use single worker
sed -i '' 's/def __init__(self, max_workers: int = 8):/def __init__(self, max_workers: int = 1):/g' src/sync/parallel_processor.py || true
sed -i '' 's/actual_workers = min(self.max_workers, max_concurrent_tasks)/actual_workers = 1/g' src/sync/parallel_processor.py || true

# Backup and create optimized environment file
echo "Creating optimized environment file..."
cp .env .env.backup || true
cat > .env << 'EOL'
# RXinDexer Environment Configuration
POSTGRES_DB=rxindexer
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres

# RPC Configuration
RADIANT_RPC_USER=rpc
RADIANT_RPC_PASSWORD=rpcpassword
RADIANT_RPC_HOST=rxindexer-radiant
RADIANT_RPC_PORT=7332
RADIANT_RPC_TIMEOUT=120

# Connection Pool Optimization
RPC_POOL_SIZE=1
RPC_MIN_REQUEST_INTERVAL=3.0
RPC_THROTTLE_FACTOR=2.0
CIRCUIT_RESET_TIMEOUT=60
RPC_MAX_RETRIES=30

# API Configuration
API_HOST=0.0.0.0
API_PORT=8000

# Database Configuration
DATABASE_HOST=rxindexer-db
DATABASE_PORT=5432
DATABASE_NAME=rxindexer
DATABASE_USER=postgres
DATABASE_PASSWORD=postgres

# Indexer Configuration
START_INDEXER=true
ENABLE_PARALLEL=false
BATCH_SIZE=50
MAX_WORKERS=1
EOL

# Rebuild images
echo "Rebuilding docker images..."
docker-compose build --no-cache

# Start containers
echo "Starting containers..."
docker-compose up -d

echo "=== Clean and rebuild completed ==="
echo "Waiting 10 seconds for containers to initialize..."
sleep 10

# Check container status
echo "=== Container Status ==="
docker ps -a | grep rxindexer

# Check logs
echo -e "\n=== Indexer Logs ==="
docker logs rxindexer-indexer --tail 20

echo -e "\n=== API Logs ==="
docker logs rxindexer-api --tail 10

echo -e "\n=== Clean rebuild complete ==="
echo "To check API endpoints, try:"
echo "  curl http://localhost:8000/api/v1/sync/status"
echo "  curl http://localhost:8000/api/v1/blocks/latest"
echo -e "\nTo monitor logs:"
echo "  docker logs rxindexer-indexer -f"
