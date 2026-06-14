"""
REST API Tests for RXinDexer

Tests the FastAPI REST API endpoints for Glyph v2 tokens, dMint contracts,
WAVE naming, and Swap/DEX functionality.
"""

import pytest
import struct
from unittest.mock import Mock, MagicMock, patch

from fastapi.testclient import TestClient

from electrumx.server.rate_limiter import (
    IPRateLimiter as _IPRateLimiter,
    DEFAULT_TRUSTED_PROXIES as _DEFAULT_TRUSTED_PROXIES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ref(txid_hex: str = 'aa' * 32, vout: int = 0) -> str:
    """Build a 72-char hex ref string."""
    return txid_hex + struct.pack('<I', vout).hex()


def _make_ref_bytes(txid_hex: str = 'aa' * 32, vout: int = 0) -> bytes:
    return bytes.fromhex(txid_hex) + struct.pack('<I', vout)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_glyph_index():
    idx = Mock()
    idx.enabled = True
    idx.token_cache = {}
    idx.get_token = Mock(return_value=None)
    idx._token_to_dict = Mock(return_value={})
    idx.get_all_tokens_summary = Mock(return_value={'total': 0, 'tokens': []})
    idx.search_tokens = Mock(return_value=[])
    idx.get_stats = Mock(return_value={'enabled': True, 'total_tokens': 0})
    idx.get_tokens_by_type = Mock(return_value=[])
    idx.get_token_holders = Mock(return_value={'holders': []})
    idx.get_token_supply = Mock(return_value=None)
    idx.get_token_burns = Mock(return_value={'burns': []})
    idx.get_token_trades = Mock(return_value={'trades': []})
    idx.get_top_holders = Mock(return_value={'top_holders': []})
    idx.get_token_history = Mock(return_value=[])
    idx.get_metadata = Mock(return_value=None)
    return idx


@pytest.fixture
def mock_wave_index():
    idx = Mock()
    idx.resolve = Mock(return_value=None)
    idx.check_available = Mock(return_value={'name': 'test', 'available': True})
    idx.get_subdomains = Mock(return_value=[])
    idx.reverse_lookup = Mock(return_value=[])
    idx.stats = Mock(return_value={'total_names': 0})
    return idx


@pytest.fixture
def mock_swap_index():
    idx = Mock()
    idx.enabled = True
    idx.order_cache = {}
    idx.get_open_orders = Mock(return_value=[])
    idx.get_orderbook = Mock(return_value={'bids': [], 'asks': []})
    idx.get_order = Mock(return_value=None)
    idx.get_swap_history = Mock(return_value=[])
    return idx


@pytest.fixture
def mock_analytics_index():
    idx = Mock()
    idx.enabled = True
    idx.get_stats = Mock(return_value={
        'enabled': True,
        'last_processed_height': 99999,
        'rich_list_entries': 2,
    })
    idx.get_balance_distribution = Mock(return_value={'1-10': 1, '10-100': 2})
    idx.get_supply_aging = Mock(return_value={'<1d': 123, '1d-1w': 456})
    idx.get_top_addresses = Mock(return_value={
        'total': 2,
        'limit': 25,
        'offset': 5,
        'rows': [{'address': 'addr1', 'balance': 10}],
    })
    idx.get_movement = Mock(return_value={
        'days': 14,
        'series': [{'day': 10, 'coins_moved': 5, 'active_addresses': 2, 'new_addresses': 1}],
    })
    return idx


@pytest.fixture
def mock_dmint_contracts():
    mgr = Mock()
    mgr.get_contracts_simple = Mock(return_value=[])
    mgr.get_contracts_extended = Mock(return_value={'contracts': []})
    mgr.get_contract = Mock(return_value=None)
    mgr.get_contracts_by_algorithm = Mock(return_value=[])
    mgr.get_most_profitable = Mock(return_value=[])
    return mgr


@pytest.fixture
def mock_db():
    db = Mock()
    db.db_height = 100000
    db.db_engine = 'rocksdb'
    return db


@pytest.fixture
def mock_daemon():
    return Mock()


@pytest.fixture
def mock_royalty_index():
    idx = Mock()
    idx.enabled = True
    idx.listing_cache = {}
    idx.get_listings = Mock(return_value=[{
        'txid': 'ab' * 32, 'vout': 0, 'ref': 'aa' * 32 + '_0',
        'price': 100000, 'royalty_total': 5000, 'value': 600, 'status': 'active',
    }])
    return idx


@pytest.fixture
def client(mock_glyph_index, mock_wave_index, mock_swap_index,
           mock_analytics_index, mock_dmint_contracts, mock_db, mock_daemon):
    """Create a TestClient with all mocks wired in (no royalty index)."""
    from electrumx.server.rest_api import app, set_indexer
    set_indexer(
        mock_glyph_index, mock_db, mock_daemon,
        wave_index=mock_wave_index,
        swap_index=mock_swap_index,
        analytics_index=mock_analytics_index,
        dmint_contracts=mock_dmint_contracts,
    )
    return TestClient(app)


@pytest.fixture
def royalty_client(mock_glyph_index, mock_wave_index, mock_swap_index,
                   mock_analytics_index, mock_dmint_contracts, mock_db,
                   mock_daemon, mock_royalty_index):
    """TestClient with the royalty index wired in."""
    from electrumx.server.rest_api import app, set_indexer
    set_indexer(
        mock_glyph_index, mock_db, mock_daemon,
        wave_index=mock_wave_index,
        swap_index=mock_swap_index,
        royalty_index=mock_royalty_index,
        analytics_index=mock_analytics_index,
        dmint_contracts=mock_dmint_contracts,
    )
    return TestClient(app)


class TestRoyaltyEndpoints:

    def test_listings_global(self, royalty_client, mock_royalty_index):
        resp = royalty_client.get('/royalties/listings')
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list) and len(data) == 1
        assert data[0]['price'] == 100000
        mock_royalty_index.get_listings.assert_called_with(
            ref=None, seller_scripthash=None, limit=100, offset=0)

    def test_listings_by_ref(self, royalty_client, mock_royalty_index):
        ref = 'cd' * 36  # 72-hex LE ref
        resp = royalty_client.get('/royalties/listings', params={'ref': ref})
        assert resp.status_code == 200
        assert mock_royalty_index.get_listings.call_args.kwargs['ref'] == bytes.fromhex(ref)

    def test_listings_bad_seller(self, royalty_client):
        resp = royalty_client.get('/royalties/listings', params={'seller': 'ab'})
        assert resp.status_code == 400  # not a 32-byte scripthash

    def test_listings_unavailable_503(self, client):
        # the default client wires no royalty index -> 503
        resp = client.get('/royalties/listings')
        assert resp.status_code == 503

    def test_stats(self, royalty_client):
        resp = royalty_client.get('/royalties/stats')
        assert resp.status_code == 200
        assert resp.json()['enabled'] is True

    def test_status_reports_royalty_indexing(self, royalty_client):
        resp = royalty_client.get('/status')
        assert resp.status_code == 200
        assert resp.json()['royalty_indexing'] is True


