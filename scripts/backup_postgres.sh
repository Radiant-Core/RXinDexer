#!/bin/bash
# PostgreSQL backup script with point-in-time recovery support

set -euo pipefail

# Configuration
DB_HOST="${DB_HOST:-postgres}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-rxindexer}"
DB_USER="${DB_USER:-postgres}"
DB_PASSWORD="${DB_PASSWORD:-password}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
S3_BUCKET="${S3_BUCKET:-}"  # Optional: S3 bucket for offsite storage
WAL_DIR="${WAL_DIR:-/var/lib/postgresql/wal}"

# Create backup directory with date
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="$BACKUP_DIR/$DATE"
mkdir -p "$BACKUP_PATH"

echo "Starting PostgreSQL backup for $DB_NAME at $(date)"

# Function to cleanup on exit
cleanup() {
    if [ $? -ne 0 ]; then
        echo "Backup failed, cleaning up..."
        rm -rf "$BACKUP_PATH"
    fi
}
trap cleanup EXIT

# 1. Create a full backup using pg_basebackup
echo "Creating base backup..."
pg_basebackup -h "$DB_HOST" -p "$DB_PORT" -D "$BACKUP_PATH/base" -U "$DB_USER" -v -P -W

# 2. Backup configuration files
echo "Backing up configuration..."
docker exec rxindexer-postgres cat /var/lib/postgresql/data/postgresql.conf > "$BACKUP_PATH/postgresql.conf"
docker exec rxindexer-postgres cat /var/lib/postgresql/data/pg_hba.conf > "$BACKUP_PATH/pg_hba.conf"

# 3. Create recovery configuration
cat > "$BACKUP_PATH/recovery.conf" << EOF
restore_command = 'cp %p %r'
standby_mode = on
primary_conninfo = 'host=$DB_HOST port=$DB_PORT user=$DB_USER'
recovery_target_time = ''
recovery_target_name = ''
EOF

# 4. Archive WAL files
echo "Archiving WAL files..."
mkdir -p "$BACKUP_PATH/wal"
docker exec rxindexer-postgres bash -c "cp $WAL_DIR/* $BACKUP_PATH/wal/" 2>/dev/null || true

# 5. Create backup metadata
cat > "$BACKUP_PATH/backup_info.json" << EOF
{
  "timestamp": "$(date -Iseconds)",
  "database": "$DB_NAME",
  "host": "$DB_HOST",
  "port": "$DB_PORT",
  "backup_type": "full",
  "wal_files": $(ls -1 "$BACKUP_PATH/wal" 2>/dev/null | wc -l),
  "size_gb": $(du -s "$BACKUP_PATH" | cut -f1 | awk '{print $1/1024/1024}')
}
EOF

# 6. Compress the backup
echo "Compressing backup..."
tar -czf "$BACKUP_PATH.tar.gz" -C "$BACKUP_DIR" "$DATE"
rm -rf "$BACKUP_PATH"

# 7. Upload to S3 if configured
if [ -n "$S3_BUCKET" ]; then
    echo "Uploading to S3..."
    aws s3 cp "$BACKUP_PATH.tar.gz" "s3://$S3_BUCKET/postgres-backups/"
    
    # Upload metadata separately for easy listing
    aws s3 cp "$BACKUP_PATH/backup_info.json" "s3://$S3_BUCKET/postgres-backups/metadata/${DATE}_info.json"
fi

# 8. Cleanup old backups
echo "Cleaning up old backups..."
find "$BACKUP_DIR" -name "*.tar.gz" -mtime +$RETENTION_DAYS -delete

# 9. Verify backup
echo "Verifying backup..."
if [ -f "$BACKUP_PATH.tar.gz" ] && [ -s "$BACKUP_PATH.tar.gz" ]; then
    echo "Backup completed successfully: $BACKUP_PATH.tar.gz"
    echo "Size: $(du -h "$BACKUP_PATH.tar.gz" | cut -f1)"
else
    echo "Error: Backup file is missing or empty"
    exit 1
fi

# 10. Create symlink to latest
ln -sf "$BACKUP_PATH.tar.gz" "$BACKUP_DIR/latest.tar.gz"

echo "Backup completed at $(date)"
