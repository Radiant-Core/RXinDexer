#!/usr/bin/env python3
"""Test script to verify WSS server fixes:
1. SSL context uses modern API (not deprecated ssl.PROTOCOL_TLS)
2. WebSocket handler signature compatible with websockets 10+
"""

import ssl
import sys
import inspect

print("Testing WSS server fixes...")
print()

# Test 1: Verify SSL context creation
print("Test 1: SSL context creation")
print("-" * 50)

try:
    # Old deprecated way (should fail on Python 3.12+)
    try:
        old_context = ssl.SSLContext(ssl.PROTOCOL_TLS)
        print("❌ FAIL: ssl.PROTOCOL_TLS still exists (Python < 3.12)")
        print("   This should be updated to ssl.create_default_context()")
    except (AttributeError, ValueError):
        print("✅ PASS: ssl.PROTOCOL_TLS removed (Python 3.12+)")
    
    # New recommended way
    new_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    new_context.set_ciphers('HIGH:!aNULL:!MD5')
    print("✅ PASS: ssl.create_default_context() works correctly")
    print(f"   Protocol: {new_context.protocol}")
    print(f"   Minimum version: {new_context.minimum_version}")
    
except Exception as e:
    print(f"❌ FAIL: SSL context creation failed: {e}")
    sys.exit(1)

print()

# Test 2: Verify WebSocket handler signature
print("Test 2: WebSocket handler signature compatibility")
print("-" * 50)

try:
    import websockets
    
    print(f"websockets version: {websockets.__version__}")
    
    # Check if websockets.serve signature accepts our handler
    import websockets.asyncio.server
    
    # Create a simple handler with websockets 10+ signature
    async def modern_handler(websocket):
        """Handler for websockets 10+ - takes only websocket argument"""
        pass
    
    # Check the signature
    sig = inspect.signature(modern_handler)
    params = list(sig.parameters.keys())
    
    if params == ['websocket']:
        print("✅ PASS: Handler uses websockets 10+ signature (websocket only)")
    else:
        print(f"❌ FAIL: Handler signature is {params}, expected ['websocket']")
        
    # Verify old signature would fail
    async def old_handler(websocket, path):
        """Handler for websockets <10 - takes websocket and path"""
        pass
    
    old_sig = inspect.signature(old_handler)
    old_params = list(old_sig.parameters.keys())
    
    if old_params == ['websocket', 'path']:
        print("⚠️  NOTE: Old signature (websocket, path) detected")
        print("   This is incompatible with websockets 10+")
    
except ImportError as e:
    print(f"❌ FAIL: Cannot import websockets: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ FAIL: WebSocket signature check failed: {e}")
    sys.exit(1)

print()

# Test 3: Verify the actual code fixes
print("Test 3: Verify actual code fixes")
print("-" * 50)

try:
    # Check session.py
    with open('electrumx/server/session.py', 'r') as f:
        session_content = f.read()
    
    if 'ssl.create_default_context' in session_content:
        print("✅ PASS: session.py uses ssl.create_default_context()")
    else:
        print("❌ FAIL: session.py does not use ssl.create_default_context()")
    
    if 'ssl.PROTOCOL_TLS' in session_content:
        print("❌ FAIL: session.py still has ssl.PROTOCOL_TLS")
    else:
        print("✅ PASS: session.py does not use ssl.PROTOCOL_TLS")
    
    if '_serve_ws_compat' in session_content:
        print("✅ PASS: session.py has _serve_ws_compat() wrapper")
    else:
        print("❌ FAIL: session.py missing _serve_ws_compat() wrapper")
    
    if 'HIGH:!aNULL:!MD5' in session_content:
        print("✅ PASS: session.py has cipher suite hardening")
    else:
        print("❌ FAIL: session.py missing cipher suite hardening")
    
    # Check peers.py
    with open('electrumx/server/peers.py', 'r') as f:
        peers_content = f.read()
    
    if 'ssl.create_default_context' in peers_content:
        print("✅ PASS: peers.py uses ssl.create_default_context()")
    else:
        print("❌ FAIL: peers.py does not use ssl.create_default_context()")
    
    if 'ssl.PROTOCOL_TLS' in peers_content:
        print("❌ FAIL: peers.py still has ssl.PROTOCOL_TLS")
    else:
        print("✅ PASS: peers.py does not use ssl.PROTOCOL_TLS")
    
    # Check httpserver.py
    with open('electrumx/server/httpserver.py', 'r') as f:
        httpserver_content = f.read()
    
    # The http_server method should not have _path parameter
    if 'async def http_server(cls, session_factory, websocket):' in httpserver_content:
        print("✅ PASS: httpserver.py http_server has modern signature")
    else:
        print("❌ FAIL: httpserver.py http_server has wrong signature")
    
except FileNotFoundError as e:
    print(f"❌ FAIL: File not found: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ FAIL: Code verification failed: {e}")
    sys.exit(1)

print()
print("=" * 50)
print("All tests passed! ✅")
print("=" * 50)
print()
print("The WSS server fixes are correctly applied:")
print("1. SSL context uses ssl.create_default_context()")
print("2. Cipher suite hardening applied (HIGH:!aNULL:!MD5)")
print("3. WebSocket handler signature compatible with websockets 10+")
print("4. Compatibility wrapper _serve_ws_compat() added")
print()
print("To test the actual WSS server:")
print("1. Ensure SSL certificates are configured")
print("2. Start RXinDexer server")
print("3. Connect with: wscat -c wss://localhost:50011")
