-- /Users/radiant/Desktop/RXinDexer/db/optimization/maintenance.sql
-- This file contains database maintenance procedures for RXinDexer

-- ============================================
-- 1. MAINTENANCE HISTORY TRACKING
-- ============================================

-- Create a table to track when maintenance was last run
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

-- ============================================
-- 2. MAINTENANCE PROCEDURES
-- ============================================

-- Function to perform table maintenance
CREATE OR REPLACE FUNCTION perform_table_maintenance() RETURNS VOID AS $$
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
    
    -- Also refresh the materialized view
    PERFORM refresh_balances_now();
    
    RAISE NOTICE 'Maintenance completed successfully';
END;
$$ LANGUAGE plpgsql;

-- Function to rebuild all indexes for a table
CREATE OR REPLACE FUNCTION rebuild_table_indexes(p_table_name TEXT) RETURNS VOID AS $$
DECLARE
    index_rec RECORD;
BEGIN
    FOR index_rec IN 
        SELECT indexname 
        FROM pg_indexes 
        WHERE tablename = p_table_name 
        AND schemaname = 'public'
    LOOP
        EXECUTE 'REINDEX INDEX ' || index_rec.indexname;
        RAISE NOTICE 'Rebuilt index: %', index_rec.indexname;
    END LOOP;
    
    -- Update maintenance history
    UPDATE maintenance_history
    SET last_reindex = NOW()
    WHERE table_name = p_table_name;
    
    -- Insert if not exists
    IF NOT FOUND THEN
        INSERT INTO maintenance_history (table_name, last_reindex)
        VALUES (p_table_name, NOW());
    END IF;
    
    RAISE NOTICE 'Rebuilt all indexes for table: %', p_table_name;
END;
$$ LANGUAGE plpgsql;
