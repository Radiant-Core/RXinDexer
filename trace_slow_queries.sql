-- /Users/radiant/Desktop/RXinDexer/trace_slow_queries.sql
-- This script adds SQL logging to identify the source of slow queries

-- Enable query logging for slow queries
ALTER SYSTEM SET log_min_duration_statement = '1000';  -- Log queries taking more than 1 second
ALTER SYSTEM SET log_line_prefix = '%t [%p]: [%l-1] user=%u,db=%d,app=%a,client=%h ';
ALTER SYSTEM SET log_statement = 'all';
ALTER SYSTEM SET log_duration = on;
ALTER SYSTEM SET log_connections = on;

-- Create table to track slow query sources
CREATE TABLE IF NOT EXISTS slow_query_log (
    id SERIAL PRIMARY KEY,
    query_text TEXT,
    duration_ms INTEGER,
    backend_pid INTEGER,
    application_name TEXT,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create function to log slow queries with call stack
CREATE OR REPLACE FUNCTION log_slow_query_with_source() RETURNS TRIGGER AS $$
DECLARE
    query_text TEXT;
    conn_info TEXT;
BEGIN
    -- Get current query text
    SELECT current_query() INTO query_text;
    
    -- Get connection info
    SELECT application_name || ' - ' || client_addr INTO conn_info
    FROM pg_stat_activity
    WHERE pid = pg_backend_pid();
    
    -- Log the slow query
    INSERT INTO slow_query_log (query_text, duration_ms, backend_pid, application_name)
    VALUES (
        query_text,
        EXTRACT(MILLISECONDS FROM CURRENT_TIMESTAMP - transaction_timestamp())::INTEGER,
        pg_backend_pid(),
        conn_info
    );
    
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Create a better implementation for the slow CREATE TEMPORARY TABLE
CREATE OR REPLACE FUNCTION create_temp_balances_efficient() RETURNS VOID AS $$
BEGIN
    -- Drop existing table if it exists
    DROP TABLE IF EXISTS temp_balances;
    
    -- Create the temporary table using the materialized view
    CREATE TEMPORARY TABLE temp_balances AS
    SELECT address, total_balance AS balance
    FROM address_balances;
    
    -- Log it happened
    INSERT INTO slow_query_log (query_text, duration_ms, backend_pid, application_name)
    VALUES (
        'Used optimized temp_balances creation',
        0,
        pg_backend_pid(),
        'Optimization override'
    );
END;
$$ LANGUAGE plpgsql;

-- Create trigger for slow queries on utxos table
DROP TRIGGER IF EXISTS log_slow_utxo_query ON utxos;
CREATE TRIGGER log_slow_utxo_query
AFTER UPDATE OR INSERT OR DELETE ON utxos
FOR EACH STATEMENT
EXECUTE FUNCTION log_slow_query_with_source();

-- Reload PostgreSQL configuration
SELECT pg_reload_conf();
