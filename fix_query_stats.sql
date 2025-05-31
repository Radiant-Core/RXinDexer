-- /Users/radiant/Desktop/RXinDexer/fix_query_stats.sql
-- This script fixes the missing unique constraint on query_stats table

-- First, check if the table exists and drop if needed
DROP TABLE IF EXISTS query_stats;

-- Recreate the table with proper constraints
CREATE TABLE query_stats (
    id SERIAL PRIMARY KEY,
    query_pattern TEXT NOT NULL,
    total_calls INTEGER NOT NULL DEFAULT 0,
    total_duration_ms BIGINT NOT NULL DEFAULT 0,
    avg_duration_ms FLOAT NOT NULL DEFAULT 0,
    first_called TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    last_called TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT unique_query_pattern UNIQUE (query_pattern)
);

-- Create an index for faster lookups
CREATE INDEX idx_query_stats_pattern ON query_stats (query_pattern);

-- Fix the log_and_analyze_query function
CREATE OR REPLACE FUNCTION log_and_analyze_query(query_text TEXT, duration_ms INTEGER) RETURNS VOID AS $$
DECLARE
    pattern TEXT;
BEGIN
    -- Extract a generic pattern from the query
    pattern := regexp_replace(
        regexp_replace(query_text, E'\'[^\']*\'', '?', 'g'), -- Replace string literals
        E'\\d+', '?', 'g' -- Replace numbers
    );
    
    -- Normalize whitespace
    pattern := regexp_replace(pattern, E'\\s+', ' ', 'g');
    
    -- Truncate very long patterns
    IF length(pattern) > 500 THEN
        pattern := substring(pattern, 1, 500) || '...';
    END IF;
    
    -- Log the query stats
    INSERT INTO query_stats (query_pattern, total_calls, total_duration_ms, avg_duration_ms, last_called)
    VALUES (pattern, 1, duration_ms, duration_ms, NOW())
    ON CONFLICT (query_pattern) DO UPDATE
    SET 
        total_calls = query_stats.total_calls + 1,
        total_duration_ms = query_stats.total_duration_ms + duration_ms,
        avg_duration_ms = (query_stats.total_duration_ms + duration_ms) / (query_stats.total_calls + 1),
        last_called = NOW();
END;
$$ LANGUAGE plpgsql;

-- Fix the capture_slow_query function
CREATE OR REPLACE FUNCTION capture_slow_query() RETURNS TRIGGER AS $$
DECLARE
    query_text TEXT;
    duration_ms INTEGER;
BEGIN
    -- Get the query text and duration
    SELECT current_query() INTO query_text;
    SELECT EXTRACT(MILLISECONDS FROM (clock_timestamp() - statement_timestamp())) INTO duration_ms;
    
    -- Only log slow queries (over 100ms)
    IF duration_ms > 100 THEN
        PERFORM log_and_analyze_query(query_text, duration_ms);
    END IF;
    
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
