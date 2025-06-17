-- /docker/db-init-scripts/05-backup-functions.sql
-- Backup and restore functions for RXinDexer database

-- Function to list available backups
CREATE OR REPLACE FUNCTION list_available_backups()
RETURNS TABLE (
    backup_id uuid,
    backup_name text,
    backup_size bigint,
    backup_time timestamp with time zone,
    backup_type text,
    status text,
    notes text
) AS $$
BEGIN
    -- Create backups table if it doesn't exist
    CREATE TABLE IF NOT EXISTS monitor.backup_history (
        backup_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        backup_name text NOT NULL,
        backup_size bigint,
        backup_time timestamp with time zone DEFAULT now(),
        backup_type text NOT NULL CHECK (backup_type IN ('full', 'incremental', 'differential')),
        status text NOT NULL CHECK (status IN ('in_progress', 'completed', 'failed', 'deleted')),
        notes text,
        created_at timestamp with time zone DEFAULT now(),
        updated_at timestamp with time zone DEFAULT now()
    );
    
    -- Grant permissions
    GRANT SELECT, INSERT, UPDATE ON monitor.backup_history TO monitor;
    
    -- Return the list of available backups
    RETURN QUERY
    SELECT 
        backup_id,
        backup_name,
        backup_size,
        backup_time,
        backup_type,
        status,
        notes
    FROM monitor.backup_history
    WHERE status = 'completed'
    ORDER BY backup_time DESC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Function to create a logical backup
CREATE OR REPLACE FUNCTION create_logical_backup(
    p_backup_name text DEFAULT NULL,
    p_backup_type text DEFAULT 'full',
    p_tables text[] DEFAULT NULL,
    p_exclude_tables text[] DEFAULT NULL,
    p_compress boolean DEFAULT true,
    p_notes text DEFAULT NULL
)
RETURNS jsonb AS $$
DECLARE
    v_backup_id uuid;
    v_backup_name text;
    v_backup_file text;
    v_backup_dir text := '/backups';
    v_tables_clause text := '';
    v_exclude_clause text := '';
    v_compress_flag text := '';
    v_command text;
    v_result int;
    v_start_time timestamp with time zone := clock_timestamp();
    v_end_time timestamp with time zone;
    v_duration interval;
    v_backup_size bigint;
    v_error_message text;
    v_success boolean := true;
    v_backup_record jsonb;
BEGIN
    -- Generate backup ID and name if not provided
    v_backup_id := gen_random_uuid();
    v_backup_name := COALESCE(
        p_backup_name, 
        'rxindexer_backup_' || to_char(now(), 'YYYYMMDD_HH24MISS') || '_' || substr(v_backup_id::text, 1, 8)
    );
    
    -- Set compression flag
    IF p_compress THEN
        v_compress_flag := '-Fc';
        v_backup_file := v_backup_dir || '/' || v_backup_name || '.dump';
    ELSE
        v_compress_flag := '-Fp';
        v_backup_file := v_backup_dir || '/' || v_backup_name || '.sql';
    END IF;
    
    -- Handle table selection
    IF p_tables IS NOT NULL AND array_length(p_tables, 1) > 0 THEN
        v_tables_clause := ' -t ' || array_to_string(p_tables, ' -t ');
    END IF;
    
    -- Handle table exclusion
    IF p_exclude_tables IS NOT NULL AND array_length(p_exclude_tables, 1) > 0 THEN
        v_exclude_clause := ' -T ' || array_to_string(p_exclude_tables, ' -T ');
    END IF;
    
    -- Insert backup record
    INSERT INTO monitor.backup_history (
        backup_id,
        backup_name,
        backup_type,
        status,
        notes
    ) VALUES (
        v_backup_id,
        v_backup_name,
        p_backup_type,
        'in_progress',
        p_notes
    ) RETURNING to_jsonb(monitor.backup_history.*) INTO v_backup_record;
    
    -- Build and execute the backup command
    v_command := format(
        'pg_dump -h %s -U %s -d %s %s %s %s -f %s',
        current_setting('listen_addresses'),
        current_user,
        current_database(),
        v_compress_flag,
        v_tables_clause,
        v_exclude_clause,
        v_backup_file
    );
    
    BEGIN
        -- Execute the backup command
        EXECUTE 'COPY (SELECT 1) TO PROGRAM ' || quote_literal(v_command);
        
        -- Get backup size
        SELECT pg_stat_file(v_backup_file)::jsonb->>'size'::bigint INTO v_backup_size;
        
        -- Update backup record
        v_end_time := clock_timestamp();
        v_duration := v_end_time - v_start_time;
        
        UPDATE monitor.backup_history
        SET 
            backup_size = v_backup_size,
            status = 'completed',
            updated_at = v_end_time,
            notes = COALESCE(notes, '') || E'\nDuration: ' || v_duration || 
                   E'\nSize: ' || pg_size_pretty(v_backup_size)
        WHERE backup_id = v_backup_id
        RETURNING to_jsonb(monitor.backup_history.*) INTO v_backup_record;
        
    EXCEPTION WHEN OTHERS THEN
        v_error_message := SQLERRM;
        v_success := false;
        v_end_time := clock_timestamp();
        
        -- Update backup record with error
        UPDATE monitor.backup_history
        SET 
            status = 'failed',
            updated_at = v_end_time,
            notes = COALESCE(notes, '') || E'\nError: ' || v_error_message
        WHERE backup_id = v_backup_id
        RETURNING to_jsonb(monitor.backup_history.*) INTO v_backup_record;
        
        -- Re-raise the exception
        RAISE EXCEPTION 'Backup failed: %', v_error_message;
    END;
    
    RETURN jsonb_build_object(
        'success', v_success,
        'backup_id', v_backup_id,
        'backup_name', v_backup_name,
        'backup_file', v_backup_file,
        'backup_size', v_backup_size,
        'start_time', v_start_time,
        'end_time', v_end_time,
        'duration', v_duration,
        'details', v_backup_record
    );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Function to restore from a logical backup
