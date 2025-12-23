#!/bin/sh
set -e

echo "=== RXINDEXER ENTRYPOINT.SH START ==="

echo "Entrypoint running as: $(whoami)"
echo "PWD: $(pwd)"
echo "Listing /app:"
ls -l /app
echo "Listing /app/indexer:"
ls -l /app/indexer
echo "Running Alembic migrations..."
alembic upgrade head

# About to run the indexer daemon

echo "About to run python -m indexer.daemon"

RETRY_DELAY=10
while true; do
  python -m indexer.daemon
  EXIT_CODE=$?
  if [ $EXIT_CODE -eq 0 ]; then
    echo "Python exited successfully. Not retrying."
    break
  fi
  echo "Python exited with code $EXIT_CODE. Retrying in $RETRY_DELAY seconds..."
  sleep $RETRY_DELAY
done
