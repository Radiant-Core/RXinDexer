#!/bin/bash
# PostgreSQL restore script with point-in-time recovery support

set -euo pipefail

# Configuration
DB_HOST="${DB_HOST:-postgres}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-rxindexer}"
DB_USER="${DB_USER:-postgres}"
DB_PASSWORD="${DB_PASSWORD:-password}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
S3_BUCKET="${S3_BUCKET:-}"

# Function to show usage
usage() {
    echo "Usage: $0 [backup_file] [--target-time 'YYYY-MM-DD HH:MI:SS'] [--download-from-s3]"
    echo "Examples:"
    echo "  $0 /backups/20231201_120000.tar.gz"
    echo "  $0 --download-from-s3 --target-time '2023-12-01 12:30:00'"
    exit 1
}

# Parse arguments
BACKUP_FILE=""
TARGET_TIME=""
DOWNLOAD_FROM_S3=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --target-time)
            TARGET_TIME="$2"
            shift 2
            ;;
        --download-from-s3)
            DOWNLOAD_FROM_S3=true
            shift
            ;;
        *.tar.gz)
            BACKUP_FILE="$1"
            shift
            ;;
        *)
            usage
            ;;
    esac
done

# If downloading from S3, find the latest backup
if [ "$DOWNLOAD_FROM_S3" = true ]; then
    if [ -z "$S3_BUCKET" ]; then
        echo "Error: S3_BUCKET not configured"
        exit 1
    fi
    
    echo "Finding latest backup in S3..."
    LATEST_BACKUP=$(aws s3 ls "s3://$S3_BUCKET/postgres-backups/" --recursive | sort | tail -n 1 | awk '{print $4}')
    
    if [ -z "$LATEST_BACKUP" ]; then
        echo "Error: No backups found in S3"
        exit 1
    fi
    
    BACKUP_FILE="/tmp/$(basename $LATEST_BACKUP)"
    echo "Downloading $LATEST_BACKUP..."
    aws s3 cp "s3://$S3_BUCKET/postgres-backups/$LATEST_BACKUP" "$BACKUP_FILE"
fi

if [ -z "$BACKUP_FILE" ] || [ ! -f "$BACKUP_FILE" ]; then
    echo "Error: Backup file not found: $BACKUP_FILE"
    usage
fi

echo "Starting PostgreSQL restore from $BACKUP_FILE"
echo "Target time: ${TARGET_TIME:-'latest'}"

# Extract backup
TEMP_DIR="/tmp/restore_$(date +%s)"
mkdir -p "$TEMP_DIR"
echo "Extracting backup..."
tar -xzf "$BACKUP_FILE" -C "$TEMP_DIR"

BACKUP_DATE=$(basename "$BACKUP_FILE" .tar.gz)
EXTRACTED_DIR="$TEMP_DIR/$BACKUP_DATE"

if [ ! -d "$EXTRACTED_DIR" ]; then
    echo "Error: Invalid backup format"
    rm -rf "$TEMP_DIR"
    exit 1
fi

# Stop PostgreSQL
echo "Stopping PostgreSQL..."
docker-compose -f docker/docker-compose.yml stop postgres

# Clear existing data
echo "Clearing existing data..."
docker exec rxindexer-postgres bash -c "rm -rf /var/lib/postgresql/data/*"

# Restore base backup
echo "Restoring base backup..."
docker cp "$EXTRACTED_DIR/base/." rxindexer-postgres:/var/lib/postgresql/data/

# Restore configuration
echo "Restoring configuration..."
docker cp "$EXTRACTED_DIR/postgresql.conf" rxindexer-postgres:/var/lib/postgresql/data/
docker cp "$EXTRACTED_DIR/pg_hba.conf" rxindexer-postgres:/var/lib/postgresql/data/

# Create recovery signal file for PostgreSQL 12+
echo "Creating recovery signal..."
docker exec rxindexer-postgres touch /var/lib/postgresql/data/recovery.signal

# Configure recovery if target time specified
if [ -n "$TARGET_TIME" ]; then
    echo "Configuring point-in-time recovery to: $TARGET_TIME"
    cat > "$TEMP_DIR/recovery.conf" << EOF
restore_command = 'cp $BACKUP_DIR/wal/%f %p'
recovery_target_time = '$TARGET_TIME'
EOF
    docker cp "$TEMP_DIR/recovery.conf" rxindexer-postgres:/var/lib/postgresql/data/
fi

# Set proper permissions
echo "Setting permissions..."
docker exec rxindexer-postgres chown -R postgres:postgres /var/lib/postgresql/data/

# Start PostgreSQL
echo "Starting PostgreSQL..."
docker-compose -f docker/docker-compose.yml start postgres

# Wait for PostgreSQL to be ready
echo "Waiting for PostgreSQL to start..."
sleep 10

# Check if recovery is in progress
for i in {1..30}; do
    if docker exec rxindexer-postgres pg_isready -U "$DB_USER" >/dev/null 2>&1; then
        echo "PostgreSQL is ready"
        break
    fi
    echo "Waiting... ($i/30)"
    sleep 5
done

# Verify restore
echo "Verifying restore..."
docker exec rxindexer-postgres psql -U "$DB_USER" -d "$DB_NAME" -c "SELECT COUNT(*) FROM blocks;" >/dev/null 2>&1 || {
    echo "Error: Database verification failed"
    exit 1
}

# Cleanup
rm -rf "$TEMP_DIR"

echo "Restore completed successfully!"
if [ -n "$TARGET_TIME" ]; then
    echo "Database restored to point-in-time: $TARGET_TIME"
else
    echo "Database restored to latest state"
fi

# Show recovery status if available
echo "Checking recovery status..."
docker exec rxindexer-postgres psql -U "$DB_USER" -d postgres -c "SELECT pg_is_in_recovery();" 2>/dev/null || echo "Could not determine recovery status"
