-- /Users/radiant/Desktop/RXinDexer/scripts/apply_utxo_partitioning.sql
-- This script implements table partitioning for the utxos table by block_height
-- with 50,000 block ranges using PostgreSQL's declarative partitioning.

-- Enable statement timeout for safety
SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SET check_function_bodies = false;
SET client_min_messages = warning;
SET row_security = off;

-- Begin transaction
BEGIN;

-- 1. Create the partitioned table with the same structure as the original
CREATE TABLE utxos_partitioned (
    txid VARCHAR(64) NOT NULL,
    vout INTEGER NOT NULL,
    address VARCHAR(128),
    amount BIGINT,
    spent BOOLEAN DEFAULT false,
    spent_txid VARCHAR(64),
    block_height INTEGER NOT NULL,
    block_hash VARCHAR(64) NOT NULL,
    token_ref VARCHAR(64),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    PRIMARY KEY (txid, vout, block_height)
) PARTITION BY RANGE (block_height);

-- 2. Create a function to manage partitions
CREATE OR REPLACE FUNCTION create_utxo_partitions()
RETURNS TRIGGER AS $$
DECLARE
    max_block INTEGER;
    partition_start INTEGER;
    partition_end INTEGER;
    partition_name TEXT;
    current_partition_name TEXT;
    partition_exists BOOLEAN;
BEGIN
    -- Get the current max block height from the blocks table
    SELECT COALESCE(MAX(height), 0) INTO max_block FROM blocks;
    
    -- Calculate current partition range
    partition_start := (max_block / 50000) * 50000;
    partition_end := partition_start + 50000;
    
    -- Create partition name
    current_partition_name := 'utxos_p' || partition_start;
    
    -- Check if partition exists
    SELECT EXISTS (
        SELECT 1 
        FROM pg_tables 
        WHERE schemaname = 'public' 
        AND tablename = current_partition_name
    ) INTO partition_exists;
    
    -- Create the partition if it doesn't exist
    IF NOT partition_exists THEN
        -- Create the partition
        EXECUTE format('
            CREATE TABLE %I PARTITION OF utxos_partitioned
            FOR VALUES FROM (%L) TO (%L)',
            current_partition_name, partition_start, partition_end
        );
        
        -- Create indexes on the new partition
        EXECUTE format('
            CREATE INDEX %I ON %I (address, spent)',
            'idx_' || current_partition_name || '_address_spent',
            current_partition_name
        );
        
        EXECUTE format('
            CREATE INDEX %I ON %I (token_ref, spent)',
            'idx_' || current_partition_name || '_token_spent',
            current_partition_name
        );
        
        EXECUTE format('
            CREATE INDEX %I ON %I (address, spent, token_ref)',
            'idx_' || current_partition_name || '_address_spent_token',
            current_partition_name
        );
        
        EXECUTE format('
            CREATE INDEX %I ON %I (address)',
            'idx_' || current_partition_name || '_address',
            current_partition_name
        );
        
        EXECUTE format('
            CREATE INDEX %I ON %I (block_height)',
            'idx_' || current_partition_name || '_block_height',
            current_partition_name
        );
        
        EXECUTE format('
            CREATE INDEX %I ON %I (token_ref)',
            'idx_' || current_partition_name || '_token_ref',
            current_partition_name
        );
        
        RAISE NOTICE 'Created new partition % for block range % to %', 
            current_partition_name, partition_start, partition_end - 1;
    END IF;
    
    -- Check if we need to create the next partition in advance
    IF (max_block + 10000) >= partition_end THEN
        partition_start := partition_end;
        partition_end := partition_start + 50000;
        current_partition_name := 'utxos_p' || partition_start;
        
        SELECT EXISTS (
            SELECT 1 
            FROM pg_tables 
            WHERE schemaname = 'public' 
            AND tablename = current_partition_name
        ) INTO partition_exists;
        
        IF NOT partition_exists THEN
            EXECUTE format('
                CREATE TABLE %I PARTITION OF utxos_partitioned
                FOR VALUES FROM (%L) TO (%L)',
                current_partition_name, partition_start, partition_end
            );
            
            -- Create the same indexes as above for the next partition
            EXECUTE format('
                CREATE INDEX %I ON %I (address, spent)',
                'idx_' || current_partition_name || '_address_spent',
                current_partition_name
            );
            
            EXECUTE format('
                CREATE INDEX %I ON %I (token_ref, spent)',
                'idx_' || current_partition_name || '_token_spent',
                current_partition_name
            );
            
            EXECUTE format('
                CREATE INDEX %I ON %I (address, spent, token_ref)',
                'idx_' || current_partition_name || '_address_spent_token',
                current_partition_name
            );
            
            EXECUTE format('
                CREATE INDEX %I ON %I (address)',
                'idx_' || current_partition_name || '_address',
                current_partition_name
            );
            
            EXECUTE format('
                CREATE INDEX %I ON %I (block_height)',
                'idx_' || current_partition_name || '_block_height',
                current_partition_name
            );
            
            EXECUTE format('
                CREATE INDEX %I ON %I (token_ref)',
                'idx_' || current_partition_name || '_token_ref',
                current_partition_name
            );
            
            RAISE NOTICE 'Created next partition % in advance for block range % to %', 
                current_partition_name, partition_start, partition_end - 1;
        END IF;
    END IF;
    
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- 3. Create a trigger to maintain partitions
CREATE OR REPLACE TRIGGER tr_maintain_utxo_partitions
AFTER INSERT ON blocks
FOR EACH STATEMENT
EXECUTE FUNCTION create_utxo_partitions();

