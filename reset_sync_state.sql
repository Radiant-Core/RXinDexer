-- /Users/radiant/Desktop/RXinDexer/reset_sync_state.sql
-- This script completely resets the sync_state table to resolve transaction issues

-- First drop the sync_state table to completely clean it
DROP TABLE IF EXISTS sync_state;

-- Create a fresh sync_state table with all required columns
CREATE TABLE sync_state (
    id INTEGER PRIMARY KEY,
    current_height INTEGER NOT NULL DEFAULT 0,
    is_syncing SMALLINT NOT NULL DEFAULT 0,
    last_updated_at FLOAT,
    last_error TEXT,
    current_hash VARCHAR(64),
    current_chainwork VARCHAR(64),
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Insert a clean initial record
INSERT INTO sync_state (
    id, 
    current_height, 
    is_syncing, 
    last_updated_at, 
    current_hash, 
    current_chainwork
) VALUES (
    1, 
    0, 
    0, 
    extract(epoch from now()), 
    '', 
    ''
);

-- Display the result
SELECT * FROM sync_state;