# ===========================================================================
# Health & Status
# ===========================================================================

class TestHealthEndpoints:

    def test_health(self, client):
        resp = client.get('/health')
        assert resp.status_code == 200
        data = resp.json()
        assert data['status'] == 'healthy'
        assert 'uptime_seconds' in data
        assert data['database'] == 'connected'

    def test_health_live(self, client):
        resp = client.get('/health/live')
        assert resp.status_code == 200
        assert resp.json()['status'] == 'alive'

    def test_health_ready(self, client):
        resp = client.get('/health/ready')
        assert resp.status_code == 200
        assert resp.json()['height'] == 100000

    def test_health_db(self, client):
        resp = client.get('/health/db')
        assert resp.status_code == 200
        data = resp.json()
        assert data['status'] == 'connected'
        assert data['height'] == 100000

    def test_status(self, client):
        resp = client.get('/status')
        assert resp.status_code == 200
        data = resp.json()
        assert data['api_version'] == '2.0.0'
        assert data['glyph_indexing'] is True
        assert data['wave_indexing'] is True
        assert data['swap_indexing'] is True
        assert data['analytics_indexing'] is True
        assert data['dmint_contracts'] is True


class TestChainAnalyticsEndpoints:

    def test_get_analytics_stats(self, client, mock_analytics_index):
        resp = client.get('/analytics/stats')
        assert resp.status_code == 200
        assert resp.json()['rich_list_entries'] == 2
        mock_analytics_index.get_stats.assert_called_once()

    def test_get_balance_distribution(self, client, mock_analytics_index):
        resp = client.get('/analytics/balance-distribution')
        assert resp.status_code == 200
        assert resp.json()['10-100'] == 2
        mock_analytics_index.get_balance_distribution.assert_called_once()

    def test_get_supply_aging(self, client, mock_analytics_index):
        resp = client.get('/analytics/supply-aging')
        assert resp.status_code == 200
        assert resp.json()['<1d'] == 123
        mock_analytics_index.get_supply_aging.assert_called_once()

    def test_get_top_addresses(self, client, mock_analytics_index):
        resp = client.get('/analytics/top-addresses?limit=25&offset=5')
        assert resp.status_code == 200
        assert resp.json()['rows'][0]['address'] == 'addr1'
        mock_analytics_index.get_top_addresses.assert_called_once_with(limit=25, offset=5)

    def test_get_movement(self, client, mock_analytics_index):
        resp = client.get('/analytics/movement?days=14')
        assert resp.status_code == 200
        assert resp.json()['days'] == 14
        mock_analytics_index.get_movement.assert_called_once_with(days=14)


# ===========================================================================
# Glyph Token Endpoints
# ===========================================================================

