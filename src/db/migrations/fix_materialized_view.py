# /Users/radiant/Desktop/RXinDexer/src/db/migrations/fix_materialized_view.py
# This script fixes the materialized view refresh mechanism to improve transaction reliability
# It avoids transaction conflicts by separating the refresh process from transaction handling

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

def fix_materialized_view():
    """
    Fixes the materialized view refresh process to prevent transaction conflicts
    and improve overall database performance.
    """
    logger.info("Starting materialized view fix process")
    
    # Get database connection from config
    engine = create_engine(Config.DATABASE_URL)
    
    with engine.connect() as conn:
        # Drop existing problematic triggers and functions
        logger.info("Dropping existing triggers and functions")
        conn.execute(text("DROP TRIGGER IF EXISTS trg_refresh_address_balances ON utxos;"))
        conn.execute(text("DROP FUNCTION IF EXISTS refresh_address_balances();"))
        conn.execute(text("DROP TABLE IF EXISTS refresh_tracking;"))
        
        # Create a table to track refresh operations
        logger.info("Creating refresh tracking table")
        conn.execute(text("""
            CREATE TABLE refresh_tracking (
                view_name TEXT PRIMARY KEY,
                last_refresh TIMESTAMP WITH TIME ZONE,
                is_refreshing BOOLEAN DEFAULT FALSE
            );
        """))
        
        # Insert initial record for address_balances
        logger.info("Adding initial refresh record")
        conn.execute(text("""
            INSERT INTO refresh_tracking (view_name, last_refresh, is_refreshing)
            VALUES ('address_balances', NOW() - INTERVAL '1 hour', FALSE)
            ON CONFLICT (view_name) DO NOTHING;
        """))
        
        # Create a non-blocking function to check if refresh is needed
        logger.info("Creating check_refresh_needed function")
        conn.execute(text("""
            CREATE OR REPLACE FUNCTION check_refresh_needed()
            RETURNS BOOLEAN AS $$
            DECLARE
                last_refresh_time TIMESTAMP WITH TIME ZONE;
                is_being_refreshed BOOLEAN;
                refresh_needed BOOLEAN := FALSE;
            BEGIN
                -- Get the last refresh info
                SELECT last_refresh, is_refreshing INTO last_refresh_time, is_being_refreshed
                FROM refresh_tracking
                WHERE view_name = 'address_balances';
                
                -- Determine if refresh is needed and not already in progress
                IF last_refresh_time IS NULL OR 
                   EXTRACT(EPOCH FROM (NOW() - last_refresh_time)) > 300 THEN
                    refresh_needed := TRUE;
                END IF;
                
                -- Don't refresh if another process is already refreshing
                IF is_being_refreshed THEN
                    refresh_needed := FALSE;
                END IF;
                
                RETURN refresh_needed;
            END;
            $$ LANGUAGE plpgsql;
        """))
        
        # Create a safer async refresh function that doesn't block transactions
        logger.info("Creating safe refresh function")
        conn.execute(text("""
            CREATE OR REPLACE FUNCTION safe_refresh_address_balances()
            RETURNS VOID AS $$
            DECLARE
                refresh_needed BOOLEAN;
            BEGIN
                -- Check if refresh is needed
                SELECT check_refresh_needed() INTO refresh_needed;
                
                -- If refresh is needed, lock and refresh
                IF refresh_needed THEN
                    -- Mark as being refreshed
                    UPDATE refresh_tracking
                    SET is_refreshing = TRUE
                    WHERE view_name = 'address_balances';
                    
                    -- Perform the refresh in a separate transaction
                    PERFORM pg_advisory_lock(hashtext('refresh_address_balances'));
                    BEGIN
                        REFRESH MATERIALIZED VIEW CONCURRENTLY address_balances;
                        
                        -- Update the last refresh timestamp and release lock
                        UPDATE refresh_tracking
                        SET last_refresh = NOW(),
                            is_refreshing = FALSE
                        WHERE view_name = 'address_balances';
                    EXCEPTION
                        WHEN OTHERS THEN
                            -- Reset the refresh flag if there's an error
                            UPDATE refresh_tracking
                            SET is_refreshing = FALSE
                            WHERE view_name = 'address_balances';
                            RAISE;
                    END;
                    PERFORM pg_advisory_unlock(hashtext('refresh_address_balances'));
                END IF;
            END;
            $$ LANGUAGE plpgsql;
        """))
        
        # Create a lightweight notification function for the trigger
        logger.info("Creating notification function")
        conn.execute(text("""
            CREATE OR REPLACE FUNCTION notify_balance_change()
            RETURNS TRIGGER AS $$
            BEGIN
                -- Just notify changes, don't do expensive operations in trigger
                PERFORM pg_notify('balance_changes', 'utxo_change');
                RETURN NULL;
            END;
            $$ LANGUAGE plpgsql;
        """))
        
        # Create the trigger that will just notify of changes, not do the refresh
        logger.info("Creating lightweight trigger")
        conn.execute(text("""
            CREATE TRIGGER trg_notify_balance_changes
            AFTER INSERT OR UPDATE OR DELETE ON utxos
            FOR EACH STATEMENT
            EXECUTE FUNCTION notify_balance_change();
        """))
        
        # Add index for optimizing the aggregate query that builds the materialized view
        logger.info("Adding specialized index for aggregation")
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_utxos_address_spent_amount ON utxos (address, spent, amount)
            WHERE spent = FALSE;
        """))

        # Update the materialized view to use the CONCURRENTLY option
        logger.info("Updating materialized view for concurrent refreshes")
        conn.execute(text("""
            DROP MATERIALIZED VIEW IF EXISTS address_balances;
            
            CREATE MATERIALIZED VIEW address_balances AS
            SELECT 
                address,
                SUM(amount) as total_balance,
                COUNT(*) as utxo_count
            FROM utxos
            WHERE spent = FALSE
            GROUP BY address
            WITH DATA;
            
            CREATE UNIQUE INDEX idx_address_balances_address
            ON address_balances (address);
        """))
        
        # Schedule periodic refresh
        logger.info("Creating scheduled refresh function")
        conn.execute(text("""
            CREATE OR REPLACE FUNCTION schedule_balance_refreshes()
            RETURNS VOID AS $$
            BEGIN
                PERFORM safe_refresh_address_balances();
            END;
            $$ LANGUAGE plpgsql;
        """))
        
        # Add a utility function to manually refresh when needed
        logger.info("Creating manual refresh utility function")
        conn.execute(text("""
            CREATE OR REPLACE FUNCTION refresh_balances_now()
            RETURNS VOID AS $$
            BEGIN
                UPDATE refresh_tracking
                SET last_refresh = NOW() - INTERVAL '1 hour'
                WHERE view_name = 'address_balances';
                
                PERFORM safe_refresh_address_balances();
            END;
            $$ LANGUAGE plpgsql;
        """))
        
        # Do initial refresh
        logger.info("Performing initial refresh")
        conn.execute(text("SELECT refresh_balances_now();"))
        
        logger.info("Materialized view fix completed successfully")
        
        return True

if __name__ == "__main__":
    if fix_materialized_view():
        logger.info("Successfully fixed materialized view refresh process")
    else:
        logger.error("Failed to fix materialized view refresh process")
        sys.exit(1)
