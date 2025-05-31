# /Users/radiant/Desktop/RXinDexer/tests/api/test_address_api.py
# This file tests the address-related API endpoints.
# It verifies balance queries, UTXO listings, and transaction history for wallet addresses.

import pytest
from unittest.mock import patch
from decimal import Decimal
from fastapi.testclient import TestClient

from src.models import UTXO, Holder
from src.main import app


class TestAddressAPI:
    """Tests for the address API endpoints."""
    
    def test_get_address_balance(self, client, db_session):
        """Test getting an address balance."""
        # Create a holder with RXD and token balances
        holder = Holder(
            address="addr123",
            rxd_balance=Decimal("50.5"),
            token_balances={"glyph:1234": 1, "glyph:5678": 2}
        )
        db_session.add(holder)
        db_session.commit()
        
        # Make API request
        response = client.get("/api/address/addr123/balance")
        
        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["address"] == "addr123"
        assert data["rxd_balance"] == "50.5"
        assert data["glyph_tokens"] == {"glyph:1234": 1, "glyph:5678": 2}
    
    def test_get_address_balance_not_found(self, client, db_session):
        """Test getting balance for a non-existent address."""
        # Make API request for non-existent address
        response = client.get("/api/address/nonexistent/balance")
        
        # Verify response
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
    
    def test_get_address_balance_from_utxos(self, client, db_session):
        """Test getting balance for an address with UTXOs but no holder record."""
        # Create UTXOs for an address
        utxo = UTXO(
            txid="tx123",
            vout=0,
            address="addr456",
            amount=Decimal("10.0"),
            spent=False,
            block_height=1,
            block_hash="hash1"
        )
        db_session.add(utxo)
        db_session.commit()
        
        # Make API request
        response = client.get("/api/address/addr456/balance")
        
        # Verify response shows zero balance (no holder record yet)
        assert response.status_code == 200
        data = response.json()
        assert data["address"] == "addr456"
        assert data["rxd_balance"] == "0"
        assert data["glyph_tokens"] == {}
    
    @patch('src.api.address.get_cached')
    @patch('src.api.address.cache_result')
    def test_address_balance_caching(self, mock_cache_result, mock_get_cached, client, db_session):
        """Test that address balance endpoint uses caching."""
        # Set up mock to return cached data
        mock_get_cached.return_value = {
            "address": "addr123",
            "rxd_balance": "100.0",
            "glyph_tokens": {"glyph:1234": 5}
        }
        
        # Make API request
        response = client.get("/api/address/addr123/balance")
        
        # Verify cached data was returned
        assert response.status_code == 200
        data = response.json()
        assert data["rxd_balance"] == "100.0"
        assert data["glyph_tokens"] == {"glyph:1234": 5}
        
        # Verify cache was checked
        mock_get_cached.assert_called_once_with("balance:addr123")
        
        # Verify database was not queried (because cache hit)
        mock_cache_result.assert_not_called()
    
    def test_get_address_utxos(self, client, db_session):
        """Test getting UTXOs for an address."""
        # Create UTXOs
        utxo1 = UTXO(
            txid="tx1",
            vout=0,
            address="addr123",
            amount=Decimal("10.0"),
            spent=False,
            block_height=1,
            block_hash="hash1"
        )
        utxo2 = UTXO(
            txid="tx2",
            vout=1,
            address="addr123",
            amount=Decimal("20.0"),
            spent=True,  # Spent UTXO
            block_height=2,
            block_hash="hash2"
        )
        db_session.add_all([utxo1, utxo2])
        db_session.commit()
        
        # Make API request for unspent UTXOs (default)
        response = client.get("/api/address/addr123/utxos")
        
        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["address"] == "addr123"
        assert len(data["utxos"]) == 1  # Only unspent by default
        assert data["utxos"][0]["txid"] == "tx1"
        assert data["utxos"][0]["amount"] == "10.0"
        assert data["utxos"][0]["spent"] is False
        
        # Make API request for all UTXOs
        response = client.get("/api/address/addr123/utxos?unspent_only=false")
        
        # Verify response includes both UTXOs
        assert response.status_code == 200
        data = response.json()
        assert len(data["utxos"]) == 2
    
    def test_get_address_utxos_pagination(self, client, db_session):
        """Test pagination for the UTXOs endpoint."""
        # Create 30 UTXOs
        utxos = []
        for i in range(30):
            utxo = UTXO(
                txid=f"tx{i}",
                vout=0,
                address="addr123",
                amount=Decimal("1.0"),
                spent=False,
                block_height=i+1,
                block_hash=f"hash{i+1}"
            )
            utxos.append(utxo)
        db_session.add_all(utxos)
        db_session.commit()
        
        # Make API request with pagination
        response = client.get("/api/address/addr123/utxos?page=2&limit=10")
        
        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert len(data["utxos"]) == 10  # Second page of 10 items
        assert data["pagination"]["page"] == 2
        assert data["pagination"]["limit"] == 10
        assert data["pagination"]["total_items"] == 30
        assert data["pagination"]["total_pages"] == 3
        assert data["pagination"]["has_next"] is True
        assert data["pagination"]["has_prev"] is True
    
    def test_get_address_transactions(self, client, db_session):
        """Test getting transaction history for an address."""
        # Create UTXOs for multiple transactions
        utxo1 = UTXO(
            txid="tx1",
            vout=0,
            address="addr123",
            amount=Decimal("10.0"),
            spent=False,
            block_height=1,
            block_hash="hash1"
        )
        utxo2 = UTXO(
            txid="tx1",
            vout=1,
            address="addr123",
            amount=Decimal("5.0"),
            spent=True,
            block_height=1,
            block_hash="hash1"
        )
        utxo3 = UTXO(
            txid="tx2",
            vout=0,
            address="addr123",
            amount=Decimal("20.0"),
            spent=False,
            block_height=2,
            block_hash="hash2"
        )
        db_session.add_all([utxo1, utxo2, utxo3])
        db_session.commit()
        
        # Make API request
        response = client.get("/api/address/addr123/transactions")
        
        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["address"] == "addr123"
        
        # Should group by transaction
        transactions = data["transactions"]
        assert len(transactions) == 2  # Two unique transactions
        
        # Find tx1 and check its UTXOs
        tx1 = next((tx for tx in transactions if tx["txid"] == "tx1"), None)
        assert tx1 is not None
        assert len(tx1["utxos"]) == 2
        
        # Find tx2 and check its UTXOs
        tx2 = next((tx for tx in transactions if tx["txid"] == "tx2"), None)
        assert tx2 is not None
        assert len(tx2["utxos"]) == 1
