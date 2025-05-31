# /Users/radiant/Desktop/RXinDexer/scripts/metrics_dashboard.py
# This file provides a simple web-based dashboard for RXinDexer metrics and monitoring.
# It visualizes data collected by the monitor.py script and provides real-time status updates.

import os
import sys
import json
import time
import argparse
import logging
import subprocess
from datetime import datetime
from pathlib import Path
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler

# Add project root to path for imports
parent_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(parent_dir))

# Import monitor module
from scripts.monitor import RXinDexerMonitor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Store metrics history
metrics_history = {
    "timestamps": [],
    "sync_progress": [],
    "api_response_times": [],
    "db_response_times": [],
    "node_response_times": [],
    "block_heights": [],
    "rxd_holders": [],
    "token_holders": []
}

# Maximum history points to keep
MAX_HISTORY_POINTS = 60

# Latest monitoring report
latest_report = {}

# Lock for thread-safe access to shared data
data_lock = threading.Lock()

class MetricsDashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the metrics dashboard."""
    
    def _set_headers(self, content_type="text/html"):
        """Set response headers."""
        self.send_response(200)
        self.send_header("Content-type", content_type)
        self.end_headers()
    
    def do_GET(self):
        """Handle GET requests."""
        if self.path == "/":
            self._serve_dashboard()
        elif self.path == "/api/metrics":
            self._serve_metrics_json()
        elif self.path == "/api/latest":
            self._serve_latest_report()
        else:
            self.send_error(404)
    
    def _serve_dashboard(self):
        """Serve the dashboard HTML page."""
        self._set_headers()
        
        # Read the dashboard HTML template
        dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard_template.html")
        
        # If the template doesn't exist, create a basic one
        if not os.path.exists(dashboard_path):
            html_content = self._generate_dashboard_template()
        else:
            with open(dashboard_path, "r") as f:
                html_content = f.read()
        
        self.wfile.write(html_content.encode())
    
    def _serve_metrics_json(self):
        """Serve metrics history as JSON."""
        self._set_headers(content_type="application/json")
        
        with data_lock:
            self.wfile.write(json.dumps(metrics_history).encode())
    
    def _serve_latest_report(self):
        """Serve the latest monitoring report as JSON."""
        self._set_headers(content_type="application/json")
        
        with data_lock:
            self.wfile.write(json.dumps(latest_report).encode())
    
    def _generate_dashboard_template(self):
        """Generate a basic dashboard HTML template."""
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>RXinDexer Metrics Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        .header {
            background-color: #2c3e50;
            color: white;
            padding: 20px;
            border-radius: 5px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .status-badge {
            padding: 8px 16px;
            border-radius: 20px;
            font-weight: bold;
        }
        .status-ok {
            background-color: #27ae60;
        }
        .status-degraded {
            background-color: #f39c12;
        }
        .status-error {
            background-color: #e74c3c;
        }
        .card {
            background-color: white;
            border-radius: 5px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        .card h2 {
            margin-top: 0;
            border-bottom: 1px solid #eee;
            padding-bottom: 10px;
            color: #2c3e50;
        }
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        .metric-value {
            font-size: 24px;
            font-weight: bold;
            margin: 10px 0;
            color: #2c3e50;
        }
        .metric-label {
            color: #7f8c8d;
            font-size: 14px;
        }
        .chart-container {
            position: relative;
            height: 300px;
            margin-bottom: 20px;
        }
        .footer {
            text-align: center;
            margin-top: 20px;
            color: #7f8c8d;
            font-size: 12px;
        }
        #last-updated {
            font-style: italic;
            color: #7f8c8d;
        }
        .refresh-button {
            background-color: #3498db;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 4px;
            cursor: pointer;
            font-weight: bold;
        }
        .refresh-button:hover {
            background-color: #2980b9;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>RXinDexer Metrics Dashboard</h1>
            <div>
                <span class="status-badge" id="overall-status">Checking...</span>
                <button class="refresh-button" onclick="refreshData()">Refresh</button>
            </div>
        </div>
        
        <div class="card">
            <h2>System Status</h2>
            <div id="last-updated">Last updated: checking...</div>
            <div class="metrics-grid">
                <div>
                    <div class="metric-label">Sync Progress</div>
                    <div class="metric-value" id="sync-progress">-</div>
                </div>
                <div>
                    <div class="metric-label">Block Height</div>
                    <div class="metric-value" id="block-height">-</div>
                </div>
                <div>
                    <div class="metric-label">Blocks Behind</div>
                    <div class="metric-value" id="blocks-behind">-</div>
                </div>
                <div>
                    <div class="metric-label">API Response</div>
                    <div class="metric-value" id="api-response">-</div>
                </div>
            </div>
        </div>
        
        <div class="card">
            <h2>Holder Statistics</h2>
            <div class="metrics-grid">
                <div>
                    <div class="metric-label">RXD Holders</div>
                    <div class="metric-value" id="rxd-holders">-</div>
                </div>
                <div>
                    <div class="metric-label">Token Holders</div>
                    <div class="metric-value" id="token-holders">-</div>
                </div>
                <div>
                    <div class="metric-label">Total Addresses</div>
                    <div class="metric-value" id="total-addresses">-</div>
                </div>
            </div>
        </div>
        
        <div class="card">
            <h2>Response Times</h2>
            <div class="metrics-grid">
                <div>
                    <div class="metric-label">API (seconds)</div>
                    <div class="metric-value" id="api-time">-</div>
                </div>
                <div>
                    <div class="metric-label">Database (seconds)</div>
                    <div class="metric-value" id="db-time">-</div>
                </div>
                <div>
                    <div class="metric-label">Radiant Node (seconds)</div>
                    <div class="metric-value" id="node-time">-</div>
                </div>
            </div>
        </div>
        
        <div class="card">
            <h2>Sync Progress History</h2>
            <div class="chart-container">
                <canvas id="syncChart"></canvas>
            </div>
        </div>
        
        <div class="card">
            <h2>Response Time History</h2>
            <div class="chart-container">
                <canvas id="responseChart"></canvas>
            </div>
        </div>
        
        <div class="footer">
            <p>RXinDexer Monitoring Dashboard | Radiant Blockchain Indexer</p>
        </div>
    </div>
    
    <script>
        // Initialize charts
        const syncCtx = document.getElementById('syncChart').getContext('2d');
        const syncChart = new Chart(syncCtx, {
            type: 'line',
            data: {
                labels: [],
                datasets: [{
                    label: 'Sync Progress (%)',
                    data: [],
                    borderColor: '#3498db',
                    backgroundColor: 'rgba(52, 152, 219, 0.1)',
                    tension: 0.1,
                    fill: true
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: false,
                        min: 0,
                        max: 100
                    }
                }
            }
        });
        
        const responseCtx = document.getElementById('responseChart').getContext('2d');
        const responseChart = new Chart(responseCtx, {
            type: 'line',
            data: {
                labels: [],
                datasets: [
                    {
                        label: 'API (s)',
                        data: [],
                        borderColor: '#2ecc71',
                        backgroundColor: 'rgba(46, 204, 113, 0.1)',
                        tension: 0.1
                    },
                    {
                        label: 'Database (s)',
                        data: [],
                        borderColor: '#e74c3c',
                        backgroundColor: 'rgba(231, 76, 60, 0.1)',
                        tension: 0.1
                    },
                    {
                        label: 'Node (s)',
                        data: [],
                        borderColor: '#f39c12',
                        backgroundColor: 'rgba(243, 156, 18, 0.1)',
                        tension: 0.1
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false
            }
        });
        
        // Function to update the dashboard with latest data
        function updateDashboard(data) {
            // Update status indicators
            const overallStatus = document.getElementById('overall-status');
            overallStatus.textContent = data.overall_status.toUpperCase();
            overallStatus.className = 'status-badge status-' + data.overall_status;
            
            // Update timestamps
            const lastUpdated = document.getElementById('last-updated');
            lastUpdated.textContent = 'Last updated: ' + new Date(data.timestamp).toLocaleString();
            
            // Update sync metrics
            if (data.checks.sync && data.checks.sync.status === 'ok') {
                document.getElementById('sync-progress').textContent = data.checks.sync.progress.toFixed(2) + '%';
                document.getElementById('block-height').textContent = data.checks.sync.current_height.toLocaleString();
                document.getElementById('blocks-behind').textContent = data.checks.sync.blocks_behind.toLocaleString();
            } else {
                document.getElementById('sync-progress').textContent = 'ERROR';
                document.getElementById('block-height').textContent = 'ERROR';
                document.getElementById('blocks-behind').textContent = 'ERROR';
            }
            
            // Update API status
            if (data.checks.api && data.checks.api.status === 'ok') {
                document.getElementById('api-response').textContent = 'Online';
                document.getElementById('api-time').textContent = data.checks.api.response_time.toFixed(3);
            } else {
                document.getElementById('api-response').textContent = 'ERROR';
                document.getElementById('api-time').textContent = 'ERROR';
            }
            
            // Update database time
            if (data.checks.database && data.checks.database.status === 'ok') {
                document.getElementById('db-time').textContent = data.checks.database.response_time.toFixed(3);
            } else {
                document.getElementById('db-time').textContent = 'ERROR';
            }
            
            // Update node time
            if (data.checks.node && data.checks.node.status === 'ok') {
                document.getElementById('node-time').textContent = data.checks.node.response_time.toFixed(3);
            } else {
                document.getElementById('node-time').textContent = 'ERROR';
            }
            
            // Update holder stats
            if (data.checks.holders && data.checks.holders.status === 'ok') {
                document.getElementById('rxd-holders').textContent = data.checks.holders.rxd_holders.toLocaleString();
                document.getElementById('token-holders').textContent = data.checks.holders.token_holders.toLocaleString();
                document.getElementById('total-addresses').textContent = data.checks.holders.total_addresses.toLocaleString();
            } else {
                document.getElementById('rxd-holders').textContent = 'ERROR';
                document.getElementById('token-holders').textContent = 'ERROR';
                document.getElementById('total-addresses').textContent = 'ERROR';
            }
        }
        
        // Function to update charts with history data
        function updateCharts(history) {
            // Format timestamps for chart labels
            const labels = history.timestamps.map(ts => {
                const date = new Date(ts);
                return date.toLocaleTimeString();
            });
            
            // Update sync progress chart
            syncChart.data.labels = labels;
            syncChart.data.datasets[0].data = history.sync_progress;
            syncChart.update();
            
            // Update response time chart
            responseChart.data.labels = labels;
            responseChart.data.datasets[0].data = history.api_response_times;
            responseChart.data.datasets[1].data = history.db_response_times;
            responseChart.data.datasets[2].data = history.node_response_times;
            responseChart.update();
        }
        
        // Function to fetch and display latest data
        function refreshData() {
            fetch('/api/latest')
                .then(response => response.json())
                .then(data => {
                    updateDashboard(data);
                })
                .catch(error => {
                    console.error('Error fetching latest data:', error);
                });
                
            fetch('/api/metrics')
                .then(response => response.json())
                .then(data => {
                    updateCharts(data);
                })
                .catch(error => {
                    console.error('Error fetching metrics history:', error);
                });
        }
        
        // Initial data load
        refreshData();
        
        // Set up auto-refresh every 10 seconds
        setInterval(refreshData, 10000);
    </script>
</body>
</html>
"""

