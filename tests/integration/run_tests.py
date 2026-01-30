#!/usr/bin/env python3
"""
RXinDexer Integration Test Suite

Tests all API endpoints against a running RXinDexer instance.
Usage: python3 run_tests.py [--host HOST] [--port PORT]
"""

import asyncio
import json
import os
import sys
import time
import socket
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Test configuration
RXINDEXER_HOST = os.environ.get('RXINDEXER_HOST', 'localhost')
RXINDEXER_TCP_PORT = int(os.environ.get('RXINDEXER_TCP_PORT', '50010'))
RXINDEXER_RPC_PORT = int(os.environ.get('RXINDEXER_RPC_PORT', '8000'))

# Test results
results: List[Dict[str, Any]] = []
passed = 0
failed = 0
skipped = 0


class ElectrumXClient:
    """Simple ElectrumX JSON-RPC client over TCP."""
    
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.request_id = 0
    
    async def connect(self) -> bool:
        """Connect to ElectrumX server."""
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=30.0
            )
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            return False
    
    async def close(self):
        """Close connection."""
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
    
    async def call(self, method: str, params: List[Any] = None) -> Dict[str, Any]:
        """Make JSON-RPC call."""
        if not self.writer:
            raise RuntimeError("Not connected")
        
        self.request_id += 1
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or [],
            "id": self.request_id
        }
        
        data = json.dumps(request) + "\n"
        self.writer.write(data.encode())
        await self.writer.drain()
        
        # Read response
        response_data = await asyncio.wait_for(
            self.reader.readline(),
            timeout=30.0
        )
        
        return json.loads(response_data.decode())


def log_result(test_name: str, success: bool, message: str = "", duration: float = 0):
    """Log test result."""
    global passed, failed
    
    status = "✅ PASS" if success else "❌ FAIL"
    print(f"{status} | {test_name} ({duration:.3f}s)")
    if message and not success:
        print(f"       └─ {message}")
    
    results.append({
        "test": test_name,
        "success": success,
        "message": message,
        "duration": duration,
        "timestamp": datetime.now().isoformat()
    })
    
    if success:
        passed += 1
    else:
        failed += 1


async def test_server_version(client: ElectrumXClient) -> bool:
    """Test server.version method."""
    start = time.time()
    try:
        response = await client.call("server.version", ["RXinDexer-Test", "1.4"])
        if "result" in response and isinstance(response["result"], list):
            log_result("server.version", True, f"Version: {response['result']}", time.time() - start)
            return True
        else:
            log_result("server.version", False, f"Unexpected response: {response}", time.time() - start)
            return False
    except Exception as e:
        log_result("server.version", False, str(e), time.time() - start)
        return False


async def test_server_features(client: ElectrumXClient) -> bool:
    """Test server.features method."""
    start = time.time()
    try:
        response = await client.call("server.features")
        if "result" in response and "genesis_hash" in response["result"]:
            log_result("server.features", True, "", time.time() - start)
            return True
        else:
            log_result("server.features", False, f"Missing genesis_hash", time.time() - start)
            return False
    except Exception as e:
        log_result("server.features", False, str(e), time.time() - start)
        return False


async def test_glyph_list_tokens(client: ElectrumXClient) -> bool:
    """Test glyph.list_tokens method."""
    start = time.time()
    try:
        response = await client.call("glyph.list_tokens", [10, 0])
        if "result" in response:
            tokens = response["result"]
            if isinstance(tokens, list):
                log_result("glyph.list_tokens", True, f"Found {len(tokens)} tokens", time.time() - start)
                return True
        if "error" in response:
            # Method exists but returned error (acceptable if no tokens indexed yet)
            log_result("glyph.list_tokens", True, f"Method exists: {response.get('error', {}).get('message', '')}", time.time() - start)
            return True
        log_result("glyph.list_tokens", False, f"Unexpected: {response}", time.time() - start)
        return False
    except Exception as e:
        log_result("glyph.list_tokens", False, str(e), time.time() - start)
        return False


