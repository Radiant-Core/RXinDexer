#!/bin/bash

# RXinDexer Database Maintenance Script
# This script handles partition management and maintenance tasks

set -euo pipefail

# Configuration
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-rxindexer}"
DB_USER="${DB_USER:-postgres}"
DB_PASSWORD="${DB_PASSWORD:-}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[$(date '+%Y-%m-%d %H:%M:%S')] WARNING:${NC} $1"
}

error() {
    echo -e "${RED}[$(date '+%Y-%m-%d %H:%M:%S')] ERROR:${NC} $1"
    exit 1
}

# Check if database is accessible
check_db_connection() {
    log "Checking database connection..."
    
    if PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "SELECT 1;" > /dev/null 2>&1; then
        log "Database connection successful"
    else
        error "Cannot connect to database. Please check your connection parameters."
    fi
}

# Create new partitions
create_partitions() {
    log "Creating new partitions..."
    
    PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "
        SELECT create_monthly_partitions();
    " || error "Failed to create partitions"
    
    log "Partition creation completed"
}

# Migrate data to partitioned tables
migrate_to_partitioned() {
    log "Migrating data to partitioned tables..."
    
    PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "
        SELECT migrate_to_partitioned_tables();
    " || error "Failed to migrate data to partitioned tables"
    
    log "Data migration completed"
}

# Show partition statistics
show_partition_stats() {
    log "Partition statistics:"
    
    PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "
        -- Show partition configuration
        SELECT 
            table_name,
            partition_type,
            retention_months,
            auto_create,
            last_partition_created,
            updated_at
        FROM partition_config
        ORDER BY table_name;
        
        -- Show partition sizes
        SELECT 
            schemaname,
            tablename,
            pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size,
            pg_total_relation_size(schemaname||'.'||tablename) as size_bytes
        FROM pg_tables 
        WHERE tablename LIKE '%_partitioned' OR tablename LIKE '%_202%'
        ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
        
        -- Show partition count
        SELECT 
            'glyph_actions' as table_name,
            COUNT(*) as partition_count
        FROM information_schema.tables 
        WHERE table_name LIKE 'glyph_actions_%'
        
        UNION ALL
        
        SELECT 
            'token_price_history' as table_name,
            COUNT(*) as partition_count
        FROM information_schema.tables 
        WHERE table_name LIKE 'token_price_history_%'
        
        UNION ALL
        
        SELECT 
            'token_volume_daily' as table_name,
            COUNT(*) as partition_count
        FROM information_schema.tables 
        WHERE table_name LIKE 'token_volume_daily_%';
    "
}

# Switch to partitioned tables (rename operation)
switch_to_partitioned() {
    log "Switching to partitioned tables..."
    
    PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "
        -- This is a critical operation - backup first!
        -- Rename original tables to backup
        ALTER TABLE glyph_actions RENAME TO glyph_actions_backup;
        ALTER TABLE token_price_history RENAME TO token_price_history_backup;
        ALTER TABLE token_volume_daily RENAME TO token_volume_daily_backup;
        
        -- Rename partitioned tables to production names
        ALTER TABLE glyph_actions_partitioned RENAME TO glyph_actions;
        ALTER TABLE token_price_history_partitioned RENAME TO token_price_history;
        ALTER TABLE token_volume_daily_partitioned RENAME TO token_volume_daily;
    " || error "Failed to switch to partitioned tables"
    
    log "Switch to partitioned tables completed"
    warn "Original tables renamed to *_backup - keep for safety or drop when confident"
}

# Drop old partitions (based on retention policy)
drop_old_partitions() {
    local months_to_keep="${1:-48}"
    
    log "Dropping partitions older than $months_to_keep months..."
    
    PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "
        -- Drop old glyph_actions partitions
        DO \$\$
        DECLARE
            partition_name TEXT;
            cutoff_date DATE := CURRENT_DATE - INTERVAL '$months_to_keep months';
        BEGIN
            FOR partition_name IN 
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_name LIKE 'glyph_actions_%' 
                AND table_name < 'glyph_actions_' || to_char(cutoff_date, 'YYYY_MM')
            LOOP
                EXECUTE 'DROP TABLE IF EXISTS ' || partition_name;
                RAISE NOTICE 'Dropped partition: %', partition_name;
            END LOOP;
            
            -- Drop old token_price_history partitions
            FOR partition_name IN 
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_name LIKE 'token_price_history_%' 
                AND table_name < 'token_price_history_' || to_char(cutoff_date, 'YYYY_MM')
            LOOP
                EXECUTE 'DROP TABLE IF EXISTS ' || partition_name;
                RAISE NOTICE 'Dropped partition: %', partition_name;
            END LOOP;
            
            -- Drop old token_volume_daily partitions
            FOR partition_name IN 
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_name LIKE 'token_volume_daily_%' 
                AND table_name < 'token_volume_daily_' || to_char(cutoff_date, 'YYYY_MM')
            LOOP
                EXECUTE 'DROP TABLE IF EXISTS ' || partition_name;
                RAISE NOTICE 'Dropped partition: %', partition_name;
            END LOOP;
        END \$\$;
    " || error "Failed to drop old partitions"
    
    log "Old partitions dropped"
}