-- 4. Create initial partitions based on existing data
DO $$
DECLARE
    max_block INTEGER;
    i INTEGER;
    partition_start INTEGER;
    partition_end INTEGER;
    partition_name TEXT;
BEGIN
    -- Get current max block height
    SELECT COALESCE(MAX(block_height), 0) INTO max_block FROM utxos;
    
    -- Create partitions in 50,000 block ranges
    FOR i IN 0..(max_block / 50000) LOOP
        partition_start := i * 50000;
        partition_end := (i + 1) * 50000;
        partition_name := 'utxos_p' || partition_start;
        
        -- Create the partition
        EXECUTE format('
            CREATE TABLE %I PARTITION OF utxos_partitioned
            FOR VALUES FROM (%L) TO (%L)',
            partition_name, partition_start, partition_end
        );
        
        -- Create indexes on the partition
        EXECUTE format('
            CREATE INDEX %I ON %I (address, spent)',
            'idx_' || partition_name || '_address_spent',
            partition_name
        );
        
        EXECUTE format('
            CREATE INDEX %I ON %I (token_ref, spent)',
            'idx_' || partition_name || '_token_spent',
            partition_name
        );
        
        EXECUTE format('
            CREATE INDEX %I ON %I (address, spent, token_ref)',
            'idx_' || partition_name || '_address_spent_token',
            partition_name
        );
        
        EXECUTE format('
            CREATE INDEX %I ON %I (address)',
            'idx_' || partition_name || '_address',
            partition_name
        );
        
        EXECUTE format('
            CREATE INDEX %I ON %I (block_height)',
            'idx_' || partition_name || '_block_height',
            partition_name
        );
        
        EXECUTE format('
            CREATE INDEX %I ON %I (token_ref)',
            'idx_' || partition_name || '_token_ref',
            partition_name
        );
        
        RAISE NOTICE 'Created initial partition % for block range % to %', 
            partition_name, partition_start, partition_end - 1;
    END LOOP;
    
    -- Create one more partition for future blocks if needed
    IF max_block > 0 THEN
        partition_start := ((max_block / 50000) + 1) * 50000;
        partition_end := partition_start + 50000;
        partition_name := 'utxos_p' || partition_start;
        
        EXECUTE format('
            CREATE TABLE %I PARTITION OF utxos_partitioned
            FOR VALUES FROM (%L) TO (%L)',
            partition_name, partition_start, partition_end
        );
        
        -- Create indexes on the future partition
        EXECUTE format('
            CREATE INDEX %I ON %I (address, spent)',
            'idx_' || partition_name || '_address_spent',
            partition_name
        );
        
        EXECUTE format('
            CREATE INDEX %I ON %I (token_ref, spent)',
            'idx_' || partition_name || '_token_spent',
            partition_name
        );
        
        EXECUTE format('
            CREATE INDEX %I ON %I (address, spent, token_ref)',
            'idx_' || partition_name || '_address_spent_token',
            partition_name
        );
        
        EXECUTE format('
            CREATE INDEX %I ON %I (address)',
            'idx_' || partition_name || '_address',
            partition_name
        );
        
        EXECUTE format('
            CREATE INDEX %I ON %I (block_height)',
            'idx_' || partition_name || '_block_height',
            partition_name
        );
        
        EXECUTE format('
            CREATE INDEX %I ON %I (token_ref)',
            'idx_' || partition_name || '_token_ref',
            partition_name
        );
        
        RAISE NOTICE 'Created future partition % for block range % to %', 
            partition_name, partition_start, partition_end - 1;
    END IF;