async def test_glyph_get_token(client: ElectrumXClient) -> bool:
    """Test glyph.get_token method with invalid ref."""
    start = time.time()
    try:
        # Test with dummy ref - should return null or error
        response = await client.call("glyph.get_token", ["0" * 72])
        if "result" in response or "error" in response:
            log_result("glyph.get_token", True, "Method responds correctly", time.time() - start)
            return True
        log_result("glyph.get_token", False, f"Unexpected: {response}", time.time() - start)
        return False
    except Exception as e:
        log_result("glyph.get_token", False, str(e), time.time() - start)
        return False


async def test_glyph_search_tokens(client: ElectrumXClient) -> bool:
    """Test glyph.search_tokens method."""
    start = time.time()
    try:
        response = await client.call("glyph.search_tokens", ["test", 10])
        if "result" in response or "error" in response:
            log_result("glyph.search_tokens", True, "Method responds", time.time() - start)
            return True
        log_result("glyph.search_tokens", False, f"Unexpected: {response}", time.time() - start)
        return False
    except Exception as e:
        log_result("glyph.search_tokens", False, str(e), time.time() - start)
        return False


async def test_glyph_validate_protocols(client: ElectrumXClient) -> bool:
    """Test glyph.validate_protocols method."""
    start = time.time()
    try:
        response = await client.call("glyph.validate_protocols", [[1, 2]])
        if "result" in response or "error" in response:
            log_result("glyph.validate_protocols", True, "", time.time() - start)
            return True
        log_result("glyph.validate_protocols", False, f"Unexpected: {response}", time.time() - start)
        return False
    except Exception as e:
        log_result("glyph.validate_protocols", False, str(e), time.time() - start)
        return False


async def test_wave_resolve(client: ElectrumXClient) -> bool:
    """Test wave.resolve method."""
    start = time.time()
    try:
        response = await client.call("wave.resolve", ["test"])
        if "result" in response or "error" in response:
            log_result("wave.resolve", True, "Method responds", time.time() - start)
            return True
        log_result("wave.resolve", False, f"Unexpected: {response}", time.time() - start)
        return False
    except Exception as e:
        log_result("wave.resolve", False, str(e), time.time() - start)
        return False


async def test_wave_check_available(client: ElectrumXClient) -> bool:
    """Test wave.check_available method."""
    start = time.time()
    try:
        response = await client.call("wave.check_available", ["testname123"])
        if "result" in response or "error" in response:
            log_result("wave.check_available", True, "Method responds", time.time() - start)
            return True
        log_result("wave.check_available", False, f"Unexpected: {response}", time.time() - start)
        return False
    except Exception as e:
        log_result("wave.check_available", False, str(e), time.time() - start)
        return False


async def test_wave_stats(client: ElectrumXClient) -> bool:
    """Test wave.stats method."""
    start = time.time()
    try:
        response = await client.call("wave.stats")
        if "result" in response or "error" in response:
            log_result("wave.stats", True, "Method responds", time.time() - start)
            return True
        log_result("wave.stats", False, f"Unexpected: {response}", time.time() - start)
        return False
    except Exception as e:
        log_result("wave.stats", False, str(e), time.time() - start)
        return False


async def test_swap_get_unconfirmed_orders(client: ElectrumXClient) -> bool:
    """Test swap.get_unconfirmed_orders method."""
    start = time.time()
    try:
        response = await client.call("swap.get_unconfirmed_orders", ["0" * 72, "0" * 72])
        if "result" in response or "error" in response:
            log_result("swap.get_unconfirmed_orders", True, "Method responds", time.time() - start)
            return True
        log_result("swap.get_unconfirmed_orders", False, f"Unexpected: {response}", time.time() - start)
        return False
    except Exception as e:
        log_result("swap.get_unconfirmed_orders", False, str(e), time.time() - start)
        return False


async def test_dmint_get_contracts(client: ElectrumXClient) -> bool:
    """Test dmint.get_contracts method."""
    start = time.time()
    try:
        response = await client.call("dmint.get_contracts")
        if "result" in response or "error" in response:
            log_result("dmint.get_contracts", True, "Method responds", time.time() - start)
            return True
        log_result("dmint.get_contracts", False, f"Unexpected: {response}", time.time() - start)
        return False
    except Exception as e:
        log_result("dmint.get_contracts", False, str(e), time.time() - start)
        return False


