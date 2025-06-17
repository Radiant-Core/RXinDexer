#!/usr/bin/env python
# /Users/radiant/Desktop/RXinDexer/api_debug.py
# This script helps diagnose API issues by checking endpoint paths and authentication settings

import requests
import sys
import os
import json
from urllib.parse import urljoin

BASE_URL = "http://localhost:8000"
API_KEY = "test-api-key-1"

# Public endpoints that should be accessible without authentication
PUBLIC_ENDPOINTS = [
    "/",
    "/health",
    "/api/v1/health",
    "/metrics",
    "/api/v1/tokens/stats",
    "/api/v1/status",
    "/api/v1/status/sync",
    "/api/v1/blocks/latest",
    "/api/v1/transactions/latest"
]

# Protected endpoints that require authentication
PROTECTED_ENDPOINTS = [
    "/api/v1/tokens/",
    "/api/v1/addresses/",
    "/api/v1/blocks/",
    "/api/v1/transactions/"
]

def test_endpoint(endpoint, use_api_key=False, description=None):
    """Test an API endpoint and report results"""
    url = urljoin(BASE_URL, endpoint)
    headers = {"X-API-Key": API_KEY} if use_api_key else {}
    
    description = description or endpoint
    
    print(f"\nTesting {description}:")
    try:
        response = requests.get(url, headers=headers, timeout=5)
        status = response.status_code
        
        if 200 <= status < 300:
            print(f"✅ Status: {status} - SUCCESS")
        elif status == 401:
            print(f"❌ Status: {status} - UNAUTHORIZED (needs API key)")
        elif status == 403:
            print(f"❌ Status: {status} - FORBIDDEN (invalid API key)")
        elif status == 404:
            print(f"❌ Status: {status} - NOT FOUND (endpoint missing)")
        elif status == 429:
            print(f"❌ Status: {status} - RATE LIMITED (too many requests)")
        else:
            print(f"❌ Status: {status} - FAILED")
            
        # Try to pretty print the response
        try:
            json_response = response.json()
            print(f"Response: {json.dumps(json_response, indent=2)[:300]}")
        except:
            print(f"Response: {response.text[:300]}")
            
        return status
    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        return None

def main():
    """Run a diagnostic check on all API endpoints"""
    print("=" * 50)
    print("RXinDexer API Diagnostic Tool")
    print("=" * 50)
    
    # Test public endpoints (should work without API key)
    print("\n[TESTING PUBLIC ENDPOINTS]")
    public_failures = 0
    
    for endpoint in PUBLIC_ENDPOINTS:
        status = test_endpoint(endpoint, use_api_key=False, description=f"{endpoint} (no auth)")
        if status != 200:
            public_failures += 1
    
    # Test protected endpoints (should require API key)
    print("\n[TESTING PROTECTED ENDPOINTS]")
    protected_failures = 0
    
    for endpoint in PROTECTED_ENDPOINTS:
        # Test without API key (should fail with 401)
        status_no_key = test_endpoint(endpoint, use_api_key=False, description=f"{endpoint} (no auth)")
        if status_no_key != 401:
            protected_failures += 1
            
        # Test with API key (should succeed with 200)
        status_with_key = test_endpoint(endpoint, use_api_key=True, description=f"{endpoint} (with auth)")
        if status_with_key != 200:
            protected_failures += 1
    
    # Print summary
    print("\n" + "=" * 50)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 50)
    print(f"Public endpoints tested: {len(PUBLIC_ENDPOINTS)}")
    print(f"Public endpoint failures: {public_failures}")
    print(f"Protected endpoints tested: {len(PROTECTED_ENDPOINTS) * 2}")
    print(f"Protected endpoint failures: {protected_failures}")
    
    if public_failures == 0 and protected_failures == 0:
        print("\n✅ ALL TESTS PASSED!")
        return 0
    else:
        print("\n❌ SOME TESTS FAILED!")
        return 1

if __name__ == "__main__":
    sys.exit(main())
