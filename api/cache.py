"""
Hybrid cache with Redis support and in-memory fallback.
Automatically uses Redis if REDIS_URL is configured, otherwise falls back to TTLCache.
"""
import time
import os
import json
import logging
from typing import Any, Optional, Callable
from functools import wraps
import threading

logger = logging.getLogger(__name__)

# Import metrics functions (lazy import to avoid circular imports)
_metrics_imported = False
_record_cache_hit = None
_record_cache_miss = None

def _ensure_metrics():
    global _metrics_imported, _record_cache_hit, _record_cache_miss
    if not _metrics_imported:
        try:
            from config.metrics import record_cache_hit, record_cache_miss
            _record_cache_hit = record_cache_hit
            _record_cache_miss = record_cache_miss
        except ImportError:
            _record_cache_hit = lambda x: None
            _record_cache_miss = lambda x: None
        _metrics_imported = True


class TTLCache:
    """Thread-safe in-memory TTL cache for API responses."""
    
    def __init__(self):
        self._cache: dict[str, tuple[Any, float]] = {}
        self._lock = threading.RLock()
    
    def get(self, key: str) -> Optional[Any]:
        """Get value if exists and not expired."""
        _ensure_metrics()
        with self._lock:
            if key in self._cache:
                value, expires_at = self._cache[key]
                if time.time() < expires_at:
                    if _record_cache_hit:
                        _record_cache_hit("memory")
                    return value
                else:
                    del self._cache[key]
            if _record_cache_miss:
                _record_cache_miss("memory")
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
    
    @property
    def backend(self) -> str:
        return "memory"
    
    @property
    def size(self) -> int:
        """Return number of non-expired entries."""
        with self._lock:
            now = time.time()
            return sum(1 for _, (_, exp) in self._cache.items() if now < exp)


class RedisCache:
    """Redis-backed cache with automatic serialization."""
    
    def __init__(self, redis_url: str):
        import redis
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._prefix = "rxindexer:"
        # Test connection
        self._redis.ping()
        logger.info("Redis cache connected successfully")
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from Redis."""
        _ensure_metrics()
        try:
            value = self._redis.get(self._prefix + key)
            if value:
                if _record_cache_hit:
                    _record_cache_hit("redis")
                return json.loads(value)
            if _record_cache_miss:
                _record_cache_miss("redis")
            return None
        except Exception as e:
            logger.warning(f"Redis get error: {e}")
            return None
    
    def set(self, key: str, value: Any, ttl_seconds: int = 60):
        """Set value in Redis with TTL."""
        try:
            self._redis.setex(
                self._prefix + key,
                ttl_seconds,
                json.dumps(value, default=str)
            )
        except Exception as e:
            logger.warning(f"Redis set error: {e}")
    
    def delete(self, key: str):
        """Delete a key from Redis."""
        try:
            self._redis.delete(self._prefix + key)
        except Exception as e:
            logger.warning(f"Redis delete error: {e}")
    
    def clear(self):
        """Clear all cache keys with our prefix."""
        try:
            keys = self._redis.keys(self._prefix + "*")
            if keys:
                self._redis.delete(*keys)
        except Exception as e:
            logger.warning(f"Redis clear error: {e}")
    
    def cleanup(self):
        """Redis handles TTL automatically, no-op."""
        pass
    
    @property
    def backend(self) -> str:
        return "redis"
    
    @property
    def size(self) -> int:
        """Return approximate number of keys."""
        try:
            keys = self._redis.keys(self._prefix + "*")
            return len(keys) if keys else 0
        except Exception:
            return 0


def _create_cache():
    """Create cache instance based on environment configuration."""
    redis_url = os.getenv("REDIS_URL")
    
    if redis_url:
        try:
            return RedisCache(redis_url)
        except Exception as e:
            logger.warning(f"Failed to connect to Redis ({e}), falling back to in-memory cache")
    
    logger.info("Using in-memory TTL cache")
    return TTLCache()


# Global cache instance - automatically selects Redis or in-memory
cache = _create_cache()


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
