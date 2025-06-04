#!/usr/bin/env python3
# /Users/radiant/Desktop/RXinDexer/api_test.py
# This script tests the RXinDexer API endpoints to verify public endpoints are accessible without API keys
# and protected endpoints properly validate API keys with correct HTTP status codes.

import requests
import time
import json
import sys
import argparse
from tabulate import tabulate
from termcolor import colored

# Base URL for API
BASE_URL = "http://localhost:8000"

# Test API key - must match one in the API_KEYS environment variable
VALID_API_KEY = "test-api-key-1"
INVALID_API_KEY = "invalid-api-key"

# Public endpoints that should not require an API key
PUBLIC_ENDPOINTS = [
    "/",
    "/metrics",
    "/api/v1/blocks/latest",
    "/api/v1/status",
    "/api/v1/status/sync",
    "/api/v1/transactions/latest",
    "/api/v1/tokens/stats",
    "/health",
    "/api/v1/health",
    "/api/docs",
    "/api/redoc",
    "/api/openapi.json",
]

# Protected endpoints that require an API key
PROTECTED_ENDPOINTS = [
    "/api/v1/address/rQSZX8K1CvPKE8BvJ7NZ6UQh9Y8TsYULSe/balance",
    "/api/v1/address/rQSZX8K1CvPKE8BvJ7NZ6UQh9Y8TsYULSe/transactions",
    "/api/v1/tokens/stats/holders",
    "/api/v1/blocks/height/1",
    "/api/v1/blocks/hash/0000000000000000000000000000000000000000000000000000000000000000",
    "/api/v1/transactions/0000000000000000000000000000000000000000000000000000000000000000",
]

