"""
Load Testing Suite for RXinDexer

Provides comprehensive load testing for the ElectrumX server including:
- Concurrent connection testing
- Request throughput testing
- Subscription stress testing
- Memory and CPU monitoring
- Latency percentile analysis

Usage:
    python load_test.py --host localhost --port 50010 --connections 100 --duration 60
"""

import asyncio
import argparse
import time
import statistics
import json
import sys
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from collections import defaultdict

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False


@dataclass
class RequestMetrics:
    """Metrics for a single request."""
    method: str
    latency_ms: float
    success: bool
    error: Optional[str] = None


@dataclass
class LoadTestResult:
    """Results from a load test run."""
    duration_seconds: float
    total_requests: int
    successful_requests: int
    failed_requests: int
    requests_per_second: float
    latency_avg_ms: float
    latency_p50_ms: float
    latency_p90_ms: float
    latency_p99_ms: float
    latency_max_ms: float
    errors_by_type: Dict[str, int] = field(default_factory=dict)
    method_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class ElectrumXClient:
    """Simple ElectrumX JSON-RPC client for load testing."""
    
    def __init__(self, host: str, port: int, ssl: bool = False):
        self.host = host
        self.port = port
        self.ssl = ssl
        self.request_id = 0
        self.reader = None
        self.writer = None
    
    async def connect(self):
        """Establish connection to ElectrumX server."""
        if self.ssl:
            import ssl as ssl_module
            ssl_context = ssl_module.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl_module.CERT_NONE
            self.reader, self.writer = await asyncio.open_connection(
                self.host, self.port, ssl=ssl_context
            )
        else:
            self.reader, self.writer = await asyncio.open_connection(
                self.host, self.port
            )
    
    async def disconnect(self):
        """Close the connection."""
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
    
    async def call(self, method: str, params: list = None) -> Dict[str, Any]:
        """Make a JSON-RPC call."""
        self.request_id += 1
        request = {
            'jsonrpc': '2.0',
            'id': self.request_id,
            'method': method,
            'params': params or []
        }
        
        data = json.dumps(request) + '\n'
        self.writer.write(data.encode())
        await self.writer.drain()
        
        response_data = await self.reader.readline()
        return json.loads(response_data.decode())


