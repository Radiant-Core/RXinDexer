#!/bin/bash
# Clean Database Rebuild Script for RXinDexer
# This script drops and recreates the database with the new schema
# aligned to the reference rxd-glyph-explorer implementation.
# Uses init.sql directly (no Alembic migrations needed).

set -e

echo "=============================================="
echo "RXinDexer Clean Database Rebuild"
echo "=============================================="
echo ""
echo "This will:"
echo "  1. Stop any running indexer"
echo "  2. Drop the existing database"
echo "  3. Create a fresh database"
echo "  4. Run init.sql to create all tables"
echo ""
echo "WARNING: All existing indexed data will be LOST!"
echo ""
read -p "Are you sure you want to continue? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 1
fi

# Change to project directory
cd "$(dirname "$0")/.."
PROJECT_DIR=$(pwd)

echo ""
echo "[1/4] Stopping any running indexer..."
docker-compose -f docker/docker-compose.yml stop indexer 2>/dev/null || true

echo ""
echo "[2/4] Dropping all tables in existing database..."
# Since we can't drop/recreate the database easily with the rxindexer user,
# we'll drop all tables and recreate them
docker-compose -f docker/docker-compose.yml exec -T db psql -U rxindexer -d rxindexer -c "
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO rxindexer;
GRANT ALL ON SCHEMA public TO public;
" || {
    echo "Note: Could not drop schema (DB may not be running)"
    echo "Starting database container..."
    docker-compose -f docker/docker-compose.yml up -d db
    sleep 8
    docker-compose -f docker/docker-compose.yml exec -T db psql -U rxindexer -d rxindexer -c "
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO rxindexer;
GRANT ALL ON SCHEMA public TO public;
"
}

echo ""
echo "[3/4] Database schema cleared..."

echo ""
echo "[4/4] Running init.sql to create schema..."
cat "$PROJECT_DIR/database/init.sql" | docker-compose -f docker/docker-compose.yml exec -T db psql -U rxindexer -d rxindexer

echo ""
echo "=============================================="
echo "Database rebuild complete!"
echo "=============================================="
echo ""
echo "New tables created:"
echo "  - glyphs (unified token table - PRIMARY)"
echo "  - glyph_actions (action tracking)"
echo "  - contract_groups (DMINT groups)"
echo "  - contracts (DMINT contracts)"
echo "  - contract_list (contract lookup)"
echo "  - stats (global statistics)"
echo "  - glyph_likes (user engagement)"
echo "  - import_state (sync tracking)"
echo ""
echo "Legacy tables (backward compat):"
echo "  - glyph_tokens, nfts, token_holders, etc."
echo ""
echo "To start syncing from genesis:"
echo "  docker-compose -f docker/docker-compose.yml up -d"
echo ""
echo "Or run the indexer manually:"
echo "  python -m indexer.sync"
echo ""
