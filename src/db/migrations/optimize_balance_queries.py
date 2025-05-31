# /Users/radiant/Desktop/RXinDexer/src/db/migrations/optimize_balance_queries.py
# This file adds optimized indexes for balance calculation queries based on performance analysis.
# It targets the most expensive queries related to UTXO balance calculations.

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

def apply_migration(conn):
    """
    Add optimized indexes for balance calculations.
    
    Args:
        conn: Active database connection with transaction
    """
    try:
        # This partial index is specifically designed for balance queries
        # It only includes unspent UTXOs and includes the amount column for covering queries
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_utxos_balance_lookup ON utxos (address, amount) 
            WHERE spent = FALSE;
        """))
        logger.info("Created optimized balance lookup index")
        
        # Add an index on the most commonly filtered value (spent=FALSE)
        # This helps with the most common WHERE clause
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_utxos_unspent ON utxos (spent) 
            WHERE spent = FALSE;
        """))
        logger.info("Created index for unspent UTXOs")
        
        # Add a specialized functional index for common aggregation patterns
        conn.execute(text("""
            CREATE MATERIALIZED VIEW IF NOT EXISTS address_balances AS
            SELECT 
                address, 
                SUM(amount) as total_balance,
                COUNT(*) as utxo_count
            FROM utxos 
            WHERE spent = FALSE 
            GROUP BY address;
            
            CREATE UNIQUE INDEX IF NOT EXISTS idx_address_balances_address ON address_balances (address);
        """))
        logger.info("Created materialized view for address balances")
        
        # Add a refresh function for the materialized view
        conn.execute(text("""
            CREATE OR REPLACE FUNCTION refresh_address_balances()
            RETURNS TRIGGER AS $$
            BEGIN
                -- Only refresh periodically to avoid constant updates
                IF (SELECT extract(epoch from now()) - COALESCE(
                       (SELECT extract(epoch from last_refresh) FROM pg_stat_user_tables 
                        WHERE relname = 'address_balances'), 0)) > 60 THEN
                    REFRESH MATERIALIZED VIEW CONCURRENTLY address_balances;
                END IF;
                RETURN NULL;
            END;
            $$ LANGUAGE plpgsql;
            
            DROP TRIGGER IF EXISTS trg_refresh_address_balances ON utxos;
            
            CREATE TRIGGER trg_refresh_address_balances
            AFTER INSERT OR UPDATE OR DELETE ON utxos
            FOR EACH STATEMENT
            EXECUTE FUNCTION refresh_address_balances();
        """))
        logger.info("Created automatic refresh trigger for address balances")
        
        return True
    except Exception as e:
        logger.error(f"Failed to create balance query optimizations: {str(e)}")
        return False
