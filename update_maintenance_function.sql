-- Drop the old function if it exists
DROP FUNCTION IF EXISTS perform_table_maintenance();

-- Create a function to generate VACUUM commands
CREATE OR REPLACE FUNCTION get_table_maintenance_commands()
RETURNS TABLE (
    command TEXT,
    description TEXT
) AS $$
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
            -- Return the VACUUM command to be executed
            command := 'VACUUM (ANALYZE, VERBOSE) ' || tbl || ';';
            description := 'Vacuum and analyze table: ' || tbl;
            RETURN NEXT;
            
            -- Update maintenance timestamp
            UPDATE maintenance_history
            SET last_vacuum = NOW(),
                last_analyze = NOW()
            WHERE table_name = tbl;
        END IF;
    END LOOP;
    
    -- Check if we need to refresh materialized views
    IF EXISTS (SELECT 1 FROM pg_matviews WHERE schemaname = 'public' AND matviewname = 'balances') THEN
        command := 'REFRESH MATERIALIZED VIEW CONCURRENTLY balances;';
        description := 'Refresh materialized view: balances';
        RETURN NEXT;
    END IF;
    
    -- Add a success message
    command := 'SELECT ''Maintenance commands generated successfully'';';
    description := 'Status';
    RETURN NEXT;
END;
$$ LANGUAGE plpgsql;

-- Grant execute permission to the maintenance user
GRANT EXECUTE ON FUNCTION get_table_maintenance_commands() TO maintenance;
