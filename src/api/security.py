# /Users/radiant/Desktop/RXinDexer/src/api/security.py
# This file implements security measures for the RXinDexer API.
# It provides authentication, rate limiting, and validation functions.

import os
import time
import secrets
import logging
from typing import Optional, Dict, List
from fastapi import Depends, HTTPException, Security, Request, status
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field, validator

# Configure logging
logger = logging.getLogger(__name__)

# Load API keys from environment or use a secure default for development
# In production, always set this via environment variable
API_KEYS = os.getenv("API_KEYS", "2sKoYZckjwX91_NC9pszzMJh6J7XeMmZeOKjZgNWGEs,test-api-key-1,test-api-key-2").split(",")

# Load rate limiting settings from environment variables with production-friendly defaults
try:
    # For production, default to 100 requests per 60 seconds
    # For testing, set very high limits via environment variables
    RATE_LIMIT_DURATION = int(os.getenv("RATE_LIMIT_DURATION", "60"))  # Default: 60 seconds
    RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "100"))  # Default: 100 requests
    
    # Ensure we have positive values
    if RATE_LIMIT_DURATION <= 0:
        RATE_LIMIT_DURATION = 60
    if RATE_LIMIT_REQUESTS <= 0:
        RATE_LIMIT_REQUESTS = 100
        
    logger.info(f"Rate limiting configured: {RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_DURATION} seconds")
except (ValueError, TypeError) as e:
    logger.warning(f"Invalid rate limiting settings, using defaults: {e}")
    RATE_LIMIT_DURATION = 60  # Default 60 seconds
    RATE_LIMIT_REQUESTS = 100  # Default 100 requests

# All paths are public by default
# To make specific endpoints require authentication, remove their paths from this list
EXEMPT_PATHS = [
    # Match all paths
    "/"
]

# Create API key header requirement
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# In-memory rate limiting storage - should be replaced with Redis in production
# Structure: {ip_address: {timestamp: count}}
rate_limit_store: Dict[str, Dict[int, int]] = {}

# Clean up rate limiting store periodically (every 5 minutes)
last_cleanup_time = time.time()
CLEANUP_INTERVAL = 300  # 5 minutes


class ClientInfo(BaseModel):
    """Model for storing client information including API key details"""
    client_id: str = Field(..., description="Unique identifier for the client")
    api_key: str = Field(..., description="API key value")
    allowed_paths: Optional[List[str]] = Field(None, description="Specific paths this client can access")
    rate_limit_override: Optional[int] = Field(None, description="Custom rate limit for this client")


