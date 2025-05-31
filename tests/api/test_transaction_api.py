# /Users/radiant/Desktop/RXinDexer/tests/api/test_transaction_api.py
# This file tests the transaction-related API endpoints.
# It verifies transaction details, block queries, and transaction search functionality.

import pytest
from unittest.mock import patch
from decimal import Decimal
from fastapi.testclient import TestClient

from src.models import UTXO, GlyphToken
from src.main import app


class TestTransactionAPI:
    """Tests for the transaction API endpoints."""
    
    def test_get_transaction(self, client, db_session):
        """Test getting details about a specific transaction."""
        # Create outputs for a transaction
        output1 = UTXO(
            txid="tx123",
            vout=0,
            address="addr1",
            amount=Decimal("10.0"),
            spent=False,
            block_height=100,
            block_hash="hash100"
        )
        output2 = UTXO(
            txid="tx123",
            vout=1,
            address="addr2",
            amount=Decimal("5.0"),
            spent=True,
            block_height=100,
            block_hash="hash100"
        )
        
        # Create input for the transaction
        input1 = UTXO(
            txid="prevtx",
            vout=0,
            address="addr0",
            amount=Decimal("20.0"),
            spent=True,
            spent_txid="tx123",  # Spent by our target transaction
            block_height=90,
            block_hash="hash90"
        )
        
        # Create a token in this transaction
        token = GlyphToken(
            ref="glyph:1234",
            type="fungible",
            metadata={"name": "Test Token"},
            current_txid="tx123",
            current_vout=0,
            genesis_txid="tx123",
            genesis_block_height=100
        )
        output1.token_ref = "glyph:1234"  # Link token to output
        
        db_session.add_all([output1, output2, input1, token])
        db_session.commit()
        
        # Make API request
        response = client.get("/api/transaction/tx123")
        
        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["txid"] == "tx123"
        assert data["block_height"] == 100
        
        # Verify inputs
        assert len(data["inputs"]) == 1
        assert data["inputs"][0]["txid"] == "prevtx"
        assert data["inputs"][0]["vout"] == 0
        assert data["inputs"][0]["address"] == "addr0"
        assert data["inputs"][0]["amount"] == "20.0"
        
        # Verify outputs
        assert len(data["outputs"]) == 2
        assert data["outputs"][0]["vout"] == 0
        assert data["outputs"][0]["address"] == "addr1"
        assert data["outputs"][0]["amount"] == "10.0"
        assert data["outputs"][0]["token_ref"] == "glyph:1234"
        assert data["outputs"][1]["vout"] == 1
        assert data["outputs"][1]["address"] == "addr2"
        assert data["outputs"][1]["amount"] == "5.0"
        
        # Verify tokens
        assert len(data["tokens"]) == 1
        assert data["tokens"][0]["ref"] == "glyph:1234"
        assert data["tokens"][0]["type"] == "fungible"
        assert data["tokens"][0]["vout"] == 0
    
    def test_get_transaction_not_found(self, client, db_session):
        """Test getting a non-existent transaction."""
        # Make API request for non-existent transaction
        response = client.get("/api/transaction/nonexistent")
        
        # Verify response
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
    
    def test_get_block_transactions(self, client, db_session):
        """Test getting transactions in a specific block."""
        # Create UTXOs for multiple transactions in the same block
        tx1_out1 = UTXO(
            txid="tx1",
            vout=0,
            address="addr1",
            amount=Decimal("10.0"),
            spent=False,
            block_height=100,
            block_hash="hash100"
        )
        tx1_out2 = UTXO(
            txid="tx1",
            vout=1,
            address="addr2",
            amount=Decimal("5.0"),
            spent=False,
            block_height=100,
            block_hash="hash100"
        )
        tx2_out1 = UTXO(
            txid="tx2",
            vout=0,
            address="addr3",
            amount=Decimal("15.0"),
            spent=False,
            token_ref="glyph:1234",  # This transaction has a token
            block_height=100,
            block_hash="hash100"
        )
        
        # Create token
        token = GlyphToken(
            ref="glyph:1234",
            type="fungible",
            metadata={"name": "Test Token"},
            current_txid="tx2",
            current_vout=0,
            genesis_txid="tx2",
            genesis_block_height=100
        )
        
        db_session.add_all([tx1_out1, tx1_out2, tx2_out1, token])
        db_session.commit()
        
        # Make API request
        response = client.get("/api/transaction/block/100")
        
        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["block_height"] == 100
        
        # Should have 2 transactions
        transactions = data["transactions"]
        assert len(transactions) == 2
        
        # Verify tx1 (no tokens)
        tx1 = next((tx for tx in transactions if tx["txid"] == "tx1"), None)
        assert tx1 is not None
        assert tx1["has_tokens"] is False
        
        # Verify tx2 (has token)
        tx2 = next((tx for tx in transactions if tx["txid"] == "tx2"), None)
        assert tx2 is not None
        assert tx2["has_tokens"] is True
    
    def test_get_block_transactions_pagination(self, client, db_session):
        """Test pagination for block transactions endpoint."""
        # Create 30 transactions in the same block
        utxos = []
        for i in range(30):
            utxo = UTXO(
                txid=f"tx{i}",
                vout=0,
                address=f"addr{i}",
                amount=Decimal("1.0"),
                spent=False,
                block_height=100,
                block_hash="hash100"
            )
            utxos.append(utxo)
        db_session.add_all(utxos)
        db_session.commit()
        
        # Make API request with pagination
        response = client.get("/api/transaction/block/100?page=2&limit=10")
        
        # Verify response
        assert response.status_code == 200
        data = response.json()
        transactions = data["transactions"]
        assert len(transactions) == 10  # Second page of 10 items
        
        # Verify pagination data
        pagination = data["pagination"]
        assert pagination["page"] == 2
        assert pagination["limit"] == 10
        assert pagination["total_items"] == 30
        assert pagination["total_pages"] == 3
        assert pagination["has_next"] is True
        assert pagination["has_prev"] is True
    
    def test_search_transaction_by_txid(self, client, db_session):
        """Test searching for a transaction by its ID."""
        # Create a transaction
        utxo = UTXO(
            txid="tx123abc",
            vout=0,
            address="addr1",
            amount=Decimal("10.0"),
            spent=False,
            block_height=100,
            block_hash="hash100"
        )
        db_session.add(utxo)
        db_session.commit()
        
        # Make API request to search by txid
        response = client.get("/api/transaction/search/tx123abc")
        
        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "transaction"
        assert len(data["results"]) == 1
        assert data["results"][0]["txid"] == "tx123abc"
        assert data["results"][0]["block_height"] == 100
    
    def test_search_transaction_by_address(self, client, db_session):
        """Test searching for transactions by address."""
        # Create UTXOs for an address
        utxo1 = UTXO(
            txid="tx1",
            vout=0,
            address="addr123",
            amount=Decimal("10.0"),
            spent=False,
            block_height=100,
            block_hash="hash100"
        )
        utxo2 = UTXO(
            txid="tx2",
            vout=0,
            address="addr123",
            amount=Decimal("5.0"),
            spent=False,
            block_height=101,
            block_hash="hash101"
        )
        db_session.add_all([utxo1, utxo2])
        db_session.commit()
        
        # Make API request to search by address
        response = client.get("/api/transaction/search/addr123")
        
        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "address"
        assert len(data["results"]) == 2
        # Results should be txids involving the address
        txids = [r["txid"] for r in data["results"]]
        assert "tx1" in txids
        assert "tx2" in txids
    
    def test_search_transaction_by_token(self, client, db_session):
        """Test searching for a token by its reference."""
        # Create a token
        token = GlyphToken(
            ref="glyph:1234",
            type="fungible",
            metadata={"name": "Test Token"},
            current_txid="tx123",
            current_vout=0,
            genesis_txid="tx123",
            genesis_block_height=100
        )
        db_session.add(token)
        db_session.commit()
        
        # Make API request to search by token reference
        response = client.get("/api/transaction/search/glyph:1234")
        
        # Verify response
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "token"
        assert len(data["results"]) == 1
        assert data["results"][0]["ref"] == "glyph:1234"
        assert data["results"][0]["type"] == "fungible"
        assert data["results"][0]["genesis_txid"] == "tx123"
    
    def test_search_transaction_not_found(self, client, db_session):
        """Test searching for a non-existent item."""
        # Make API request with a query that doesn't match anything
        response = client.get("/api/transaction/search/nonexistent")
        
        # Verify response
        assert response.status_code == 404
        assert "no results found" in response.json()["detail"].lower()
