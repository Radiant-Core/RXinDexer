#!/usr/bin/env python3
# /Users/radiant/Desktop/RXinDexer/rate_limit_test.py
# This script is specifically designed to test rate limiting by making concurrent requests to an endpoint.
# It uses concurrent.futures to send multiple requests simultaneously to trigger rate limiting.

import time
import concurrent.futures
import requests
import sys
from colorama import Fore, Style, init

# Initialize colorama
init()

# Base URL for the API
BASE_URL = "http://localhost:8000"
# Endpoint to test
ENDPOINT = "/health"
# Full URL
URL = f"{BASE_URL}{ENDPOINT}"

def make_request(i):
    """Make a request to the API and return the status code"""
    try:
        response = requests.get(URL, timeout=2)
        return i, response.status_code
    except requests.RequestException as e:
        return i, f"Error: {str(e)}"

def test_rate_limiting(num_threads=20, num_requests_per_thread=5):
    """Test rate limiting by making concurrent requests"""
    print(f"{Fore.BLUE}Testing rate limiting with {num_threads} threads and {num_requests_per_thread} requests per thread ({num_threads * num_requests_per_thread} total requests){Style.RESET_ALL}")
    
    # Create a list to hold the futures
    results = []
    rate_limited = False
    
    # Use ThreadPoolExecutor to run requests concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        # Create a future for each request
        futures = [executor.submit(make_request, i) for i in range(num_threads * num_requests_per_thread)]
        
        # Process the results as they complete
        for future in concurrent.futures.as_completed(futures):
            try:
                i, status = future.result()
                results.append((i, status))
                if status == 429:
                    rate_limited = True
            except Exception as e:
                print(f"{Fore.RED}Error: {str(e)}{Style.RESET_ALL}")
    
    # Count the status codes
    status_counts = {}
    for _, status in results:
        if status not in status_counts:
            status_counts[status] = 0
        status_counts[status] += 1
        
    # Display results
    print(f"\n{Fore.YELLOW}Results:{Style.RESET_ALL}")
    for status, count in sorted(status_counts.items()):
        color = Fore.GREEN
        if status == 429:
            color = Fore.YELLOW
        elif isinstance(status, str) or status >= 400:
            color = Fore.RED
        print(f"{color}Status {status}: {count} requests{Style.RESET_ALL}")
    
    # Check if rate limiting was triggered
    if rate_limited:
        print(f"{Fore.GREEN}✅ Rate limiting successfully triggered!{Style.RESET_ALL}")
    else:
        print(f"{Fore.RED}❌ No rate limiting triggered despite sending {len(results)} requests{Style.RESET_ALL}")
    
    return rate_limited

if __name__ == "__main__":
    # Set the number of threads and requests per thread
    threads = 20
    requests_per_thread = 5
    
    if len(sys.argv) > 1:
        threads = int(sys.argv[1])
    if len(sys.argv) > 2:
        requests_per_thread = int(sys.argv[2])
    
    # Run the test
    test_rate_limiting(threads, requests_per_thread)