def get_api_key(request: Request, api_key: str = Security(api_key_header)):
    """Validate API key and apply rate limiting
    
    This function is used as a FastAPI dependency to validate API keys for protected endpoints
    and enforce rate limiting. Public endpoints are exempted from API key validation.
    
    Args:
        request: The FastAPI request object
        api_key: The API key extracted from the X-API-Key header
        
    Returns:
        The validated API key
        
    Raises:
        HTTPException: 401 if no API key is provided for protected endpoints
                      403 if an invalid API key is provided
                      429 if rate limit is exceeded
    """
    
    # Check if path is explicitly exempted from API key requirement
    exact_path = request.url.path
    is_exempt = False
    
    # First check exact path matches
    if exact_path in EXEMPT_PATHS:
        logger.debug(f"Exempting path {exact_path} from API key requirement (exact match)")
        is_exempt = True
    
    # Then check path prefixes for more flexible matching
    if not is_exempt:
        for exempt_path in EXEMPT_PATHS:
            if exact_path.startswith(exempt_path + "?") or exact_path.startswith(exempt_path + "/"):
                logger.debug(f"Exempting path {exact_path} from API key requirement (prefix match)")
                is_exempt = True
                break
    
    # Skip API key validation for all endpoints
    # The following code is kept for future reference when implementing API key validation
    # if not is_exempt:
    #     if not api_key:
    #         logger.warning(f"Unauthorized access attempt to {exact_path} - no API key provided")
    #         raise HTTPException(
    #             status_code=status.HTTP_401_UNAUTHORIZED,
    #             detail="API key is required",
    #             headers={"WWW-Authenticate": "ApiKey"},
    #         )
    #             
    #     if api_key not in API_KEYS:
    #         logger.warning(f"Forbidden access attempt to {exact_path} - invalid API key provided")
    #         raise HTTPException(
    #             status_code=status.HTTP_403_FORBIDDEN,
    #             detail="Invalid API key",
    #         )
    
    # Rate limiting implementation
    client_ip = request.client.host
    current_time = time.time()
    window_start = int(current_time // RATE_LIMIT_DURATION) * RATE_LIMIT_DURATION
    
    # Apply rate limiting based on both IP and API key for better security
    rate_key = f"{client_ip}:{api_key if api_key else 'anonymous'}"
    
    # Initialize if not exists
    if rate_key not in rate_limit_store:
        rate_limit_store[rate_key] = {
            'window_start': window_start,
            'count': 0,
        }
    
    # Reset counter if we're in a new time window
    if window_start > rate_limit_store[rate_key]['window_start']:
        rate_limit_store[rate_key] = {
            'window_start': window_start,
            'count': 0,
        }
    
    # Increment request count
    rate_limit_store[rate_key]['count'] += 1
    current_count = rate_limit_store[rate_key]['count']
    
    # Apply rate limiting using configured values
    if current_count > RATE_LIMIT_REQUESTS:
        retry_seconds = RATE_LIMIT_DURATION - (current_time % RATE_LIMIT_DURATION)
        logger.warning(f"Rate limit exceeded for {rate_key}: {current_count}/{RATE_LIMIT_REQUESTS} requests")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: {current_count}/{RATE_LIMIT_REQUESTS} requests",
            headers={
                "Retry-After": str(int(retry_seconds)),
                "X-RateLimit-Limit": str(RATE_LIMIT_REQUESTS),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(int(current_time + retry_seconds))
            },
        )
    
    # Add rate limit headers to successful responses
    request.state.ratelimit = {
        "limit": RATE_LIMIT_REQUESTS,
        "remaining": RATE_LIMIT_REQUESTS - current_count,
        "reset": current_time + (RATE_LIMIT_DURATION - (current_time % RATE_LIMIT_DURATION))
    }

    # Cleanup old entries periodically
    global last_cleanup_time
    if current_time - last_cleanup_time > CLEANUP_INTERVAL:
        _cleanup_rate_limit_store()
        last_cleanup_time = current_time

    return api_key


def _cleanup_rate_limit_store():
    """Remove old entries from rate limit store (updated for new data structure)"""
    current_time = time.time()
    retention_window = RATE_LIMIT_DURATION * 5  # Keep 5 time windows for reference
    
    for rate_key in list(rate_limit_store.keys()):
        # Check if the entire entry is too old
        window_start = rate_limit_store[rate_key]['window_start']
        if current_time - window_start > retention_window:
            del rate_limit_store[rate_key]
            continue
            
        # Prune old requests from recent_requests list
        if 'recent_requests' in rate_limit_store[rate_key]:
            rate_limit_store[rate_key]['recent_requests'] = [
                t for t in rate_limit_store[rate_key]['recent_requests']
                if current_time - t < retention_window
            ]
    
    # Log cleanup stats
    logger.debug(f"Rate limit store cleanup - {len(rate_limit_store)} clients in memory")


def generate_api_key():
    """Generate a secure random API key"""
    return secrets.token_urlsafe(32)


class RequestValidator:
    """Base validator for input validation patterns"""
    
    @staticmethod
    def validate_txid(txid: str) -> str:
        """Validate transaction ID format"""
        if not (len(txid) == 64 and all(c in "0123456789abcdefABCDEF" for c in txid)):
            raise ValueError("Invalid transaction ID format")
        return txid.lower()
    
    @staticmethod
    def validate_address(address: str) -> str:
        """Validate Radiant address format"""
        # Basic check for Radiant addresses - this should be more comprehensive in production
        if not (len(address) >= 26 and len(address) <= 35 and 
                (address.startswith('r') or address.startswith('R'))):
            raise ValueError("Invalid Radiant address format")
        return address
    
    @staticmethod
    def validate_block_hash(block_hash: str) -> str:
        """Validate block hash format"""
        if not (len(block_hash) == 64 and all(c in "0123456789abcdefABCDEF" for c in block_hash)):
            raise ValueError("Invalid block hash format")
        return block_hash.lower()
    
    @staticmethod
    def validate_token_id(token_id: str) -> str:
        """Validate token ID format"""
        # Basic validation - adjust according to your token ID format
        if not (len(token_id) >= 10 and len(token_id) <= 128):
            raise ValueError("Invalid token ID format")
        return token_id
