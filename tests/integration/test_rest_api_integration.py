#!/usr/bin/env python3
"""
RXinDexer REST API Integration Test Suite

Tests all REST API endpoints against a running RXinDexer instance.
Requires a live server with REST_API_ENABLED=1.

Usage:
  # Against local server:
  RXINDEXER_REST_URL=http://localhost:8000 pytest tests/integration/test_rest_api_integration.py -v

  # Against Docker:
  RXINDEXER_REST_URL=http://localhost:8000 RXINDEXER_API_KEY=mykey pytest tests/integration/test_rest_api_integration.py -v
"""

import os
import json
import pytest

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    import websockets
    HAS_WS = True
except ImportError:
    HAS_WS = False

import asyncio

# Configuration
BASE_URL = os.environ.get('RXINDEXER_REST_URL', 'http://localhost:8000')
API_KEY = os.environ.get('RXINDEXER_API_KEY', '')

# Test data — zero refs/hashes for smoke testing against empty or live chain
ZERO_REF = '0' * 72
ZERO_SCRIPTHASH = '0' * 64
ZERO_ORDER_ID = '0' * 72

pytestmark = pytest.mark.skipif(not HAS_HTTPX, reason='httpx not installed')


@pytest.fixture
def headers():
    h = {'Accept': 'application/json'}
    if API_KEY:
        h['x-api-key'] = API_KEY
    return h


@pytest.fixture
def client(headers):
    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=15.0) as c:
        yield c


# ============================================================
# Health & Info
# ============================================================

class TestHealth:
    def test_health(self, client):
        r = client.get('/health')
        assert r.status_code == 200
        data = r.json()
        assert data['status'] == 'ok'
        assert 'uptime_seconds' in data

    def test_docs_available(self, client):
        r = client.get('/docs')
        assert r.status_code == 200


# ============================================================
# Glyph Token Endpoints
# ============================================================

class TestGlyphEndpoints:
    def test_get_token_not_found(self, client):
        r = client.get(f'/glyphs/token/{ZERO_REF}')
        assert r.status_code in (200, 404)

    def test_get_stats(self, client):
        r = client.get('/glyphs/stats')
        assert r.status_code == 200
        data = r.json()
        assert 'enabled' in data
        assert 'total_tokens' in data or 'by_type' in data or data.get('enabled') is not None

    def test_get_balance(self, client):
        r = client.get(f'/glyphs/balance/{ZERO_SCRIPTHASH}/{ZERO_REF}')
        assert r.status_code in (200, 400)

    def test_get_balances_for_scripthash(self, client):
        r = client.get(f'/glyphs/balances/{ZERO_SCRIPTHASH}')
        assert r.status_code == 200

    def test_get_history(self, client):
        r = client.get(f'/glyphs/history/{ZERO_REF}')
        assert r.status_code == 200

    def test_search_tokens(self, client):
        r = client.get('/glyphs/search', params={'query': 'test', 'limit': 5})
        assert r.status_code == 200

    def test_list_tokens(self, client):
        r = client.get('/glyphs/tokens', params={'limit': 5, 'offset': 0})
        assert r.status_code == 200
        data = r.json()
        assert 'tokens' in data or 'total' in data or isinstance(data, list)

    def test_get_holders(self, client):
        r = client.get(f'/glyphs/holders/{ZERO_REF}', params={'limit': 5})
        assert r.status_code == 200
        data = r.json()
        assert 'holders' in data or 'total_holders' in data

    def test_get_top_holders(self, client):
        r = client.get(f'/glyphs/top-holders/{ZERO_REF}', params={'limit': 5})
        assert r.status_code == 200

    def test_get_supply(self, client):
        r = client.get(f'/glyphs/supply/{ZERO_REF}')
        assert r.status_code in (200, 404)

    def test_get_tokens_by_type(self, client):
        r = client.get('/glyphs/by-type/1', params={'limit': 5})
        assert r.status_code == 200


# ============================================================
# dMint Endpoints
# ============================================================

class TestDmintEndpoints:
    def test_get_contracts(self, client):
        r = client.get('/dmint/contracts')
        assert r.status_code == 200

    def test_get_contract(self, client):
        r = client.get(f'/dmint/contract/{ZERO_REF}')
        assert r.status_code in (200, 404)

    def test_get_by_algorithm(self, client):
        r = client.get('/dmint/by-algorithm/sha256d')
        assert r.status_code == 200

    def test_get_most_profitable(self, client):
        r = client.get('/dmint/profitable', params={'limit': 5})
        assert r.status_code == 200


# ============================================================
# WAVE Endpoints
# ============================================================