CREATE OR REPLACE FUNCTION restore_from_backup(
    p_backup_id uuid,
    p_database_name text DEFAULT NULL,
    p_clean boolean DEFAULT false,
    p_create_database boolean DEFAULT false,
    p_drop_objects boolean DEFAULT false,
    p_tables text[] DEFAULT NULL,
    p_exclude_tables text[] DEFAULT NULL
)
RETURNS jsonb AS $$
DECLARE
    v_backup_record record;
    v_backup_file text;
    v_database_name text;
    v_tables_clause text := '';
    v_exclude_clause text := '';
    v_clean_flag text := '';
    v_create_flag text := '';
    v_drop_objects_flag text := '';
    v_command text;
    v_result int;
    v_start_time timestamp with time zone := clock_timestamp();
    v_end_time timestamp with time zone;
    v_duration interval;
    v_error_message text;
    v_success boolean := true;
    v_restore_record jsonb;
BEGIN
    -- Get backup record
    SELECT * INTO v_backup_record
    FROM monitor.backup_history
    WHERE backup_id = p_backup_id
    AND status = 'completed';
    
    IF v_backup_record IS NULL THEN
        RAISE EXCEPTION 'Backup with ID % not found or not completed', p_backup_id;
    END IF;
    
    -- Set database name
    v_database_name := COALESCE(p_database_name, current_database());
    
    -- Build backup file path
    v_backup_file := '/backups/' || v_backup_record.backup_name || 
                     CASE 
                         WHEN v_backup_record.backup_type = 'full' AND v_backup_record.backup_name LIKE '%.dump' THEN '.dump'
                         WHEN v_backup_record.backup_type = 'full' THEN '.sql'
                         ELSE '.dump'
                     END;
    
    -- Check if backup file exists
    IF NOT EXISTS (SELECT 1 FROM pg_ls_dir('/backups') WHERE pg_ls_dir = v_backup_record.backup_name || 
                  CASE 
                      WHEN v_backup_record.backup_type = 'full' AND v_backup_record.backup_name LIKE '%.dump' THEN '.dump'
                      WHEN v_backup_record.backup_type = 'full' THEN '.sql'
                      ELSE '.dump'
                  END) THEN
        RAISE EXCEPTION 'Backup file not found: %', v_backup_file;
    END IF;
    
    -- Handle table selection
    IF p_tables IS NOT NULL AND array_length(p_tables, 1) > 0 THEN
        v_tables_clause := ' -t ' || array_to_string(p_tables, ' -t ');
    END IF;
    
    -- Handle table exclusion
    IF p_exclude_tables IS NOT NULL AND array_length(p_exclude_tables, 1) > 0 THEN
        v_exclude_clause := ' -T ' || array_to_string(p_exclude_tables, ' -T ');
    END IF;
    
    -- Set clean flag
    IF p_clean THEN
        v_clean_flag := '--clean --if-exists';
    END IF;
    
    -- Set create database flag
    IF p_create_database THEN
        v_create_flag := '--create';
    END IF;
    
    -- Set drop objects flag
    IF p_drop_objects THEN
        v_drop_objects_flag := '--clean';
    END IF;
    
    -- Build and execute the restore command
    IF v_backup_file LIKE '%.dump' THEN
        -- For custom format dump
        v_command := format(
            'pg_restore -h %s -U %s -d %s %s %s %s %s %s',
            current_setting('listen_addresses'),
            current_user,
            v_database_name,
            v_clean_flag,
            v_create_flag,
            v_drop_objects_flag,
            v_tables_clause,
            v_backup_file
        );
    ELSE
        -- For plain SQL dump
        v_command := format(
            'psql -h %s -U %s -d %s -f %s',
            current_setting('listen_addresses'),
            current_user,
            v_database_name,
            v_backup_file
        );
    END IF;
    
    -- Create restore record
    INSERT INTO monitor.restore_history (
        restore_id,
        backup_id,
        database_name,
        status,
        command,
        started_at
    ) VALUES (
        gen_random_uuid(),
        p_backup_id,
        v_database_name,
        'in_progress',
        v_command,
        v_start_time
    ) RETURNING to_jsonb(monitor.restore_history.*) INTO v_restore_record;
    
    BEGIN
        -- Execute the restore command
        EXECUTE 'COPY (SELECT 1) TO PROGRAM ' || quote_literal(v_command);
        
        -- Update restore record
        v_end_time := clock_timestamp();
        v_duration := v_end_time - v_start_time;
        
        UPDATE monitor.restore_history
        SET 
            status = 'completed',
            completed_at = v_end_time,
            duration = v_duration,
            success = true
        WHERE restore_id = (v_restore_record->>'restore_id')::uuid
        RETURNING to_jsonb(monitor.restore_history.*) INTO v_restore_record;
        
    EXCEPTION WHEN OTHERS THEN
        v_error_message := SQLERRM;
        v_success := false;
        v_end_time := clock_timestamp();
        
        -- Update restore record with error
        UPDATE monitor.restore_history
        SET 
            status = 'failed',
            completed_at = v_end_time,
            duration = v_end_time - v_start_time,
            success = false,
            error_message = v_error_message
        WHERE restore_id = (v_restore_record->>'restore_id')::uuid
        RETURNING to_jsonb(monitor.restore_history.*) INTO v_restore_record;
        
        -- Re-raise the exception
        RAISE EXCEPTION 'Restore failed: %', v_error_message;
    END;
    
    RETURN jsonb_build_object(
        'success', v_success,
        'restore_id', v_restore_record->>'restore_id',
        'backup_id', p_backup_id,
        'database_name', v_database_name,
        'start_time', v_start_time,
        'end_time', v_end_time,
        'duration', v_duration,
        'details', v_restore_record
    );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Create restore history table if it doesn't exist
