#!/bin/sh
set -e

echo "=== RXINDEXER API WAIT_FOR_SCHEMA START ==="

# Wait for DB to accept connections
DB_HOST="db"
DB_USER="rxindexer"
DB_NAME="rxindexer"
DB_PASS="dsUEZPX1mqwPhRlicEGbjhERjioXqgdcvoEKCZMkwLc="

export PGPASSWORD="$DB_PASS"
echo "[wait_for_schema] Waiting for DB to accept connections..."
while ! pg_isready -h $DB_HOST -U $DB_USER -d $DB_NAME > /dev/null 2>&1; do
  sleep 2
done

echo "[wait_for_schema] Database connection established. Running Alembic migrations..."

# Run Alembic migrations to create schema
cd /app
alembic upgrade head

# Verify the blocks table exists
echo "[wait_for_schema] Verifying 'blocks' table exists..."
WAITED_SECONDS=0
MAX_WAIT_SECONDS=60 # Lower wait time since we just ran migrations
while : ; do
  TABLE_EXISTS=$(psql -h $DB_HOST -U $DB_USER -d $DB_NAME -tAc "SELECT to_regclass('public.blocks') IS NOT NULL;" | tr -d '\r')
  if [ "$TABLE_EXISTS" = "t" ]; then
    echo "[wait_for_schema] 'blocks' table found. Proceeding to start API."
    break
  fi
  sleep 2
  WAITED_SECONDS=$((WAITED_SECONDS+2))
  if [ $WAITED_SECONDS -ge $MAX_WAIT_SECONDS ]; then
    echo "[wait_for_schema] ERROR: 'blocks' table not found after running migrations. Something is wrong with the Alembic setup."
    exit 1
  fi
  if [ $((WAITED_SECONDS % 10)) -eq 0 ]; then
    echo "[wait_for_schema] Still waiting for 'blocks' table after $WAITED_SECONDS seconds..."
  fi
done

# Start the API service
echo "[wait_for_schema] Schema verified. Starting API service..."
exec "$@"