class TestWaveEndpoints:
    def test_resolve(self, client):
        r = client.get('/wave/resolve/testname')
        assert r.status_code in (200, 404)

    def test_check_available(self, client):
        r = client.get('/wave/available/testname')
        assert r.status_code == 200
        data = r.json()
        assert 'available' in data or 'name' in data

    def test_subdomains(self, client):
        r = client.get('/wave/testname/subdomains')
        assert r.status_code in (200, 404)

    def test_reverse_lookup(self, client):
        r = client.get(f'/wave/reverse/{ZERO_SCRIPTHASH}')
        assert r.status_code == 200

    def test_wave_stats(self, client):
        r = client.get('/wave/stats')
        assert r.status_code == 200


# ============================================================
# Swap Endpoints
# ============================================================

class TestSwapEndpoints:
    def test_get_orders(self, client):
        r = client.get('/swaps/orders')
        assert r.status_code == 200

    def test_get_order(self, client):
        r = client.get(f'/swaps/orders/{ZERO_ORDER_ID}')
        assert r.status_code in (200, 404)

    def test_get_history(self, client):
        r = client.get('/swaps/history')
        assert r.status_code == 200

    def test_swap_stats(self, client):
        r = client.get('/swaps/stats')
        assert r.status_code == 200


# ============================================================
# Mempool Endpoints (NEW)
# ============================================================

class TestMempoolEndpoints:
    def test_mempool_stats(self, client):
        r = client.get('/mempool/stats')
        assert r.status_code == 200
        data = r.json()
        assert 'enabled' in data

    def test_mempool_glyph_balance(self, client):
        r = client.get(f'/mempool/glyphs/balance/{ZERO_SCRIPTHASH}/{ZERO_REF}')
        assert r.status_code in (200, 503)

    def test_mempool_glyph_txs(self, client):
        r = client.get(f'/mempool/glyphs/txs/{ZERO_SCRIPTHASH}')
        assert r.status_code in (200, 503)

    def test_mempool_token_txs(self, client):
        r = client.get(f'/mempool/glyphs/token/{ZERO_REF}')
        assert r.status_code in (200, 503)

    def test_mempool_swap_orders(self, client):
        r = client.get('/mempool/swaps/orders')
        assert r.status_code in (200, 503)

    def test_mempool_user_orders(self, client):
        r = client.get(f'/mempool/swaps/user/{ZERO_SCRIPTHASH}')
        assert r.status_code in (200, 503)


# ============================================================
# Rate Limiting
# ============================================================

class TestRateLimiting:
    def test_rate_limit_not_triggered_normal(self, client):
        """Normal usage should not trigger rate limit."""
        for _ in range(10):
            r = client.get('/health')
            assert r.status_code == 200


# ============================================================
# WebSocket Endpoint (NEW)
# ============================================================

@pytest.mark.skipif(not HAS_WS, reason='websockets not installed')
class TestWebSocket:
    @pytest.mark.asyncio
    async def test_ws_connect_and_ping(self):
        ws_url = BASE_URL.replace('http://', 'ws://').replace('https://', 'wss://') + '/ws'
        async with websockets.connect(ws_url) as ws:
            # Should receive connected message
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert msg['event'] == 'connected'

            # Send ping
            await ws.send(json.dumps({'action': 'ping'}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert msg['event'] == 'pong'

    @pytest.mark.asyncio
    async def test_ws_subscribe(self):
        ws_url = BASE_URL.replace('http://', 'ws://').replace('https://', 'wss://') + '/ws'
        async with websockets.connect(ws_url) as ws:
            await asyncio.wait_for(ws.recv(), timeout=5)  # connected

            await ws.send(json.dumps({
                'action': 'subscribe',
                'refs': [ZERO_REF],
                'scripthashes': [ZERO_SCRIPTHASH],
            }))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert msg['event'] == 'subscribed'
            assert msg['refs'] == 1
            assert msg['scripthashes'] == 1

    @pytest.mark.asyncio
    async def test_ws_invalid_json(self):
        ws_url = BASE_URL.replace('http://', 'ws://').replace('https://', 'wss://') + '/ws'
        async with websockets.connect(ws_url) as ws:
            await asyncio.wait_for(ws.recv(), timeout=5)  # connected

            await ws.send('not json')
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert msg['event'] == 'error'


# ============================================================
# Error Handling
# ============================================================

class TestErrorHandling:
    def test_invalid_hex_ref(self, client):
        r = client.get('/glyphs/token/not_hex_at_all')
        assert r.status_code in (400, 404, 422)

    def test_nonexistent_route(self, client):
        r = client.get('/nonexistent/route')
        assert r.status_code == 404


# ============================================================
# Standalone runner
# ============================================================

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
