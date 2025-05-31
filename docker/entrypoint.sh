#!/bin/bash
# /Users/radiant/Desktop/RXinDexer/docker/entrypoint.sh
# This script serves as the entrypoint for the Docker container.
# It performs initialization tasks before starting the main application.

# Exit on error, but allow for custom error handling
set -e

# Enable debug mode if requested
if [ "$DEBUG" = "true" ]; then
  set -x
fi

# Print startup banner
echo "==============================="
echo "RXinDexer Container Starting"
echo "==============================="
echo "Environment: $([ "$PRODUCTION" = "true" ] && echo "Production" || echo "Development")"
echo "$(date)"
echo

# Function for graceful shutdown
cleanup() {
  echo "Container stopping, performing cleanup..."
  # Add any cleanup tasks here
  exit 0
}

# Trap SIGTERM and SIGINT
trap cleanup SIGTERM SIGINT

# Set up database configuration with fallbacks
if [ -z "$DATABASE_URL" ]; then
  echo "Configuring database connection parameters..."
  export DB_HOST=${DB_HOST:-db}
  export DB_USER=${DB_USER:-postgres}
  export DB_PASSWORD=${DB_PASSWORD:-postgres}
  export DB_PORT=${DB_PORT:-5432}
  export DB_NAME=${DB_NAME:-rxindexer}
  export DATABASE_URL="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
  echo "Using constructed DATABASE_URL: $DATABASE_URL"
fi

# Ensure proper environment flag is set
export IN_DOCKER=true

# Parse database URL properly
parse_db_url() {
  if [[ "$DATABASE_URL" == postgresql://* ]]; then
    # Extract host and port using regex
    POSTGRES_HOST=$(echo $DATABASE_URL | sed -E 's/^postgresql:\/\/([^:]+):([^@]+)@([^:]+):([0-9]+)\/(.+)$/\3/')
    POSTGRES_PORT=$(echo $DATABASE_URL | sed -E 's/^postgresql:\/\/([^:]+):([^@]+)@([^:]+):([0-9]+)\/(.+)$/\4/')
    POSTGRES_USER=$(echo $DATABASE_URL | sed -E 's/^postgresql:\/\/([^:]+):([^@]+)@([^:]+):([0-9]+)\/(.+)$/\1/')
    POSTGRES_DB=$(echo $DATABASE_URL | sed -E 's/^postgresql:\/\/([^:]+):([^@]+)@([^:]+):([0-9]+)\/(.+)$/\5/')
    echo "Parsed PostgreSQL connection: Host=$POSTGRES_HOST, Port=$POSTGRES_PORT, User=$POSTGRES_USER, DB=$POSTGRES_DB"
    return 0
  elif [[ "$DATABASE_URL" == sqlite://* ]]; then
    echo "Using SQLite database: $DATABASE_URL"
    # No need to wait for SQLite
    return 1
  else
    echo "Unknown database type in URL: $DATABASE_URL"
    return 1
  fi
}

# Wait for database to be ready with timeout
wait_for_db() {
  local retries=${DB_CONNECT_RETRIES:-30}
  local wait_time=${DB_CONNECT_WAIT:-2}
  local attempt=1
  
  echo "Waiting for PostgreSQL to be ready... (max $retries attempts)"
  
  while [ $attempt -le $retries ]; do
    if pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER"; then
      echo "$POSTGRES_HOST:$POSTGRES_PORT - accepting connections"
      echo "PostgreSQL is up - continuing"
      return 0
    fi
    
    echo "$POSTGRES_HOST:$POSTGRES_PORT - no response"
    echo "PostgreSQL is unavailable - sleeping"
    sleep $wait_time
    attempt=$((attempt+1))
  done
  
  echo "ERROR: Failed to connect to PostgreSQL after $retries attempts"
  echo "WARNING: Continuing without database connection - application may fail!"
  return 1
}

# Check database connectivity
if [ -n "$DATABASE_URL" ]; then
  if parse_db_url; then
    wait_for_db
  fi
fi

# Apply database migrations and initialization with error handling
if [ "$INIT_DB" = "true" ]; then
  echo "Initializing database..."
  if python -m src.db.init_db; then
    echo "Database initialization completed successfully"
  else
    echo "WARNING: Database initialization encountered errors"
    # Don't exit - allow application to handle this gracefully
  fi
  
  # Apply API initialization if in API context
  if [ "$IN_API" = "true" ]; then
    echo "API initialization complete - error filtering applied"
  fi
fi

# Check if we need to run the indexer
if [ "$START_INDEXER" = "true" ]; then
  echo "Starting indexer in the background..."
  # Run with proper error handling and logging
  python -m src.indexer --sync-only >> /app/logs/indexer.log 2>&1 &
  INDEXER_PID=$!
  echo "Indexer started with PID: $INDEXER_PID"
  
  # Write PID to file for monitoring
  echo $INDEXER_PID > /app/indexer.pid
fi

# Ensure log directory exists and is writable
mkdir -p /app/logs
touch /app/logs/app.log
echo "Log directory ready: /app/logs"

# Print final startup message
echo "Starting RXinDexer with the fixed entry point"
echo "==============================="

# Force the correct module path regardless of what was passed in
echo "Using direct entry point: python docker-entry.py"
cd /app
exec python docker-entry.py

# This will only execute if the exec fails
echo "ERROR: Failed to execute the entry point"
exit 1
