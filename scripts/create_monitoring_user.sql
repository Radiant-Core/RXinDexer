-- /Users/radiant/Desktop/RXinDexer/scripts/create_monitoring_user.sql
-- This script creates a read-only monitoring user with limited permissions

-- Create monitoring role if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'monitor') THEN
        CREATE ROLE monitor WITH LOGIN PASSWORD 'monitor_password' NOSUPERUSER INHERIT NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
END
$$;

-- Grant necessary permissions
GRANT pg_monitor TO monitor;
GRANT CONNECT ON DATABASE rxindexer TO monitor;

-- Grant read access to all tables
GRANT USAGE ON SCHEMA public TO monitor;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO monitor;

-- Grant execute on monitoring functions
GRANT EXECUTE ON FUNCTION pg_stat_file(text) TO monitor;
GRANT EXECUTE ON FUNCTION pg_ls_dir(text) TO monitor;
GRANT EXECUTE ON FUNCTION pg_read_file(text) TO monitor;
GRANT EXECUTE ON FUNCTION pg_read_file(text, bigint, bigint) TO monitor;

-- Grant access to statistics views
GRANT SELECT ON pg_stat_database TO monitor;
GRANT SELECT ON pg_stat_user_tables TO monitor;
GRANT SELECT ON pg_statio_user_tables TO monitor;
GRANT SELECT ON pg_stat_user_indexes TO monitor;
GRANT SELECT ON pg_statio_user_indexes TO monitor;
GRANT SELECT ON pg_stat_activity TO monitor;
GRANT SELECT ON pg_stat_bgwriter TO monitor;
GRANT SELECT ON pg_stat_wal_receiver TO monitor;
GRANT SELECT ON pg_stat_subscription TO monitor;
GRANT SELECT ON pg_stat_replication TO monitor;
GRANT SELECT ON pg_stat_ssl TO monitor;

-- Create a view for monitoring partition information
CREATE OR REPLACE VIEW monitor.partition_info AS
SELECT 
    nmsp_parent.nspname AS parent_schema,
    parent.relname AS parent_table,
    nmsp_child.nspname AS child_schema,
    child.relname AS child_table,
    pg_size_pretty(pg_total_relation_size(quote_ident(nmsp_child.nspname) || '.' || quote_ident(child.relname))) AS size,
    pg_stat_get_live_tuples(child.oid) AS row_count,
    pg_stat_get_last_analyze_time(child.oid) AS last_analyze,
    pg_stat_get_last_autoanalyze_time(child.oid) AS last_autoanalyze,
    pg_stat_get_last_vacuum_time(child.oid) AS last_vacuum,
    pg_stat_get_last_autovacuum_time(child.oid) AS last_autovacuum
FROM pg_inherits 
JOIN pg_class parent ON pg_inherits.inhparent = parent.oid 
JOIN pg_class child ON pg_inherits.inhrelid = child.oid 
JOIN pg_namespace nmsp_parent ON nmsp_parent.oid = parent.relnamespace 
JOIN pg_namespace nmsp_child ON nmsp_child.oid = child.relnamespace;

-- Grant access to the monitoring view
GRANT SELECT ON monitor.partition_info TO monitor;

-- Create a view for long-running queries
CREATE OR REPLACE VIEW monitor.long_running_queries AS
SELECT 
    pid,
    usename,
    application_name,
    client_addr,
    now() - query_start AS duration,
    state,
    query
FROM pg_stat_activity 
WHERE state != 'idle' 
AND query_start < (now() - interval '5 minutes')
ORDER BY duration DESC;

-- Grant access to long running queries view
GRANT SELECT ON monitor.long_running_queries TO monitor;

-- Create a view for database size information
CREATE OR REPLACE VIEW monitor.database_size AS
SELECT 
    d.datname AS database,
    pg_size_pretty(pg_database_size(d.datname)) AS size,
    pg_size_pretty(pg_total_relation_size(quote_ident(schemaname) || '.' || quote_ident(tablename))) AS largest_table_size,
    schemaname || '.' || tablename AS largest_table
FROM pg_database d
CROSS JOIN LATERAL (
    SELECT 
        n.nspname AS schemaname,
        c.relname AS tablename,
        pg_total_relation_size(n.nspname || '.' || c.relname) AS size
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r'
    AND n.nspname NOT IN ('pg_catalog', 'information_schema')
    AND n.nspname !~ '^pg_toast'
    ORDER BY size DESC
    LIMIT 1
) t
WHERE d.datname = current_database();

-- Grant access to database size view
GRANT SELECT ON monitor.database_size TO monitor;
