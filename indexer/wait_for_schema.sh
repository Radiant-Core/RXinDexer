#!/bin/sh
set -e

echo "=== RXINDEXER INDEXER ENTRYPOINT ===" 

# Database connection settings
DB_HOST="${POSTGRES_HOST:-db}"
DB_USER="${POSTGRES_USER:-rxindexer}"
DB_NAME="${POSTGRES_DB:-rxindexer}"
DB_PASS="${POSTGRES_PASSWORD:-rxindexerpass}"
export PGPASSWORD="$DB_PASS"

# Wait for DB to accept connections
echo "[indexer] Waiting for database at $DB_HOST..."
MAX_DB_WAIT=120
DB_WAIT=0
while ! pg_isready -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" > /dev/null 2>&1; do
  sleep 2
  DB_WAIT=$((DB_WAIT + 2))
  if [ $DB_WAIT -ge $MAX_DB_WAIT ]; then
    echo "[indexer][ERROR] Database not ready after ${MAX_DB_WAIT}s"
    exit 1
  fi
  echo "[indexer] Waiting for database... (${DB_WAIT}s)"
done
echo "[indexer] Database connection established."

# Wait for schema to be created by API (which runs Alembic)
echo "[indexer] Waiting for 'blocks' table (created by API migrations)..."
MAX_SCHEMA_WAIT=120
SCHEMA_WAIT=0
while : ; do
  TABLE_EXISTS=$(psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT to_regclass('public.blocks') IS NOT NULL;" 2>/dev/null | tr -d '\r' || echo "f")
  if [ "$TABLE_EXISTS" = "t" ]; then
    echo "[indexer] Schema ready. Starting indexer daemon."
    break
  fi
  sleep 3
  SCHEMA_WAIT=$((SCHEMA_WAIT + 3))
  if [ $SCHEMA_WAIT -ge $MAX_SCHEMA_WAIT ]; then
    echo "[indexer][ERROR] Schema not ready after ${MAX_SCHEMA_WAIT}s. Is the API running?"
    exit 1
  fi
  if [ $((SCHEMA_WAIT % 15)) -eq 0 ]; then
    echo "[indexer] Still waiting for schema... (${SCHEMA_WAIT}s)"
  fi
done

# Execute the command passed to the container
# If no command is passed, run the indexer daemon
if [ $# -eq 0 ]; then
  # Run the indexer daemon with retry logic
  RETRY_DELAY=10
  while true; do
    echo "[indexer] Starting daemon..."
    python -m indexer.daemon
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 0 ]; then
      echo "[indexer] Daemon exited cleanly."
      break
    fi
    echo "[indexer] Daemon exited with code $EXIT_CODE. Retrying in ${RETRY_DELAY}s..."
    sleep $RETRY_DELAY
  done
else
  # Run the command passed to the container
  echo "[indexer] Running custom command: $@"
  exec "$@"
fi