class TestGlyphEndpoints:

    def test_get_all_glyphs(self, client, mock_glyph_index):
        mock_glyph_index.get_all_tokens_summary.return_value = {
            'total': 2, 'tokens': [{'ref': 'a'*72}], 'limit': 100, 'offset': 0,
        }
        resp = client.get('/glyphs')
        assert resp.status_code == 200
        mock_glyph_index.get_all_tokens_summary.assert_called_once()

    def test_get_all_glyphs_with_type_filter(self, client, mock_glyph_index):
        resp = client.get('/glyphs?token_type=1')
        assert resp.status_code == 200
        mock_glyph_index.get_all_tokens_summary.assert_called_with(
            limit=100, offset=0, token_type=1,
        )

    def test_search_glyphs(self, client, mock_glyph_index):
        mock_glyph_index.search_tokens.return_value = [{'ref': 'a'*72, 'name': 'Test'}]
        resp = client.get('/glyphs/search?q=Test')
        assert resp.status_code == 200
        data = resp.json()
        assert data['query'] == 'Test'
        assert data['count'] == 1

    def test_search_glyphs_with_protocols(self, client, mock_glyph_index):
        resp = client.get('/glyphs/search?q=Token&protocols=1,4')
        assert resp.status_code == 200
        mock_glyph_index.search_tokens.assert_called_with('Token', protocols=[1, 4], limit=50)

    def test_get_glyph_stats(self, client, mock_glyph_index):
        mock_glyph_index.get_stats.return_value = {
            'enabled': True, 'total_tokens': 42,
            'by_type': {'FT': 20, 'NFT': 15, 'DAT': 5, 'dMint': 2},
        }
        resp = client.get('/glyphs/stats')
        assert resp.status_code == 200
        assert resp.json()['total_tokens'] == 42

    def test_get_glyphs_by_type(self, client, mock_glyph_index):
        mock_glyph_index.get_tokens_by_type.return_value = {'tokens': [], 'next_cursor': None}
        resp = client.get('/glyphs/by-type/1')
        assert resp.status_code == 200
        mock_glyph_index.get_tokens_by_type.assert_called_with(1, limit=100, cursor=None)

    def test_get_glyph_by_ref(self, client, mock_glyph_index):
        ref = _make_ref()
        token = Mock()
        mock_glyph_index.get_token.return_value = token
        mock_glyph_index._token_to_dict.return_value = {'ref': ref, 'name': 'Test'}
        resp = client.get(f'/glyphs/{ref}')
        assert resp.status_code == 200
        assert resp.json()['name'] == 'Test'

    def test_get_glyph_not_found(self, client, mock_glyph_index):
        ref = _make_ref()
        mock_glyph_index.get_token.return_value = None
        resp = client.get(f'/glyphs/{ref}')
        assert resp.status_code == 404

    def test_get_glyph_invalid_ref(self, client):
        resp = client.get('/glyphs/invalid')
        assert resp.status_code == 422  # FastAPI validation error (length)


# ===========================================================================
# Token Analytics Endpoints
# ===========================================================================

class TestTokenAnalyticsEndpoints:

    def test_get_holders(self, client, mock_glyph_index):
        ref = _make_ref()
        mock_glyph_index.get_token_holders.return_value = {
            'ref': ref, 'total_holders': 5, 'holders': [],
        }
        resp = client.get(f'/tokens/{ref}/holders')
        assert resp.status_code == 200
        assert resp.json()['total_holders'] == 5

    def test_get_supply(self, client, mock_glyph_index):
        ref = _make_ref()
        mock_glyph_index.get_token_supply.return_value = {
            'ref': ref, 'total_supply': 21000000, 'circulating_supply': 10500000,
        }
        resp = client.get(f'/tokens/{ref}/supply')
        assert resp.status_code == 200
        assert resp.json()['total_supply'] == 21000000

    def test_get_supply_not_found(self, client, mock_glyph_index):
        ref = _make_ref()
        mock_glyph_index.get_token_supply.return_value = None
        resp = client.get(f'/tokens/{ref}/supply')
        assert resp.status_code == 404

    def test_get_burns(self, client, mock_glyph_index):
        ref = _make_ref()
        resp = client.get(f'/tokens/{ref}/burns')
        assert resp.status_code == 200

    def test_get_trades(self, client, mock_glyph_index):
        ref = _make_ref()
        resp = client.get(f'/tokens/{ref}/trades?limit=10')
        assert resp.status_code == 200

    def test_get_top_holders(self, client, mock_glyph_index):
        ref = _make_ref()
        resp = client.get(f'/tokens/{ref}/top-holders?limit=50')
        assert resp.status_code == 200

    def test_get_history(self, client, mock_glyph_index):
        ref = _make_ref()
        mock_glyph_index.get_token_history.return_value = [
            {'height': 100, 'txid': 'a'*64, 'event': 'deploy'},
        ]
        resp = client.get(f'/tokens/{ref}/history')
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_get_metadata(self, client, mock_glyph_index):
        ref = _make_ref()
        token = Mock()
        token.metadata_hash = b'\x01' * 32
        mock_glyph_index.get_token.return_value = token
        mock_glyph_index.get_metadata.return_value = {'v': 2, 'name': 'Test'}
        resp = client.get(f'/tokens/{ref}/metadata')
        assert resp.status_code == 200
        assert resp.json()['metadata']['name'] == 'Test'

    def test_get_metadata_no_hash(self, client, mock_glyph_index):
        ref = _make_ref()
        token = Mock()
        token.metadata_hash = None
        mock_glyph_index.get_token.return_value = token
        resp = client.get(f'/tokens/{ref}/metadata')
        assert resp.status_code == 200
        assert resp.json()['metadata'] is None


# ===========================================================================
# dMint Endpoints
# ===========================================================================

