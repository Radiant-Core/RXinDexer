#!/bin/bash
# /Users/radiant/Desktop/RXinDexer/scripts/deploy.sh
# This script automates the deployment of the RXinDexer application to a production environment.
# It handles environment setup, container builds, and initial sync process.

set -e

# Parse arguments
ENVIRONMENT=${1:-production}
CONFIG_FILE="./deploy/config.$ENVIRONMENT.env"
SKIP_BUILD=${SKIP_BUILD:-false}
SKIP_RADIANT=${SKIP_RADIANT:-false}
FORCE=${FORCE:-false}

# Check if config file exists
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Error: Configuration file $CONFIG_FILE not found!"
    echo "Usage: ./scripts/deploy.sh [environment]"
    echo "Available environments: production, staging, development"
    exit 1
fi

# Load configuration
source "$CONFIG_FILE"
echo "Deploying RXinDexer to $ENVIRONMENT environment..."

# Create necessary directories
mkdir -p ./logs
mkdir -p ./data/postgres
mkdir -p ./data/redis
mkdir -p ./data/radiant

# Generate .env file from template
echo "Generating environment configuration..."
envsubst < "./deploy/env.template" > "./.env"
echo "Configuration generated in .env"

# Build Docker images if needed
if [ "$SKIP_BUILD" != "true" ]; then
    echo "Building Docker images..."
    docker-compose build
fi

# Start database and Redis services first
echo "Starting database and Redis services..."
docker-compose up -d db redis

# Wait for database to be ready
echo "Waiting for database to be ready..."
sleep 10

# Start Radiant Node if not skipped
if [ "$SKIP_RADIANT" != "true" ]; then
    echo "Starting Radiant Node (this may take a while to sync)..."
    docker-compose up -d radiant
    
    # Wait for Radiant Node to start
    echo "Waiting for Radiant Node to start..."
    sleep 20
fi

# Initialize database
echo "Initializing database schema..."
docker-compose run --rm rxindexer-api python -m src.db.init_db

# Start indexer and API services
echo "Starting RXinDexer services..."
docker-compose up -d rxindexer-api rxindexer-indexer

# Check if services are running
echo "Checking if services are running..."
sleep 5
docker-compose ps

echo "Deployment completed successfully!"
echo "API is available at: http://localhost:${API_PORT:-8000}"
echo "To monitor the application, use: python scripts/monitor.py"
echo "To view logs: docker-compose logs -f [service]"

# Final instructions
echo ""
echo "NOTE: Radiant Node will take time to sync the blockchain."
echo "You can monitor the sync progress with: python scripts/monitor.py --continuous"
