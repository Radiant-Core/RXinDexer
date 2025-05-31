# /Users/radiant/Desktop/RXinDexer/tests/api/test_health_endpoints.py
# This file tests the health check endpoints to ensure they're responding correctly.
# It verifies both the /health and /api/v1/health endpoints.

import pytest
from fastapi.testclient import TestClient
from src.api.main import app

client = TestClient(app)

def test_root_health_endpoint():
    """Test that the root /health endpoint returns the expected response"""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    
    # Check required fields
    assert "status" in data
    assert "timestamp" in data
    assert "components" in data
    
    # Verify status value
    assert data["status"] == "healthy"
    
    # Verify components structure
    assert "api" in data["components"]
    assert "database" in data["components"]
    
    # Verify component statuses
    assert data["components"]["api"] == "online"

def test_api_v1_health_endpoint():
    """Test that the /api/v1/health endpoint returns the expected response"""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    
    # Check required fields
    assert "status" in data
    assert "timestamp" in data
    assert "components" in data
    
    # Verify status value
    assert data["status"] == "healthy"
    
    # Verify components structure
    assert "api" in data["components"]
    assert "database" in data["components"]
    
    # Verify component statuses
    assert data["components"]["api"] == "online"

def test_health_endpoints_match():
    """Test that both health endpoints return the same response structure"""
    root_response = client.get("/health").json()
    api_v1_response = client.get("/api/v1/health").json()
    
    # Remove timestamp as it will be different between calls
    del root_response["timestamp"]
    del api_v1_response["timestamp"]
    
    # The responses should now be identical
    assert root_response == api_v1_response
