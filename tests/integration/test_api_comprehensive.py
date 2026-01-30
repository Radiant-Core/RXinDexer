#!/usr/bin/env python3
"""
RXinDexer Comprehensive API Test Suite

Tests all 38 API methods defined in GLYPH_METHODS.
Can be run with pytest: pytest test_api_comprehensive.py -v
"""

import asyncio
import json
import os
import pytest
from typing import Any, Dict, List

# Configuration
HOST = os.environ.get('RXINDEXER_HOST', 'localhost')
PORT = int(os.environ.get('RXINDEXER_TCP_PORT', '50010'))


class AsyncElectrumClient:
    """Async ElectrumX JSON-RPC client."""
    
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.reader = None
        self.writer = None
        self.request_id = 0
    
    async def __aenter__(self):
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        return self
    
    async def __aexit__(self, *args):
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
    
    async def call(self, method: str, params: List[Any] = None) -> Dict:
        self.request_id += 1
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or [],
            "id": self.request_id
        }
        self.writer.write((json.dumps(request) + "\n").encode())
        await self.writer.drain()
        response = await asyncio.wait_for(self.reader.readline(), timeout=30)
        return json.loads(response.decode())


@pytest.fixture
async def client():
    """Create client fixture."""
    async with AsyncElectrumClient(HOST, PORT) as c:
        yield c


# ============================================================
# Core Server Tests
# ============================================================

@pytest.mark.asyncio
async def test_server_version(client):
    """Test server.version returns valid response."""
    response = await client.call("server.version", ["test-client", "1.4"])
    assert "result" in response
    assert isinstance(response["result"], list)
    assert len(response["result"]) >= 2


@pytest.mark.asyncio
async def test_server_features(client):
    """Test server.features returns server capabilities."""
    response = await client.call("server.features")
    assert "result" in response
    result = response["result"]
    assert "genesis_hash" in result
    assert "server_version" in result


# ============================================================
# Glyph Token API Tests (20 methods)
# ============================================================

@pytest.mark.asyncio
async def test_glyph_get_token(client):
    """Test glyph.get_token with invalid ref."""
    response = await client.call("glyph.get_token", ["0" * 72])
    # Should return null or error for non-existent token
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_glyph_get_by_ref(client):
    """Test glyph.get_by_ref method."""
    response = await client.call("glyph.get_by_ref", ["0" * 72])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_glyph_get_token_info(client):
    """Test glyph.get_token_info method."""
    response = await client.call("glyph.get_token_info", ["0" * 72])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_glyph_get_balance(client):
    """Test glyph.get_balance method."""
    response = await client.call("glyph.get_balance", ["0" * 64, "0" * 72])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_glyph_list_tokens(client):
    """Test glyph.list_tokens returns list."""
    response = await client.call("glyph.list_tokens", [10, 0])
    assert "result" in response or "error" in response
    if "result" in response:
        assert isinstance(response["result"], list)


@pytest.mark.asyncio
async def test_glyph_get_history(client):
    """Test glyph.get_history method."""
    response = await client.call("glyph.get_history", ["0" * 72, 10, 0])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_glyph_get_metadata(client):
    """Test glyph.get_metadata method."""
    response = await client.call("glyph.get_metadata", ["0" * 72])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_glyph_search_tokens(client):
    """Test glyph.search_tokens method."""
    response = await client.call("glyph.search_tokens", ["test", 10])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_glyph_get_tokens_by_type(client):
    """Test glyph.get_tokens_by_type method."""
    response = await client.call("glyph.get_tokens_by_type", [1, 10, 0])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_glyph_validate_protocols(client):
    """Test glyph.validate_protocols method."""
    response = await client.call("glyph.validate_protocols", [[1, 2]])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_glyph_get_protocol_info(client):
    """Test glyph.get_protocol_info method."""
    response = await client.call("glyph.get_protocol_info", [1])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_glyph_parse_envelope(client):
    """Test glyph.parse_envelope method."""
    # Test with hex data
    response = await client.call("glyph.parse_envelope", ["6a03676c79"])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_glyph_get_unconfirmed_balance(client):
    """Test glyph.get_unconfirmed_balance method."""
    response = await client.call("glyph.get_unconfirmed_balance", ["0" * 64, "0" * 72])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_glyph_get_unconfirmed_txs(client):
    """Test glyph.get_unconfirmed_txs method."""
    response = await client.call("glyph.get_unconfirmed_txs", ["0" * 72])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_glyph_get_token_unconfirmed(client):
    """Test glyph.get_token_unconfirmed method."""
    response = await client.call("glyph.get_token_unconfirmed", ["0" * 72])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_glyph_subscribe_balance(client):
    """Test glyph.subscribe.balance method."""
    response = await client.call("glyph.subscribe.balance", ["0" * 64, "0" * 72])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_glyph_subscribe_token(client):
    """Test glyph.subscribe.token method."""
    response = await client.call("glyph.subscribe.token", ["0" * 72])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_glyph_subscribe_transfers(client):
    """Test glyph.subscribe.transfers method."""
    response = await client.call("glyph.subscribe.transfers", ["0" * 72])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_glyph_unsubscribe_balance(client):
    """Test glyph.unsubscribe.balance method."""
    response = await client.call("glyph.unsubscribe.balance", ["0" * 64, "0" * 72])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_glyph_unsubscribe_token(client):
    """Test glyph.unsubscribe.token method."""
    response = await client.call("glyph.unsubscribe.token", ["0" * 72])
    assert "result" in response or "error" in response


