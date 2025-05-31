# /Users/radiant/Desktop/RXinDexer/tests/api/test_token_api.py
# This file tests the token-related API endpoints.
# It verifies token metadata queries, listings, and transfer history functionality.

import pytest
from unittest.mock import patch
from decimal import Decimal
from fastapi.testclient import TestClient

from src.models import GlyphToken, UTXO
from src.main import app


class TestTokenAPI:
    """Tests for the token API endpoints."""
    
    def test_get_token_info(self, client, db_session):
        """Test getting information about a specific token."""
        # Create a token
        token = GlyphToken(
            ref="glyph:1234",
            type="fungible",
            metadata={"name": "Test Token", "decimals": 8, "symbol": "TEST"},
            current_txid="tx123",
            current_vout=0,
            genesis_txid="tx100",
            genesis_block_height=100
        )
        db_session.add(token)
        
        # Create UTXO for the current token location
        utxo = UTXO(
            txid="tx123",
            vout=0,
            address="token_owner",
            amount=Decimal("0"),
            spent=False,
            token_ref="glyph:1234",
            block_height=150,
            block_hash="hash150"
        )
        db_session.add(utxo)
        db_session.commit()
        
        # Make API request
        response = client.get("/api/token/glyph:1234")
        
        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["ref"] == "glyph:1234"
        assert data["type"] == "fungible"
        assert data["metadata"]["name"] == "Test Token"
        assert data["metadata"]["symbol"] == "TEST"
        assert data["genesis_txid"] == "tx100"
        assert data["genesis_block_height"] == 100
        assert data["current_owner"] == "token_owner"
    
    def test_get_token_info_not_found(self, client, db_session):
        """Test getting information about a non-existent token."""
        # Make API request for non-existent token
        response = client.get("/api/token/glyph:nonexistent")
        
        # Verify response
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
    
    @patch('src.api.token.get_cached')
    @patch('src.api.token.cache_result')
    def test_token_info_caching(self, mock_cache_result, mock_get_cached, client, db_session):
        """Test that token info endpoint uses caching."""
        # Set up mock to return cached data
        mock_get_cached.return_value = {
            "ref": "glyph:1234",
            "type": "fungible",
            "metadata": {"name": "Cached Token", "decimals": 8},
            "genesis_txid": "tx100",
            "genesis_block_height": 100,
            "current_owner": "cached_owner"
        }
        
        # Make API request
        response = client.get("/api/token/glyph:1234")
        
        # Verify cached data was returned
        assert response.status_code == 200
        data = response.json()
        assert data["metadata"]["name"] == "Cached Token"
        assert data["current_owner"] == "cached_owner"
        
        # Verify cache was checked
        mock_get_cached.assert_called_once_with("token:glyph:1234")
        
        # Verify database was not queried (because cache hit)
        mock_cache_result.assert_not_called()
    
    def test_list_tokens(self, client, db_session):
        """Test listing all tokens."""
        # Create tokens of different types
        token1 = GlyphToken(
            ref="glyph:1234",
            type="fungible",
            metadata={"name": "Fungible Token", "decimals": 8},
            current_txid="tx1",
            current_vout=0,
            genesis_txid="tx1",
            genesis_block_height=100
        )
        token2 = GlyphToken(
            ref="glyph:5678",
            type="non-fungible",
            metadata={"name": "NFT", "image": "ipfs://..."},
            current_txid="tx2",
            current_vout=0,
            genesis_txid="tx2",
            genesis_block_height=200
        )
        token3 = GlyphToken(
            ref="glyph:9012",
            type="dmint",
            metadata={"name": "DMint Token", "rules": "..."},
            current_txid="tx3",
            current_vout=0,
            genesis_txid="tx3",
            genesis_block_height=300
        )
        db_session.add_all([token1, token2, token3])
        db_session.commit()
        
        # Make API request for all tokens
        response = client.get("/api/token/")
        
        # Verify response
        assert response.status_code == 200
        data = response.json()
        tokens = data["tokens"]
        assert len(tokens) == 3
        
        # Verify pagination data
        assert data["pagination"]["total_items"] == 3
        assert data["pagination"]["page"] == 1
        
        # Make API request with type filter
        response = client.get("/api/token/?token_type=fungible")
        
        # Verify filtered response
        assert response.status_code == 200
        data = response.json()
        tokens = data["tokens"]
        assert len(tokens) == 1
        assert tokens[0]["ref"] == "glyph:1234"
        assert tokens[0]["type"] == "fungible"
    
    def test_list_tokens_pagination(self, client, db_session):
        """Test pagination for token listing."""
        # Create 25 tokens
        tokens = []
        for i in range(25):
            token = GlyphToken(
                ref=f"glyph:{1000+i}",
                type="fungible",
                metadata={"name": f"Token {i}"},
                current_txid=f"tx{i}",
                current_vout=0,
                genesis_txid=f"tx{i}",
                genesis_block_height=100+i
            )
            tokens.append(token)
        db_session.add_all(tokens)
        db_session.commit()
        
        # Make API request with pagination
        response = client.get("/api/token/?page=2&limit=10")
        
        # Verify response
        assert response.status_code == 200
        data = response.json()
        tokens_page = data["tokens"]
        assert len(tokens_page) == 10  # Second page of 10 items
        assert data["pagination"]["page"] == 2
        assert data["pagination"]["limit"] == 10
        assert data["pagination"]["total_items"] == 25
        assert data["pagination"]["total_pages"] == 3
        assert data["pagination"]["has_next"] is True
        assert data["pagination"]["has_prev"] is True
    
    def test_get_token_history(self, client, db_session):
        """Test getting the transfer history of a token."""
        # Create token
        token = GlyphToken(
            ref="glyph:1234",
            type="fungible",
            metadata={"name": "Test Token"},
            current_txid="tx3",
            current_vout=0,
            genesis_txid="tx1",
            genesis_block_height=100
        )
        db_session.add(token)
        
        # Create UTXOs showing the token's history
        utxo1 = UTXO(
            txid="tx1",
            vout=0,
            address="owner1",
            amount=Decimal("0"),
            spent=True,
            spent_txid="tx2",
            token_ref="glyph:1234",
            block_height=100,
            block_hash="hash100"
        )
        utxo2 = UTXO(
            txid="tx2",
            vout=1,
            address="owner2",
            amount=Decimal("0"),
            spent=True,
            spent_txid="tx3",
            token_ref="glyph:1234",
            block_height=110,
            block_hash="hash110"
        )
        utxo3 = UTXO(
            txid="tx3",
            vout=0,
            address="owner3",
            amount=Decimal("0"),
            spent=False,
            token_ref="glyph:1234",
            block_height=120,
            block_hash="hash120"
        )
        db_session.add_all([utxo1, utxo2, utxo3])
        db_session.commit()
        
        # Make API request
        response = client.get("/api/token/glyph:1234/history")
        
        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["ref"] == "glyph:1234"
        
        history = data["history"]
        assert len(history) == 3
        
        # Check the history is in order
        assert history[0]["txid"] == "tx1"
        assert history[0]["address"] == "owner1"
        assert history[0]["spent"] is True
        
        assert history[1]["txid"] == "tx2"
        assert history[1]["address"] == "owner2"
        assert history[1]["spent"] is True
        
        assert history[2]["txid"] == "tx3"
        assert history[2]["address"] == "owner3"
        assert history[2]["spent"] is False
