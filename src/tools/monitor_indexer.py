# /Users/radiant/Desktop/RXinDexer/src/tools/monitor_indexer.py
# This script provides monitoring and statistics for the RXinDexer indexer.
# It collects and displays real-time metrics about block syncing, RPC health, and database status.

import os
import time
import logging
import argparse
import subprocess
import datetime
import re
import json
from tabulate import tabulate
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("indexer_monitor")

class IndexerMonitor:
    """Monitor for the RXinDexer indexer to track performance and health metrics"""
    
    def __init__(self, container_name='rxindexer-indexer', log_path='/app/logs/indexer.log'):
        """Initialize the monitor with container and log information"""
        self.container_name = container_name
        self.log_path = log_path
        self.metrics = {
            'last_block': 0,
            'blocks_per_minute': 0,
            'transactions_per_minute': 0,
            'success_rate': 0,
            'total_requests': 0,
            'failed_requests': 0,
            'request_sent_errors': 0,
            'last_health_check': None,
            'node_response_time': 0,
            'start_time': datetime.datetime.now()
        }
        self.block_times = []
        self.transaction_counts = []
    
    def run_docker_command(self, command):
        """Run a command in the indexer container and return results"""
        try:
            full_command = f"docker exec {self.container_name} {command}"
            result = subprocess.run(full_command, shell=True, capture_output=True, text=True)
            return result.stdout.strip()
        except Exception as e:
            logger.error(f"Error running docker command: {str(e)}")
            return ""
    
    def parse_logs(self, lines=300):
        """Extract metrics from the indexer logs"""
        try:
            # Get recent logs
            log_content = self.run_docker_command(f"tail -n {lines} {self.log_path}")
            
            # Extract block information
            block_pattern = r"Parsing block (\d+) with (\d+) transactions"
            block_matches = re.findall(block_pattern, log_content)
            if block_matches:
                latest_block = max([int(match[0]) for match in block_matches])
                self.metrics['last_block'] = latest_block
                
                # Calculate blocks per minute
                self._calculate_block_rate(block_matches)
                
                # Calculate transactions per minute
                self._calculate_transaction_rate(block_matches)
            
            # Extract RPC connection statistics
            stats_pattern = r"Connection stats: Success rate: ([\d\.]+)%, Total requests: (\d+), Failed: (\d+), Request-sent errors: (\d+)"
            stats_matches = re.findall(stats_pattern, log_content)
            if stats_matches:
                self.metrics['success_rate'] = float(stats_matches[-1][0])
                self.metrics['total_requests'] = int(stats_matches[-1][1])
                self.metrics['failed_requests'] = int(stats_matches[-1][2])
                self.metrics['request_sent_errors'] = int(stats_matches[-1][3])
            
            # Extract health check information
            health_pattern = r"Node health check: Current block height: (\d+), Response time: ([\d\.]+)s"
            health_matches = re.findall(health_pattern, log_content)
            if health_matches:
                self.metrics['node_response_time'] = float(health_matches[-1][1])
                
            # Extract circuit breaker status
            circuit_pattern = r"Circuit (OPENED|CLOSED|moved to HALF-OPEN state)"
            circuit_matches = re.findall(circuit_pattern, log_content)
            if circuit_matches:
                self.metrics['circuit_status'] = circuit_matches[-1]
            else:
                self.metrics['circuit_status'] = "CLOSED"  # Default to closed if not found
                
        except Exception as e:
            logger.error(f"Error parsing logs: {str(e)}")
    
    def _calculate_block_rate(self, block_matches):
        """Calculate the blocks processed per minute"""
        current_time = datetime.datetime.now()
        recent_blocks = [int(match[0]) for match in block_matches[-20:]]
        
        # Add current block and time to history
        if recent_blocks:
            self.block_times.append((recent_blocks[-1], current_time))
            
            # Keep only last 10 minutes of data
            cutoff_time = current_time - datetime.timedelta(minutes=10)
            self.block_times = [bt for bt in self.block_times if bt[1] >= cutoff_time]
            
            # Calculate rate if we have enough data
            if len(self.block_times) >= 2:
                oldest_block, oldest_time = self.block_times[0]
                newest_block, newest_time = self.block_times[-1]
                
                # Calculate blocks per minute
                time_diff = (newest_time - oldest_time).total_seconds() / 60
                if time_diff > 0:
                    block_diff = newest_block - oldest_block
                    self.metrics['blocks_per_minute'] = block_diff / time_diff
    
    def _calculate_transaction_rate(self, block_matches):
        """Calculate transactions processed per minute"""
        current_time = datetime.datetime.now()
        
        # Add current transactions to history
        tx_count = sum(int(match[1]) for match in block_matches[-20:])
        self.transaction_counts.append((tx_count, current_time))
        
        # Keep only last 10 minutes of data
        cutoff_time = current_time - datetime.timedelta(minutes=10)
        self.transaction_counts = [tc for tc in self.transaction_counts if tc[1] >= cutoff_time]
        
        # Calculate rate if we have enough data
        if len(self.transaction_counts) >= 2:
            oldest_tx, oldest_time = self.transaction_counts[0]
            newest_tx, newest_time = self.transaction_counts[-1]
            
            # Calculate transactions per minute
            time_diff = (newest_time - oldest_time).total_seconds() / 60
            if time_diff > 0:
                self.metrics['transactions_per_minute'] = newest_tx / time_diff
    
    def get_node_info(self):
        """Get information about the Radiant node via RPC"""
        try:
            # Check node status through the indexer container
            result = self.run_docker_command("python -c \"import json, os; from bitcoinrpc.authproxy import AuthServiceProxy; rpc_user=os.environ['RADIANT_RPC_USER']; rpc_pass=os.environ['RADIANT_RPC_PASSWORD']; rpc_host=os.environ.get('RADIANT_RPC_URL', 'radiant'); rpc_port=os.environ.get('RADIANT_RPC_PORT', '7332'); conn = AuthServiceProxy(f'http://{rpc_user}:{rpc_pass}@{rpc_host}:{rpc_port}'); print(json.dumps({'blockcount': conn.getblockcount(), 'connections': conn.getconnectioncount()}, indent=2))\"")
            
            # Parse the JSON response
            try:
                node_info = json.loads(result)
                self.metrics['node_block_height'] = node_info.get('blockcount', 0)
                self.metrics['node_connections'] = node_info.get('connections', 0)
            except json.JSONDecodeError:
                logger.error(f"Could not parse node info: {result}")
        except Exception as e:
            logger.error(f"Error getting node info: {str(e)}")
    
    def display_metrics(self):
        """Display the current metrics in a formatted table"""
        # Calculate sync progress
        if hasattr(self.metrics, 'node_block_height') and self.metrics['node_block_height'] > 0:
            sync_progress = (self.metrics['last_block'] / self.metrics['node_block_height']) * 100
            sync_status = f"{sync_progress:.2f}% ({self.metrics['last_block']}/{self.metrics['node_block_height']})"
        else:
            sync_status = f"Unknown ({self.metrics['last_block']}/unknown)"
        
        # Calculate uptime
        uptime = datetime.datetime.now() - self.metrics['start_time']
        uptime_str = str(uptime).split('.')[0]  # Remove microseconds
        
        # Prepare data for tabulate
        headers = ["Metric", "Value"]
        data = [
            ["Sync Status", sync_status],
            ["Blocks Per Minute", f"{self.metrics['blocks_per_minute']:.2f}"],
            ["Transactions Per Minute", f"{self.metrics['transactions_per_minute']:.2f}"],
            ["RPC Success Rate", f"{self.metrics['success_rate']:.1f}%"],
            ["Total RPC Requests", self.metrics['total_requests']],
            ["Failed RPC Requests", self.metrics['failed_requests']],
            ["Request-sent Errors", self.metrics['request_sent_errors']],
            ["Node Response Time", f"{self.metrics['node_response_time']:.3f}s"],
            ["Circuit Breaker Status", self.metrics.get('circuit_status', 'Unknown')],
            ["Monitor Uptime", uptime_str]
        ]
        
        # Display the table
        table = tabulate(data, headers, tablefmt="grid")
        print("\n" + "="*50)
        print(f"RXinDexer Monitor - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*50)
        print(table)
        print("="*50 + "\n")
    
    def run_monitor(self, interval=30):
        """Run the monitor continuously"""
        logger.info(f"Starting RXinDexer monitor, checking every {interval} seconds")
        
        try:
            while True:
                self.parse_logs()
                self.get_node_info()
                self.display_metrics()
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Monitor stopped by user")
        except Exception as e:
            logger.error(f"Monitor error: {str(e)}")

def main():
    parser = argparse.ArgumentParser(description="Monitor the RXinDexer indexer")
    parser.add_argument("--interval", type=int, default=30, help="Update interval in seconds")
    parser.add_argument("--container", type=str, default="rxindexer-indexer", help="Docker container name")
    args = parser.parse_args()
    
    monitor = IndexerMonitor(container_name=args.container)
    monitor.run_monitor(interval=args.interval)

if __name__ == "__main__":
    main()
