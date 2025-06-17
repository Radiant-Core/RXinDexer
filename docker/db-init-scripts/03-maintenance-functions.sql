-- /docker/db-init-scripts/03-maintenance-functions.sql
-- Database maintenance functions for RXinDexer

-- Function to analyze all tables in the database
CREATE OR REPLACE FUNCTION analyze_database()
RETURNS TABLE(
    table_name TEXT,
    status TEXT,
    duration INTERVAL
) AS $$
DECLARE
    start_time TIMESTAMPTZ;
    end_time TIMESTAMPTZ;
    r RECORD;
BEGIN
    -- Create and return a table with the results
    CREATE TEMP TABLE results (
        table_name TEXT,
        status TEXT,
        duration INTERVAL
    ) ON COMMIT DROP;
    
    -- Analyze each table
    FOR r IN 
        SELECT quote_ident(n.nspname) || '.' || quote_ident(c.relname) AS full_table_name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r'
        AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        AND n.nspname !~ '^pg_toast'
        AND c.relpersistence = 'p' -- Only permanent tables
    LOOP
        BEGIN
            start_time := clock_timestamp();
            EXECUTE 'ANALYZE VERBOSE ' || r.full_table_name;
            end_time := clock_timestamp();
            
            INSERT INTO results VALUES (
                r.full_table_name,
                'SUCCESS',
                (end_time - start_time)
            );
            
        EXCEPTION WHEN OTHERS THEN
            INSERT INTO results VALUES (
                r.full_table_name,
                'ERROR: ' || SQLERRM,
                (clock_timestamp() - start_time)
            );
        END;
    END LOOP;
    
    -- Return the results
    RETURN QUERY SELECT * FROM results;
END;
$$ LANGUAGE plpgsql;

-- Function to get database maintenance recommendations
CREATE OR REPLACE FUNCTION get_maintenance_recommendations()
RETURNS TABLE(
    recommendation TEXT,
    priority TEXT,
    details TEXT
) AS $$
BEGIN
    -- Check for tables that haven't been analyzed recently
    RETURN QUERY
    WITH table_stats AS (
        SELECT 
            schemaname || '.' || relname AS table_name,
            last_autoanalyze,
            last_analyze,
            n_live_tup,
            n_dead_tup,
            CASE 
                WHEN last_analyze IS NULL THEN 'never'
                ELSE now() - last_analyze::text
            END AS last_analyze_interval
        FROM pg_stat_user_tables
        WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
    )
    SELECT 
        'Table needs analyze: ' || table_name AS recommendation,
        CASE 
            WHEN last_analyze IS NULL AND n_live_tup > 1000 THEN 'high'
            WHEN last_analyze < now() - interval '7 days' AND n_live_tup > 1000 THEN 'medium'
            WHEN last_analyze < now() - interval '1 day' AND n_dead_tup > 1000 THEN 'low'
            ELSE 'info'
        END AS priority,
        'Last analyzed: ' || COALESCE(last_analyze_interval::text, 'never') || 
        ', live tuples: ' || n_live_tup || 
        ', dead tuples: ' || n_dead_tup AS details
    FROM table_stats
    WHERE 
        (last_analyze IS NULL AND n_live_tup > 1000) OR
        (last_analyze < now() - interval '7 days' AND n_live_tup > 1000) OR
        (last_analyze < now() - interval '1 day' AND n_dead_tup > 1000)
    ORDER BY 
        CASE 
            WHEN last_analyze IS NULL AND n_live_tup > 1000 THEN 1
            WHEN last_analyze < now() - interval '7 days' AND n_live_tup > 1000 THEN 2
            ELSE 3
        END,
        n_live_tup DESC;
    
    -- Check for bloated tables
    RETURN QUERY
    WITH table_bloat AS (
        SELECT
            schemaname || '.' || relname AS table_name,
            n_dead_tup,
            n_live_tup,
            ROUND((n_dead_tup::numeric / NULLIF(n_live_tup + n_dead_tup, 0)) * 100, 2) AS dead_tup_percent
        FROM pg_stat_user_tables
        WHERE n_live_tup > 1000  -- Only consider tables with significant data
        AND n_dead_tup > 1000    -- Only if there's significant dead tuples
    )
    SELECT 
        'Table has high dead tuple percentage: ' || table_name AS recommendation,
        CASE 
            WHEN dead_tup_percent > 20 THEN 'high'
            WHEN dead_tup_percent > 10 THEN 'medium'
            ELSE 'low'
        END AS priority,
        'Dead tuples: ' || n_dead_tup || 
        ' (' || dead_tup_percent || '% of ' || (n_live_tup + n_dead_tup) || ' total rows)' AS details
    FROM table_bloat
    WHERE dead_tup_percent > 5
    ORDER BY dead_tup_percent DESC;
    
    -- Check for unused indexes
    RETURN QUERY
    SELECT 
        'Unused or rarely used index: ' || schemaname || '.' || indexrelname AS recommendation,
        CASE 
            WHEN idx_scan = 0 THEN 'medium'
            WHEN idx_scan < 1000 AND idx_scan > 0 THEN 'low'
            ELSE 'info'
        END AS priority,
        'Scans: ' || idx_scan || ', size: ' || pg_size_pretty(pg_relation_size(indexrelid)) AS details
    FROM pg_stat_user_indexes
    WHERE 
        idx_scan < 1000  -- Indexes with few scans
        AND idx_scan::float / (SELECT setting::float FROM pg_settings WHERE name = 'pg_stat_statements.max') < 0.01  -- Less than 1% of all queries
        AND pg_relation_size(indexrelid) > 1024 * 1024  -- Only indexes larger than 1MB
    ORDER BY pg_relation_size(indexrelid) DESC;
    
    -- Check for missing indexes on foreign keys
    RETURN QUERY
    SELECT 
        'Missing index on foreign key: ' || tc.table_schema || '.' || tc.table_name || '(' || kcu.column_name || ')' AS recommendation,
        'medium' AS priority,
        'References ' || ccu.table_schema || '.' || ccu.table_name || '(' || ccu.column_name || ')' AS details
    FROM 
        information_schema.table_constraints AS tc 
        JOIN information_schema.key_column_usage AS kcu
          ON tc.constraint_name = kcu.constraint_name
          AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage AS ccu
          ON ccu.constraint_name = tc.constraint_name
          AND ccu.table_schema = tc.table_schema
    WHERE tc.constraint_type = 'FOREIGN KEY'
    AND NOT EXISTS (
        SELECT 1
        FROM pg_indexes
        WHERE 
            tablename = tc.table_name AND 
            schemaname = tc.table_schema AND
            indexdef LIKE '%' || kcu.column_name || '%' AND
            indexdef LIKE '%' || ccu.column_name || '%'
    )
    GROUP BY tc.table_schema, tc.table_name, kcu.column_name, ccu.table_schema, ccu.table_name, ccu.column_name;
END;
$$ LANGUAGE plpgsql;

-- Grant execute on maintenance functions to monitor user
GRANT EXECUTE ON FUNCTION analyze_database() TO monitor;
GRANT EXECUTE ON FUNCTION get_maintenance_recommendations() TO monitor;
