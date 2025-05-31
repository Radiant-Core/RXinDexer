# /Users/radiant/Desktop/RXinDexer/tests/api/test_holder_api.py
# This file tests the holder-related API endpoints.
# It verifies wallet holder counts for RXD and Glyph tokens, richlist functionality, and holder statistics.

import pytest
from unittest.mock import patch
from decimal import Decimal
from fastapi.testclient import TestClient

from src.models import Holder
from src.main import app


class TestHolderAPI:
    """Tests for the holder API endpoints."""
    
    def test_get_rxd_holder_count(self, client, db_session):
        """Test getting the count of RXD holders."""
        # Create holders with different RXD balances
        holder1 = Holder(
            address="addr1",
            rxd_balance=Decimal("100.0"),
            token_balances={}
        )
        holder2 = Holder(
            address="addr2",
            rxd_balance=Decimal("50.0"),
            token_balances={}
        )
        holder3 = Holder(
            address="addr3",
            rxd_balance=Decimal("0.0"),  # Zero balance
            token_balances={"glyph:1234": 1}
        )
        db_session.add_all([holder1, holder2, holder3])
        db_session.commit()
        
        # Make API request for all holders (min_balance=0)
        response = client.get("/api/holder/count/rxd?min_balance=0")
        
        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["asset"] == "RXD"
        assert data["min_balance"] == 0
        assert data["holder_count"] == 2  # 2 holders with balance > 0
        
        # Make API request with minimum balance filter
        response = client.get("/api/holder/count/rxd?min_balance=75")
        
        # Verify filtered response
        assert response.status_code == 200
        data = response.json()
        assert data["holder_count"] == 1  # Only 1 holder with balance >= 75
    
    @patch('src.api.holder.get_cached')
    @patch('src.api.holder.cache_result')
    def test_rxd_holder_count_caching(self, mock_cache_result, mock_get_cached, client, db_session):
        """Test that RXD holder count endpoint uses caching."""
        # Set up mock to return cached data
        mock_get_cached.return_value = {
            "asset": "RXD",
            "min_balance": 0,
            "holder_count": 123  # Cached count
        }
        
        # Make API request
        response = client.get("/api/holder/count/rxd?min_balance=0")
        
        # Verify cached data was returned
        assert response.status_code == 200
        data = response.json()
        assert data["holder_count"] == 123
        
        # Verify cache was checked
        mock_get_cached.assert_called_once_with("holders:rxd:0")
        
        # Verify database was not queried (because cache hit)
        mock_cache_result.assert_not_called()
    
    def test_get_token_holder_count(self, client, db_session):
        """Test getting the count of holders for a specific token."""
        # Create holders with different token balances
        holder1 = Holder(
            address="addr1",
            rxd_balance=Decimal("100.0"),
            token_balances={"glyph:1234": 1, "glyph:5678": 5}
        )
        holder2 = Holder(
            address="addr2",
            rxd_balance=Decimal("50.0"),
            token_balances={"glyph:1234": 10}  # Same token as holder1
        )
        holder3 = Holder(
            address="addr3",
            rxd_balance=Decimal("0.0"),
            token_balances={"glyph:5678": 2}  # Different token
        )
        db_session.add_all([holder1, holder2, holder3])
        db_session.commit()
        
        # Make API request for glyph:1234 token holders
        response = client.get("/api/holder/count/token/glyph:1234")
        
        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["asset"] == "glyph:1234"
        assert data["holder_count"] == 2  # 2 holders for glyph:1234
        
        # Make API request for glyph:5678 token holders
        response = client.get("/api/holder/count/token/glyph:5678")
        
        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["asset"] == "glyph:5678"
        assert data["holder_count"] == 2  # 2 holders for glyph:5678
    
    def test_get_rxd_richlist(self, client, db_session):
        """Test getting the RXD richlist."""
        # Create holders with different RXD balances
        holders = []
        for i in range(150):  # Create 150 holders
            holder = Holder(
                address=f"addr{i}",
                rxd_balance=Decimal(f"{1000-i}.0"),  # Descending order of balance
                token_balances={}
            )
            holders.append(holder)
        db_session.add_all(holders)
        db_session.commit()
        
        # Make API request with default limit (100)
        response = client.get("/api/holder/richlist/rxd")
        
        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["asset"] == "RXD"
        richlist = data["richlist"]
        assert len(richlist) == 100  # Default limit is 100
        
        # Verify richlist is ordered by balance (descending)
        assert richlist[0]["address"] == "addr0"
        assert richlist[0]["balance"] == "1000.0"
        assert richlist[1]["address"] == "addr1"
        assert richlist[1]["balance"] == "999.0"
        
        # Make API request with custom limit
        response = client.get("/api/holder/richlist/rxd?limit=10")
        
        # Verify response with custom limit
        assert response.status_code == 200
        data = response.json()
        richlist = data["richlist"]
        assert len(richlist) == 10  # Custom limit of 10
    
    def test_get_holder_stats(self, client, db_session):
        """Test getting holder statistics."""
        # Create holders with different balances and tokens
        holder1 = Holder(
            address="addr1",
            rxd_balance=Decimal("100.0"),
            token_balances={"glyph:1234": 1}  # Both RXD and tokens
        )
        holder2 = Holder(
            address="addr2",
            rxd_balance=Decimal("50.0"),
            token_balances={}  # RXD only
        )
        holder3 = Holder(
            address="addr3",
            rxd_balance=Decimal("0.0"),
            token_balances={"glyph:5678": 2}  # Tokens only
        )
        db_session.add_all([holder1, holder2, holder3])
        db_session.commit()
        
        # Make API request
        response = client.get("/api/holder/stats")
        
        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["total_addresses"] == 3
        assert data["rxd_holders"] == 2  # addr1, addr2
        assert data["token_holders"] == 2  # addr1, addr3
        assert data["mixed_holders"] == 1  # addr1 (has both)
