#!/bin/bash
# /Users/radiant/Desktop/RXinDexer/docker/entrypoint.sh
# This script serves as the entrypoint for the Docker container.
# It performs initialization tasks before starting the main application.

set -e

# Force database settings for Docker environment
export DB_HOST=db
export DB_USER=postgres
export DB_PASSWORD=postgres
export DB_PORT=5432
export DB_NAME=rxindexer
export DATABASE_URL="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"

# Wait for PostgreSQL to be ready
if [ -n "$DATABASE_URL" ]; then
  echo "Waiting for PostgreSQL to be ready..."
  
  POSTGRES_HOST=$(echo $DATABASE_URL | sed -e 's/^.*@//' -e 's/:.*//')
  POSTGRES_PORT=$(echo $DATABASE_URL | sed -e 's/^.*://' -e 's/\/.*//')
  
  until pg_isready -h $POSTGRES_HOST -p $POSTGRES_PORT -U postgres; do
    echo "PostgreSQL is unavailable - sleeping"
    sleep 2
  done
  
  echo "PostgreSQL is up - continuing"
fi

# Initialize database if requested
if [ "$INIT_DB" = "true" ]; then
  echo "Initializing database..."
  python -m src.db.init_db
fi

# Start the indexer in the background if requested
if [ "$START_INDEXER" = "true" ]; then
  echo "Starting indexer in the background..."
  python -m src.indexer --sync-only &
fi

# Execute the command passed to docker run
exec "$@"