END $$;

-- 5. Copy data from the original table to the partitioned table
-- This is done in batches to avoid long-running transactions
DO $$
DECLARE
    batch_size INTEGER := 100000;
    offset_val INTEGER := 0;
    total_rows BIGINT;
    processed_rows BIGINT := 0;
    start_time TIMESTAMP;
    end_time TIMESTAMP;
    elapsed INTERVAL;
    remaining INTERVAL;
    estimated_total INTERVAL;
    estimated_completion TIMESTAMP;
BEGIN
    -- Get total rows to process
    EXECUTE 'SELECT COUNT(*) FROM utxos' INTO total_rows;
    
    RAISE NOTICE 'Starting to copy % rows from utxos to utxos_partitioned in batches of %', 
        total_rows, batch_size;
    
    start_time := clock_timestamp();
    
    -- Process in batches
    WHILE offset_val < total_rows LOOP
        -- Insert a batch of rows
        EXECUTE format('
            INSERT INTO utxos_partitioned
            SELECT * FROM utxos
            ORDER BY block_height, txid, vout
            LIMIT %s OFFSET %s',
            batch_size, offset_val
        );
        
        GET DIAGNOSTICS processed_rows = ROW_COUNT;
        offset_val := offset_val + batch_size;
        
        -- Calculate progress and estimate time remaining
        end_time := clock_timestamp();
        elapsed := end_time - start_time;
        
        IF total_rows > 0 AND offset_val > 0 THEN
            estimated_total := (elapsed * total_rows / offset_val);
            remaining := estimated_total - elapsed;
            estimated_completion := now() + remaining;
            
            RAISE NOTICE 'Copied %/% rows (%.2f%%) - Estimated completion: %',
                LEAST(offset_val, total_rows),
                total_rows,
                (LEAST(offset_val, total_rows)::float / total_rows) * 100,
                estimated_completion;
        END IF;
        
        -- Commit each batch to avoid long transactions
        COMMIT;
    END LOOP;
    
    RAISE NOTICE 'Completed copying % rows in %', 
        total_rows, 
        clock_timestamp() - start_time;
END $$;

-- 6. Swap the tables
-- First, drop the trigger to prevent it from firing during the swap
DROP TRIGGER IF EXISTS tr_maintain_utxo_partitions ON blocks;

-- Rename tables
ALTER TABLE utxos RENAME TO utxos_old;
ALTER TABLE utxos_partitioned RENAME TO utxos;

-- Recreate the trigger on the new table
CREATE TRIGGER tr_maintain_utxo_partitions
AFTER INSERT ON blocks
FOR EACH STATEMENT
EXECUTE FUNCTION create_utxo_partitions();

-- 7. Verify the data
DO $$
DECLARE
    old_count BIGINT;
    new_count BIGINT;
BEGIN
    EXECUTE 'SELECT COUNT(*) FROM utxos_old' INTO old_count;
    EXECUTE 'SELECT COUNT(*) FROM utxos' INTO new_count;
    
    IF old_count = new_count THEN
        RAISE NOTICE 'Verification successful: % rows in both old and new tables', new_count;
    ELSE
        RAISE EXCEPTION 'Verification failed: old table has % rows, new table has % rows', 
            old_count, new_count;
    END IF;
END $$;

-- 8. Drop the old table (commented out for safety, uncomment after verification)
-- DROP TABLE IF EXISTS utxos_old;

-- 9. Create a view for backward compatibility if needed
CREATE OR REPLACE VIEW v_utxos AS SELECT * FROM utxos;

-- 10. Analyze the new table
ANALYZE utxos;

