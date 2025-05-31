-- Reset database state by dropping and recreating the sync_state table
DROP TABLE IF EXISTS sync_state;

-- Create a fresh sync_state table with all required columns
CREATE TABLE sync_state (
    id INTEGER PRIMARY KEY,
    current_height INTEGER NOT NULL DEFAULT 0,
    is_syncing SMALLINT NOT NULL DEFAULT 0,
    last_updated_at FLOAT,
    last_error TEXT,
    current_chainwork VARCHAR(64)
);

-- Insert an initial record
INSERT INTO sync_state (id, current_height, is_syncing, last_updated_at)
VALUES (1, 0, 0, extract(epoch from now()));

-- Verify the record was created
SELECT * FROM sync_state;