class TestDMintEndpoints:

    def test_get_contracts_extended(self, client, mock_dmint_contracts):
        mock_dmint_contracts.get_contracts_extended.return_value = {
            'contracts': [{'ref': 'a'*72, 'algorithm': 1}],
        }
        resp = client.get('/dmint/contracts?format=extended')
        assert resp.status_code == 200

    def test_get_contracts_v2_default(self, client, mock_dmint_contracts):
        mock_dmint_contracts.get_contracts_v2.return_value = {
            'items': [{'ref': 'a'*72, 'algo_id': 1}],
            'next_cursor': None,
        }
        resp = client.get('/dmint/contracts')
        assert resp.status_code == 200
        mock_dmint_contracts.get_contracts_v2.assert_called_once()

    def test_get_contracts_simple(self, client, mock_dmint_contracts):
        resp = client.get('/dmint/contracts?format=simple')
        assert resp.status_code == 200
        mock_dmint_contracts.get_contracts_simple.assert_called_once()

    def test_get_single_contract(self, client, mock_dmint_contracts):
        ref = _make_ref()
        mock_dmint_contracts.get_contract.return_value = {'ref': ref}
        resp = client.get(f'/dmint/contracts/{ref}')
        assert resp.status_code == 200

    def test_get_single_contract_not_found(self, client, mock_dmint_contracts):
        ref = _make_ref()
        mock_dmint_contracts.get_contract.return_value = None
        resp = client.get(f'/dmint/contracts/{ref}')
        assert resp.status_code == 404

    def test_get_single_contract_icon_debug(self, client, mock_dmint_contracts):
        ref = _make_ref()
        mock_dmint_contracts.get_contract.return_value = {
            'ref': ref,
            'icon_type': 'image/png',
            'icon_url': 'https://example.com/icon.png',
            'icon_ref': 'ipfs://bafybeigdyrzt',
            'icon_data': 'aabbccdd',
        }
        resp = client.get(f'/dmint/contracts/{ref}/icon-debug')
        assert resp.status_code == 200
        data = resp.json()
        assert data['ref'] == ref
        assert data['icon_type'] == 'image/png'
        assert data['icon_url'] == 'https://example.com/icon.png'
        assert data['icon_ref'] == 'ipfs://bafybeigdyrzt'
        assert data['icon_data'] == 'aabbccdd'

    def test_get_contract_icon_serves_bytes(self, client, mock_dmint_contracts):
        ref = _make_ref()
        mock_dmint_contracts.get_contract.return_value = {
            'ref': ref,
            'icon_type': 'image/png',
            'icon_data': 'aabbccdd',
        }
        resp = client.get(f'/dmint/contracts/{ref}/icon')
        assert resp.status_code == 200
        assert resp.headers['content-type'] == 'image/png'
        assert resp.content == bytes.fromhex('aabbccdd')
        assert 'max-age' in resp.headers.get('cache-control', '')

    def test_get_contract_icon_404_when_no_embedded(self, client, mock_dmint_contracts):
        ref = _make_ref()
        mock_dmint_contracts.get_contract.return_value = {
            'ref': ref,
            'icon_url': 'https://example.com/icon.png',
        }
        resp = client.get(f'/dmint/contracts/{ref}/icon')
        assert resp.status_code == 404

    def test_get_contract_icon_404_when_contract_missing(self, client, mock_dmint_contracts):
        ref = _make_ref()
        mock_dmint_contracts.get_contract.return_value = None
        resp = client.get(f'/dmint/contracts/{ref}/icon')
        assert resp.status_code == 404

    def test_contracts_list_strips_icon_hex_and_redirects_embedded(self, client, mock_dmint_contracts):
        ref = 'a' * 72
        mock_dmint_contracts.get_contracts_v2.return_value = {
            'items': [{
                'token_ref': ref,
                'icon': {'type': 'image/png', 'url': None, 'data_hex': 'aabbcc'},
            }],
            'cursor_next': None,
        }
        # Default (include_icon_data=false): hex stripped, embedded icon -> lazy URL.
        resp = client.get('/dmint/contracts')
        assert resp.status_code == 200
        icon = resp.json()['items'][0]['icon']
        assert icon['data_hex'] is None
        assert icon['url'] == f'/dmint/contracts/{ref}/icon'

    def test_contracts_list_keeps_icon_hex_when_requested(self, client, mock_dmint_contracts):
        ref = 'a' * 72
        mock_dmint_contracts.get_contracts_v2.return_value = {
            'items': [{
                'token_ref': ref,
                'icon': {'type': 'image/png', 'url': None, 'data_hex': 'aabbcc'},
            }],
            'cursor_next': None,
        }
        resp = client.get('/dmint/contracts?include_icon_data=true')
        assert resp.status_code == 200
        icon = resp.json()['items'][0]['icon']
        assert icon['data_hex'] == 'aabbcc'

    def test_get_algorithms(self, client):
        resp = client.get('/dmint/algorithms')
        assert resp.status_code == 200
        data = resp.json()
        assert len(data['algorithms']) == 5
        assert data['algorithms'][0]['name'] == 'SHA256D'
        assert data['algorithms'][1]['name'] == 'BLAKE3'
        assert data['algorithms'][2]['name'] == 'KangarooTwelve'
        assert len(data['daa_modes']) == 5

    def test_get_by_algorithm(self, client, mock_dmint_contracts):
        resp = client.get('/dmint/by-algorithm/1')
        assert resp.status_code == 200
        mock_dmint_contracts.get_contracts_by_algorithm.assert_called_with(1)

    def test_get_profitable(self, client, mock_dmint_contracts):
        resp = client.get('/dmint/profitable?limit=5')
        assert resp.status_code == 200
        mock_dmint_contracts.get_most_profitable.assert_called_with(limit=5)