def update_metrics_history(report):
    """Update metrics history with the latest monitoring report."""
    with data_lock:
        global latest_report
        latest_report = report
        
        # Update timestamps
        metrics_history["timestamps"].append(report["timestamp"])
        
        # Update sync progress
        if report["checks"]["sync"]["status"] == "ok":
            metrics_history["sync_progress"].append(report["checks"]["sync"]["progress"])
        else:
            metrics_history["sync_progress"].append(None)
        
        # Update response times
        metrics_history["api_response_times"].append(
            report["checks"]["api"]["response_time"] if report["checks"]["api"]["status"] == "ok" else None
        )
        metrics_history["db_response_times"].append(
            report["checks"]["database"]["response_time"] if report["checks"]["database"]["status"] == "ok" else None
        )
        metrics_history["node_response_times"].append(
            report["checks"]["node"]["response_time"] if report["checks"]["node"]["status"] == "ok" else None
        )
        
        # Update block heights
        if report["checks"]["node"]["status"] == "ok":
            metrics_history["block_heights"].append(report["checks"]["node"]["block_height"])
        else:
            metrics_history["block_heights"].append(None)
        
        # Update holder stats
        if report["checks"]["holders"]["status"] == "ok":
            metrics_history["rxd_holders"].append(report["checks"]["holders"]["rxd_holders"])
            metrics_history["token_holders"].append(report["checks"]["holders"]["token_holders"])
        else:
            metrics_history["rxd_holders"].append(None)
            metrics_history["token_holders"].append(None)
        
        # Trim history if it exceeds the maximum length
        if len(metrics_history["timestamps"]) > MAX_HISTORY_POINTS:
            for key in metrics_history:
                metrics_history[key] = metrics_history[key][-MAX_HISTORY_POINTS:]

