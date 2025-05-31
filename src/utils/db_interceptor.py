# /Users/radiant/Desktop/RXinDexer/src/utils/db_interceptor.py
# This module provides a database connection interceptor to prevent problematic queries
# It intercepts SQL before execution and provides safe alternatives for known problematic queries

import logging
from sqlalchemy import event
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Track if interceptor is initialized
_interceptor_initialized = False

def setup_query_interceptor(engine):
    """
    Sets up an event listener to intercept and modify problematic SQL queries
    before they are sent to the database.
    
    Args:
        engine: SQLAlchemy engine to attach the interceptor to
    """
    global _interceptor_initialized
    
    if _interceptor_initialized:
        logger.info("Query interceptor already initialized")
        return
    
    @event.listens_for(engine, "before_cursor_execute")
    def intercept_query(conn, cursor, statement, parameters, context, executemany):
        """
        Intercepts SQL queries before execution to identify and modify problematic queries.
        
        Args:
            conn: Connection
            cursor: Cursor
            statement: SQL statement
            parameters: Query parameters
            context: Execution context
            executemany: Whether this is an executemany operation
        """
        # Check for the problematic JOIN between utxos and glyph_tokens
        if ("utxos JOIN glyph_tokens ON utxos.token_ref = glyph_tokens.ref" in statement and 
            "WHERE utxos.spent = false AND utxos.token_ref IS NOT NULL" in statement):
            
            logger.warning("Intercepted problematic JOIN query, replacing with safer version")
            
            # Replace with safer query that separates the operations
            safer_statement = """
            WITH unspent_tokens AS (
                SELECT address, token_ref
                FROM utxos
                WHERE spent = false AND token_ref IS NOT NULL
            )
            SELECT ut.address, ut.token_ref
            FROM unspent_tokens ut
            WHERE EXISTS (
                SELECT 1 FROM glyph_tokens gt WHERE gt.ref = ut.token_ref
            )
            """
            
            # Log the query replacement
            logger.info(f"Original query: {statement}")
            logger.info(f"Replaced with: {safer_statement}")
            
            # Override the statement
            statement = safer_statement
            
            # Return modified statement and parameters
            return statement, parameters
    
    logger.info("Query interceptor initialized successfully")
    _interceptor_initialized = True
    return True

def safe_execute_query(conn, query, params=None):
    """
    Safely executes a query, catching and handling any problematic JOINs.
    
    Args:
        conn: Database connection
        query: SQL query to execute
        params: Query parameters
        
    Returns:
        Query results
    """
    # Check for problematic JOIN
    if ("utxos JOIN glyph_tokens" in query and 
        "WHERE utxos.spent = false AND utxos.token_ref IS NOT NULL" in query):
        
        logger.warning("Replacing problematic JOIN query with safer version")
        
        # Use a safer alternative that avoids the problematic JOIN
        safer_query = """
        WITH unspent_tokens AS (
            SELECT address, token_ref
            FROM utxos
            WHERE spent = false AND token_ref IS NOT NULL
        )
        SELECT ut.address, ut.token_ref
        FROM unspent_tokens ut
        WHERE EXISTS (
            SELECT 1 FROM glyph_tokens gt WHERE gt.ref = ut.token_ref
        )
        """
        
        return conn.execute(safer_query, params or {})
    
    # Execute the original query if it's safe
    return conn.execute(query, params or {})