-- 11. Create a function to manually create a new partition if needed
CREATE OR REPLACE FUNCTION create_utxo_partition_manually(start_block INTEGER)
RETURNS TEXT AS $$
DECLARE
    partition_name TEXT;
    partition_start INTEGER;
    partition_end INTEGER;
BEGIN
    -- Calculate the partition range
    partition_start := (start_block / 50000) * 50000;
    partition_end := partition_start + 50000;
    partition_name := 'utxos_p' || partition_start;
    
    -- Create the partition
    EXECUTE format('
        CREATE TABLE IF NOT EXISTS %I PARTITION OF utxos
        FOR VALUES FROM (%L) TO (%L)',
        partition_name, partition_start, partition_end
    );
    
    -- Create indexes on the new partition
    EXECUTE format('
        CREATE INDEX IF NOT EXISTS %I ON %I (address, spent)',
        'idx_' || partition_name || '_address_spent',
        partition_name
    );
    
    EXECUTE format('
        CREATE INDEX IF NOT EXISTS %I ON %I (token_ref, spent)',
        'idx_' || partition_name || '_token_spent',
        partition_name
    );
    
    EXECUTE format('
        CREATE INDEX IF NOT EXISTS %I ON %I (address, spent, token_ref)',
        'idx_' || partition_name || '_address_spent_token',
        partition_name
    );
    
    EXECUTE format('
        CREATE INDEX IF NOT EXISTS %I ON %I (address)',
        'idx_' || partition_name || '_address',
        partition_name
    );
    
    EXECUTE format('
        CREATE INDEX IF NOT EXISTS %I ON %I (block_height)',
        'idx_' || partition_name || '_block_height',
        partition_name
    );
    
    EXECUTE format('
        CREATE INDEX IF NOT EXISTS %I ON %I (token_ref)',
        'idx_' || partition_name || '_token_ref',
        partition_name
    );
    
    RETURN format('Created partition %s for block range %s to %s', 
                 partition_name, partition_start, partition_end - 1);
END;
$$ LANGUAGE plpgsql;

-- 12. Grant necessary permissions if using different users
-- GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO your_app_user;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO your_app_user;

-- 13. Create a function to check partition coverage
CREATE OR REPLACE FUNCTION check_utxo_partition_coverage()
RETURNS TABLE (
    partition_name TEXT,
    range_start BIGINT,
    range_end BIGINT,
    row_count BIGINT,
    size_pretty TEXT
) AS $$
BEGIN
    RETURN QUERY
    WITH partition_info AS (
        SELECT 
            nmsp_parent.nspname AS parent_schema,
            parent.relname AS parent,
            nmsp_child.nspname AS child_schema,
            child.relname AS child_name,
            pg_get_expr(child.relpartbound, child.oid) AS child_limits,
            pg_size_pretty(pg_total_relation_size(child.oid)) AS size_pretty,
            (SELECT count(*) FROM ONLY public.utxos) as total_rows
        FROM pg_inherits
        JOIN pg_class parent ON pg_inherits.inhparent = parent.oid
        JOIN pg_class child ON pg_inherits.inhrelid = child.oid
        JOIN pg_namespace nmsp_parent ON nmsp_parent.oid = parent.relnamespace
        JOIN pg_namespace nmsp_child ON nmsp_child.oid = child.relnamespace
        WHERE parent.relname = 'utxos'
    )
    SELECT 
        child_name::TEXT,
        split_part(split_part(child_limits, 'FROM (', 2), ')', 1)::BIGINT as range_start,
        split_part(split_part(child_limits, 'TO (', 2), ')', 1)::BIGINT as range_end,
        (SELECT COUNT(*) FROM ONLY public.utxos WHERE block_height >= split_part(split_part(child_limits, 'FROM (', 2), ')', 1)::INTEGER 
                                                AND block_height < split_part(split_part(child_limits, 'TO (', 2), ')', 1)::INTEGER) as row_count,
        size_pretty::TEXT
    FROM partition_info
    ORDER BY range_start;
END;
$$ LANGUAGE plpgsql;

-- 14. Add a comment to document the partitioning
COMMENT ON TABLE utxos IS 'Partitioned table for UTXOs, partitioned by block_height in ranges of 50,000 blocks';