def run_monitor_thread(api_url, interval):
    """Run the monitoring thread that collects metrics."""
    monitor = RXinDexerMonitor(api_url=api_url)
    
    while True:
        try:
            # Run full check and get report
            report = monitor.run_full_check()
            
            # Update metrics history
            update_metrics_history(report)
            
            # Sleep for interval
            time.sleep(interval)
        except Exception as e:
            logger.error(f"Error in monitoring thread: {str(e)}")
            time.sleep(interval)

def run_server(port):
    """Run the HTTP server for the metrics dashboard."""
    server_address = ('', port)
    httpd = HTTPServer(server_address, MetricsDashboardHandler)
    logger.info(f"Starting metrics dashboard server on port {port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping metrics dashboard server")
        httpd.server_close()

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="RXinDexer Metrics Dashboard")
    parser.add_argument(
        "--api-url", 
        default="http://localhost:8000",
        help="URL of the RXinDexer API"
    )
    parser.add_argument(
        "--port", 
        type=int,
        default=8080,
        help="Port to run the dashboard server on"
    )
    parser.add_argument(
        "--interval", 
        type=int,
        default=30,
        help="Interval between metric collection in seconds"
    )
    parser.add_argument(
        "--no-browser", 
        action="store_true",
        help="Don't automatically open the dashboard in a browser"
    )
    return parser.parse_args()

def main():
    """Main entry point for the metrics dashboard."""
    args = parse_args()
    
    # Start monitoring thread
    monitor_thread = threading.Thread(
        target=run_monitor_thread,
        args=(args.api_url, args.interval),
        daemon=True
    )
    monitor_thread.start()
    
    # Open browser if requested
    if not args.no_browser:
        url = f"http://localhost:{args.port}"
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    
    # Run HTTP server (this will block until interrupted)
    try:
        run_server(args.port)
        return 0
    except Exception as e:
        logger.error(f"Error running dashboard server: {str(e)}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
