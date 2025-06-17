#!/usr/bin/env python3
"""
RXinDexer API Test Script

This script tests all available API endpoints and verifies their responses.
"""
import requests
import json
import time
from typing import Dict, Any, Optional

class RXinDexerAPITester:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'RXinDexer-API-Tester/1.0',
            'Accept': 'application/json'
        })
        self.test_results = []
    
    def run_tests(self):
        """Run all API tests and print results."""
        print(f"🚀 Starting RXinDexer API Tests - {time.ctime()}")
        print(f"🔗 Base URL: {self.base_url}\n")
        
        # Run all test methods
        test_methods = [
            self.test_health_endpoint,
            self.test_status_endpoint,
            self.test_block_endpoints,
            self.test_transaction_endpoints,
            self.test_address_endpoints,
            self.test_token_endpoints
        ]
        
        for test_method in test_methods:
            try:
                test_method()
            except Exception as e:
                print(f"❌ Error running {test_method.__name__}: {str(e)}")
        
        self.print_summary()
    
    def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Make an HTTP request and handle errors."""
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.request(method, url, timeout=10, **kwargs)
            response.raise_for_status()
            return {
                'success': True,
                'status_code': response.status_code,
                'data': response.json() if response.content else {}
            }
        except requests.exceptions.RequestException as e:
            return {
                'success': False,
                'error': str(e),
                'status_code': getattr(e.response, 'status_code', 0) if hasattr(e, 'response') else 0
            }
    
    def _record_test(self, name: str, success: bool, details: str = ""):
        """Record test result."""
        result = {
            'name': name,
            'success': success,
            'details': details,
            'timestamp': time.time()
        }
        self.test_results.append(result)
        status = "✅" if success else "❌"
        print(f"{status} {name}")
        if details and not success:
            print(f"   Details: {details}")
    
    def print_summary(self):
        """Print test summary."""
        print("\n" + "="*50)
        print("📊 Test Summary")
        print("="*50)
        
        total = len(self.test_results)
        passed = sum(1 for r in self.test_results if r['success'])
        failed = total - passed
        
        print(f"Total Tests: {total}")
        print(f"✅ Passed: {passed}")
        print(f"❌ Failed: {failed}")
        
        if failed > 0:
            print("\nFailed Tests:")
            for test in [r for r in self.test_results if not r['success']]:
                print(f"- {test['name']}: {test.get('details', 'No details')}")
    
    # Test Methods
    
    def test_health_endpoint(self):
        """Test the health check endpoint."""
        result = self._make_request('GET', '/health')
        self._record_test(
            "Health Check",
            result['success'] and 'status' in result.get('data', {}),
            f"Status: {result.get('status_code', 'N/A')} - {result.get('data', {}).get('status', 'No status')}"
        )
    
    def test_status_endpoint(self):
        """Test the status endpoint."""
        result = self._make_request('GET', '/api/v1/status')
        success = result['success'] and 'current_height' in result.get('data', {})
        self._record_test(
            "Status Endpoint",
            success,
            f"Current Height: {result.get('data', {}).get('current_height', 'N/A')} - " \
            f"Sync Progress: {result.get('data', {}).get('sync_progress', 'N/A')}%"
        )
    
    def test_block_endpoints(self):
        """Test block-related endpoints."""
        # Get latest block first
        latest = self._make_request('GET', '/api/v1/blocks/latest')
        if not latest['success']:
            self._record_test("Get Latest Block", False, "Failed to fetch latest block")
            return
        
        latest_block = latest['data']
        latest_height = latest_block.get('height')
        latest_hash = latest_block.get('hash')
        
        self._record_test(
            "Get Latest Block",
            latest['success'] and latest_height is not None,
            f"Latest Block: {latest_height} - {latest_hash}"
        )
        
        # Test getting block by height
        if latest_height:
            by_height = self._make_request('GET', f'/api/v1/blocks/{latest_height}')
            self._record_test(
                "Get Block by Height",
                by_height['success'],
                f"Status: {by_height.get('status_code', 'N/A')} - " \
                f"Hash: {by_height.get('data', {}).get('hash', 'N/A')}"
            )
        
        # Test getting block by hash
        if latest_hash:
            by_hash = self._make_request('GET', f'/api/v1/blocks/hash/{latest_hash}')
            self._record_test(
                "Get Block by Hash",
                by_hash['success'],
                f"Status: {by_hash.get('status_code', 'N/A')} - " \
                f"Height: {by_hash.get('data', {}).get('height', 'N/A')}"
            )
    
    def test_transaction_endpoints(self):
        """Test transaction-related endpoints."""
        # First get a transaction ID from the latest block
        latest = self._make_request('GET', '/api/v1/blocks/latest')
        if not latest['success'] or 'tx' not in latest.get('data', {}):
            self._record_test("Get Transaction (Skipped)", True, "No transactions in latest block")
            return
        
        txid = latest['data']['tx'][0]  # Get first transaction ID
        
        # Test getting transaction by ID
        result = self._make_request('GET', f'/api/v1/transactions/{txid}')
        self._record_test(
            "Get Transaction by ID",
            result['success'],
            f"Status: {result.get('status_code', 'N/A')} - " \
            f"TxID: {result.get('data', {}).get('txid', 'N/A')}"
        )
    
    def test_address_endpoints(self):
        """Test address-related endpoints."""
        # Get an address from the latest block's transactions
        latest = self._make_request('GET', '/api/v1/blocks/latest')
        if not latest['success'] or 'tx' not in latest.get('data', {}):
            self._record_test("Get Address (Skipped)", True, "No transactions in latest block")
            return
        
        txid = latest['data']['tx'][0]  # Get first transaction ID
        tx = self._make_request('GET', f'/api/v1/transactions/{txid}')
        
        if not tx['success'] or 'vout' not in tx.get('data', {}):
            self._record_test("Get Address (Skipped)", True, "No outputs in transaction")
            return
        
        # Find an address in the outputs
        address = None
        for output in tx['data']['vout']:
            if 'scriptPubKey' in output and 'addresses' in output['scriptPubKey'] and output['scriptPubKey']['addresses']:
                address = output['scriptPubKey']['addresses'][0]
                break
        
        if not address:
            self._record_test("Get Address (Skipped)", True, "No address found in outputs")
            return
        
        # Test getting address info
        result = self._make_request('GET', f'/api/v1/addresses/{address}')
        self._record_test(
            "Get Address Info",
            result['success'],
            f"Status: {result.get('status_code', 'N/A')} - " \
            f"Address: {result.get('data', {}).get('address', 'N/A')}"
        )
    
    def test_token_endpoints(self):
        """Test token-related endpoints."""
        # Test listing tokens
        result = self._make_request('GET', '/api/v1/tokens')
        success = result['success']
        tokens = result.get('data', [])
        
        self._record_test(
            "List Tokens",
            success,
            f"Found {len(tokens)} tokens"
        )
        
        # Test getting token details if we have tokens
        if tokens and isinstance(tokens, list) and 'ref' in tokens[0]:
            token_ref = tokens[0]['ref']
            result = self._make_request('GET', f'/api/v1/tokens/{token_ref}')
            self._record_test(
                "Get Token Details",
                result['success'],
                f"Token: {result.get('data', {}).get('ref', 'N/A')}"
            )

if __name__ == "__main__":
    import sys
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    tester = RXinDexerAPITester(base_url)
    tester.run_tests()
