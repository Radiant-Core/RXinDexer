# /Users/radiant/Desktop/RXinDexer/src/db/migrations/force_optimization.py
# This script creates SQL hooks and optimizations to intercept and redirect slow queries
# It forces the use of the materialized view for all balance-related operations

import os
import sys
import logging
from sqlalchemy import create_engine, text
from datetime import datetime

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import Config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def apply_force_optimizations():
    """
    Apply aggressive optimizations to intercept and redirect slow queries.
    This will create database-level functions that the application will use
    instead of direct SQL to ensure performance.
    """
    logger.info("Starting forced optimization application")
    
    # Get database connection from config
    engine = create_engine(Config.DATABASE_URL)
    
    with engine.connect() as conn:
        # First ensure our materialized view is up to date
        logger.info("Refreshing materialized view")
        conn.execute(text("SELECT refresh_balances_now()"))
        
        # Create optimized stored procedures that application can use
        logger.info("Creating optimized stored procedures")
        
        # This function completely replaces the slow temp table creation
        conn.execute(text("""
            CREATE OR REPLACE FUNCTION get_temp_balances() 
            RETURNS TABLE(address VARCHAR, balance NUMERIC) AS $$
            BEGIN
                RETURN QUERY SELECT a.address, a.total_balance 
                FROM address_balances a;
            END;
            $$ LANGUAGE plpgsql;
        """))
        
        # Replace the large balance query
        conn.execute(text("""
            CREATE OR REPLACE FUNCTION get_large_balances(threshold NUMERIC) 
            RETURNS TABLE(address VARCHAR, total_balance NUMERIC) AS $$
            BEGIN
                RETURN QUERY 
                SELECT a.address, a.total_balance 
                FROM address_balances a
                WHERE a.total_balance > threshold;
            END;
            $$ LANGUAGE plpgsql;
        """))
        
        # Create a super efficient function for updating holder balances
        conn.execute(text("""
            CREATE OR REPLACE FUNCTION update_holder_balances_efficient() 
            RETURNS INTEGER AS $$
            DECLARE
                update_count INTEGER;
            BEGIN
                -- Ensure fresh data
                PERFORM refresh_balances_now();
                
                -- Single operation to update all holders
                WITH updated AS (
                    INSERT INTO holders (address, rxd_balance, token_balances, first_seen_at, last_updated_at)
                    SELECT 
                        address, 
                        total_balance, 
                        '{}'::jsonb, 
                        NOW(), 
                        NOW() 
                    FROM address_balances
                    ON CONFLICT (address) DO UPDATE 
                    SET 
                        rxd_balance = EXCLUDED.rxd_balance,
                        last_updated_at = NOW()
                    RETURNING address
                )
                SELECT COUNT(*) INTO update_count FROM updated;
                
                -- Reset balances for addresses no longer with UTXOs
                UPDATE holders 
                SET rxd_balance = 0, 
                    last_updated_at = NOW() 
                WHERE rxd_balance > 0 
                AND address NOT IN (SELECT address FROM address_balances);
                
                RETURN update_count;
            END;
            $$ LANGUAGE plpgsql;
        """))
        
        # Create direct database level functions for common slow operations
        conn.execute(text("""
            CREATE OR REPLACE FUNCTION create_utxo_temp_table() 
            RETURNS VOID AS $$
            BEGIN
                DROP TABLE IF EXISTS temp_balances;
                
                CREATE TEMP TABLE temp_balances AS
                SELECT address, total_balance AS balance
                FROM address_balances;
                
                -- For logging/monitoring
                RAISE NOTICE 'Created temp_balances using materialized view (optimized)';
            END;
            $$ LANGUAGE plpgsql;
        """))
        
        # Replace direct database usage in the UTXO parser
        conn.execute(text("""
            CREATE OR REPLACE FUNCTION log_large_balances(threshold NUMERIC DEFAULT 1000000000) 
            RETURNS VOID AS $$
            DECLARE
                rec RECORD;
            BEGIN
                FOR rec IN 
                    SELECT address, total_balance
                    FROM address_balances
                    WHERE total_balance > threshold
                LOOP
                    RAISE NOTICE 'Address % has a large balance of % RXD', rec.address, rec.total_balance;
                END LOOP;
            END;
            $$ LANGUAGE plpgsql;
        """))
        
        # Add a hook to postgresql.conf
        logger.info("Optimizing PostgreSQL configuration")
        conn.execute(text("""
            ALTER SYSTEM SET shared_preload_libraries = 'auto_explain';
            ALTER SYSTEM SET auto_explain.log_min_duration = '1s';
            ALTER SYSTEM SET auto_explain.log_analyze = 'on';
            ALTER SYSTEM SET log_min_duration_statement = '2s';
            
            -- Performance tuning
            ALTER SYSTEM SET work_mem = '128MB';
            ALTER SYSTEM SET maintenance_work_mem = '256MB'; 
            ALTER SYSTEM SET max_parallel_workers_per_gather = '4';
            ALTER SYSTEM SET max_parallel_workers = '8';
            ALTER SYSTEM SET effective_cache_size = '4GB';
            ALTER SYSTEM SET jit = 'on';
            ALTER SYSTEM SET jit_above_cost = '10000';
            ALTER SYSTEM SET jit_inline_above_cost = '50000';
            ALTER SYSTEM SET jit_optimize_above_cost = '50000';
        """))
        
        # Reload configuration
        conn.execute(text("SELECT pg_reload_conf()"))
        
        logger.info("Forced optimization applied successfully")
        
        return True

if __name__ == "__main__":
    if apply_force_optimizations():
        logger.info("Successfully applied forced optimizations")
    else:
        logger.error("Failed to apply forced optimizations")
        sys.exit(1)
