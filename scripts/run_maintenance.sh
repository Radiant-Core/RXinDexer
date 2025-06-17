#!/bin/bash
# /Users/radiant/Desktop/RXinDexer/scripts/run_maintenance.sh
# This script runs database maintenance commands from the get_maintenance_commands() function

# Set default values
: ${POSTGRES_HOST:=db}
: ${POSTGRES_PORT:=5432}
: ${POSTGRES_USER:=postgres}
: ${POSTGRES_DB:=rxindexer}
: ${LOG_DIR:=/app/logs}
# Maintenance window in 24-hour format (HH:MM or H)
: ${MAINTENANCE_WINDOW_START:=02:00}  # 2 AM
: ${MAINTENANCE_WINDOW_END:=04:00}    # 4 AM

# Create log directory if it doesn't exist
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/maintenance_$(date +%Y%m%d_%H%M%S).log"

# Function to log messages
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Function to convert time string to minutes since midnight
time_to_minutes() {
    local time_str=$1
    # Handle HH:MM format
    if [[ "$time_str" =~ ^([0-9]{1,2}):([0-9]{2})$ ]]; then
        local hours=${BASH_REMATCH[1]}
        local minutes=${BASH_REMATCH[2]}
        echo $((10#$hours * 60 + 10#$minutes))
    # Handle H or HH format
    elif [[ "$time_str" =~ ^[0-9]{1,2}$ ]]; then
        echo $((10#$time_str * 60))
    else
        log "ERROR: Invalid time format: $time_str. Expected HH:MM or H"
        exit 1
    fi
}

# Function to check if current time is within maintenance window
in_maintenance_window() {
    # Get current time in UTC
    local current_hour=$(date -u +%H)
    local current_minute=$(date -u +%M)
    local current_minutes=$((10#$current_hour * 60 + 10#$current_minute))
    
    # Convert window times to minutes since midnight
    local start_minutes
    local end_minutes
    
    if ! start_minutes=$(time_to_minutes "$MAINTENANCE_WINDOW_START"); then
        log "ERROR: Failed to parse MAINTENANCE_WINDOW_START: $MAINTENANCE_WINDOW_START"
        return 1
    fi
    
    if ! end_minutes=$(time_to_minutes "$MAINTENANCE_WINDOW_END"); then
        log "ERROR: Failed to parse MAINTENANCE_WINDOW_END: $MAINTENANCE_WINDOW_END"
        return 1
    fi
    
    log "Current UTC time: ${current_hour}:${current_minute} (${current_minutes} minutes), Maintenance window: ${MAINTENANCE_WINDOW_START}-${MAINTENANCE_WINDOW_END} (${start_minutes}-${end_minutes} minutes)"
    
    # Check if we're in the maintenance window
    if [ "$start_minutes" -lt "$end_minutes" ]; then
        # Normal case: window within the same day
        if [ "$current_minutes" -ge "$start_minutes" ] && [ "$current_minutes" -lt "$end_minutes" ]; then
            log "In maintenance window (normal case: $MAINTENANCE_WINDOW_START-$MAINTENANCE_WINDOW_END UTC)"
            return 0
        fi
    else
        # Window crosses midnight
        if [ "$current_minutes" -ge "$start_minutes" ] || [ "$current_minutes" -lt "$end_minutes" ]; then
            log "In maintenance window (crosses midnight: $MAINTENANCE_WINDOW_START-$MAINTENANCE_WINDOW_END UTC)"
            return 0
        fi
    fi
    
    log "Not in maintenance window (current: ${current_hour}:${current_minute} UTC, window: ${MAINTENANCE_WINDOW_START}-${MAINTENANCE_WINDOW_END} UTC)"
    return 1
}

# Function to execute a single maintenance command
execute_command() {
    local cmd="$1"
    local start_time=$(date +%s)
    local exit_code=0
    local output
    
    log "Executing: $cmd"
    
    # Execute the command and capture output
    output=$(psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
        -c "$cmd" -v ON_ERROR_STOP=1 2>&1)
    exit_code=$?
    
    # Log the output
    echo "$output" | while IFS= read -r line; do
        log "  $line"
    done
    
    # Calculate duration
    local end_time=$(date +%s)
    local duration=$((end_time - start_time))
    
    # Extract table name for VACUUM/ANALYZE commands
    local table_name=""
    if [[ "$cmd" == VACUUM* ]]; then
        table_name=$(echo "$cmd" | grep -oP 'VACUUM\s+(?:ANALYZE\s+)?\K\w+' || true)
    fi
    
    # Update maintenance history
    if [ -n "$table_name" ]; then
        if [ $exit_code -eq 0 ]; then
            psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
                -c "SELECT update_maintenance_history('$table_name', 'VACUUM', true, NULL);" >/dev/null 2>&1 || true
        else
            psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
                -c "SELECT update_maintenance_history('$table_name', 'VACUUM', false, 'Command failed with exit code $exit_code');" >/dev/null 2>&1 || true
        fi
    fi
    
    log "  Command completed in ${duration}s"
    return $exit_code
}

# Main function
main() {
    log "Starting database maintenance"
    
    # Check if in maintenance window
    if ! in_maintenance_window; then
        log "Not in maintenance window ($MAINTENANCE_WINDOW_START:00-$MAINTENANCE_WINDOW_END:00 UTC), skipping maintenance"
        return 0
    fi
    
    log "In maintenance window, proceeding with maintenance tasks"
    
    # Get commands and execute them in priority order
    log "Fetching maintenance commands from database..."
    
    # Create a temporary file for commands
    local cmd_file=$(mktemp)
    
    # Get commands from database
    if ! psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
        -c "COPY (SELECT command FROM get_maintenance_commands() ORDER BY priority) TO STDOUT;" > "$cmd_file"; then
        log "ERROR: Failed to get maintenance commands from database"
        return 1
    fi
    
    # Execute each command
    local total_commands=0
    local failed_commands=0
    
    while IFS= read -r cmd; do
        if [ -n "$cmd" ]; then
            total_commands=$((total_commands + 1))
            if ! execute_command "$cmd"; then
                failed_commands=$((failed_commands + 1))
                log "WARNING: Command failed: $cmd"
            fi
        fi
    done < "$cmd_file"
    
    # Clean up
    rm -f "$cmd_file"
    
    # Log summary
    log "Maintenance completed: $((total_commands - failed_commands))/$total_commands commands succeeded"
    
    if [ $failed_commands -gt 0 ]; then
        log "WARNING: $failed_commands command(s) failed"
        return 1
    else
        log "Maintenance completed successfully"
        return 0
    fi
}

# Run main function and log output
main 2>&1 | tee -a "$LOG_FILE"
exit ${PIPESTATUS[0]}