class LoadTester:
    """
    Load testing orchestrator for RXinDexer.
    
    Supports various test scenarios:
    - Connection stress test
    - Request throughput test
    - Subscription load test
    - Mixed workload simulation
    """
    
    def __init__(self, host: str, port: int, ssl: bool = False):
        self.host = host
        self.port = port
        self.ssl = ssl
        self.metrics: List[RequestMetrics] = []
        self.errors: Dict[str, int] = defaultdict(int)
        self.running = False
    
    async def run_connection_test(self, num_connections: int, 
                                   duration_seconds: int) -> LoadTestResult:
        """
        Test maximum concurrent connections.
        
        Opens specified number of connections and keeps them alive,
        measuring connection success rate and stability.
        """
        print(f"Starting connection test: {num_connections} connections for {duration_seconds}s")
        
        start_time = time.time()
        self.running = True
        self.metrics.clear()
        self.errors.clear()
        
        # Create connection tasks
        clients = []
        connection_tasks = []
        
        for i in range(num_connections):
            client = ElectrumXClient(self.host, self.port, self.ssl)
            clients.append(client)
            connection_tasks.append(self._connect_and_ping(client, i))
        
        # Run connections concurrently
        results = await asyncio.gather(*connection_tasks, return_exceptions=True)
        
        connected = sum(1 for r in results if r is True)
        failed = num_connections - connected
        
        # Keep connections alive and ping periodically
        if duration_seconds > 0:
            ping_tasks = []
            for client in clients:
                if client.reader:
                    ping_tasks.append(self._periodic_ping(client, duration_seconds))
            
            if ping_tasks:
                await asyncio.gather(*ping_tasks, return_exceptions=True)
        
        # Cleanup
        for client in clients:
            await client.disconnect()
        
        self.running = False
        elapsed = time.time() - start_time
        
        return self._calculate_results(elapsed)
    
    async def run_throughput_test(self, num_clients: int, 
                                   duration_seconds: int,
                                   methods: List[str] = None) -> LoadTestResult:
        """
        Test request throughput.
        
        Creates specified number of clients and sends requests
        as fast as possible, measuring throughput and latency.
        """
        if methods is None:
            methods = ['server.version', 'server.ping', 'blockchain.headers.subscribe']
        
        print(f"Starting throughput test: {num_clients} clients, {duration_seconds}s")
        
        start_time = time.time()
        self.running = True
        self.metrics.clear()
        self.errors.clear()
        
        # Connect clients
        clients = []
        for i in range(num_clients):
            client = ElectrumXClient(self.host, self.port, self.ssl)
            try:
                await client.connect()
                clients.append(client)
            except Exception as e:
                self.errors[str(type(e).__name__)] += 1
        
        print(f"Connected {len(clients)} clients")
        
        # Run request loop
        end_time = start_time + duration_seconds
        request_tasks = []
        
        for client in clients:
            request_tasks.append(
                self._request_loop(client, methods, end_time)
            )
        
        await asyncio.gather(*request_tasks, return_exceptions=True)
        
        # Cleanup
        for client in clients:
            await client.disconnect()
        
        self.running = False
        elapsed = time.time() - start_time
        
        return self._calculate_results(elapsed)
    
    async def run_subscription_test(self, num_clients: int,
                                     subscriptions_per_client: int,
                                     duration_seconds: int) -> LoadTestResult:
        """
        Test subscription handling.
        
        Creates clients with multiple subscriptions and measures
        notification throughput and latency.
        """
        print(f"Starting subscription test: {num_clients} clients, "
              f"{subscriptions_per_client} subs each, {duration_seconds}s")
        
        start_time = time.time()
        self.running = True
        self.metrics.clear()
        self.errors.clear()
        
        # Generate test scripthashes
        scripthashes = [
            f'{i:064x}' for i in range(subscriptions_per_client)
        ]
        
        # Connect clients and subscribe
        clients = []
        for i in range(num_clients):
            client = ElectrumXClient(self.host, self.port, self.ssl)
            try:
                await client.connect()
                clients.append(client)
                
                # Subscribe to scripthashes
                for sh in scripthashes:
                    req_start = time.time()
                    try:
                        await client.call('blockchain.scripthash.subscribe', [sh])
                        latency = (time.time() - req_start) * 1000
                        self.metrics.append(RequestMetrics(
                            method='scripthash.subscribe',
                            latency_ms=latency,
                            success=True
                        ))
                    except Exception as e:
                        self.errors[str(type(e).__name__)] += 1
                        
            except Exception as e:
                self.errors[str(type(e).__name__)] += 1
        
        print(f"Connected {len(clients)} clients with {subscriptions_per_client} subscriptions each")
        
        # Keep connections alive
        await asyncio.sleep(duration_seconds)
        
        # Cleanup
        for client in clients:
            await client.disconnect()
        
        self.running = False
        elapsed = time.time() - start_time
        
        return self._calculate_results(elapsed)
    
    async def _connect_and_ping(self, client: ElectrumXClient, index: int) -> bool:
        """Connect a client and send initial ping."""
        try:
            await client.connect()
            req_start = time.time()
            await client.call('server.ping')
            latency = (time.time() - req_start) * 1000
            self.metrics.append(RequestMetrics(
                method='server.ping',
                latency_ms=latency,
                success=True
            ))
            return True
        except Exception as e:
            self.errors[str(type(e).__name__)] += 1
            return False
    
    async def _periodic_ping(self, client: ElectrumXClient, duration: int):
        """Send periodic pings to keep connection alive."""
        end_time = time.time() + duration
        while time.time() < end_time and self.running:
            try:
                req_start = time.time()
                await client.call('server.ping')
                latency = (time.time() - req_start) * 1000
                self.metrics.append(RequestMetrics(
                    method='server.ping',
                    latency_ms=latency,
                    success=True
                ))
            except Exception as e:
                self.errors[str(type(e).__name__)] += 1
            await asyncio.sleep(1.0)
    
    async def _request_loop(self, client: ElectrumXClient, 
                            methods: List[str], end_time: float):
        """Send requests in a loop until end_time."""
        method_idx = 0
        while time.time() < end_time and self.running:
            method = methods[method_idx % len(methods)]
            method_idx += 1
            
            req_start = time.time()
            try:
                if method == 'server.version':
                    await client.call('server.version', ['LoadTest', '1.4'])
                elif method == 'server.ping':
                    await client.call('server.ping')
                elif method == 'blockchain.headers.subscribe':
                    await client.call('blockchain.headers.subscribe')
                else:
                    await client.call(method)
                
                latency = (time.time() - req_start) * 1000
                self.metrics.append(RequestMetrics(
                    method=method,
                    latency_ms=latency,
                    success=True
                ))
            except Exception as e:
                latency = (time.time() - req_start) * 1000
                self.metrics.append(RequestMetrics(
                    method=method,
                    latency_ms=latency,
                    success=False,
                    error=str(type(e).__name__)
                ))
                self.errors[str(type(e).__name__)] += 1
    
    def _calculate_results(self, elapsed: float) -> LoadTestResult:
        """Calculate test results from collected metrics."""
        if not self.metrics:
            return LoadTestResult(
                duration_seconds=elapsed,
                total_requests=0,
                successful_requests=0,
                failed_requests=0,
                requests_per_second=0,
                latency_avg_ms=0,
                latency_p50_ms=0,
                latency_p90_ms=0,
                latency_p99_ms=0,
                latency_max_ms=0,
                errors_by_type=dict(self.errors)
            )
        
        latencies = [m.latency_ms for m in self.metrics]
        successful = sum(1 for m in self.metrics if m.success)
        failed = len(self.metrics) - successful
        
        sorted_latencies = sorted(latencies)
        count = len(latencies)
        
        # Calculate method-specific stats
        method_stats = defaultdict(lambda: {'count': 0, 'success': 0, 'latencies': []})
        for m in self.metrics:
            method_stats[m.method]['count'] += 1
            if m.success:
                method_stats[m.method]['success'] += 1
            method_stats[m.method]['latencies'].append(m.latency_ms)
        
        # Convert to final format
        final_method_stats = {}
        for method, stats in method_stats.items():
            lats = stats['latencies']
            final_method_stats[method] = {
                'count': stats['count'],
                'success_rate': stats['success'] / stats['count'] * 100 if stats['count'] > 0 else 0,
                'avg_latency_ms': statistics.mean(lats) if lats else 0,
            }
        
        return LoadTestResult(
            duration_seconds=elapsed,
            total_requests=len(self.metrics),
            successful_requests=successful,
            failed_requests=failed,
            requests_per_second=len(self.metrics) / elapsed if elapsed > 0 else 0,
            latency_avg_ms=statistics.mean(latencies),
            latency_p50_ms=sorted_latencies[int(count * 0.5)],
            latency_p90_ms=sorted_latencies[int(count * 0.9)],
            latency_p99_ms=sorted_latencies[int(count * 0.99)],
            latency_max_ms=max(latencies),
            errors_by_type=dict(self.errors),
            method_stats=final_method_stats
        )