# ===========================================================================
# WAVE Endpoints
# ===========================================================================

class TestWAVEEndpoints:

    def test_resolve_name(self, client, mock_wave_index):
        mock_wave_index.resolve.return_value = {
            'name': 'alice', 'owner': 'ab' * 32, 'zone': {'address': '1Alice...'},
        }
        resp = client.get('/wave/resolve/alice')
        assert resp.status_code == 200
        assert resp.json()['name'] == 'alice'

    def test_resolve_not_found(self, client, mock_wave_index):
        mock_wave_index.resolve.return_value = None
        resp = client.get('/wave/resolve/nobody')
        assert resp.status_code == 200
        data = resp.json()
        assert data['available'] is True
        assert data['resolved'] is False

    def test_check_available(self, client, mock_wave_index):
        resp = client.get('/wave/available/newname')
        assert resp.status_code == 200

    def test_subdomains(self, client, mock_wave_index):
        resp = client.get('/wave/alice/subdomains')
        assert resp.status_code == 200

    def test_reverse_lookup(self, client, mock_wave_index):
        sh = 'ab' * 32
        resp = client.get(f'/wave/reverse/{sh}')
        assert resp.status_code == 200

    def test_reverse_lookup_invalid(self, client):
        resp = client.get('/wave/reverse/xyz')
        assert resp.status_code == 422  # FastAPI validation (length)

    def test_wave_stats(self, client, mock_wave_index):
        resp = client.get('/wave/stats')
        assert resp.status_code == 200


# ===========================================================================
# Swap Endpoints
# ===========================================================================

class TestSwapEndpoints:

    def test_get_orders(self, client, mock_swap_index):
        resp = client.get('/swaps/orders')
        assert resp.status_code == 200

    def test_get_orders_with_pair(self, client, mock_swap_index):
        base = _make_ref()
        quote = _make_ref('bb' * 32)
        resp = client.get(f'/swaps/orders?base_ref={base}&quote_ref={quote}')
        assert resp.status_code == 200
        mock_swap_index.get_orderbook.assert_called_once()

    def test_get_single_order(self, client, mock_swap_index):
        oid = _make_ref()
        mock_swap_index.get_order.return_value = {'order_id': oid}
        resp = client.get(f'/swaps/orders/{oid}')
        assert resp.status_code == 200

    def test_get_single_order_not_found(self, client, mock_swap_index):
        oid = _make_ref()
        mock_swap_index.get_order.return_value = None
        resp = client.get(f'/swaps/orders/{oid}')
        assert resp.status_code == 404

    def test_swap_history_requires_base_ref(self, client, mock_swap_index):
        resp = client.get('/swaps/history')
        assert resp.status_code == 200
        assert resp.json().get('error')  # base_ref required

    def test_swap_history_with_base_ref(self, client, mock_swap_index):
        base = _make_ref()
        resp = client.get(f'/swaps/history?base_ref={base}')
        assert resp.status_code == 200
        mock_swap_index.get_swap_history.assert_called_once()

    def test_swap_stats(self, client, mock_swap_index):
        resp = client.get('/swaps/stats')
        assert resp.status_code == 200
        assert resp.json()['enabled'] is True


# ===========================================================================
# R8 — Proxy-Aware Rate Limiting (unit tests for _get_client_ip)
# ===========================================================================

