# /Users/radiant/Desktop/RXinDexer/src/db/init_functions.py
# This file creates custom PostgreSQL functions required by the RXinDexer application.
# It runs independently of other database initialization processes to ensure functions are properly created.

import logging
import sys
from pathlib import Path

# Add parent directory to path for imports
parent_dir = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(parent_dir))

from sqlalchemy import text
from src.models.database import engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def create_pg_functions():
    """Create PostgreSQL functions needed by the application."""
    logger.info("Creating PostgreSQL functions...")
    
    try:
        # Use separate connections for each function to ensure they are created
        # even if one fails
        
        # Create jsonb_object_length function
        with engine.connect() as conn:
            try:
                conn.execute(text("""
                CREATE OR REPLACE FUNCTION jsonb_object_length(jsonb)
                RETURNS integer AS
                $$
                    SELECT CASE
                        WHEN $1 IS NULL OR $1 = 'null'::jsonb THEN 0
                        WHEN jsonb_typeof($1) <> 'object' THEN 0
                        ELSE (SELECT count(*) FROM jsonb_object_keys($1))
                    END;
                $$ LANGUAGE SQL IMMUTABLE STRICT;
                """))
                conn.commit()
                logger.info("Created jsonb_object_length function")
            except Exception as e:
                logger.error(f"Error creating jsonb_object_length: {str(e)}")
        
        # Create jsonb_exists function
        with engine.connect() as conn:
            try:
                conn.execute(text("""
                CREATE OR REPLACE FUNCTION jsonb_exists(jsonb, text)
                RETURNS boolean AS
                $$
                    SELECT CASE
                        WHEN $1 = 'null'::jsonb OR $1 IS NULL THEN false
                        WHEN jsonb_typeof($1) <> 'object' THEN false
                        ELSE $1 ? $2
                    END;
                $$ LANGUAGE SQL IMMUTABLE STRICT;
                """))
                conn.commit()
                logger.info("Created jsonb_exists function")
            except Exception as e:
                logger.error(f"Error creating jsonb_exists: {str(e)}")
        
        return True
    except Exception as e:
        logger.error(f"Error creating PostgreSQL functions: {str(e)}")
        return False

if __name__ == "__main__":
    create_pg_functions()
