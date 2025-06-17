#!/usr/bin/env python3
# /Users/radiant/Desktop/RXinDexer/simple_security_test.py
# A simplified test script to verify RXinDexer API security features

import subprocess
import json
import time

# Base URL for the API
BASE_URL = "http://localhost:8000"
# API key for testing - using exact key from container environment
API_KEY = "test-api-key-1"

def run_curl_command(command):
    """Run a curl command and return the output"""
    try:
        result = subprocess.run(command, capture_output=True, text=True, shell=True)
        return result.stdout, result.returncode
    except Exception as e:
        return f"Error: {str(e)}", 1

def test_health_endpoint():
    """Test health endpoint (should be accessible without API key)"""
    print("Testing health endpoint...")
    cmd = f"curl -s {BASE_URL}/health"
    output, exit_code = run_curl_command(cmd)
    
    if exit_code == 0:
        try:
            data = json.loads(output)
            if data.get("status") == "healthy":
                print("✅ Health endpoint accessible")
                print(f"   Status: {data.get('status')}")
                return True
            else:
                print("❌ Health endpoint returned unexpected response")
                print(f"   Response: {output}")
                return False
        except json.JSONDecodeError:
            print("❌ Health endpoint returned invalid JSON")
            print(f"   Response: {output}")
            return False
    else:
        print("❌ Health endpoint request failed")
        print(f"   Error: {output}")
        return False

def test_protected_endpoint_without_key():
    """Test protected endpoint without API key (should fail with 401 or 403)"""
    print("\nTesting protected endpoint without API key...")
    # Use a specific token endpoint that should be protected
    cmd = f"curl -s -i {BASE_URL}/api/v1/tokens/"
    output, exit_code = run_curl_command(cmd)
    
    # Save the output for debugging
    with open('api_key_test_debug.txt', 'w') as f:
        f.write(output)
    
    # Parse the status code from the output
    status_code = None
    for line in output.split('\n'):
        if line.startswith('HTTP/'):
            try:
                status_code = int(line.split()[1])
                break
            except (IndexError, ValueError):
                pass
    
    if status_code in [401, 403]:
        print(f"✅ Protected endpoint correctly rejected request without API key (Status {status_code})")
        return True
    else:
        print(f"❌ Protected endpoint returned unexpected status code: {status_code or 'unknown'}")
        print(f"   First 150 characters of response: {output[:150]}...")
        return False

def test_protected_endpoint_with_key():
    """Test protected endpoint with valid API key (should succeed)"""
    print("\nTesting protected endpoint with valid API key...")
    cmd = f"curl -s -H 'X-API-Key: {API_KEY}' {BASE_URL}/api/v1/tokens/"
    output, exit_code = run_curl_command(cmd)
    
    # Check for status code in the response
    status_cmd = f"curl -s -o /dev/null -w '%{{http_code}}' -H 'X-API-Key: {API_KEY}' {BASE_URL}/api/v1/tokens/"
    status, _ = run_curl_command(status_cmd)
    
    if status.strip() == "200":
        print("✅ Protected endpoint accessible with API key")
        return True
    else:
        print("❌ Protected endpoint returned unexpected response")
        print(f"   Response: {output}")
        return False

def test_security_headers():
    """Test security headers in API response"""
    print("\nTesting security headers...")
    
    # Use direct curl command to get full HTTP response with headers
    cmd = f"curl -s -i http://localhost:8000/health"
    output, exit_code = run_curl_command(cmd)
    
    # Save the full response for debugging
    with open('headers_debug.txt', 'w') as f:
        f.write(output)
    
    print("\nHeaders received:")
    print(output)
    
    # Define expected security headers and their values (case-insensitive keys)
    expected_headers = {
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "x-xss-protection": "1; mode=block",
        "content-security-policy": "default-src 'self';"
    }
    
    # Parse the headers from the response
    response_headers = {}
    in_headers = True
    
    for line in output.split('\n'):
        # Stop when we hit an empty line (end of headers)
        if in_headers and not line.strip():
            in_headers = False
            continue
            
        # Only process header lines
        if in_headers and ':' in line:
            parts = line.split(':', 1)
            if len(parts) == 2:
                header_name = parts[0].strip().lower()
                header_value = parts[1].strip()
                response_headers[header_name] = header_value
    
    # Check for each required security header
    headers_present = []
    for header, expected_value in expected_headers.items():
        if header in response_headers:
            found_value = response_headers[header]
            print(f"✅ {header} present: {header}: {found_value}")
            headers_present.append(header)
        else:
            print(f"❌ {header.upper()} missing")
    
    if len(headers_present) == len(expected_headers):
        print("✅ All security headers are present")
        return True
    else:
        print(f"❌ {len(expected_headers) - len(headers_present)} security headers missing")
        return False

def test_rate_limiting(num_requests=30):
    """Test rate limiting by making multiple requests in quick succession"""
    print(f"\nTesting rate limiting with {num_requests} requests...")
    
    success_count = 0
    failure_count = 0
    rate_limited = False
    
    # Make multiple requests rapidly with no delay - should hit rate limit
    print("Sending rapid requests from same IP address...")
    
    # Build a test string with multiple curl commands to run concurrently
    test_cmds = []
    for i in range(num_requests):
        test_cmds.append(f"curl -s -o /dev/null -w '%{{http_code}}\n' {BASE_URL}/health")
    
    # Run all requests in a tight loop - should definitely hit the rate limit
    cmd = " && ".join(test_cmds)
    output, exit_code = run_curl_command(cmd)
    
    # Check if any of the responses were rate limited (429)
    responses = output.strip().split('\n')
    for i, resp in enumerate(responses):
        if resp == "429":
            print(f"✅ Rate limit triggered at request #{i+1}")
            rate_limited = True
            break
    
    if not rate_limited:
        print(f"⚠️ Made {len(responses)} requests without hitting rate limit")
        print("First 10 response codes: " + ", ".join(responses[:10]))
    
    return rate_limited

def run_all_tests():
    """Run all security tests"""
    print(f"Testing RXinDexer API at {BASE_URL}\n")
    
    # Store test results
    results = {
        "health_endpoint": test_health_endpoint(),
        "protected_endpoint_without_key": test_protected_endpoint_without_key(),
        "protected_endpoint_with_key": test_protected_endpoint_with_key(),
        "security_headers": test_security_headers(),
        "rate_limiting": test_rate_limiting()
    }
    
    # Summarize results
    print("\n" + "=" * 50)
    print("SECURITY TEST RESULTS")
    print("=" * 50)
    
    passed = sum(1 for r in results.values() if r)
    failed = sum(1 for r in results.values() if not r)
    
    print(f"Tests passed: {passed}")
    print(f"Tests failed: {failed}")
    
    if failed == 0:
        print("\n✅ All security tests passed!")
    else:
        print("\n❌ Some security tests failed")

if __name__ == "__main__":
    run_all_tests()
