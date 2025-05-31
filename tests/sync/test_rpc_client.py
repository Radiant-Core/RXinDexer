# /Users/radiant/Desktop/RXinDexer/tests/sync/test_rpc_client.py
# This file tests the RPC client that communicates with Radiant Node.
# It verifies that the client correctly handles API calls, retries, and error conditions.

import pytest
from unittest.mock import patch, MagicMock
from bitcoinrpc.authproxy import JSONRPCException

from src.sync.rpc_client import RadiantRPC


class TestRadiantRPC:
    """Tests for the RadiantRPC client."""
    
    @patch('src.sync.rpc_client.AuthServiceProxy')
    def test_init_connects_to_node(self, mock_auth_proxy):
        """Test that the RPC client connects to the node on initialization."""
        # Create RPC client
        rpc = RadiantRPC()
        
        # Verify that AuthServiceProxy was called
        mock_auth_proxy.assert_called_once()
    
    @patch('src.sync.rpc_client.AuthServiceProxy')
    def test_get_block_count(self, mock_auth_proxy):
        """Test getting the current block count."""
        # Set up mock
        mock_rpc = MagicMock()
        mock_rpc.getblockcount.return_value = 100
        mock_auth_proxy.return_value = mock_rpc
        
        # Create RPC client and call method
        rpc = RadiantRPC()
        result = rpc.get_block_count()
        
        # Verify result
        assert result == 100
        mock_rpc.getblockcount.assert_called_once()
    
    @patch('src.sync.rpc_client.AuthServiceProxy')
    def test_get_block(self, mock_auth_proxy):
        """Test getting a block with transaction data."""
        # Set up mock
        mock_rpc = MagicMock()
        mock_block = {
            "hash": "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f",
            "height": 1,
            "tx": ["d5ada6c79a4abd38fe2a95d8a4b86bec64af447eb3c5216e7e6bc278387d614e"]
        }
        mock_rpc.getblock.return_value = mock_block
        mock_auth_proxy.return_value = mock_rpc
        
        # Create RPC client and call method
        rpc = RadiantRPC()
        result = rpc.get_block("000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f", 2)
        
        # Verify result
        assert result == mock_block
        mock_rpc.getblock.assert_called_once_with(
            "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f", 2
        )
    
    @patch('src.sync.rpc_client.AuthServiceProxy')
    def test_retry_on_failure(self, mock_auth_proxy):
        """Test that the RPC client retries on failure."""
        # Set up mock to fail on first call, succeed on second
        mock_rpc = MagicMock()
        mock_rpc.getblockcount.side_effect = [
            JSONRPCException({"code": -28, "message": "Loading block index..."}),
            100
        ]
        mock_auth_proxy.return_value = mock_rpc
        
        # Create RPC client and call method
        rpc = RadiantRPC()
        result = rpc._call_with_retry("getblockcount", max_retries=3)
        
        # Verify result
        assert result == 100
        assert mock_rpc.getblockcount.call_count == 2
    
    @patch('src.sync.rpc_client.AuthServiceProxy')
    def test_max_retries_exceeded(self, mock_auth_proxy):
        """Test that an exception is raised when max retries is exceeded."""
        # Set up mock to always fail
        mock_rpc = MagicMock()
        error = JSONRPCException({"code": -28, "message": "Loading block index..."})
        mock_rpc.getblockcount.side_effect = error
        mock_auth_proxy.return_value = mock_rpc
        
        # Create RPC client and call method
        rpc = RadiantRPC()
        
        # Should raise the exception after max retries
        with pytest.raises(JSONRPCException):
            rpc._call_with_retry("getblockcount", max_retries=3)
        
        # Verify call count
        assert mock_rpc.getblockcount.call_count == 3
    
    @patch('src.sync.rpc_client.AuthServiceProxy')
    def test_get_glyph_token(self, mock_auth_proxy):
        """Test getting a Glyph token."""
        # Set up mock
        mock_rpc = MagicMock()
        mock_token = {
            "ref": "glyph:1234",
            "type": "fungible",
            "metadata": {"name": "Test Token", "decimals": 8}
        }
        mock_rpc.__getattr__("ref.get").return_value = mock_token
        mock_auth_proxy.return_value = mock_rpc
        
        # Create RPC client and call method
        rpc = RadiantRPC()
        result = rpc.get_glyph_token("glyph:1234")
        
        # Verify result
        assert result == mock_token
        mock_rpc.__getattr__("ref.get").assert_called_once_with("glyph:1234")
    
    @patch('src.sync.rpc_client.AuthServiceProxy')
    def test_token_not_found(self, mock_auth_proxy):
        """Test handling of token not found error."""
        # Set up mock
        mock_rpc = MagicMock()
        error = JSONRPCException({"code": -1, "message": "Token not found"})
        mock_rpc.__getattr__("ref.get").side_effect = error
        mock_auth_proxy.return_value = mock_rpc
        
        # Create RPC client and call method
        rpc = RadiantRPC()
        result = rpc.get_glyph_token("glyph:nonexistent")
        
        # Should return None for not found
        assert result is None