class APITester:
    def __init__(self, base_url, valid_key, invalid_key):
        self.base_url = base_url
        self.valid_key = valid_key
        self.invalid_key = invalid_key
        self.results = []
        
    def make_request(self, endpoint, api_key=None):
        """Make a request to an endpoint with optional API key"""
        url = f"{self.base_url}{endpoint}"
        headers = {}
        if api_key:
            headers["X-API-Key"] = api_key
            
        try:
            start_time = time.time()
            response = requests.get(url, headers=headers, timeout=5)
            elapsed = time.time() - start_time
            
            security_headers = {
                "X-Content-Type-Options": response.headers.get("X-Content-Type-Options"),
                "X-Frame-Options": response.headers.get("X-Frame-Options"),
                "X-XSS-Protection": response.headers.get("X-XSS-Protection"),
                "Content-Security-Policy": response.headers.get("Content-Security-Policy")
            }
            
            return {
                "status_code": response.status_code,
                "time": elapsed,
                "content_length": len(response.content),
                "security_headers": security_headers
            }
        except Exception as e:
            return {
                "status_code": 0,
                "time": 0,
                "content_length": 0,
                "error": str(e)
            }
    
    def test_public_endpoints(self):
        """Test all public endpoints - should be accessible without API key"""
        print(colored("\n🔍 Testing Public Endpoints (No API Key Required)", "blue", attrs=["bold"]))
        
        for endpoint in PUBLIC_ENDPOINTS:
            result = self.make_request(endpoint)
            test_pass = result["status_code"] in [200, 301, 302, 307, 308]
            self.results.append({
                "endpoint": endpoint,
                "type": "Public",
                "api_key": "None",
                "status": result["status_code"],
                "time": f"{result['time']:.3f}s",
                "size": f"{result['content_length']/1024:.1f}KB",
                "success": test_pass,
                "details": result.get("error", "")
            })
            
            # Print immediate feedback
            status_color = "green" if test_pass else "red"
            status_text = colored(f"{result['status_code']}", status_color)
            print(f"  {endpoint} → {status_text}")
    
    def test_protected_endpoints_no_key(self):
        """Test protected endpoints without API key - should return 401"""
        print(colored("\n🔍 Testing Protected Endpoints (No API Key)", "blue", attrs=["bold"]))
        
        for endpoint in PROTECTED_ENDPOINTS:
            result = self.make_request(endpoint)
            test_pass = result["status_code"] == 401
            self.results.append({
                "endpoint": endpoint,
                "type": "Protected",
                "api_key": "None",
                "status": result["status_code"],
                "time": f"{result['time']:.3f}s",
                "size": f"{result['content_length']/1024:.1f}KB",
                "success": test_pass,
                "details": "Should return 401 Unauthorized"
            })
            
            # Print immediate feedback
            status_color = "green" if test_pass else "red"
            status_text = colored(f"{result['status_code']}", status_color)
            print(f"  {endpoint} → {status_text}")
    
    def test_protected_endpoints_invalid_key(self):
        """Test protected endpoints with invalid API key - should return 403"""
        print(colored("\n🔍 Testing Protected Endpoints (Invalid API Key)", "blue", attrs=["bold"]))
        
        for endpoint in PROTECTED_ENDPOINTS:
            result = self.make_request(endpoint, self.invalid_key)
            test_pass = result["status_code"] == 403
            self.results.append({
                "endpoint": endpoint,
                "type": "Protected",
                "api_key": "Invalid",
                "status": result["status_code"],
                "time": f"{result['time']:.3f}s",
                "size": f"{result['content_length']/1024:.1f}KB",
                "success": test_pass,
                "details": "Should return 403 Forbidden"
            })
            
            # Print immediate feedback
            status_color = "green" if test_pass else "red"
            status_text = colored(f"{result['status_code']}", status_color)
            print(f"  {endpoint} → {status_text}")
    
    def test_protected_endpoints_valid_key(self):
        """Test protected endpoints with valid API key - should return 200 or 404 (if resource not found)"""
        print(colored("\n🔍 Testing Protected Endpoints (Valid API Key)", "blue", attrs=["bold"]))
        
        for endpoint in PROTECTED_ENDPOINTS:
            result = self.make_request(endpoint, self.valid_key)
            # Protected endpoints might return 404 if the resource doesn't exist - that's ok for this test
            test_pass = result["status_code"] in [200, 404] 
            self.results.append({
                "endpoint": endpoint,
                "type": "Protected",
                "api_key": "Valid",
                "status": result["status_code"],
                "time": f"{result['time']:.3f}s",
                "size": f"{result['content_length']/1024:.1f}KB",
                "success": test_pass,
                "details": "Should return 200 or 404 (resource not found)"
            })
            
            # Print immediate feedback
            status_color = "green" if test_pass else "red"
            status_text = colored(f"{result['status_code']}", status_color)
            print(f"  {endpoint} → {status_text}")
    
    def test_rate_limiting(self):
        """Test rate limiting is properly disabled/configured by making rapid requests"""
        print(colored("\n🔍 Testing Rate Limiting", "blue", attrs=["bold"]))
        
        # Use a public endpoint to test rate limiting
        endpoint = "/api/v1/status"
        
        # Make 20 rapid requests
        all_success = True
        for i in range(20):
            result = self.make_request(endpoint)
            if result["status_code"] == 429:
                all_success = False
                self.results.append({
                    "endpoint": f"{endpoint} (Request {i+1})",
                    "type": "Rate Limit",
                    "api_key": "None",
                    "status": result["status_code"],
                    "time": f"{result['time']:.3f}s",
                    "size": f"{result['content_length']/1024:.1f}KB",
                    "success": False,
                    "details": "Rate limiting still triggered"
                })
                print(f"  Request {i+1} → {colored('429 (Rate Limited)', 'red')}")
                break
            
            # Brief pause to not completely overwhelm the server
            time.sleep(0.05)
        
        if all_success:
            self.results.append({
                "endpoint": endpoint,
                "type": "Rate Limit",
                "api_key": "None",
                "status": 200,
                "time": "N/A",
                "size": "N/A",
                "success": True,
                "details": "20 rapid requests completed without rate limiting"
            })
            print(f"  20 rapid requests → {colored('All Succeeded', 'green')}")
    
    def test_security_headers(self):
        """Test that security headers are properly added to responses"""
        print(colored("\n🔍 Testing Security Headers", "blue", attrs=["bold"]))
        
        # Test a mix of public and protected endpoints
        test_endpoints = ["/", "/api/v1/status", "/api/v1/blocks/latest"]
        
        for endpoint in test_endpoints:
            result = self.make_request(endpoint)
            headers = result.get("security_headers", {})
            
            required_headers = [
                "X-Content-Type-Options",
                "X-Frame-Options",
                "X-XSS-Protection",
                "Content-Security-Policy"
            ]
            
            missing_headers = [h for h in required_headers if not headers.get(h)]
            test_pass = len(missing_headers) == 0
            
            self.results.append({
                "endpoint": endpoint,
                "type": "Security",
                "api_key": "None",
                "status": result["status_code"],
                "time": f"{result['time']:.3f}s",
                "size": f"{result['content_length']/1024:.1f}KB",
                "success": test_pass,
                "details": f"Missing headers: {', '.join(missing_headers)}" if missing_headers else "All security headers present"
            })
            
            # Print immediate feedback
            status_color = "green" if test_pass else "red"
            status_text = colored("PASS" if test_pass else "FAIL", status_color)
            print(f"  {endpoint} → {status_text}")
    
    def run_all_tests(self):
        """Run all API tests"""
        print(colored("\n🚀 Starting RXinDexer API Tests", "yellow", attrs=["bold"]))
        print(f"Base URL: {self.base_url}")
        
        # Run all test groups
        self.test_public_endpoints()
        self.test_protected_endpoints_no_key()
        self.test_protected_endpoints_invalid_key()
        self.test_protected_endpoints_valid_key()
        self.test_rate_limiting()
        self.test_security_headers()
        
        # Print summary
        self.print_summary()
    
    def print_summary(self):
        """Print a summary of all test results"""
        print(colored("\n📊 Test Results Summary", "yellow", attrs=["bold"]))
        
        # Calculate totals
        total_tests = len(self.results)
        passed_tests = sum(1 for r in self.results if r["success"])
        failed_tests = total_tests - passed_tests
        
        # Print overall stats
        success_rate = (passed_tests / total_tests) * 100 if total_tests > 0 else 0
        print(f"Total Tests: {total_tests}")
        print(f"Passed: {colored(passed_tests, 'green')}")
        print(f"Failed: {colored(failed_tests, 'red')}")
        print(f"Success Rate: {colored(f'{success_rate:.1f}%', 'blue')}")
        
        # Create table data for detailed results
        table_data = []
        for r in self.results:
            status = colored("✓", "green") if r["success"] else colored("✗", "red")
            endpoint = r["endpoint"]
            test_type = r["type"]
            api_key = r["api_key"]
            http_status = r["status"]
            
            table_data.append([status, endpoint, test_type, api_key, http_status])
        
        # Print table
        print("\nDetailed Results:")
        headers = ["Result", "Endpoint", "Type", "API Key", "HTTP Status"]
        print(tabulate(table_data, headers=headers, tablefmt="simple"))
        
        # Print conclusion
        if failed_tests == 0:
            print(colored("\n✅ All tests passed! The API is working correctly.", "green", attrs=["bold"]))
        else:
            print(colored(f"\n❌ {failed_tests} tests failed. See above for details.", "red", attrs=["bold"]))
            
        # Print recommendations
        print(colored("\n📝 Recommendations:", "yellow"))
        if any(r["type"] == "Public" and not r["success"] for r in self.results):
            print("- Some public endpoints are not accessible without API key.")
        if any(r["type"] == "Protected" and r["api_key"] == "None" and not r["success"] for r in self.results):
            print("- Protected endpoints should return 401 when no API key is provided.")
        if any(r["type"] == "Protected" and r["api_key"] == "Invalid" and not r["success"] for r in self.results):
            print("- Protected endpoints should return 403 when an invalid API key is provided.")
        if any(r["type"] == "Rate Limit" and not r["success"] for r in self.results):
            print("- Rate limiting is still too aggressive for testing.")
        if any(r["type"] == "Security" and not r["success"] for r in self.results):
            print("- Not all security headers are being applied correctly.")

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Test RXinDexer API endpoints")
    parser.add_argument("--url", default=BASE_URL, help="Base URL for the API")
    parser.add_argument("--key", default=VALID_API_KEY, help="Valid API key to use for testing")
    parser.add_argument("--bad-key", default=INVALID_API_KEY, help="Invalid API key to use for testing")
    args = parser.parse_args()
    
    # Create tester and run tests
    tester = APITester(args.url, args.key, args.bad_key)
    tester.run_all_tests()

if __name__ == "__main__":
    main()