class TestProxyAwareClientIP:
    """R8: _get_client_ip() must honour X-Forwarded-For when TRUST_PROXY=1."""

    def _make_request(self, headers: dict, client_host: str = '127.0.0.1'):
        """Build a minimal Mock Request with the given headers and client."""
        req = Mock()
        req.headers = headers
        client = Mock()
        client.host = client_host
        req.client = client
        return req

    def test_no_proxy_returns_client_host(self):
        """Without TRUST_PROXY, always return request.client.host."""
        from electrumx.server import rest_api
        original = rest_api._TRUST_PROXY
        try:
            rest_api._TRUST_PROXY = False
            req = self._make_request({}, client_host='203.0.113.5')
            assert rest_api._get_client_ip(req) == '203.0.113.5'
        finally:
            rest_api._TRUST_PROXY = original

    def test_trust_proxy_reads_x_forwarded_for(self):
        """With TRUST_PROXY=True, take the Nth-from-right entry (skip N innermost proxies).

        idx = max(0, len(parts) - TRUST_PROXY_HOPS)
        With 2 entries and hops=1: idx = max(0, 2-1) = 1 → second entry is the
        last trusted hop (the entry just beyond our own proxy layer).
        """
        from electrumx.server import rest_api
        original_tp = rest_api._TRUST_PROXY
        original_hops = rest_api._TRUST_PROXY_HOPS
        try:
            rest_api._TRUST_PROXY = True
            rest_api._TRUST_PROXY_HOPS = 1
            req = self._make_request(
                {'x-forwarded-for': '203.0.113.10, 10.0.0.1'},
                client_host='127.0.0.1',
            )
            # 2 parts, hops=1 → idx = max(0, 2-1) = 1 → '10.0.0.1'
            ip = rest_api._get_client_ip(req)
            assert ip == '10.0.0.1'
        finally:
            rest_api._TRUST_PROXY = original_tp
            rest_api._TRUST_PROXY_HOPS = original_hops

    def test_trust_proxy_two_hops(self):
        """TRUST_PROXY_HOPS=2 skips two innermost proxy entries."""
        from electrumx.server import rest_api
        original_tp = rest_api._TRUST_PROXY
        original_hops = rest_api._TRUST_PROXY_HOPS
        try:
            rest_api._TRUST_PROXY = True
            rest_api._TRUST_PROXY_HOPS = 2
            req = self._make_request(
                {'x-forwarded-for': '198.51.100.7, 10.0.0.2, 10.0.0.1'},
                client_host='127.0.0.1',
            )
            # 3 entries, hops=2 → idx = max(0, 3-2) = 1 → '10.0.0.2'
            ip = rest_api._get_client_ip(req)
            assert ip == '10.0.0.2'
        finally:
            rest_api._TRUST_PROXY = original_tp
            rest_api._TRUST_PROXY_HOPS = original_hops

    def test_trust_proxy_falls_back_to_x_real_ip(self):
        """When X-Forwarded-For is absent, fall back to X-Real-IP."""
        from electrumx.server import rest_api
        original_tp = rest_api._TRUST_PROXY
        try:
            rest_api._TRUST_PROXY = True
            req = self._make_request(
                {'x-real-ip': '203.0.113.99'},
                client_host='127.0.0.1',
            )
            ip = rest_api._get_client_ip(req)
            assert ip == '203.0.113.99'
        finally:
            rest_api._TRUST_PROXY = original_tp

    def test_trust_proxy_falls_back_to_client_host_when_no_headers(self):
        """When proxy headers are absent, fall back to request.client.host."""
        from electrumx.server import rest_api
        original_tp = rest_api._TRUST_PROXY
        try:
            rest_api._TRUST_PROXY = True
            req = self._make_request({}, client_host='10.0.0.5')
            ip = rest_api._get_client_ip(req)
            assert ip == '10.0.0.5'
        finally:
            rest_api._TRUST_PROXY = original_tp

    def test_trust_proxy_single_entry_forwarded_for(self):
        """Single-entry X-Forwarded-For with TRUST_PROXY_HOPS=1."""
        from electrumx.server import rest_api
        original_tp = rest_api._TRUST_PROXY
        original_hops = rest_api._TRUST_PROXY_HOPS
        try:
            rest_api._TRUST_PROXY = True
            rest_api._TRUST_PROXY_HOPS = 1
            req = self._make_request(
                {'x-forwarded-for': '192.0.2.42'},
                client_host='127.0.0.1',
            )
            ip = rest_api._get_client_ip(req)
            assert ip == '192.0.2.42'
        finally:
            rest_api._TRUST_PROXY = original_tp
            rest_api._TRUST_PROXY_HOPS = original_hops

    def test_rate_limit_uses_forwarded_ip_not_localhost(self, monkeypatch):
        """_rate_limit uses the real client IP (not 127.0.0.1) when TRUST_PROXY active."""
        from electrumx.server import rest_api
        # Patch module-level state so this test is isolated
        monkeypatch.setattr(rest_api, '_TRUST_PROXY', True)
        monkeypatch.setattr(rest_api, '_TRUST_PROXY_HOPS', 1)
        monkeypatch.setattr(rest_api, '_rate_buckets', {})

        req = Mock()
        req.headers = {'x-forwarded-for': '203.0.113.77'}
        client = Mock()
        client.host = '127.0.0.1'
        req.client = client

        # Call _get_client_ip directly — the forwarded IP must be returned
        ip = rest_api._get_client_ip(req)
        assert ip == '203.0.113.77'
        # Also confirm 127.0.0.1 is not the result (the key point of R8)
        assert ip != '127.0.0.1'


# ===========================================================================
# XFF-spoof hardening — trusted-proxy peer gate on _get_client_ip
# (mirrors the ElectrumX rate-limiter fix, commit c4637e6)
# ===========================================================================

