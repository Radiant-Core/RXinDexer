# /Users/radiant/Desktop/RXinDexer/fix_system_consistency.py
# This script fixes inconsistencies between database schema, models, and API endpoints
# It ensures all required tables exist and API endpoints handle errors properly
# Supports both incremental fixes and clean rebuilds of the system

import os
import sys
import logging
import argparse
import time
from pathlib import Path
from importlib import import_module

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def fix_database_schema(clean_rebuild=False):
    """Fix inconsistencies in database schema by creating missing tables"""
    logger.info("Step 1: Fixing database schema...")
    try:
        # Import and run the database fix script
        from src.db.migrations.fix_missing_tables import main as fix_db_tables
        fix_db_tables(clean_rebuild=clean_rebuild)
        logger.info("Database schema fix completed successfully")
        return True
    except Exception as e:
        logger.error(f"Error fixing database schema: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def patch_api_endpoints():
    """Apply patches to fix API endpoint issues"""
    logger.info("Step 2: Patching API endpoints...")
    try:
        # Import and run the API fix script
        from src.api.api_fixes import apply_api_fixes
        apply_api_fixes()
        logger.info("API endpoint patches applied successfully")
        return True
    except Exception as e:
        logger.error(f"Error patching API endpoints: {str(e)}")
        return False

def restart_services():
    """Restart necessary services to apply changes"""
    logger.info("Step 3: Restarting services...")
    try:
        # Stop API service
        os.system("docker stop rxindexer-api")
        logger.info("API service stopped")
        
        # Wait a moment
        time.sleep(2)
        
        # Start API service
        os.system("docker start rxindexer-api")
        logger.info("API service started")
        
        # Wait for API to come up
        time.sleep(5)
        logger.info("Services restarted successfully")
        return True
    except Exception as e:
        logger.error(f"Error restarting services: {str(e)}")
        return False

def verify_database_schema():
    """Verify database schema matches model definitions"""
    logger.info("Step 4: Verifying database schema...")
    try:
        # Import SQLAlchemy inspection functions
        from sqlalchemy import inspect
        # Import our database models
        from src.models.database import engine
        from src.models import (
            Base, UTXO, GlyphToken, Holder, SyncState, Block, Transaction,
            NFTMetadata, NFTCollection, NFTTransfer,
            UserProfile, Container, ContainerHistory,
            TimeSeriesMetric, RichList, TokenDistribution,
            MarketData, ActivityMetric
        )
        
        # Get database inspector
        inspector = inspect(engine)
        
        # Get all tables from models
        model_tables = set(Base.metadata.tables.keys())
        
        # Get all tables from database
        db_tables = set(inspector.get_table_names())
        
        # Check for missing tables
        missing_tables = model_tables - db_tables
        if missing_tables:
            logger.warning(f"Tables still missing in database: {missing_tables}")
            return False
        
        logger.info("All model tables exist in database")
        return True
    except Exception as e:
        logger.error(f"Error verifying database schema: {str(e)}")
        return False

def test_api_endpoints():
    """Test API endpoints to verify they're working"""
    logger.info("Step 5: Testing API endpoints...")
    try:
        import requests
        import json
        
        # Define API endpoints to test
        endpoints = [
            {"name": "Health", "url": "http://localhost:8000/health", "requires_api_key": False},
            {"name": "Recent Blocks", "url": "http://localhost:8000/api/v1/blocks/recent", "requires_api_key": True},
            {"name": "Latest Block", "url": "http://localhost:8000/api/v1/blocks/latest", "requires_api_key": True},
            {"name": "Transactions", "url": "http://localhost:8000/api/v1/transactions/recent", "requires_api_key": True},
            {"name": "Metrics", "url": "http://localhost:8000/metrics", "requires_api_key": False},
            {"name": "Token List", "url": "http://localhost:8000/api/v1/tokens/", "requires_api_key": True}
        ]
        
        # Test each endpoint
        results = []
        headers = {"X-API-Key": "test-api-key-1"} if True else {}
        
        for endpoint in endpoints:
            try:
                if endpoint["requires_api_key"]:
                    response = requests.get(endpoint["url"], headers=headers, timeout=10)
                else:
                    response = requests.get(endpoint["url"], timeout=10)
                
                status = "SUCCESS" if response.status_code < 400 else "FAILURE"
                results.append({
                    "endpoint": endpoint["name"],
                    "url": endpoint["url"],
                    "status": status,
                    "status_code": response.status_code,
                    "response": response.json() if status == "SUCCESS" else response.text[:100]
                })
                logger.info(f"Endpoint {endpoint['name']}: {status}")
            except Exception as e:
                results.append({
                    "endpoint": endpoint["name"],
                    "url": endpoint["url"],
                    "status": "ERROR",
                    "error": str(e)
                })
                logger.error(f"Error testing endpoint {endpoint['name']}: {str(e)}")
        
        # Count successes and failures
        success_count = sum(1 for r in results if r["status"] == "SUCCESS")
        failure_count = len(results) - success_count
        
        # Output test results
        logger.info(f"API endpoint testing completed: {success_count} successful, {failure_count} failed")
        
        return results, success_count, failure_count
    except Exception as e:
        logger.error(f"Error testing API endpoints: {str(e)}")
        return [], 0, 1

def main():
    """Main function to fix system consistency"""
    logger.info("Starting system consistency fix")
    
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Fix inconsistencies between database schema, models, and API endpoints")
    parser.add_argument("--skip-restart", action="store_true", help="Skip restarting services")
    parser.add_argument("--only-test", action="store_true", help="Only run tests without making changes")
    parser.add_argument("--clean-rebuild", action="store_true", help="Perform a clean rebuild of the database schema")
    args = parser.parse_args()
    
    results = {
        "database_schema": False,
        "api_patches": False,
        "service_restart": False,
        "schema_verification": False,
        "api_tests": {
            "success_count": 0,
            "failure_count": 0,
            "results": []
        }
    }
    
    if args.clean_rebuild:
        logger.warning("-" * 80)
        logger.warning("CLEAN REBUILD MODE ACTIVE")
        logger.warning("This will drop and recreate all database tables and reset all data!")
        logger.warning("-" * 80)
        time.sleep(2)  # Give a moment for the user to see the warning
    
    if not args.only_test:
        # Step 1: Fix database schema
        results["database_schema"] = fix_database_schema(clean_rebuild=args.clean_rebuild)
        
        # Step 2: Patch API endpoints
        results["api_patches"] = patch_api_endpoints()
        
        # Step 3: Restart services
        if not args.skip_restart:
            results["service_restart"] = restart_services()
        else:
            logger.info("Skipping service restart")
            results["service_restart"] = "skipped"
    else:
        logger.info("Running in test-only mode")
    
    # Step 4: Verify database schema
    results["schema_verification"] = verify_database_schema()
    
    # Step 5: Test API endpoints
    api_results, success_count, failure_count = test_api_endpoints()
    results["api_tests"]["results"] = api_results
    results["api_tests"]["success_count"] = success_count
    results["api_tests"]["failure_count"] = failure_count
    
    # Output summary
    logger.info("\n" + "-" * 50)
    logger.info("SYSTEM CONSISTENCY FIX SUMMARY")
    logger.info("-" * 50)
    if args.clean_rebuild:
        logger.info("MODE: CLEAN REBUILD")
    elif args.only_test:
        logger.info("MODE: TEST ONLY")
    else:
        logger.info("MODE: INCREMENTAL FIX")
    logger.info("-" * 50)  
    logger.info(f"Database Schema Fix: {'SUCCESS' if results['database_schema'] else 'FAILURE'}")
    logger.info(f"API Endpoint Patches: {'SUCCESS' if results['api_patches'] else 'FAILURE'}")
    logger.info(f"Service Restart: {'SUCCESS' if results['service_restart'] == True else 'SKIPPED' if results['service_restart'] == 'skipped' else 'FAILURE'}")
    logger.info(f"Database Schema Verification: {'SUCCESS' if results['schema_verification'] else 'FAILURE'}")
    logger.info(f"API Endpoint Tests: {results['api_tests']['success_count']} successful, {results['api_tests']['failure_count']} failed")
    logger.info("-" * 50)
    
    # Return overall success/failure
    overall_success = (
        (results["database_schema"] if not args.only_test else True) and
        (results["api_patches"] if not args.only_test else True) and
        (results["service_restart"] == True or results["service_restart"] == "skipped" if not args.only_test else True) and
        results["schema_verification"] and
        results["api_tests"]["failure_count"] == 0
    )
    
    if overall_success:
        logger.info("System consistency fix completed SUCCESSFULLY")
        return 0
    else:
        logger.error("System consistency fix completed with ERRORS")
        return 1

if __name__ == "__main__":
    sys.exit(main())
