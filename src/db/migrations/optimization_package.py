# /Users/radiant/Desktop/RXinDexer/src/db/migrations/optimization_package.py
# This file packages all of our database optimizations into a single migration
# It ensures all optimizations are included in fresh installations

import logging
import os
from sqlalchemy import create_engine, text
from alembic import op
import sqlalchemy as sa

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Migration revision ID
revision = '20250527_optimize_balance_queries'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    """Apply all database optimizations in a single migration"""
    conn = op.get_bind()
    
    logger.info("Applying database performance optimizations...")
    
    # Create refresh tracking table if it doesn't exist
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS refresh_tracking (
            view_name VARCHAR PRIMARY KEY,
            last_refresh TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """))
    
    # Create the materialized view for address balances
    conn.execute(text("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS address_balances AS
        SELECT 
            address,
            SUM(amount) as total_balance
        FROM utxos
        WHERE spent = FALSE
        GROUP BY address
    """))
    
    # Create indexes on the materialized view
    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_address_balances_address ON address_balances (address)
    """))
    
    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_address_balances_balance ON address_balances (total_balance DESC)
    """))
    
    # Create the refresh function
    conn.execute(text("""
        CREATE OR REPLACE FUNCTION refresh_balances_now() RETURNS VOID AS $$
        DECLARE
            start_time TIMESTAMP := clock_timestamp();
            last_refresh TIMESTAMP;
        BEGIN
            -- Check when the view was last refreshed
            SELECT refresh_tracking.last_refresh INTO last_refresh
            FROM refresh_tracking
            WHERE view_name = 'address_balances';
            
            -- Only refresh if it's been more than 1 minute since the last refresh
            -- This prevents excessive refreshes under high load
            IF last_refresh IS NULL OR last_refresh < NOW() - INTERVAL '1 minute' THEN
                -- Refresh the materialized view concurrently if possible
                BEGIN
                    REFRESH MATERIALIZED VIEW CONCURRENTLY address_balances;
                    EXCEPTION WHEN OTHERS THEN
                        -- Fall back to regular refresh if concurrent refresh fails
                        REFRESH MATERIALIZED VIEW address_balances;
                END;
                
                -- Update the last refresh time
                UPDATE refresh_tracking
                SET last_refresh = NOW()
                WHERE view_name = 'address_balances';
                
                -- Insert if not exists
                IF NOT FOUND THEN
                    INSERT INTO refresh_tracking (view_name, last_refresh)
                    VALUES ('address_balances', NOW());
                END IF;
                
                RAISE NOTICE 'Refreshed address_balances in % ms', 
                            extract(millisecond from clock_timestamp() - start_time);
            ELSE
                RAISE NOTICE 'Skipped refresh of address_balances (last refreshed % ago)', 
                            NOW() - last_refresh;
            END IF;
        END;
        $$ LANGUAGE plpgsql;
    """))
    
    # Create optimized function for balance calculations
    conn.execute(text("""
        CREATE OR REPLACE FUNCTION get_address_balance_efficient(address_param VARCHAR) 
        RETURNS NUMERIC AS $$
        DECLARE
            balance NUMERIC;
        BEGIN
            -- Get balance from materialized view (much faster)
            SELECT total_balance INTO balance
            FROM address_balances
            WHERE address = address_param;
            
            -- Return 0 if not found
            RETURN COALESCE(balance, 0);
        END;
        $$ LANGUAGE plpgsql;
    """))
    
    # Create function to eliminate the slow HAVING query
    conn.execute(text("""
        CREATE OR REPLACE FUNCTION get_large_balances_direct(threshold NUMERIC)
        RETURNS TABLE(address VARCHAR, total_balance NUMERIC) AS $$
        BEGIN
            -- Use the materialized view instead of the expensive query
            RETURN QUERY
            SELECT a.address, a.total_balance
            FROM address_balances a
            WHERE a.total_balance > threshold;
        END;
        $$ LANGUAGE plpgsql;
    """))
    
    # Create function to update holder balances efficiently
    conn.execute(text("""
        CREATE OR REPLACE FUNCTION update_holder_balances_efficient() RETURNS INTEGER AS $$
        DECLARE
            updated_count INTEGER := 0;
        BEGIN
            -- Make sure the materialized view is up to date
            PERFORM refresh_balances_now();
            
            -- Update holders table from the materialized view
            WITH updated_holders AS (
                UPDATE holders h
                SET rxd_balance = a.total_balance,
                    updated_at = NOW()
                FROM address_balances a
                WHERE h.address = a.address
                AND h.rxd_balance <> a.total_balance
                RETURNING h.id
            )
            SELECT COUNT(*) INTO updated_count FROM updated_holders;
            
            -- Insert any new addresses from the materialized view
            WITH new_addresses AS (
                INSERT INTO holders (address, rxd_balance, first_seen_at, last_seen_at, updated_at)
                SELECT 
                    a.address, 
                    a.total_balance,
                    NOW(),
                    NOW(),
                    NOW()
                FROM address_balances a
                LEFT JOIN holders h ON a.address = h.address
                WHERE h.id IS NULL
                AND a.total_balance > 0
                RETURNING id
            )
            SELECT updated_count + COUNT(*) INTO updated_count FROM new_addresses;
            
            RETURN updated_count;
        END;
        $$ LANGUAGE plpgsql;
    """))
    
    # Create function to intercept the creation of temp_balances
    conn.execute(text("""
        CREATE OR REPLACE FUNCTION create_temp_balances_efficient() RETURNS VOID AS $$
        BEGIN
            -- Drop existing table if it exists
            DROP TABLE IF EXISTS temp_balances;
            
            -- Create temp table from materialized view instead of aggregating UTXO table
            CREATE TEMPORARY TABLE temp_balances AS
            SELECT address, total_balance AS balance
            FROM address_balances;
            
            -- Log that we used the efficient version
            RAISE NOTICE 'Created temp_balances using materialized view (optimized)';
        END;
        $$ LANGUAGE plpgsql;
    """))
    
    # Create the optimized view for unspent UTXOs
    conn.execute(text("""
        CREATE OR REPLACE VIEW optimized_utxos_unspent AS
        SELECT address, amount, txid, vout, spent, spent_txid, block_height, block_hash, created_at, updated_at
        FROM utxos
        WHERE spent = FALSE;
    """))
    
    # Create optimized indexes
    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS idx_utxos_address_spent ON utxos (address) WHERE spent = FALSE;
        CREATE INDEX IF NOT EXISTS idx_utxos_block_height ON utxos (block_height);
        CREATE INDEX IF NOT EXISTS idx_holders_address ON holders (address);
        CREATE INDEX IF NOT EXISTS idx_holders_balance ON holders (rxd_balance DESC);
    """))
    
    # Disable the slow query in favor of the optimized version
    conn.execute(text("""
        CREATE OR REPLACE FUNCTION get_addresses_with_large_balances(threshold NUMERIC) 
        RETURNS TABLE(address VARCHAR, balance NUMERIC) AS $$
        BEGIN
            -- IMPORTANT: We use the materialized view instead of the slow query
            -- This completely eliminates the slow query pattern:
            -- SELECT address, SUM(amount) FROM utxos WHERE spent = FALSE GROUP BY address HAVING SUM(amount) > threshold
            RETURN QUERY
            SELECT a.address, a.total_balance
            FROM address_balances a
            WHERE a.total_balance > threshold;
        END;
        $$ LANGUAGE plpgsql;
    """))
    
    # Create a function to log large balances without the expensive query
    conn.execute(text("""
        CREATE OR REPLACE FUNCTION log_large_balances(threshold NUMERIC) RETURNS INTEGER AS $$
        DECLARE
            count_large INTEGER;
        BEGIN
            -- Use the materialized view instead of the slow query
            SELECT COUNT(*) INTO count_large
            FROM address_balances
            WHERE total_balance > threshold;
            
            RAISE NOTICE '% addresses have balances larger than %', count_large, threshold;
            RETURN count_large;
        END;
        $$ LANGUAGE plpgsql;
    """))
    
    # Add cache warming function
    conn.execute(text("""
        CREATE OR REPLACE FUNCTION warm_database_cache() RETURNS VOID AS $$
        BEGIN
            -- Load the materialized view into memory
            PERFORM COUNT(*) FROM address_balances;
            
            -- Load the most frequently accessed indices into memory
            PERFORM COUNT(*) FROM utxos WHERE spent = FALSE;
            PERFORM COUNT(*) FROM holders;
            
            RAISE NOTICE 'Database cache warmed up';
        END;
        $$ LANGUAGE plpgsql;
    """))
    
    # Create a function to monitor and terminate slow queries
    conn.execute(text("""
        CREATE OR REPLACE FUNCTION monitor_and_terminate_slow_queries() RETURNS INTEGER AS $$
        DECLARE
            killed INTEGER := 0;
            slow_query RECORD;
        BEGIN
            -- Find slow queries matching the problematic pattern
            FOR slow_query IN
                SELECT 
                    pid,
                    query,
                    EXTRACT(EPOCH FROM (NOW() - query_start)) AS duration_sec
                FROM pg_stat_activity
                WHERE state = 'active'
                  AND query LIKE '%SELECT address, SUM(amount) as total_balance%FROM utxos%'
                  AND query_start < NOW() - INTERVAL '5 seconds'
            LOOP
                -- Terminate the query
                PERFORM pg_terminate_backend(slow_query.pid);
                killed := killed + 1;
                RAISE NOTICE 'Terminated slow query (pid: %, running for % sec): %',
                            slow_query.pid, slow_query.duration_sec, left(slow_query.query, 100);
            END LOOP;
            
            RETURN killed;
        END;
        $$ LANGUAGE plpgsql;
    """))
    
    # Create a function that will be called by our monitoring script
    conn.execute(text("""
        CREATE OR REPLACE FUNCTION run_optimization_maintenance() RETURNS VOID AS $$
        BEGIN
            -- Refresh materialized views
            PERFORM refresh_balances_now();
            
            -- Terminate any slow queries
            PERFORM monitor_and_terminate_slow_queries();
            
            -- Warm the cache
            PERFORM warm_database_cache();
            
            -- Analyze tables
            ANALYZE utxos;
            ANALYZE holders;
            ANALYZE address_balances;
            
            RAISE NOTICE 'Optimization maintenance completed';
        END;
        $$ LANGUAGE plpgsql;
    """))
    
    # Initial refresh of the materialized view
    conn.execute(text("SELECT refresh_balances_now()"))
    
    # Initial cache warming
    conn.execute(text("SELECT warm_database_cache()"))
    
    logger.info("Database optimizations applied successfully")


def downgrade():
    """Remove all optimizations"""
    conn = op.get_bind()
    
    # Drop functions
    for function in [
        "refresh_balances_now()",
        "get_address_balance_efficient(VARCHAR)",
        "get_large_balances_direct(NUMERIC)",
        "update_holder_balances_efficient()",
        "create_temp_balances_efficient()",
        "get_addresses_with_large_balances(NUMERIC)",
        "log_large_balances(NUMERIC)",
        "warm_database_cache()",
        "monitor_and_terminate_slow_queries()",
        "run_optimization_maintenance()"
    ]:
        conn.execute(text(f"DROP FUNCTION IF EXISTS {function}"))
    
    # Drop view
    conn.execute(text("DROP VIEW IF EXISTS optimized_utxos_unspent"))
    
    # Drop materialized view
    conn.execute(text("DROP MATERIALIZED VIEW IF EXISTS address_balances"))
    
    # Drop tracking table
    conn.execute(text("DROP TABLE IF EXISTS refresh_tracking"))
    
    logger.info("Database optimizations removed")
