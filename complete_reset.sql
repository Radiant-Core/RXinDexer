-- Complete reset of database state with ALL required columns
DROP TABLE IF EXISTS sync_state;

-- Create a fresh sync_state table with ALL required columns
CREATE TABLE sync_state (
    id INTEGER PRIMARY KEY,
    current_height INTEGER NOT NULL DEFAULT 0,
    current_hash VARCHAR(64),
    is_syncing SMALLINT NOT NULL DEFAULT 0,
    last_updated_at FLOAT,
    last_error TEXT,
    current_chainwork VARCHAR(64),
    created_at TIMESTAMP WITHOUT TIME ZONE,
    updated_at TIMESTAMP WITHOUT TIME ZONE
);

-- Insert an initial record
INSERT INTO sync_state (id, current_height, is_syncing, last_updated_at, created_at, updated_at)
VALUES (1, 0, 0, extract(epoch from now()), now(), now());

-- Verify the record was created
SELECT * FROM sync_state;