class TestProxyTrustedPeerGate:
    """REST :8000 is published on 0.0.0.0, so a client connecting DIRECTLY
    could set `X-Forwarded-For: <victim>` to evade its own per-IP rate limit or
    poison a victim's REST bucket. _get_client_ip must honour the forwarded
    chain ONLY when request.client.host is itself a trusted proxy."""

    def _make_request(self, headers: dict, client_host: str):
        req = Mock()
        req.headers = headers
        client = Mock()
        client.host = client_host
        req.client = client
        return req

    def test_xff_honoured_when_peer_is_trusted_proxy(self, monkeypatch):
        """Peer IS the configured reverse proxy -> its X-Forwarded-For is trusted."""
        from electrumx.server import rest_api
        monkeypatch.setattr(rest_api, '_TRUST_PROXY', True)
        monkeypatch.setattr(rest_api, '_TRUST_PROXY_HOPS', 1)
        monkeypatch.setattr(
            rest_api, '_TRUSTED_PROXIES',
            _IPRateLimiter._parse_networks('172.18.0.0/16'),
        )
        req = self._make_request(
            {'x-forwarded-for': '203.0.113.7, 10.0.0.1'},
            client_host='172.18.0.3',  # the Caddy container's bridge IP
        )
        # hops=1 -> right-most appended hop is the trusted proxy's value.
        assert rest_api._get_client_ip(req) == '10.0.0.1'

    def test_xff_ignored_when_peer_is_untrusted_direct_client(self, monkeypatch):
        """A direct client (peer NOT in the allowlist) cannot spoof XFF even
        with TRUST_PROXY=1; the raw socket peer is used instead."""
        from electrumx.server import rest_api
        monkeypatch.setattr(rest_api, '_TRUST_PROXY', True)
        monkeypatch.setattr(rest_api, '_TRUST_PROXY_HOPS', 1)
        monkeypatch.setattr(
            rest_api, '_TRUSTED_PROXIES',
            _IPRateLimiter._parse_networks('172.18.0.0/16'),
        )
        attacker = self._make_request(
            {'x-forwarded-for': '203.0.113.7'},  # spoofed victim IP
            client_host='198.51.100.99',         # real direct peer to :8000
        )
        ip = rest_api._get_client_ip(attacker)
        # The spoofed victim must NOT become the rate-limit key.
        assert ip == '198.51.100.99'
        assert ip != '203.0.113.7'

    def test_x_real_ip_ignored_from_untrusted_peer(self, monkeypatch):
        """The same gate applies to the x-real-ip fallback header."""
        from electrumx.server import rest_api
        monkeypatch.setattr(rest_api, '_TRUST_PROXY', True)
        monkeypatch.setattr(
            rest_api, '_TRUSTED_PROXIES',
            _IPRateLimiter._parse_networks('172.18.0.0/16'),
        )
        attacker = self._make_request(
            {'x-real-ip': '203.0.113.7'},
            client_host='198.51.100.99',
        )
        assert rest_api._get_client_ip(attacker) == '198.51.100.99'

    def test_default_allowlist_trusts_rfc1918_peer_not_public(self, monkeypatch):
        """Default allowlist (loopback + RFC1918): a bridge peer is trusted, a
        public direct peer is not."""
        from electrumx.server import rest_api
        monkeypatch.setattr(rest_api, '_TRUST_PROXY', True)
        monkeypatch.setattr(rest_api, '_TRUST_PROXY_HOPS', 1)
        monkeypatch.setattr(
            rest_api, '_TRUSTED_PROXIES',
            _IPRateLimiter._parse_networks(_DEFAULT_TRUSTED_PROXIES),
        )
        proxy = self._make_request(
            {'x-forwarded-for': '203.0.113.7'}, client_host='172.18.0.9')
        assert rest_api._get_client_ip(proxy) == '203.0.113.7'
        direct = self._make_request(
            {'x-forwarded-for': '203.0.113.7'}, client_host='8.8.8.8')
        assert rest_api._get_client_ip(direct) == '8.8.8.8'

    def test_rate_limit_buckets_keyed_per_attacker_not_victim(self, monkeypatch):
        """End-to-end: an untrusted direct client spoofing XFF gets bucketed
        under its OWN peer, leaving the victim's bucket untouched."""
        from electrumx.server import rest_api
        monkeypatch.setattr(rest_api, '_TRUST_PROXY', True)
        monkeypatch.setattr(rest_api, '_TRUST_PROXY_HOPS', 1)
        monkeypatch.setattr(
            rest_api, '_TRUSTED_PROXIES',
            _IPRateLimiter._parse_networks('172.18.0.0/16'),
        )
        monkeypatch.setattr(rest_api, '_rate_buckets', {})
        monkeypatch.setenv('REST_RATE_LIMIT_PER_MIN', '600')

        attacker = self._make_request(
            {'x-forwarded-for': '203.0.113.7'},  # spoofed victim
            client_host='198.51.100.99',
        )
        rest_api._rate_limit(attacker)
        # The bucket was created under the attacker's real peer, NOT the victim.
        assert '198.51.100.99' in rest_api._rate_buckets
        assert '203.0.113.7' not in rest_api._rate_buckets


# ===========================================================================
# Edge Cases
# ===========================================================================

class TestEdgeCases:

    def test_invalid_hex_ref(self, client):
        bad_ref = 'zz' * 36
        resp = client.get(f'/glyphs/{bad_ref}')
        assert resp.status_code == 400

    def test_pagination_limits(self, client, mock_glyph_index):
        resp = client.get('/glyphs?limit=501')
        assert resp.status_code == 422  # exceeds le=500

    def test_offset_negative(self, client, mock_glyph_index):
        resp = client.get('/glyphs?offset=-1')
        assert resp.status_code == 422  # below ge=0


# ===========================================================================
# M2 — Internal error leakage (info disclosure)
# ===========================================================================

