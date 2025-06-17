#!/bin/bash
# scripts/db_maintenance.sh

# Set default values if not provided
: ${POSTGRES_HOST:=db}
: ${POSTGRES_PORT:=5432}
: ${POSTGRES_DB:=rxindexer}
: ${POSTGRES_USER:=maintenance}
: ${POSTGRES_PASSWORD:=maintenance_password}
: ${LOG_DIR:=/var/log/postgresql}
: ${BACKUP_DIR:=/var/lib/postgresql/backups}
: ${RETENTION_DAYS:=7}

# Create log directory if it doesn't exist
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/maintenance_$(date +%Y%m%d_%H%M%S).log"

# Log function
log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Function to run psql commands
run_psql() {
    PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -c "$1" 2>&1 | tee -a "$LOG_FILE"
}

# Check if we're in the maintenance window (default: 2 AM to 4 AM)
should_run_maintenance() {
    local current_hour=$(date +%H)
    [[ "$current_hour" -ge 2 && "$current_hour" -lt 4 ]]
}

# Main execution
main() {
    log "Starting database maintenance"
    
    # Check if we should run maintenance based on time
    if ! should_run_maintenance; then
        log "Outside maintenance window (2 AM - 4 AM), skipping maintenance"
        return 0
    fi

    log "Generating maintenance commands..."
    
    # Get the maintenance commands
    IFS=$'\n'
    commands=($(PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA -c "SELECT command FROM get_table_maintenance_commands() WHERE command NOT LIKE 'SELECT%';"))
    
    # Execute each command
    for cmd in "${commands[@]}"; do
        if [ -n "$cmd" ]; then
            log "Executing: $cmd"
            run_psql "$cmd"
        fi
    done
    
    log "Database maintenance completed successfully"
    
    # Clean up old log files
    find "$LOG_DIR" -name "maintenance_*.log" -mtime +$RETENTION_DAYS -delete
}

# Run the main function
main "$@"
