-- /docker/db-init-scripts/02-monitoring-views.sql
-- Create monitoring views for database observability

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

-- Grant access to all monitoring views
GRANT SELECT ON ALL TABLES IN SCHEMA monitor TO monitor;
