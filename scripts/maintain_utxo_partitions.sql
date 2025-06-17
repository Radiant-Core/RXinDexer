-- scripts/maintain_utxo_partitions.sql
-- Function to maintain utxo partitions that can be called directly or from a trigger

-- Drop the function if it exists to avoid conflicts
DROP FUNCTION IF EXISTS maintain_utxo_partitions() CASCADE;

-- Create the function
CREATE OR REPLACE FUNCTION maintain_utxo_partitions()
RETURNS TRIGGER AS $$
DECLARE
    max_block INTEGER;
    partition_start INTEGER;
    partition_end INTEGER;
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
    IF NOT partition_exists AND partition_start >= 0 THEN
        -- Create the partition
        EXECUTE format('
            CREATE TABLE %I PARTITION OF utxos
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
                CREATE TABLE %I PARTITION OF utxos
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

-- Drop the existing trigger if it exists
DROP TRIGGER IF EXISTS tr_maintain_utxo_partitions ON blocks;

-- Recreate the trigger
CREATE TRIGGER tr_maintain_utxo_partitions
AFTER INSERT ON blocks
FOR EACH STATEMENT
EXECUTE FUNCTION maintain_utxo_partitions();

-- Create a function that can be called directly
CREATE OR REPLACE FUNCTION create_utxo_partitions()
RETURNS VOID AS $$
BEGIN
    PERFORM maintain_utxo_partitions();
    RETURN;
END;
$$ LANGUAGE plpgsql;
