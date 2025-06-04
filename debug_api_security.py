#!/usr/bin/env python3
"""Debug script to examine API security behavior directly using curl"""

import subprocess
import sys

BASE_URL = "http://localhost:8000"
API_KEY = "test-api-key-1"  # One of the test API keys

def run_curl(command):
    """Run a curl command and return the output"""
    process = subprocess.run(command, shell=True, capture_output=True, text=True)
    return process.stdout

# Test the token endpoint without an API key
print("\nTOKEN ENDPOINT WITHOUT API KEY:")
response = run_curl(f"curl -s -i {BASE_URL}/api/v1/tokens/")
print(response)

# Test with valid API key
print("\nTOKEN ENDPOINT WITH API KEY:")
response = run_curl(f"curl -s -i -H 'X-API-Key: {API_KEY}' {BASE_URL}/api/v1/tokens/")
print(response)

# Test direct FastAPI middleware behavior by using the raw health endpoint
print("\nHEALTH ENDPOINT (HEADERS CHECK):")
response = run_curl(f"curl -s -i {BASE_URL}/health")
print(response)

print("\nComplete security headers that would pass the test:")
print("X-Content-Type-Options: nosniff")
print("X-Frame-Options: DENY")
print("X-XSS-Protection: 1; mode=block")
print("Content-Security-Policy: default-src 'self';")