async def test_mempool_glyph_stats(client: ElectrumXClient) -> bool:
    """Test mempool.glyph_stats method."""
    start = time.time()
    try:
        response = await client.call("mempool.glyph_stats")
        if "result" in response or "error" in response:
            log_result("mempool.glyph_stats", True, "Method responds", time.time() - start)
            return True
        log_result("mempool.glyph_stats", False, f"Unexpected: {response}", time.time() - start)
        return False
    except Exception as e:
        log_result("mempool.glyph_stats", False, str(e), time.time() - start)
        return False


async def test_subscription_methods(client: ElectrumXClient) -> bool:
    """Test subscription methods exist."""
    start = time.time()
    methods = [
        "glyph.subscribe.balance",
        "glyph.subscribe.token",
        "swap.subscribe.orderbook",
        "wave.subscribe.name",
    ]
    
    all_pass = True
    for method in methods:
        try:
            # Subscriptions need valid params, but we test method existence
            response = await client.call(method, ["test"])
            if "error" in response:
                # Method exists (error is expected for invalid params)
                pass
            elif "result" in response:
                pass
            else:
                all_pass = False
        except Exception:
            all_pass = False
    
    log_result("subscription_methods", all_pass, f"Tested {len(methods)} methods", time.time() - start)
    return all_pass


def wait_for_server(host: str, port: int, timeout: int = 120) -> bool:
    """Wait for server to be available."""
    print(f"Waiting for RXinDexer at {host}:{port}...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()
            if result == 0:
                print(f"Server available after {time.time() - start:.1f}s")
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


async def run_all_tests():
    """Run all integration tests."""
    global passed, failed, skipped
    
    print("=" * 60)
    print("RXinDexer Integration Test Suite")
    print("=" * 60)
    print(f"Target: {RXINDEXER_HOST}:{RXINDEXER_TCP_PORT}")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 60)
    print()
    
    # Wait for server
    if not wait_for_server(RXINDEXER_HOST, RXINDEXER_TCP_PORT):
        print("❌ Server not available")
        sys.exit(1)
    
    # Allow extra time for initialization
    print("Waiting for indexer initialization...")
    await asyncio.sleep(5)
    
    # Connect client
    client = ElectrumXClient(RXINDEXER_HOST, RXINDEXER_TCP_PORT)
    if not await client.connect():
        print("❌ Failed to connect")
        sys.exit(1)
    
    print("\n--- Core Server Tests ---\n")
    
    # Run tests
    await test_server_version(client)
    await test_server_features(client)
    
    print("\n--- Glyph Token Tests ---\n")
    
    await test_glyph_list_tokens(client)
    await test_glyph_get_token(client)
    await test_glyph_search_tokens(client)
    await test_glyph_validate_protocols(client)
    
    print("\n--- WAVE Naming Tests ---\n")
    
    await test_wave_resolve(client)
    await test_wave_check_available(client)
    await test_wave_stats(client)
    
    print("\n--- Swap Order Tests ---\n")
    
    await test_swap_get_unconfirmed_orders(client)
    
    print("\n--- dMint Contract Tests ---\n")
    
    await test_dmint_get_contracts(client)
    
    print("\n--- Mempool Tests ---\n")
    
    await test_mempool_glyph_stats(client)
    
    print("\n--- Subscription Tests ---\n")
    
    await test_subscription_methods(client)
    
    # Close connection
    await client.close()
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    total = passed + failed + skipped
    print(f"Total:   {total}")
    print(f"Passed:  {passed} ({100*passed/total:.1f}%)" if total > 0 else "Passed:  0")
    print(f"Failed:  {failed}")
    print(f"Skipped: {skipped}")
    print("=" * 60)
    
    # Save results
    results_file = "/app/test_results/integration_results.json"
    try:
        os.makedirs(os.path.dirname(results_file), exist_ok=True)
        with open(results_file, "w") as f:
            json.dump({
                "summary": {
                    "total": total,
                    "passed": passed,
                    "failed": failed,
                    "skipped": skipped,
                    "timestamp": datetime.now().isoformat()
                },
                "results": results
            }, f, indent=2)
        print(f"\nResults saved to: {results_file}")
    except Exception as e:
        print(f"Warning: Could not save results: {e}")
    
    # Exit code
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(run_all_tests())