# Show what partitions would be dropped (dry run)
dry_run_drop() {
    local months_to_keep="${1:-48}"
    
    log "Dry run - showing partitions older than $months_to_keep months that would be dropped..."
    
    PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "
        WITH cutoff_date AS (
            SELECT CURRENT_DATE - INTERVAL '$months_to_keep months' as cutoff
        )
        SELECT 
            'glyph_actions' as table_type,
            table_name as partition_name,
            pg_size_pretty(pg_total_relation_size(table_schema||'.'||table_name)) as size
        FROM information_schema.tables, cutoff_date
        WHERE table_name LIKE 'glyph_actions_%' 
        AND table_name < 'glyph_actions_' || to_char(cutoff_date, 'YYYY_MM')
        
        UNION ALL
        
        SELECT 
            'token_price_history' as table_type,
            table_name as partition_name,
            pg_size_pretty(pg_total_relation_size(table_schema||'.'||table_name)) as size
        FROM information_schema.tables, cutoff_date
        WHERE table_name LIKE 'token_price_history_%' 
        AND table_name < 'token_price_history_' || to_char(cutoff_date, 'YYYY_MM')
        
        UNION ALL
        
        SELECT 
            'token_volume_daily' as table_type,
            table_name as partition_name,
            pg_size_pretty(pg_total_relation_size(table_schema||'.'||table_name)) as size
        FROM information_schema.tables, cutoff_date
        WHERE table_name LIKE 'token_volume_daily_%' 
        AND table_name < 'token_volume_daily_' || to_char(cutoff_date, 'YYYY_MM')
        ORDER BY table_type, partition_name;
    "
}

# Update partition configuration
update_partition_config() {
    local table="$1"
    local months="$2"
    
    if [[ -z "$table" || -z "$months" ]]; then
        error "Usage: $0 update-config <table_name> <months>"
    fi
    
    log "Updating partition retention for $table to $months months"
    
    PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "
        UPDATE partition_config 
        SET retention_months = $months, updated_at = NOW()
        WHERE table_name = '$table';
    " || error "Failed to update partition configuration"
    
    log "Partition configuration updated"
}

# Show help
show_help() {
    cat << EOF
RXinDexer Database Maintenance Script (Partitioning)

Usage: $0 [COMMAND] [OPTIONS]

Commands:
    create-partitions      Create new monthly partitions
    migrate               Migrate data to partitioned tables
    switch                Switch production to use partitioned tables
    stats                 Show partition statistics and sizes
    drop-old [months]     Drop partitions older than N months (default: 48)
    dry-run [months]      Show what partitions would be dropped
    update-config         Update partition retention configuration
    help                  Show this help message

Examples:
    $0 create-partitions              # Create new partitions
    $0 migrate                        # Migrate existing data
    $0 stats                          # Show statistics
    $0 switch                         # Switch to partitioned tables
    $0 drop-old 24                    # Drop partitions older than 24 months
    $0 dry-run 24                     # Preview what would be dropped
    $0 update-config glyph_actions 18  # Change retention to 18 months

Environment Variables:
    DB_HOST     Database host (default: localhost)
    DB_PORT     Database port (default: 5432)  
    DB_NAME     Database name (default: rxindexer)
    DB_USER     Database user (default: postgres)
    DB_PASSWORD Database password (required)

Partitioning Workflow:
1. $0 create-partitions    # Create initial partitions
2. $0 migrate             # Migrate existing data  
3. $0 stats               # Verify migration
4. $0 switch              # Switch to partitioned tables
5. $0 drop-old 12         # Optionally drop very old partitions

EOF
}

# Main script logic
case "${1:-help}" in
    "create-partitions")
        check_db_connection
        create_partitions
        ;;
    "migrate")
        check_db_connection
        migrate_to_partitioned
        ;;
    "switch")
        check_db_connection
        switch_to_partitioned
        ;;
    "stats")
        check_db_connection
        show_partition_stats
        ;;
    "drop-old")
        check_db_connection
        drop_old_partitions "${2:-36}"
        ;;
    "dry-run")
        check_db_connection
        dry_run_drop "${2:-36}"
        ;;
    "update-config")
        update_partition_config "$2" "$3"
        ;;
    "help"|*)
        show_help
        ;;
esac
