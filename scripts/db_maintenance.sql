-- scripts/db_maintenance.sql
-- Function to perform routine database maintenance
CREATE OR REPLACE FUNCTION perform_database_maintenance()
RETURNS TABLE (
    task TEXT,
    status TEXT,
    details TEXT
) AS $$
BEGIN
    -- 1. Update partition information
    PERFORM maintain_utxo_partitions();
    
    -- 2. Analyze all partitions
    PERFORM analyze_partitions();
    
    -- 3. Vacuum if needed (auto-vacuum should handle this, but we can force analyze)
    ANALYZE VERBOSE;
    
    -- 4. Check for long-running transactions
    RETURN QUERY
    SELECT 
        'Long-running transactions' as task,
        'INFO' as status,
        'Found ' || count(*) || ' transactions running longer than 10 minutes' as details
    FROM pg_stat_activity
    WHERE now() - query_start > interval '10 minutes'
    AND pid <> pg_backend_pid();
    
    -- 5. Check for locks
    RETURN QUERY
    SELECT 
        'Blocked queries' as task,
        'WARNING' as status,
        'Found ' || count(*) || ' blocked queries' as details
    FROM pg_locks l
    JOIN pg_stat_activity a ON a.pid = l.pid
    JOIN pg_locks w ON l.transactionid = w.transactionid 
        AND l.locktype = 'transactionid' 
        AND l.pid <> w.pid
        AND l.granted = true
        AND NOT w.granted;
        
    -- 6. Return success status
    RETURN QUERY
    SELECT 
        'Maintenance completed' as task,
        'SUCCESS' as status,
        'Database maintenance completed at ' || now()::text as details;
    
EXCEPTION WHEN OTHERS THEN
    RETURN QUERY
    SELECT 
        'Maintenance failed' as task,
        'ERROR' as status,
        SQLERRM as details;
END;
$$ LANGUAGE plpgsql;

-- Function to analyze all partitions
CREATE OR REPLACE FUNCTION analyze_partitions()
RETURNS VOID AS $$
DECLARE
    r RECORD;
BEGIN
    RAISE NOTICE 'Analyzing all partitions...';
    
    -- Analyze the parent table (will cascade to all partitions)
    ANALYZE VERBOSE utxos;
    
    -- Also analyze other large tables
    ANALYZE VERBOSE blocks;
    ANALYZE VERBOSE transactions;
    
    RAISE NOTICE 'Partition analysis completed at %', now();
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'Error analyzing partitions: %', SQLERRM;
END;
$$ LANGUAGE plpgsql;
