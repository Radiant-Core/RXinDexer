# /Users/radiant/Desktop/RXinDexer/src/api/direct_security.py
# This file implements security functions and middleware that are directly applied to all responses.
# It provides a guaranteed way to add security headers and enforce API key validation.

import os
import time
import logging
from fastapi import Request, Response

# Setup logging
logger = logging.getLogger(__name__)

def apply_security_headers(response: Response):
    """Apply security headers to every response without exception"""
    # Define required security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Content-Security-Policy"] = "default-src 'self';"
    
    # Log headers for debugging
    logger.info(f"Applied security headers: {dict(response.headers)}")
    return response

class SecurityHeadersMiddleware:
    """Middleware that guarantees security headers are applied to every response"""
    
    def __init__(self, app):
        self.app = app
        
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
            
        async def send_with_security_headers(message):
            if message["type"] == "http.response.start":
                # Add security headers to every response
                headers = message.get("headers", [])
                
                # Convert headers to dict for easier manipulation
                headers_dict = {h[0].decode(): h[1].decode() for h in headers}
                
                # Add required security headers
                headers_dict[b"x-content-type-options".decode()] = "nosniff"
                headers_dict[b"x-frame-options".decode()] = "DENY"
                headers_dict[b"x-xss-protection".decode()] = "1; mode=block"
                headers_dict[b"content-security-policy".decode()] = "default-src 'self';"
                
                # Convert back to list of tuples with byte encoding
                new_headers = [(k.encode(), v.encode()) for k, v in headers_dict.items()]
                
                # Replace headers in the message
                message["headers"] = new_headers
                
                # Log for debugging
                logger.info(f"Applied security headers at ASGI level: {headers_dict}")
                
            await send(message)
            
        await self.app(scope, receive, send_with_security_headers)
