# /Users/radiant/Desktop/RXinDexer/tests/conftest.py
# This file contains pytest fixtures and configuration for the RXinDexer test suite.
# It provides common test data and mocked dependencies for unit and integration tests.

import os
import pytest
import json
from decimal import Decimal
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

# Add project root to path for imports
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from src.models.database import Base
from src.models import UTXO, GlyphToken, Holder, SyncState
from src.main import app


# Create in-memory SQLite database for tests
@pytest.fixture
def db_engine():
    """Create a SQLite in-memory database engine for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session(db_engine):
    """Create a database session for testing."""
    Session = sessionmaker(bind=db_engine)
    session = Session()
    
    try:
        yield session
    finally:
        session.close()


# Mock the database dependency in FastAPI
@pytest.fixture
def client(db_session):
    """Create a test client for the FastAPI application."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    
    from src.models.database import get_db
    app.dependency_overrides[get_db] = override_get_db
    
    with TestClient(app) as client:
        yield client


# Mock RPC client for testing
@pytest.fixture
def mock_rpc():
    """Create a mock RadiantRPC client."""
    mock = MagicMock()
    
    # Define common RPC responses
    mock.get_block_count.return_value = 100
    mock.get_block_hash.return_value = "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"
    
    # Mock block data
    with open(Path(__file__).parent / "data" / "block.json", "r") as f:
        mock.get_block.return_value = json.load(f)
    
    # Mock transaction data
    with open(Path(__file__).parent / "data" / "transaction.json", "r") as f:
        mock.get_raw_transaction.return_value = json.load(f)
    
    return mock


# Sample test data
@pytest.fixture
def sample_utxo():
    """Create a sample UTXO for testing."""
    return UTXO(
        txid="d5ada6c79a4abd38fe2a95d8a4b86bec64af447eb3c5216e7e6bc278387d614e",
        vout=0,
        address="12c6DSiU4Rq3P4ZxziKxzrL5LmMBrzjrJX",
        amount=Decimal("50.0"),
        spent=False,
        block_height=1,
        block_hash="000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"
    )


@pytest.fixture
def sample_glyph_token():
    """Create a sample Glyph token for testing."""
    return GlyphToken(
        ref="glyph:1234",
        type="fungible",
        metadata={"name": "Test Token", "decimals": 8},
        current_txid="d5ada6c79a4abd38fe2a95d8a4b86bec64af447eb3c5216e7e6bc278387d614e",
        current_vout=0,
        genesis_txid="d5ada6c79a4abd38fe2a95d8a4b86bec64af447eb3c5216e7e6bc278387d614e",
        genesis_block_height=1
    )


@pytest.fixture
def sample_holder():
    """Create a sample holder for testing."""
    return Holder(
        address="12c6DSiU4Rq3P4ZxziKxzrL5LmMBrzjrJX",
        rxd_balance=Decimal("50.0"),
        token_balances={"glyph:1234": 1}
    )


@pytest.fixture
def sample_sync_state():
    """Create a sample sync state for testing."""
    return SyncState(
        id=1,
        current_height=1,
        current_hash="000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f",
        current_chainwork="0000000000000000000000000000000000000000000000000000000100010001",
        is_syncing=0
    )
