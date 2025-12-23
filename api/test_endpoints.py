#!/usr/bin/env python3
"""
RXinDexer API Endpoint Test Script
==================================

This script performs basic health checks on critical API endpoints.
It should be run after deployment to verify that the API is functioning correctly.
"""

import requests
import sys
import time
import json
from typing import Dict, List, Any, Tuple, Optional
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("api_test")

# API base URL - change this for different environments
BASE_URL = "http://localhost:8000"  # Default for local development

# Timeout for requests in seconds
REQUEST_TIMEOUT = 10

# Test categories and their endpoints
ENDPOINTS = {
    "health": [
        "/health",
        "/health/db",
        "/db-health",
    ],
    "core": [
        "/blocks/recent",
        "/transactions/recent",
    ],
    "glyph_tokens": [
        "/tokens/search",
        "/tokens/recent",
        "/tokens/stats",
    ]
}

def test_endpoint(url: str, expected_status: int = 200) -> Tuple[bool, Optional[Dict]]:
    """Test if an endpoint is accessible and returns the expected status code."""
    try:
        logger.info(f"Testing endpoint: {url}")
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        if response.status_code == expected_status:
            logger.info(f"✅ {url} - Status: {response.status_code}")
            try:
                return True, response.json()
            except:
                return True, {"status": "success", "non_json_response": True}
        else:
            logger.error(f"❌ {url} - Expected status {expected_status}, got {response.status_code}")
            return False, None
    except Exception as e:
        logger.error(f"❌ {url} - Error: {str(e)}")
        return False, None

def run_tests() -> bool:
    """Run all endpoint tests and return overall success status."""
    all_passed = True
    results = {
        "passed": [],
        "failed": []
    }

    # Test health endpoints first
    for endpoint in ENDPOINTS["health"]:
        url = f"{BASE_URL}{endpoint}"
        success, _ = test_endpoint(url)
        if success:
            results["passed"].append(endpoint)
        else:
            results["failed"].append(endpoint)
            all_passed = False

    # If health checks fail, don't test other endpoints
    if not all_passed:
        logger.error("❌ Health checks failed - skipping other tests")
        return False

    # Test all other endpoint categories
    for category, endpoints in ENDPOINTS.items():
        if category == "health":
            continue
            
        logger.info(f"\nTesting {category} endpoints...")
        for endpoint in endpoints:
            url = f"{BASE_URL}{endpoint}"
            success, _ = test_endpoint(url)
            if success:
                results["passed"].append(endpoint)
            else:
                results["failed"].append(endpoint)
                all_passed = False

    # Print summary
    logger.info("\n========== TEST SUMMARY ==========")
    logger.info(f"Total endpoints tested: {len(results['passed']) + len(results['failed'])}")
    logger.info(f"Passed: {len(results['passed'])}")
    logger.info(f"Failed: {len(results['failed'])}")
    
    if results["failed"]:
        logger.error("Failed endpoints:")
        for endpoint in results["failed"]:
            logger.error(f"  - {endpoint}")

    return all_passed

def test_specific_glyph_endpoints():
    """Run more specific tests on glyph token endpoints that require parameters."""
    logger.info("\n========== TESTING SPECIFIC GLYPH ENDPOINTS ==========")
    
    # Test token search with parameters
    try:
        url = f"{BASE_URL}/tokens/search?type=nft&limit=5"
        logger.info(f"Testing: {url}")
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            tokens = response.json()
            logger.info(f"✅ Search by type returned {len(tokens)} tokens")
        else:
            logger.error(f"❌ Search by type failed with status {response.status_code}")
    except Exception as e:
        logger.error(f"❌ Error testing token search: {str(e)}")
    
    # Test protocol endpoint if any protocols exist
    try:
        url = f"{BASE_URL}/tokens/stats"
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            stats = response.json()
            protocol_counts = stats.get("protocol_usage", [])
            if protocol_counts and len(protocol_counts) > 0:
                protocol_id = protocol_counts[0].get("protocol_id", 1)
                url = f"{BASE_URL}/tokens/protocol/{protocol_id}"
                logger.info(f"Testing: {url}")
                response = requests.get(url, timeout=REQUEST_TIMEOUT)
                if response.status_code == 200:
                    tokens = response.json()
                    logger.info(f"✅ Protocol {protocol_id} returned {len(tokens)} tokens")
                else:
                    logger.error(f"❌ Protocol endpoint failed with status {response.status_code}")
    except Exception as e:
        logger.error(f"❌ Error testing protocol endpoint: {str(e)}")

if __name__ == "__main__":
    logger.info("Starting RXinDexer API endpoint tests...")
    
    # Parse command line arguments
    if len(sys.argv) > 1:
        BASE_URL = sys.argv[1]
    
    logger.info(f"Using API base URL: {BASE_URL}")
    
    # Run basic tests
    success = run_tests()
    
    # Run specific glyph token tests
    test_specific_glyph_endpoints()
    
    if success:
        logger.info("✅ All endpoint tests passed!")
        sys.exit(0)
    else:
        logger.error("❌ Some endpoint tests failed")
        sys.exit(1)