CREATE TABLE IF NOT EXISTS monitor.restore_history (
    restore_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    backup_id uuid REFERENCES monitor.backup_history(backup_id),
    database_name text NOT NULL,
    status text NOT NULL CHECK (status IN ('in_progress', 'completed', 'failed')),
    command text,
    error_message text,
    success boolean,
    started_at timestamp with time zone DEFAULT now(),
    completed_at timestamp with time zone,
    duration interval GENERATED ALWAYS AS (completed_at - started_at) STORED,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

-- Create indexes on backup and restore history tables
CREATE INDEX IF NOT EXISTS idx_backup_history_backup_time ON monitor.backup_history(backup_time);
CREATE INDEX IF NOT EXISTS idx_backup_history_status ON monitor.backup_history(status);
CREATE INDEX IF NOT EXISTS idx_restore_history_backup_id ON monitor.restore_history(backup_id);
CREATE INDEX IF NOT EXISTS idx_restore_history_status ON monitor.restore_history(status);

-- Grant permissions on backup and restore functions and tables
GRANT EXECUTE ON FUNCTION list_available_backups() TO monitor;
GRANT EXECUTE ON FUNCTION create_logical_backup(text, text, text[], text[], boolean, text) TO monitor;
GRANT EXECUTE ON FUNCTION restore_from_backup(uuid, text, boolean, boolean, boolean, text[], text[]) TO monitor;
GRANT SELECT, INSERT, UPDATE ON monitor.backup_history TO monitor;
GRANT SELECT, INSERT, UPDATE ON monitor.restore_history TO monitor;
GRANT USAGE ON SCHEMA monitor TO monitor;