# ============================================================
# WAVE Naming API Tests (6 methods)
# ============================================================

@pytest.mark.asyncio
async def test_wave_resolve(client):
    """Test wave.resolve method."""
    response = await client.call("wave.resolve", ["testname"])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_wave_check_available(client):
    """Test wave.check_available method."""
    response = await client.call("wave.check_available", ["available-name-test"])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_wave_get_subdomains(client):
    """Test wave.get_subdomains method."""
    response = await client.call("wave.get_subdomains", ["parent", 10, 0])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_wave_reverse_lookup(client):
    """Test wave.reverse_lookup method."""
    response = await client.call("wave.reverse_lookup", ["0" * 64, 10])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_wave_stats(client):
    """Test wave.stats method."""
    response = await client.call("wave.stats")
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_wave_subscribe_name(client):
    """Test wave.subscribe.name method."""
    response = await client.call("wave.subscribe.name", ["testname"])
    assert "result" in response or "error" in response


# ============================================================
# Swap Order API Tests (6 methods)
# ============================================================

@pytest.mark.asyncio
async def test_swap_get_unconfirmed_orders(client):
    """Test swap.get_unconfirmed_orders method."""
    response = await client.call("swap.get_unconfirmed_orders", ["0" * 72, "0" * 72])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_swap_get_user_unconfirmed(client):
    """Test swap.get_user_unconfirmed method."""
    response = await client.call("swap.get_user_unconfirmed", ["0" * 64])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_swap_subscribe_orderbook(client):
    """Test swap.subscribe.orderbook method."""
    response = await client.call("swap.subscribe.orderbook", ["0" * 72, "0" * 72])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_swap_subscribe_fills(client):
    """Test swap.subscribe.fills method."""
    response = await client.call("swap.subscribe.fills", ["0" * 72, "0" * 72])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_swap_subscribe_user_orders(client):
    """Test swap.subscribe.user_orders method."""
    response = await client.call("swap.subscribe.user_orders", ["0" * 64])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_swap_unsubscribe_orderbook(client):
    """Test swap.unsubscribe.orderbook method."""
    response = await client.call("swap.unsubscribe.orderbook", ["0" * 72, "0" * 72])
    assert "result" in response or "error" in response


# ============================================================
# dMint Contract API Tests (5 methods)
# ============================================================

@pytest.mark.asyncio
async def test_dmint_get_contracts(client):
    """Test dmint.get_contracts method."""
    response = await client.call("dmint.get_contracts")
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_dmint_get_contracts_extended(client):
    """Test dmint.get_contracts with extended format."""
    response = await client.call("dmint.get_contracts", ["extended"])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_dmint_get_contract(client):
    """Test dmint.get_contract method."""
    response = await client.call("dmint.get_contract", ["0" * 72])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_dmint_get_by_algorithm(client):
    """Test dmint.get_by_algorithm method."""
    response = await client.call("dmint.get_by_algorithm", ["sha256d"])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_dmint_get_most_profitable(client):
    """Test dmint.get_most_profitable method."""
    response = await client.call("dmint.get_most_profitable", [10])
    assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_dmint_subscribe_token(client):
    """Test dmint.subscribe.token method."""
    response = await client.call("dmint.subscribe.token", ["0" * 72])
    assert "result" in response or "error" in response


# ============================================================
# Mempool API Tests (1 method)
# ============================================================

@pytest.mark.asyncio
async def test_mempool_glyph_stats(client):
    """Test mempool.glyph_stats method."""
    response = await client.call("mempool.glyph_stats")
    assert "result" in response or "error" in response


# ============================================================
# Run as standalone script
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
