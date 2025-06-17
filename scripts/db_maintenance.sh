#!/bin/bash
# /Users/radiant/Desktop/RXinDexer/scripts/db_maintenance.sh
# This script handles database maintenance tasks including backups, VACUUM, and ANALYZE
# Integrates with the optimization functions in db/optimization/

set -eo pipefail

# Load environment variables
if [ -f /app/.env ]; then
    export $(grep -v '^#' /app/.env | xargs)
fi

# Default values
BACKUP_DIR=${BACKUP_DIR:-"/backups"}
LOG_DIR=${LOG_DIR:-"/app/logs"}
RETENTION_DAYS=${RETENTION_DAYS:-30}
DATE=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/db_maintenance_${DATE}.log"
MAINTENANCE_USER=${MAINTENANCE_USER:-"$POSTGRES_USER"}
MAINTENANCE_PASSWORD=${MAINTENANCE_PASSWORD:-"$POSTGRES_PASSWORD"}

# Create directories if they don't exist
mkdir -p "$BACKUP_DIR" "$LOG_DIR"

# Log function
log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Function to run psql command
run_psql() {
    local query=$1
    local dbname=${2:-$POSTGRES_DB}
    local user=${3:-$MAINTENANCE_USER}
    local password=${4:-$MAINTENANCE_PASSWORD}
    
    PGPASSWORD="$password" psql -v ON_ERROR_STOP=1 -h "$POSTGRES_HOST" -U "$user" -d "$dbname" -c "$query"
}

# Function to check if maintenance should run based on maintenance window
should_run_maintenance() {
    local current_hour=$(date +%H)
    local start_hour=${MAINTENANCE_WINDOW_START%%:*}
    local end_hour=${MAINTENANCE_WINDOW_END%%:*}
    
    # If window crosses midnight
    if [ "$start_hour" -gt "$end_hour" ]; then
        if [ "$current_hour" -ge "$start_hour" ] || [ "$current_hour" -lt "$end_hour" ]; then
            return 0
        fi
    else
        if [ "$current_hour" -ge "$start_hour" ] && [ "$current_hour" -lt "$end_hour" ]; then
            return 0
        fi
    fi
    
    return 1
}

# Function to check if table needs maintenance
needs_maintenance() {
    local table_name=$1
    local threshold_days=${2:-7}  # Default to 7 days if not specified
    
    local last_maintained=$(run_psql "SELECT last_vacuum FROM maintenance_history WHERE table_name = '$table_name';" | grep -E '^ [0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}' | xargs)
    
    if [ -z "$last_maintained" ]; then
        return 0  # Never maintained, needs maintenance
    fi
    
    local days_since_maintenance=$(( ($(date +%s) - $(date -d "$last_maintained" +%s)) / 86400 ))
    
    if [ "$days_since_maintenance" -ge "$threshold_days" ]; then
        return 0  # Needs maintenance
    else
        return 1  # Doesn't need maintenance yet
    fi
}

# Start maintenance
log "Starting database maintenance at $(date)"
log "Host: $POSTGRES_HOST, Database: $POSTGRES_DB, User: $MAINTENANCE_USER"

# Check if we should run maintenance based on maintenance window
if ! should_run_maintenance; then
    log "Outside maintenance window ($MAINTENANCE_WINDOW_START-$MAINTENANCE_WINDOW_END), skipping maintenance"
    exit 0
fi

# 1. Run maintenance using the optimization functions
log "Running database maintenance using optimization functions"
run_psql "SELECT perform_table_maintenance();"

# 2. Maintain UTXO partitions
log "Maintaining UTXO partitions"
run_psql "SELECT maintain_utxo_partitions();"

# 3. Check for long-running queries
log "Checking for long-running queries"
run_psql "SELECT pid, now() - query_start as duration, query, state 
          FROM pg_stat_activity 
          WHERE state != 'idle' 
          AND now() - query_start > interval '5 minutes' 
          ORDER BY duration DESC;"

# 4. Refresh materialized views if needed
log "Refreshing materialized views"
run_psql "SELECT refresh_balances_now();"

# 5. Create backup if it's the right time (e.g., daily at 2 AM)
if [ "$(date +%H)" = "02" ]; then
    log "Creating database backup"
    local backup_file="$BACKUP_DIR/rxindexer_${DATE}.dump"
    
    if PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -F c -f "$backup_file" "$POSTGRES_DB"; then
        log "Backup created successfully: $backup_file"
        
        # Verify backup integrity
        if PGPASSWORD="$POSTGRES_PASSWORD" pg_restore -l "$backup_file" >/dev/null 2>&1; then
            log "Backup verification successful"
            
            # Cleanup old backups
            find "$BACKUP_DIR" -name "rxindexer_*.dump" -mtime +$RETENTION_DAYS -delete
            log "Cleaned up backups older than $RETENTION_DAYS days"
        else
            log "ERROR: Backup verification failed!"
            rm -f "$backup_file"  # Remove corrupted backup
            exit 1
        fi
    else
        log "ERROR: Backup creation failed!"
        exit 1
    fi
fi

# 6. Log maintenance completion
log "Database maintenance completed successfully at $(date)"

# Clean up old log files (keep logs for RETENTION_DAYS)
find "$LOG_DIR" -name "db_maintenance_*.log" -mtime +$RETENTION_DAYS -delete

# Exit with success
exit 0
