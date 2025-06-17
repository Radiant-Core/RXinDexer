-- /docker/db-init-scripts/01-monitoring-user.sql
-- Create monitoring schema and user with appropriate permissions

-- Create monitoring schema if it doesn't exist
CREATE SCHEMA IF NOT EXISTS monitor;

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

-- Grant read access to all tables in public schema
GRANT USAGE ON SCHEMA public TO monitor;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO monitor;

-- Set default privileges for future tables
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO monitor;

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
