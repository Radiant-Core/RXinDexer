#!/bin/sh
set -e

echo "=== RXINDEXER API ENTRYPOINT ==="

# Database connection settings
DB_HOST="${POSTGRES_HOST:-db}"
DB_USER="${POSTGRES_USER:-rxindexer}"
DB_NAME="${POSTGRES_DB:-rxindexer}"
DB_PASS="${POSTGRES_PASSWORD:-rxindexerpass}"
export PGPASSWORD="$DB_PASS"

# Wait for database to be ready
echo "[entrypoint] Waiting for database at $DB_HOST..."
MAX_DB_WAIT=60
DB_WAIT=0
while ! pg_isready -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" > /dev/null 2>&1; do
  sleep 2
  DB_WAIT=$((DB_WAIT + 2))
  if [ $DB_WAIT -ge $MAX_DB_WAIT ]; then
    echo "[entrypoint][ERROR] Database not ready after ${MAX_DB_WAIT}s"
    exit 1
  fi
  echo "[entrypoint] Waiting for database... (${DB_WAIT}s)"
done
echo "[entrypoint] Database is ready!"

# Run Alembic migrations with retries
cd /app
MAX_ATTEMPTS=10
ATTEMPT=1
RETRY_DELAY=3

echo "[entrypoint] Running Alembic migrations..."
until alembic upgrade head; do
  if [ $ATTEMPT -ge $MAX_ATTEMPTS ]; then
    echo "[entrypoint][ERROR] Alembic migration failed after $MAX_ATTEMPTS attempts."
    exit 1
  fi
  echo "[entrypoint][WARN] Migration attempt $ATTEMPT failed. Retrying in ${RETRY_DELAY}s..."
  ATTEMPT=$((ATTEMPT + 1))
  sleep $RETRY_DELAY
done
echo "[entrypoint] Alembic migrations completed successfully."

# Start the API
echo "[entrypoint] Starting API server..."
exec uvicorn api.main:app --host 0.0.0.0 --port 8000
