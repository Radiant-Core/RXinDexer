"""
REST API Tests for RXinDexer

Tests the FastAPI REST API endpoints for Glyph v2 tokens, dMint contracts,
WAVE naming, and Swap/DEX functionality.
"""

import pytest
import struct
from unittest.mock import Mock, MagicMock, patch

from fastapi.testclient import TestClient


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
    idx.get_open_orders = Mock(return_value={'orders': []})
    idx.get_order = Mock(return_value=None)
    idx.get_trade_history = Mock(return_value={'trades': []})
    idx.stats = Mock(return_value={'total_orders': 0})
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
def client(mock_glyph_index, mock_wave_index, mock_swap_index,
           mock_dmint_contracts, mock_db, mock_daemon):
    """Create a TestClient with all mocks wired in."""
    from electrumx.server.rest_api import app, set_indexer
    set_indexer(
        mock_glyph_index, mock_db, mock_daemon,
        wave_index=mock_wave_index,
        swap_index=mock_swap_index,
        dmint_contracts=mock_dmint_contracts,
    )
    return TestClient(app)


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
        assert data['dmint_contracts'] is True


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
        resp = client.get('/glyphs/by-type/1')
        assert resp.status_code == 200
        mock_glyph_index.get_tokens_by_type.assert_called_with(1, limit=100, offset=0)

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
        resp = client.get('/dmint/contracts')
        assert resp.status_code == 200

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

    def test_swap_history(self, client, mock_swap_index):
        resp = client.get('/swaps/history')
        assert resp.status_code == 200

    def test_swap_stats(self, client, mock_swap_index):
        resp = client.get('/swaps/stats')
        assert resp.status_code == 200


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