class TestErrorSanitization:
    """M2: 500/raw-exception paths must return a generic message, never leak
    the underlying DB/daemon exception string to the client."""

    _SECRET = 'SECRET_DB_PATH /var/lib/rxindexer/rocksdb corrupted at offset 0xdeadbeef'

    def test_500_returns_generic_detail_not_internal_string(self, client, mock_glyph_index):
        # Force the handler's inner call to raise with a sensitive message.
        mock_glyph_index.get_all_tokens_summary.side_effect = RuntimeError(self._SECRET)
        resp = client.get('/glyphs')
        assert resp.status_code == 500
        detail = resp.json()['detail']
        assert detail == 'Internal error'
        # The leaked internals must NOT appear anywhere in the response body.
        assert 'SECRET_DB_PATH' not in resp.text
        assert 'rocksdb' not in resp.text
        assert 'deadbeef' not in resp.text

    def test_transaction_error_does_not_leak_daemon_string(self, client, mock_daemon):
        # The daemon raises with a sensitive internal message.
        async def _boom(*a, **k):
            raise RuntimeError(self._SECRET)
        mock_daemon.getrawtransaction = _boom
        resp = client.get('/transaction/' + 'ab' * 32)
        assert resp.status_code == 404
        detail = resp.json()['detail']
        assert detail == 'Transaction not found'
        assert 'SECRET_DB_PATH' not in resp.text
        assert 'rocksdb' not in resp.text

    def test_analytics_500_returns_generic_detail(self, client, mock_analytics_index):
        mock_analytics_index.get_top_addresses.side_effect = RuntimeError(self._SECRET)
        resp = client.get('/analytics/top-addresses?limit=10&offset=0')
        assert resp.status_code == 500
        assert resp.json()['detail'] == 'Internal error'
        assert 'SECRET_DB_PATH' not in resp.text


# ===========================================================================
# M1 — Analytics rich-list offset cap (DoS)
# ===========================================================================

class TestTopAddressesOffsetCap:
    """M1: `offset` on the public /analytics/top-addresses path must be bounded
    so an attacker can't rotate it to bust the cache and force full scans."""

    def test_oversized_offset_rejected(self, client, mock_analytics_index):
        from electrumx.server.rest_api import _TOP_ADDRESSES_MAX_OFFSET
        too_big = _TOP_ADDRESSES_MAX_OFFSET + 1
        resp = client.get(f'/analytics/top-addresses?limit=10&offset={too_big}')
        assert resp.status_code == 422  # exceeds le bound
        # The expensive index method must never be invoked for a rejected request.
        mock_analytics_index.get_top_addresses.assert_not_called()

    def test_max_offset_accepted(self, client, mock_analytics_index):
        from electrumx.server.rest_api import _TOP_ADDRESSES_MAX_OFFSET
        resp = client.get(
            f'/analytics/top-addresses?limit=10&offset={_TOP_ADDRESSES_MAX_OFFSET}'
        )
        assert resp.status_code == 200

    def test_scan_cached_across_offset_rotation(self, client):
        """End-to-end: rotating offset hits the cached scan, not a fresh scan.

        Drives a real AnalyticsIndex with an iterator that counts BALANCE-prefix
        scans, then rotates offset across several requests.
        """
        import struct as _struct
        from electrumx.server import rest_api
        from electrumx.server.analytics_index import AnalyticsIndex, AnalyticsDBKeys

        scan_count = {'n': 0}

        class _CountingDB:
            def __init__(self):
                self._store = {}

            def get(self, key):
                return self._store.get(key)

            def iterator(self, prefix=b'', reverse=False, include_value=True):
                if prefix == AnalyticsDBKeys.BALANCE:
                    scan_count['n'] += 1
                items = [(k, v) for k, v in self._store.items() if k.startswith(prefix)]
                items.sort(key=lambda kv: kv[0], reverse=reverse)
                return iter(items)

        class _Coin:
            VALUE_PER_COIN = 100_000_000
            P2PKH_VERBYTE = b'\x00'
            P2SH_VERBYTES = [b'\x05']

        class _Env:
            analytics_index = True
            reorg_limit = 10
            coin = _Coin()

        class _DB:
            def __init__(self):
                self.utxo_db = _CountingDB()
                self.db_height = 100

        db = _DB()
        for i in range(40):
            hashX = bytes([i % 256]) * 11 + i.to_bytes(2, 'big')
            db.utxo_db._store[AnalyticsDBKeys.BALANCE + hashX] = _struct.pack(
                '<Q', (i + 1) * _Coin.VALUE_PER_COIN
            )
        real_idx = AnalyticsIndex(db, _Env())

        # Swap the real analytics index in for this test only.
        prev = rest_api._analytics_index
        prev_cache = rest_api._cache
        try:
            rest_api._analytics_index = real_idx
            # Fresh REST TTL cache so prior tests' entries don't interfere.
            rest_api._cache = type(prev_cache)()
            for off in range(0, 25, 5):
                r = client.get(f'/analytics/top-addresses?limit=5&offset={off}')
                assert r.status_code == 200
            # 5 distinct offsets => 5 REST cache misses, but only ONE keyspace scan.
            assert scan_count['n'] == 1
        finally:
            rest_api._analytics_index = prev
            rest_api._cache = prev_cache


# ===========================================================================
# LOW — Unbounded query params (protocols / algorithm_ids)
# ===========================================================================

class TestQueryParamLengthBounds:

    def test_protocols_too_long_rejected(self, client, mock_glyph_index):
        long_val = '1,' * 200  # 400 chars > max_length 256
        resp = client.get(f'/glyphs/search?q=x&protocols={long_val}')
        assert resp.status_code == 422

    def test_algorithm_ids_too_long_rejected(self, client, mock_dmint_contracts):
        long_val = '1,' * 200  # > max_length 256
        resp = client.get(f'/dmint/contracts?algorithm_ids={long_val}')
        assert resp.status_code == 422

    def test_protocols_within_bound_accepted(self, client, mock_glyph_index):
        resp = client.get('/glyphs/search?q=x&protocols=1,2,3')
        assert resp.status_code == 200
