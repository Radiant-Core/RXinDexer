#!/usr/bin/env python3
# Test script for RXinDexer optimization package

import sys
import os
import logging
from rxindexer.optimization import DatabaseOptimizer
import psycopg2

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

def get_db_connection():
    """Create a database connection."""
    try:
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "db"),
            port=os.getenv("DB_PORT", "5432"),
            dbname=os.getenv("DB_NAME", "rxindexer"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "postgres")
        )
        conn.autocommit = True
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        sys.exit(1)

def test_database_optimizer():
    """Test the DatabaseOptimizer class."""
    logger.info("Testing DatabaseOptimizer...")
    conn = get_db_connection()
    
    try:
        optimizer = DatabaseOptimizer(conn)
        
        # Test getting database version
        version = optimizer.get_database_version()
        logger.info(f"Database version: {version}")
        
        # Test getting table stats
        stats = optimizer.get_table_stats()
        logger.info("Table stats:")
        for table, size in stats.items():
            logger.info(f"  {table}: {size} MB")
        
        return True
    except Exception as e:
        logger.error(f"DatabaseOptimizer test failed: {e}")
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    logger.info("Starting optimization package test...")
    
    # Run tests
    test_results = {
        "DatabaseOptimizer": test_database_optimizer(),
    }
    
    # Print summary
    logger.info("\nTest Summary:")
    logger.info("-" * 50)
    for test, result in test_results.items():
        status = "PASSED" if result else "FAILED"
        logger.info(f"{test}: {status}")
    
    # Exit with non-zero code if any test failed
    if not all(test_results.values()):
        sys.exit(1)
