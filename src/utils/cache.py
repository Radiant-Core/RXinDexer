# /Users/radiant/Desktop/RXinDexer/src/utils/cache.py
# This file implements caching utilities for API responses.
# It provides Redis-based caching to improve performance of frequently accessed endpoints.

import os
import json
import logging
from typing import Dict, Any, Optional, Union, Callable
from functools import wraps
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Cache configuration from environment variables
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
DEV_MODE = os.getenv("DEV_MODE", "true").lower() == "true"
ENABLE_CACHE = os.getenv("ENABLE_CACHE", "true").lower() == "true"
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "300"))

# Redis connection settings
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

logger = logging.getLogger(__name__)

# Initialize Redis client (lazy connection)
_redis_client = None
_memory_cache = {}  # Simple in-memory cache for development mode

def _get_redis():
    """
    Get or create Redis client with connection pooling.
    Uses lazy initialization to avoid connecting unless needed.
    
    Returns:
        Redis client instance or None if connection fails
    """
    global _redis_client
    
    # Skip Redis if caching is disabled or in development mode with no Redis
    if not ENABLE_CACHE:
        logger.debug("Caching is disabled via ENABLE_CACHE setting")
        return None
        
    if _redis_client is None:
        try:
            # Import Redis here to allow the app to run without redis installed
            import redis
            
            _redis_client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=REDIS_DB,
                password=REDIS_PASSWORD,
                socket_timeout=2,
                socket_connect_timeout=2,
                retry_on_timeout=True,
                decode_responses=False  # Keep as bytes for proper JSON handling
            )
            logger.info(f"Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
        except redis.RedisError as e:
            logger.warning(f"Failed to connect to Redis: {str(e)}")
            # Return None, will cause cache functions to be no-ops
    
    return _redis_client

def get_cached(key: str) -> Optional[Dict[str, Any]]:
    """
    Get cached data for a key.
    
    Args:
        key: Cache key
        
    Returns:
        Cached data or None if not found or cache is unavailable
    """
    # Skip if caching is disabled
    if not ENABLE_CACHE:
        return None
        
    # First check in-memory cache if in development mode
    global _memory_cache
    full_key = f"rxindexer:{key}"
    
    if DEV_MODE and full_key in _memory_cache:
        # Check if item is expired in memory cache
        item = _memory_cache[full_key]
        if time.time() < item['expires']:
            logger.debug(f"Memory cache hit for {key}")
            return item['data']
        else:
            # Remove expired item
            del _memory_cache[full_key]
    
    # Then try Redis if available
    r = _get_redis()
    if not r:
        return None
    
    try:
        data = r.get(full_key)
        if data:
            parsed_data = json.loads(data)
            # Also cache in memory for faster access next time
            if DEV_MODE:
                _memory_cache[full_key] = {
                    'data': parsed_data,
                    'expires': time.time() + CACHE_TTL
                }
            return parsed_data
    except Exception as e:  # Catch all exceptions, not just Redis/JSON
        logger.warning(f"Failed to get cached data for {key}: {str(e)}")
    
    return None

def cache_result(key: str, data: Dict[str, Any], ttl: int = CACHE_TTL) -> bool:
    """
    Cache data with expiration.
    
    Args:
        key: Cache key
        data: Data to cache
        ttl: Time-to-live in seconds (defaults to CACHE_TTL from environment)
        
    Returns:
        True if cached successfully, False otherwise
    """
    # Skip if caching is disabled
    if not ENABLE_CACHE:
        return False
    
    full_key = f"rxindexer:{key}"
    success = False
    
    # Always cache in memory in development mode
    if DEV_MODE:
        try:
            global _memory_cache
            _memory_cache[full_key] = {
                'data': data,
                'expires': time.time() + ttl
            }
            success = True
            logger.debug(f"Cached {key} in memory cache")
        except Exception as e:
            logger.warning(f"Failed to cache in memory for {key}: {str(e)}")
    
    # Then try Redis if available
    r = _get_redis()
    if not r:
        return success  # Return memory cache result if Redis unavailable
    
    try:
        r.setex(
            full_key,
            ttl,
            json.dumps(data)
        )
        return True
    except Exception as e:  # Catch all exceptions, not just Redis/TypeError
        logger.warning(f"Failed to cache result for {key}: {str(e)}")
        return success  # Return memory cache result if Redis failed

def invalidate_cache(key_pattern: str) -> int:
    """
    Invalidate cache keys matching a pattern.
    
    Args:
        key_pattern: Pattern to match keys (e.g., "balance:*")
        
    Returns:
        Number of keys deleted
    """
    count = 0
    
    # Skip if caching is disabled
    if not ENABLE_CACHE:
        return 0
    
    # Clear matching keys from memory cache in development mode
    if DEV_MODE:
        import re
        global _memory_cache
        pattern_regex = re.compile(f"rxindexer:{key_pattern.replace('*', '.*')}")
        
        # Find matching keys
        keys_to_delete = [k for k in _memory_cache.keys() if pattern_regex.match(k)]
        
        # Delete matching keys
        for k in keys_to_delete:
            del _memory_cache[k]
            count += 1
        
        if count > 0:
            logger.debug(f"Invalidated {count} keys from memory cache for pattern {key_pattern}")
    
    # Then try Redis if available
    r = _get_redis()
    if not r:
        return count
    
    try:
        pattern = f"rxindexer:{key_pattern}"
        redis_keys = r.keys(pattern)
        if redis_keys:
            deleted = r.delete(*redis_keys)
            logger.debug(f"Invalidated {deleted} keys from Redis for pattern {key_pattern}")
            return count + deleted
        return count
    except Exception as e:
        logger.warning(f"Failed to invalidate Redis cache for {key_pattern}: {str(e)}")
        return count

def flush_all_cache() -> bool:
    """
    Flush all caches (both Redis and in-memory).
    
    Returns:
        True if flushed successfully, False otherwise
    """
    success = False
    
    # Skip if caching is disabled
    if not ENABLE_CACHE:
        return False
    
    # Clear memory cache in development mode
    if DEV_MODE:
        global _memory_cache
        try:
            _memory_cache.clear()
            success = True
            logger.debug("Cleared in-memory cache")
        except Exception as e:
            logger.warning(f"Failed to clear memory cache: {str(e)}")
    
    # Then try Redis if available
    r = _get_redis()
    if not r:
        return success
    
    try:
        r.flushdb()
        logger.info("Flushed Redis cache")
        return True
    except Exception as e:
        logger.warning(f"Failed to flush Redis cache: {str(e)}")
        return success


def cache_decorator(ttl: int = CACHE_TTL):
    """
    Decorator to cache function results.
    
    Args:
        ttl: Cache TTL in seconds
        
    Returns:
        Decorated function
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Skip caching if disabled
            if not ENABLE_CACHE:
                return await func(*args, **kwargs)
            
            # Generate cache key from function name and arguments
            key_parts = [func.__name__]
            
            # Add positional arguments to cache key
            for arg in args:
                if hasattr(arg, '__dict__'):
                    # Skip Session objects or other complex objects
                    continue
                key_parts.append(str(arg))
            
            # Add keyword arguments to cache key
            for k, v in sorted(kwargs.items()):
                if k == 'db' or (hasattr(v, '__dict__') and not isinstance(v, (str, int, float, bool))):
                    # Skip database session or complex objects
                    continue
                key_parts.append(f"{k}:{v}")
            
            cache_key = ":".join(key_parts)
            
            # Try to get from cache first
            cached_result = get_cached(cache_key)
            if cached_result is not None:
                return cached_result
            
            # Call the function if not cached
            result = await func(*args, **kwargs)
            
            # Cache the result
            if result is not None:
                cache_result(cache_key, result, ttl)
                
            return result
        return wrapper
    return decorator
