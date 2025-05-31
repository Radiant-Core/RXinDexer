# /Users/radiant/Desktop/RXinDexer/tests/utils/test_cache.py
# This file tests the Redis caching utilities for the RXinDexer application.
# It verifies that cache operations like get, set, and invalidate work correctly.

import pytest
from unittest.mock import patch, MagicMock
import json

from src.utils.cache import get_cached, cache_result, invalidate_cache, flush_all_cache


class TestCache:
    """Tests for the cache utilities."""
    
    @patch('src.utils.cache._get_redis')
    def test_get_cached_hit(self, mock_get_redis):
        """Test retrieving a cached item that exists."""
        # Set up mock
        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps({"key": "value"}).encode()
        mock_get_redis.return_value = mock_redis
        
        # Call get_cached
        result = get_cached("test_key")
        
        # Verify result
        assert result == {"key": "value"}
        
        # Verify Redis was called correctly
        mock_redis.get.assert_called_once_with("rxindexer:test_key")
    
    @patch('src.utils.cache._get_redis')
    def test_get_cached_miss(self, mock_get_redis):
        """Test retrieving a cached item that doesn't exist."""
        # Set up mock
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_get_redis.return_value = mock_redis
        
        # Call get_cached
        result = get_cached("test_key")
        
        # Verify result
        assert result is None
        
        # Verify Redis was called correctly
        mock_redis.get.assert_called_once_with("rxindexer:test_key")
    
    @patch('src.utils.cache._get_redis')
    def test_get_cached_redis_error(self, mock_get_redis):
        """Test handling Redis errors gracefully."""
        # Set up mock to raise exception
        mock_redis = MagicMock()
        mock_redis.get.side_effect = Exception("Redis error")
        mock_get_redis.return_value = mock_redis
        
        # Call get_cached (should not raise exception)
        result = get_cached("test_key")
        
        # Verify result
        assert result is None
    
    @patch('src.utils.cache._get_redis')
    def test_cache_result_success(self, mock_get_redis):
        """Test caching a result successfully."""
        # Set up mock
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis
        
        # Call cache_result
        data = {"key": "value", "number": 123}
        result = cache_result("test_key", data, ttl=300)
        
        # Verify result
        assert result is True
        
        # Verify Redis was called correctly
        mock_redis.setex.assert_called_once_with(
            "rxindexer:test_key", 
            300, 
            json.dumps(data)
        )
    
    @patch('src.utils.cache._get_redis')
    def test_cache_result_redis_error(self, mock_get_redis):
        """Test handling Redis errors when caching."""
        # Set up mock to raise exception
        mock_redis = MagicMock()
        mock_redis.setex.side_effect = Exception("Redis error")
        mock_get_redis.return_value = mock_redis
        
        # Call cache_result (should not raise exception)
        result = cache_result("test_key", {"key": "value"}, ttl=300)
        
        # Verify result
        assert result is False
    
    @patch('src.utils.cache._get_redis')
    def test_invalidate_cache_success(self, mock_get_redis):
        """Test invalidating cache keys by pattern."""
        # Set up mock
        mock_redis = MagicMock()
        mock_redis.keys.return_value = [b"rxindexer:key1", b"rxindexer:key2"]
        mock_redis.delete.return_value = 2
        mock_get_redis.return_value = mock_redis
        
        # Call invalidate_cache
        result = invalidate_cache("key*")
        
        # Verify result
        assert result == 2
        
        # Verify Redis was called correctly
        mock_redis.keys.assert_called_once_with("rxindexer:key*")
        mock_redis.delete.assert_called_once_with(b"rxindexer:key1", b"rxindexer:key2")
    
    @patch('src.utils.cache._get_redis')
    def test_invalidate_cache_no_keys(self, mock_get_redis):
        """Test invalidating cache when no keys match the pattern."""
        # Set up mock
        mock_redis = MagicMock()
        mock_redis.keys.return_value = []
        mock_get_redis.return_value = mock_redis
        
        # Call invalidate_cache
        result = invalidate_cache("nonexistent*")
        
        # Verify result
        assert result == 0
        
        # Verify Redis was called correctly
        mock_redis.keys.assert_called_once_with("rxindexer:nonexistent*")
        mock_redis.delete.assert_not_called()
    
    @patch('src.utils.cache._get_redis')
    def test_flush_all_cache(self, mock_get_redis):
        """Test flushing all RXinDexer cache keys."""
        # Set up mock
        mock_redis = MagicMock()
        mock_redis.keys.return_value = [b"rxindexer:key1", b"rxindexer:key2"]
        mock_get_redis.return_value = mock_redis
        
        # Call flush_all_cache
        result = flush_all_cache()
        
        # Verify result
        assert result is True
        
        # Verify Redis was called correctly
        mock_redis.keys.assert_called_once_with("rxindexer:*")
        mock_redis.delete.assert_called_once_with(b"rxindexer:key1", b"rxindexer:key2")
