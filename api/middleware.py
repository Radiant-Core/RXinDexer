# Comprehensive middleware for security and rate limiting
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
import time
from typing import Dict, Tuple
from collections import defaultdict, deque
import threading

class RateLimiter:
    """
    Token bucket rate limiter with different limits for different endpoint types.
    Thread-safe implementation for production use.
    """
    
    def __init__(self):
        self.clients = defaultdict(lambda: {
            'analytics': deque(),      # 100/hour for analytics endpoints
            'search': deque(),         # 1000/hour for search endpoints  
            'basic': deque(),          # 5000/hour for basic queries
            'wallet': deque()          # 10000/hour for wallet endpoints
        })
        self.lock = threading.Lock()
        
        # Rate limits (requests per hour)
        self.limits = {
            'analytics': 100,
            'search': 1000, 
            'basic': 5000,
            'wallet': 10000
        }
        
        # Time windows (in seconds)
        self.windows = {
            'analytics': 3600,  # 1 hour
            'search': 3600,     # 1 hour
            'basic': 3600,      # 1 hour  
            'wallet': 3600      # 1 hour
        }
    
    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request headers, handling proxies."""
        # Check for forwarded headers first (load balancer/proxy)
        forwarded_for = request.headers.get('X-Forwarded-For')
        if forwarded_for:
            return forwarded_for.split(',')[0].strip()
        
        real_ip = request.headers.get('X-Real-IP')
        if real_ip:
            return real_ip
        
        # Fallback to direct client IP
        return request.client.host if request.client else 'unknown'
    
    def _classify_endpoint(self, path: str) -> str:
        """Classify endpoint type for appropriate rate limiting."""
        if any(endpoint in path for endpoint in [
            '/top-wallets', '/top-glyph-users', '/top-glyph-containers',
            '/rxd-holder-count', '/token-holder-count'
        ]):
            return 'analytics'
        
        elif any(endpoint in path for endpoint in [
            '/search-nfts', '/search-glyph-tokens', '/recent-transactions',
            '/recent-blocks', '/recent-tokens', '/recent-nfts'
        ]):
            return 'search'
        
        elif any(endpoint in path for endpoint in [
            '/address/', '/wallet/', '/transaction/', '/utxos'
        ]):
            return 'wallet'
        
        else:
            return 'basic'
    
    def _cleanup_old_requests(self, client_requests: deque, window: int):
        """Remove requests outside the time window."""
        current_time = time.time()
        while client_requests and current_time - client_requests[0] > window:
            client_requests.popleft()
    
    def is_allowed(self, request: Request) -> Tuple[bool, Dict]:
        """Check if request is allowed based on rate limits."""
        client_ip = self._get_client_ip(request)
        endpoint_type = self._classify_endpoint(str(request.url.path))
        current_time = time.time()
        
        with self.lock:
            client_data = self.clients[client_ip]
            client_requests = client_data[endpoint_type]
            
            # Clean up old requests
            self._cleanup_old_requests(client_requests, self.windows[endpoint_type])
            
            # Check if limit exceeded
            if len(client_requests) >= self.limits[endpoint_type]:
                return False, {
                    'limit': self.limits[endpoint_type],
                    'window': self.windows[endpoint_type],
                    'endpoint_type': endpoint_type,
                    'current_count': len(client_requests)
                }
            
            # Add current request
            client_requests.append(current_time)
            
            return True, {
                'limit': self.limits[endpoint_type],
                'window': self.windows[endpoint_type], 
                'endpoint_type': endpoint_type,
                'current_count': len(client_requests)
            }

# Global rate limiter instance
rate_limiter = RateLimiter()

async def rate_limit_middleware(request: Request, call_next):
    """
    Rate limiting middleware to protect API from abuse.
    Different limits for different endpoint types.
    """
    # Skip rate limiting for health checks and internal endpoints
    if request.url.path in ['/health', '/db-health', '/status', '/docs', '/redoc', '/openapi.json']:
        response = await call_next(request)
        return response
    
    # Check rate limit
    is_allowed, limit_info = rate_limiter.is_allowed(request)
    
    if not is_allowed:
        return JSONResponse(
            status_code=429,
            content={
                'error': 'Rate limit exceeded',
                'message': f"Too many requests for {limit_info['endpoint_type']} endpoints",
                'limit': limit_info['limit'],
                'window_seconds': limit_info['window'],
                'current_count': limit_info['current_count'],
                'retry_after': limit_info['window']
            },
            headers={
                'X-RateLimit-Limit': str(limit_info['limit']),
                'X-RateLimit-Remaining': str(max(0, limit_info['limit'] - limit_info['current_count'])),
                'X-RateLimit-Reset': str(int(time.time() + limit_info['window'])),
                'Retry-After': str(limit_info['window'])
            }
        )
    
    # Process request
    response = await call_next(request)
    
    # Add rate limit headers to successful responses
    response.headers['X-RateLimit-Limit'] = str(limit_info['limit'])
    response.headers['X-RateLimit-Remaining'] = str(max(0, limit_info['limit'] - limit_info['current_count']))
    response.headers['X-RateLimit-Reset'] = str(int(time.time() + limit_info['window']))
    
    return response

# Security headers middleware
async def security_headers_middleware(request: Request, call_next):
    """
    Add security headers to all responses.
    """
    response = await call_next(request)
    
    # Add security headers
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Content-Security-Policy'] = "default-src 'self'"
    
    return response