-- 15. Create a function to get partition information
CREATE OR REPLACE FUNCTION get_utxo_partition_info()
RETURNS TABLE (
    partition_name TEXT,
    range_start BIGINT,
    range_end BIGINT,
    row_count BIGINT,
    size_pretty TEXT,
    last_analyzed TIMESTAMPTZ
) AS $$
BEGIN
    RETURN QUERY
    WITH partition_info AS (
        SELECT 
            nmsp_parent.nspname AS parent_schema,
            parent.relname AS parent,
            nmsp_child.nspname AS child_schema,
            child.relname AS child_name,
            pg_get_expr(child.relpartbound, child.oid) AS child_limits,
            pg_size_pretty(pg_total_relation_size(child.oid)) AS size_pretty,
            (SELECT last_analyze FROM pg_stat_all_tables WHERE relid = child.oid) as last_analyzed
        FROM pg_inherits
        JOIN pg_class parent ON pg_inherits.inhparent = parent.oid
        JOIN pg_class child ON pg_inherits.inhrelid = child.oid
        JOIN pg_namespace nmsp_parent ON nmsp_parent.oid = parent.relnamespace
        JOIN pg_namespace nmsp_child ON nmsp_child.oid = child.relnamespace
        WHERE parent.relname = 'utxos'
    )
    SELECT 
        child_name::TEXT,
        split_part(split_part(child_limits, 'FROM (', 2), ')', 1)::BIGINT as range_start,
        split_part(split_part(child_limits, 'TO (', 2), ')', 1)::BIGINT as range_end,
        (SELECT COUNT(*) FROM ONLY public.utxos WHERE block_height >= split_part(split_part(child_limits, 'FROM (', 2), ')', 1)::INTEGER 
                                                AND block_height < split_part(split_part(child_limits, 'TO (', 2), ')', 1)::INTEGER) as row_count,
        size_pretty::TEXT,
        last_analyzed
    FROM partition_info
    ORDER BY range_start;
END;
$$ LANGUAGE plpgsql;

-- 16. Create a function to analyze all partitions
CREATE OR REPLACE FUNCTION analyze_utxo_partitions()
RETURNS VOID AS $$
DECLARE
    r RECORD;
BEGIN
    RAISE NOTICE 'Analyzing all utxo partitions...';
    
    FOR r IN 
        SELECT n.nspname, c.relname 
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r' 
        AND n.nspname = 'public' 
        AND c.relname LIKE 'utxos_p%'
    LOOP
        EXECUTE format('ANALYZE %I.%I', r.nspname, r.relname);
        RAISE NOTICE 'Analyzed %.%', r.nspname, r.relname;
    END LOOP;
    
    RAISE NOTICE 'Analysis complete.';
END;
$$ LANGUAGE plpgsql;

-- 17. Create a function to get the current partition for a block height
CREATE OR REPLACE FUNCTION get_utxo_partition(block_height INTEGER)
RETURNS TEXT AS $$
DECLARE
    partition_start INTEGER;
    partition_name TEXT;
BEGIN
    partition_start := (block_height / 50000) * 50000;
    partition_name := 'utxos_p' || partition_start;
    RETURN partition_name;
END;
$$ LANGUAGE plpgsql;

-- 18. Print final instructions
\echo '
\n=== UTXO Table Partitioning Complete ===\n'
\echo '1. The utxos table has been partitioned by block_height in ranges of 50,000 blocks.'
\echo '2. A trigger has been created to automatically create new partitions as needed.'
\echo '3. Old data has been copied to the new partitioned table.'
\echo '4. The original table has been renamed to utxos_old.'
\echo '5. A view named v_utxos has been created for backward compatibility.'
\echo '\nTo verify the partitioning, run:'
\echo '  SELECT * FROM get_utxo_partition_info() ORDER BY range_start;'
\echo '\nTo manually create a new partition for a specific block height, run:'
\echo '  SELECT create_utxo_partition_manually(block_height);'
\echo '\nTo analyze all partitions:'
\echo '  SELECT analyze_utxo_partitions();'
\echo '\nOnce you have verified that the new partitioned table is working correctly,'
\echo 'you can drop the old table with:'
\echo '  DROP TABLE IF EXISTS utxos_old;'
\echo '\n=== End of Script ===\n'

-- 19. Commit the transaction
COMMIT;
