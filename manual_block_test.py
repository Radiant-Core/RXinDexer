#!/usr/bin/env python3
# /Users/radiant/Desktop/RXinDexer/manual_block_test.py
# Tests block and transaction parsing in the container environment

import os
import sys
import json
import time
import logging
import subprocess
from sqlalchemy import text, create_engine
from sqlalchemy.orm import sessionmaker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Initialize database connection
db_url = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@db:5432/rxindexer")
engine = create_engine(db_url, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Add project path
sys.path.append('/app')

try:
    from src.sync.rpc_client import RadiantRPC
    from src.parser.block_parser import BlockParser
except ImportError as e:
    logger.error(f"Failed to import required modules: {str(e)}")
    sys.exit(1)

def reset_sync_state(db):
    """Reset the sync_state to match reality (no blocks)"""
    logger.info("Resetting sync state to height 0...")
    try:
        with db.begin():
            db.execute(text(
                "UPDATE sync_state SET current_height = 0, current_hash = '', "
                "is_syncing = 0, last_updated_at = NOW() WHERE id = 1;"
            ))
        logger.info("Sync state reset successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to reset sync state: {str(e)}")
        return False

def test_direct_block_insertion(db, height=1):
    """Test direct block insertion with block parser"""
    logger.info(f"Testing block insertion for height {height}...")
    try:
        rpc = RadiantRPC()
        parser = BlockParser(rpc, db)
        
        block_hash = rpc.get_block_hash(height)
        block_data = rpc.get_block(block_hash)
        
        logger.info(f"Parsing block {height} with {len(block_data.get('tx', []))} transactions")
        stats = parser.parse_block(block_data, height, block_hash)
        
        block_count = db.execute(text("SELECT COUNT(*) FROM blocks WHERE height = :height"),
                               {"height": height}).scalar()
        tx_count = db.execute(text("SELECT COUNT(*) FROM transactions WHERE block_height = :height"),
                            {"height": height}).scalar()
        
        logger.info(f"Block stats: {stats}, Blocks: {block_count}, Transactions: {tx_count}")
        return block_count > 0 and tx_count > 0
    except Exception as e:
        logger.error(f"Error testing block insertion: {str(e)}")
        return False

def check_api_endpoint():
    """Check if API serves block data"""
    logger.info("Checking API endpoint for block data...")
    try:
        result = subprocess.run(
            ["curl", "-s", "http://localhost:8000/blocks/latest"],
            capture_output=True, text=True, timeout=10
        )
        logger.info(f"API response: {result.stdout}")
        return "height" in result.stdout
    except subprocess.TimeoutExpired:
        logger.error("API request timed out")
        return False
    except Exception as e:
        logger.error(f"API request failed: {str(e)}")
        return False

def run_tests_in_container():
    """Run tests in the indexer container"""
    logger.info("Running tests in container...")
    with SessionLocal() as db:
        try:
            reset_sync_state(db)
            single_success = test_direct_block_insertion(db, 1)
            logger.info(f"Single block test {'successful' if single_success else 'failed'}")
            
            if single_success:
                logger.info("Testing multiple blocks...")
                success_count = 0
                for height in range(2, 12):
                    if test_direct_block_insertion(db, height):
                        success_count += 1
                logger.info(f"Multiple blocks test: {success_count}/10 successful")
            
            db.commit()
            return True
        except Exception as e:
            logger.error(f"Test execution failed: {str(e)}")
            db.rollback()
            return False
        finally:
            db.close()

def diagnose_rpc_client():
    """Diagnose RPC client connectivity"""
    logger.info("Diagnosing RPC client...")
    try:
        rpc = RadiantRPC(max_retries=5, retry_delay=2)
        
        # Test basic RPC methods
        tests = [
            ("get_block_count", []),
            ("get_block_hash", [1]),
            ("get_block", ["00000000839a8e6886ab5951d76f411475428afc90947ee320161bbf18eb6048"])
        ]
        
        success_count = 0
        for method_name, args in tests:
            try:
                method = getattr(rpc, method_name)
                result = method(*args)
                if result:
                    logger.info(f"✅ RPC method {method_name} succeeded")
                    success_count += 1
            except Exception as e:
                logger.error(f"❌ RPC method {method_name} failed: {str(e)}")
        
        logger.info(f"RPC test: {success_count}/{len(tests)} methods successful")
        return success_count > 0
    except Exception as e:
        logger.error(f"RPC diagnosis failed: {str(e)}")
        return False

def main():
    """Main test orchestrator"""
    logger.info("=== RXinDexer Manual Block Test ===")
    
    if run_tests_in_container():
        logger.info("Block parser tests completed successfully")
    else:
        logger.error("Block parser tests failed")
    
    if diagnose_rpc_client():
        logger.info("RPC client diagnosis completed")
    else:
        logger.error("RPC client diagnosis failed")
    
    if check_api_endpoint():
        logger.info("API endpoint check successful")
    else:
        logger.error("API endpoint check failed")
    
    logger.info("=== Manual Test Complete ===")

if __name__ == "__main__":
    try:
        main()
    finally:
        engine.dispose()