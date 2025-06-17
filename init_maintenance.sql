-- Create maintenance history table if it doesn't exist
CREATE TABLE IF NOT EXISTS maintenance_history (
    table_name TEXT PRIMARY KEY,
    last_vacuum TIMESTAMP WITH TIME ZONE,
    last_analyze TIMESTAMP WITH TIME ZONE,
    last_reindex TIMESTAMP WITH TIME ZONE
);

-- Initialize maintenance history for key tables
INSERT INTO maintenance_history (table_name, last_vacuum, last_analyze, last_reindex)
VALUES 
    ('utxos', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day'),
    ('transactions', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day'),
    ('blocks', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day'),
    ('holders', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day'),
    ('glyph_tokens', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day')
ON CONFLICT (table_name) DO NOTHING;

-- Create or replace the perform_table_maintenance function
CREATE OR REPLACE FUNCTION perform_table_maintenance() 
RETURNS VOID AS $$
DECLARE
    tables_to_maintain TEXT[] := ARRAY['utxos', 'transactions', 'blocks', 'holders', 'glyph_tokens'];
    tbl TEXT;
    last_maintenance TIMESTAMP WITH TIME ZONE;
BEGIN
    -- Process each table
    FOREACH tbl IN ARRAY tables_to_maintain LOOP
        -- Check when it was last maintained
        SELECT last_vacuum INTO last_maintenance 
        FROM maintenance_history
        WHERE table_name = tbl;
        
        -- If more than 12 hours since last maintenance or NULL
        IF last_maintenance IS NULL OR last_maintenance < NOW() - INTERVAL '12 hours' THEN
            RAISE NOTICE 'Performing maintenance on %', tbl;
            
            -- VACUUM to reclaim space and update statistics
            EXECUTE 'VACUUM (ANALYZE, VERBOSE) ' || tbl;
            
            -- Update maintenance timestamp
            UPDATE maintenance_history
            SET last_vacuum = NOW(),
                last_analyze = NOW()
            WHERE table_name = tbl;
        END IF;
    END LOOP;
    
    -- Also refresh the materialized view if it exists
    IF EXISTS (SELECT 1 FROM pg_matviews WHERE schemaname = 'public' AND matviewname = 'balances') THEN
        BEGIN
            REFRESH MATERIALIZED VIEW CONCURRENTLY balances;
            RAISE NOTICE 'Refreshed materialized view: balances';
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'Could not refresh materialized view: %', SQLERRM;
        END;
    END IF;
    
    RAISE NOTICE 'Maintenance completed successfully';
END;
$$ LANGUAGE plpgsql;

-- Grant execute permission to the maintenance user
GRANT EXECUTE ON FUNCTION perform_table_maintenance() TO maintenance;
GRANT SELECT, INSERT, UPDATE ON maintenance_history TO maintenance;