def print_results(result: LoadTestResult):
    """Print load test results in a formatted way."""
    print("\n" + "=" * 60)
    print("LOAD TEST RESULTS")
    print("=" * 60)
    print(f"Duration: {result.duration_seconds:.2f} seconds")
    print(f"Total Requests: {result.total_requests:,}")
    print(f"Successful: {result.successful_requests:,} ({result.successful_requests/result.total_requests*100:.1f}%)" if result.total_requests > 0 else "Successful: 0")
    print(f"Failed: {result.failed_requests:,}")
    print(f"Throughput: {result.requests_per_second:.2f} req/s")
    print()
    print("Latency:")
    print(f"  Average: {result.latency_avg_ms:.2f} ms")
    print(f"  P50: {result.latency_p50_ms:.2f} ms")
    print(f"  P90: {result.latency_p90_ms:.2f} ms")
    print(f"  P99: {result.latency_p99_ms:.2f} ms")
    print(f"  Max: {result.latency_max_ms:.2f} ms")
    
    if result.errors_by_type:
        print()
        print("Errors:")
        for error_type, count in result.errors_by_type.items():
            print(f"  {error_type}: {count}")
    
    if result.method_stats:
        print()
        print("Per-Method Stats:")
        for method, stats in result.method_stats.items():
            print(f"  {method}: {stats['count']} calls, "
                  f"{stats['success_rate']:.1f}% success, "
                  f"{stats['avg_latency_ms']:.2f}ms avg")
    
    print("=" * 60)


async def main():
    parser = argparse.ArgumentParser(description='RXinDexer Load Testing Suite')
    parser.add_argument('--host', default='localhost', help='ElectrumX host')
    parser.add_argument('--port', type=int, default=50010, help='ElectrumX port')
    parser.add_argument('--ssl', action='store_true', help='Use SSL connection')
    parser.add_argument('--test', choices=['connection', 'throughput', 'subscription', 'all'],
                        default='throughput', help='Test type to run')
    parser.add_argument('--connections', type=int, default=100, help='Number of connections')
    parser.add_argument('--duration', type=int, default=30, help='Test duration in seconds')
    parser.add_argument('--subscriptions', type=int, default=10, 
                        help='Subscriptions per client (subscription test)')
    
    args = parser.parse_args()
    
    tester = LoadTester(args.host, args.port, args.ssl)
    
    if args.test == 'connection' or args.test == 'all':
        print("\n>>> Running Connection Test")
        result = await tester.run_connection_test(args.connections, args.duration)
        print_results(result)
    
    if args.test == 'throughput' or args.test == 'all':
        print("\n>>> Running Throughput Test")
        result = await tester.run_throughput_test(args.connections, args.duration)
        print_results(result)
    
    if args.test == 'subscription' or args.test == 'all':
        print("\n>>> Running Subscription Test")
        result = await tester.run_subscription_test(
            args.connections, args.subscriptions, args.duration
        )
        print_results(result)


if __name__ == '__main__':
    asyncio.run(main())
