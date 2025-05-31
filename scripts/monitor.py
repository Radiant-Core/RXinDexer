# /Users/radiant/Desktop/RXinDexer/scripts/monitor.py
# This script provides monitoring functionality for the RXinDexer application status.
# It checks sync status, database health, API responsiveness, but does NOT manage the application.

import os
import sys
import time
import logging
import argparse
import requests
from datetime import datetime
from pathlib import Path

# Add project root to path for imports
parent_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(parent_dir))

from src.models.database import engine
from src.sync.rpc_client import RadiantRPC
from src.sync.sync_manager import SyncManager
from src.models.database import get_db

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class RXinDexerMonitor:
    """
    Monitoring tool for RXinDexer application.
    Provides health checks and performance metrics.
    """
    
    def __init__(self, api_url="http://localhost:8000"):
        """Initialize the monitor with the API URL."""
        self.api_url = api_url
        self.rpc = RadiantRPC()
        self.db = next(get_db())
    
    def check_api_health(self):
        """Check if the API is responsive."""
        try:
            start_time = time.time()
            response = requests.get(f"{self.api_url}/health")
            elapsed = time.time() - start_time
            
            if response.status_code == 200:
                logger.info(f"API Health: OK (responded in {elapsed:.3f}s)")
                return True, response.json(), elapsed
            else:
                logger.error(f"API Health: FAIL (status code {response.status_code})")
                return False, None, elapsed
        except Exception as e:
            logger.error(f"API Health: ERROR ({str(e)})")
            return False, None, 0
    
    def check_sync_status(self):
        """Check the blockchain sync status."""
        try:
            sync_manager = SyncManager(self.db)
            status = sync_manager.get_sync_status()
            
            progress = status["progress"]
            blocks_behind = status["node_height"] - status["current_height"]
            
            logger.info(f"Sync Status: {progress:.2f}% ({blocks_behind} blocks behind)")
            
            if blocks_behind > 100:
                logger.warning(f"Sync is significantly behind: {blocks_behind} blocks")
            
            return True, status
        except Exception as e:
            logger.error(f"Sync Status Check: ERROR ({str(e)})")
            return False, None
    
    def check_database_connection(self):
        """Check if the database connection is healthy."""
        try:
            start_time = time.time()
            # Execute a simple query to test connection
            with engine.connect() as connection:
                result = connection.execute("SELECT 1").scalar()
            elapsed = time.time() - start_time
            
            logger.info(f"Database Connection: OK (responded in {elapsed:.3f}s)")
            return True, elapsed
        except Exception as e:
            logger.error(f"Database Connection: ERROR ({str(e)})")
            return False, 0
    
    def check_node_connection(self):
        """Check if the Radiant Node connection is healthy."""
        try:
            start_time = time.time()
            block_count = self.rpc.get_block_count()
            elapsed = time.time() - start_time
            
            logger.info(f"Radiant Node: OK (block height: {block_count}, responded in {elapsed:.3f}s)")
            return True, block_count, elapsed
        except Exception as e:
            logger.error(f"Radiant Node: ERROR ({str(e)})")
            return False, None, 0
    
    def get_holder_stats(self):
        """Get holder statistics from the API."""
        try:
            response = requests.get(f"{self.api_url}/api/holder/stats")
            if response.status_code == 200:
                stats = response.json()
                logger.info(f"Holder Stats: {stats['rxd_holders']} RXD holders, {stats['token_holders']} token holders")
                return True, stats
            else:
                logger.error(f"Holder Stats: FAIL (status code {response.status_code})")
                return False, None
        except Exception as e:
            logger.error(f"Holder Stats: ERROR ({str(e)})")
            return False, None
    
    def run_full_check(self):
        """Run all health checks and return a comprehensive report."""
        report = {
            "timestamp": datetime.now().isoformat(),
            "checks": {}
        }
        
        # Check API health
        api_ok, api_data, api_response_time = self.check_api_health()
        report["checks"]["api"] = {
            "status": "ok" if api_ok else "error",
            "response_time": api_response_time
        }
        
        # Check sync status
        sync_ok, sync_data = self.check_sync_status()
        if sync_ok:
            report["checks"]["sync"] = {
                "status": "ok",
                "progress": sync_data["progress"],
                "current_height": sync_data["current_height"],
                "node_height": sync_data["node_height"],
                "blocks_behind": sync_data["node_height"] - sync_data["current_height"]
            }
        else:
            report["checks"]["sync"] = {"status": "error"}
        
        # Check database connection
        db_ok, db_response_time = self.check_database_connection()
        report["checks"]["database"] = {
            "status": "ok" if db_ok else "error",
            "response_time": db_response_time
        }
        
        # Check node connection
        node_ok, block_height, node_response_time = self.check_node_connection()
        report["checks"]["node"] = {
            "status": "ok" if node_ok else "error",
            "block_height": block_height,
            "response_time": node_response_time
        }
        
        # Get holder stats
        holders_ok, holder_stats = self.get_holder_stats()
        if holders_ok:
            report["checks"]["holders"] = {
                "status": "ok",
                "rxd_holders": holder_stats["rxd_holders"],
                "token_holders": holder_stats["token_holders"],
                "total_addresses": holder_stats["total_addresses"]
            }
        else:
            report["checks"]["holders"] = {"status": "error"}
        
        # Overall status
        all_checks = [api_ok, sync_ok, db_ok, node_ok, holders_ok]
        report["overall_status"] = "ok" if all(all_checks) else "degraded" if any(all_checks) else "error"
        
        return report

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="RXinDexer Monitoring Tool")
    parser.add_argument(
        "--api-url", 
        default="http://localhost:8000",
        help="URL of the RXinDexer API"
    )
    parser.add_argument(
        "--continuous", 
        action="store_true",
        help="Run monitoring continuously with interval"
    )
    parser.add_argument(
        "--interval", 
        type=int,
        default=60,
        help="Interval between checks in seconds (for continuous mode)"
    )
    parser.add_argument(
        "--output-json",
        type=str,
        help="Output JSON file to write the report"
    )
    
    return parser.parse_args()

def main():
    """Main entry point for the monitoring script."""
    args = parse_args()
    
    monitor = RXinDexerMonitor(api_url=args.api_url)
    
    if args.continuous:
        logger.info(f"Starting continuous monitoring (interval: {args.interval}s)")
        try:
            while True:
                report = monitor.run_full_check()
                
                if args.output_json:
                    import json
                    with open(args.output_json, 'w') as f:
                        json.dump(report, f, indent=2)
                    logger.info(f"Report written to {args.output_json}")
                
                logger.info(f"Overall status: {report['overall_status'].upper()}")
                logger.info(f"Next check in {args.interval} seconds...")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            logger.info("Monitoring stopped by user")
    else:
        report = monitor.run_full_check()
        
        if args.output_json:
            import json
            with open(args.output_json, 'w') as f:
                json.dump(report, f, indent=2)
            logger.info(f"Report written to {args.output_json}")
        
        logger.info(f"Overall status: {report['overall_status'].upper()}")
        
        # Return non-zero exit code if there are issues
        return 0 if report["overall_status"] == "ok" else 1

if __name__ == "__main__":
    sys.exit(main())
