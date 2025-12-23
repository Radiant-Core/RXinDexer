"""
Simple in-memory cache with TTL for API responses.
For production at scale, consider Redis instead.
"""
import time
from typing import Any, Optional, Callable
from functools import wraps
import threading

class TTLCache:
    """Thread-safe TTL cache for API responses."""
    
    def __init__(self):
        self._cache: dict[str, tuple[Any, float]] = {}
        self._lock = threading.RLock()
    
    def get(self, key: str) -> Optional[Any]:
        """Get value if exists and not expired."""
        with self._lock:
            if key in self._cache:
                value, expires_at = self._cache[key]
                if time.time() < expires_at:
                    return value
                else:
                    del self._cache[key]
            return None
    
    def set(self, key: str, value: Any, ttl_seconds: int = 60):
        """Set value with TTL."""
        with self._lock:
            self._cache[key] = (value, time.time() + ttl_seconds)
    
    def delete(self, key: str):
        """Delete a key."""
        with self._lock:
            self._cache.pop(key, None)
    
    def clear(self):
        """Clear all cache."""
        with self._lock:
            self._cache.clear()
    
    def cleanup(self):
        """Remove expired entries."""
        with self._lock:
            now = time.time()
            expired = [k for k, (_, exp) in self._cache.items() if now >= exp]
            for k in expired:
                del self._cache[k]


# Global cache instance
cache = TTLCache()


def cached(ttl_seconds: int = 60, key_prefix: str = ""):
    """
    Decorator to cache function results.
    
    Usage:
        @cached(ttl_seconds=30, key_prefix="blocks")
        def get_recent_blocks():
            ...
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Build cache key from function name and arguments
            cache_key = f"{key_prefix}:{func.__name__}:{str(args)}:{str(sorted(kwargs.items()))}"
            
            # Try to get from cache
            cached_value = cache.get(cache_key)
            if cached_value is not None:
                return cached_value
            
            # Execute function and cache result
            result = func(*args, **kwargs)
            cache.set(cache_key, result, ttl_seconds)
            return result
        
        return wrapper
    return decorator


# Cache TTL constants (in seconds)
CACHE_TTL_SHORT = 10      # 10 seconds - for rapidly changing data (recent blocks/txs)
CACHE_TTL_MEDIUM = 60     # 1 minute - for moderately changing data
CACHE_TTL_LONG = 300      # 5 minutes - for slow-changing data (rich list, stats)
CACHE_TTL_VERY_LONG = 900 # 15 minutes - for expensive queries (holder counts)
