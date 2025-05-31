# /Users/radiant/Desktop/RXinDexer/tests/api/test_address_endpoints.py
# This file tests the address-related API endpoints to ensure proper functionality.
# It verifies balance, UTXO and transaction history endpoints with mocked database responses.

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.api.main import app
from src.models import UTXO, Holder

client = TestClient(app)

# Test constants
TEST_ADDRESS = "rx1qtest0123456789abcdef0123456789abcdef01"

@pytest.fixture
def mock_db_session():
    """Create a mock database session for testing"""
    session = MagicMock(spec=Session)
    return session

@pytest.fixture
def mock_get_db(mock_db_session):
    """Patch the get_db dependency to use our mock session"""
    with patch("src.api.address.get_db") as mock:
        mock.return_value = iter([mock_db_session])
        yield mock

@pytest.fixture
def mock_cache():
    """Mock the caching functions"""
    with patch("src.api.address.get_cached") as mock_get_cached:
        with patch("src.api.address.cache_result") as mock_cache_result:
            mock_get_cached.return_value = None
            yield mock_get_cached, mock_cache_result

def test_get_address_balance_not_found(mock_get_db, mock_db_session, mock_cache):
    """Test the balance endpoint when address is not found"""
    # Configure mocks
    mock_db_session.query.return_value.filter.return_value.first.return_value = None
    mock_db_session.query.return_value.filter.return_value.count.return_value = 0
    
    # Call the endpoint
    response = client.get(f"/api/v1/address/{TEST_ADDRESS}/balance")
    
    # Verify response
    assert response.status_code == 404
    assert response.json()["detail"] == "Address not found"

def test_get_address_balance_found(mock_get_db, mock_db_session, mock_cache):
    """Test the balance endpoint when address is found"""
    # Configure mock holder
    mock_holder = MagicMock()
    mock_holder.address = TEST_ADDRESS
    mock_holder.rxd_balance = 100.0
    mock_holder.token_balances = {"token1": "10", "token2": "20"}
    
    # Configure mocks
    mock_db_session.query.return_value.filter.return_value.first.return_value = mock_holder
    
    # Call the endpoint
    response = client.get(f"/api/v1/address/{TEST_ADDRESS}/balance")
    
    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert data["address"] == TEST_ADDRESS
    assert data["rxd_balance"] == "100.0"
    assert data["glyph_tokens"] == {"token1": "10", "token2": "20"}

def test_get_address_utxos(mock_get_db, mock_db_session):
    """Test the UTXOs endpoint"""
    # Create mock UTXOs
    mock_utxo1 = MagicMock(spec=UTXO)
    mock_utxo1.txid = "txid1"
    mock_utxo1.vout = 0
    mock_utxo1.amount = 50.0
    mock_utxo1.token_ref = None
    mock_utxo1.spent = False
    mock_utxo1.block_height = 100
    
    mock_utxo2 = MagicMock(spec=UTXO)
    mock_utxo2.txid = "txid2"
    mock_utxo2.vout = 1
    mock_utxo2.amount = 25.0
    mock_utxo2.token_ref = "token1"
    mock_utxo2.spent = False
    mock_utxo2.block_height = 110
    
    # Configure mock query
    mock_query = mock_db_session.query.return_value
    mock_query.filter.return_value = mock_query
    mock_query.filter.return_value.count.return_value = 2
    mock_query.limit.return_value = mock_query
    mock_query.offset.return_value = [mock_utxo1, mock_utxo2]
    
    # Call the endpoint
    response = client.get(f"/api/v1/address/{TEST_ADDRESS}/utxos")
    
    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert data["address"] == TEST_ADDRESS
    assert len(data["utxos"]) == 2
    assert data["utxos"][0]["txid"] == "txid1"
    assert data["utxos"][1]["txid"] == "txid2"
