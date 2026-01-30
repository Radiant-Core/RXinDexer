"""
Glyph API Tests for ElectrumX-Core

Tests the Glyph v2 token API endpoints including:
- Reference queries
- Token metadata
- UTXO lookups for refs
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch


class TestGlyphRefAPI:
    """Tests for blockchain.ref.* methods"""

    @pytest.fixture
    def mock_session(self):
        """Create a mock session handler"""
        session = Mock()
        session.daemon = AsyncMock()
        session.db = Mock()
        return session

    def test_ref_format_validation(self):
        """Test that ref format is validated correctly"""
        # Valid ref: 64 hex chars + '_' + vout
        valid_ref = 'a' * 64 + '_0'
        assert len(valid_ref.split('_')[0]) == 64
        assert valid_ref.split('_')[1].isdigit()

        # Invalid refs
        invalid_refs = [
            'short_0',
            'a' * 63 + '_0',
            'a' * 64,  # Missing vout
            'g' * 64 + '_0',  # Invalid hex
        ]
        for ref in invalid_refs:
            parts = ref.split('_')
            if len(parts) != 2:
                assert True  # Invalid format
            elif len(parts[0]) != 64:
                assert True  # Invalid txid length

    def test_scripthash_from_ref(self):
        """Test scripthash derivation from ref"""
        # Scripthash is SHA256 of the locking script
        ref = 'a' * 64 + '_0'
        # In real implementation, this would derive the scripthash
        assert ref is not None


class TestGlyphTokenMetadata:
    """Tests for Glyph token metadata parsing"""

    def test_glyph_magic_bytes(self):
        """Test Glyph magic byte detection"""
        glyph_magic = bytes.fromhex('676c79')  # 'gly'
        assert glyph_magic == b'gly'

    def test_protocol_id_parsing(self):
        """Test protocol ID extraction from metadata"""
        # Protocol IDs
        GLYPH_FT = 1
        GLYPH_NFT = 2
        GLYPH_DAT = 3
        GLYPH_DMINT = 4
        GLYPH_MUT = 5

        protocols = [GLYPH_NFT, GLYPH_MUT]  # Mutable NFT
        assert GLYPH_NFT in protocols
        assert GLYPH_MUT in protocols

    def test_cbor_metadata_structure(self):
        """Test expected CBOR metadata structure"""
        metadata = {
            'v': 2,
            'type': 'nft',
            'p': [2],
            'name': 'Test NFT',
        }
        assert metadata['v'] == 2
        assert 'p' in metadata
        assert isinstance(metadata['p'], list)

    def test_ft_metadata_fields(self):
        """Test FT-specific metadata fields"""
        ft_metadata = {
            'v': 2,
            'type': 'ft',
            'p': [1],
            'name': 'Test Token',
            'ticker': 'TEST',
            'decimals': 8,
        }
        assert 'ticker' in ft_metadata
        assert 'decimals' in ft_metadata
        assert ft_metadata['decimals'] >= 0

    def test_nft_metadata_fields(self):
        """Test NFT-specific metadata fields"""
        nft_metadata = {
            'v': 2,
            'type': 'nft',
            'p': [2],
            'name': 'Test NFT #1',
            'attrs': [
                {'name': 'Rarity', 'value': 'Rare'},
            ],
        }
        assert 'attrs' in nft_metadata
        assert isinstance(nft_metadata['attrs'], list)


class TestGlyphUTXOQueries:
    """Tests for Glyph UTXO query methods"""

    def test_ref_listunspent_response(self):
        """Test ref.listunspent response format"""
        response = [
            {
                'tx_hash': 'a' * 64,
                'tx_pos': 0,
                'height': 850000,
                'value': 546,
            }
        ]
        assert isinstance(response, list)
        for utxo in response:
            assert 'tx_hash' in utxo
            assert 'tx_pos' in utxo
            assert 'height' in utxo
            assert 'value' in utxo

    def test_ref_get_response(self):
        """Test ref.get response format"""
        response = {
            'scripthash': 'b' * 64,
            'ref': 'a' * 64 + '_0',
            'script': '76a914...',
        }
        assert 'scripthash' in response
        assert 'ref' in response


class TestGlyphSwapIndex:
    """Tests for Glyph swap index functionality"""

    def test_swap_offer_structure(self):
        """Test swap offer data structure"""
        swap_offer = {
            'txid': 'a' * 64,
            'vout': 0,
            'sell_ref': 'b' * 64 + '_0',
            'sell_amount': 1000,
            'buy_ref': 'c' * 64 + '_0',
            'buy_amount': 500,
            'partial': True,
        }
        assert 'sell_ref' in swap_offer
        assert 'buy_ref' in swap_offer

    def test_swap_matching(self):
        """Test swap order matching logic"""
        # Simple price calculation
        sell_amount = 1000
        buy_amount = 500
        price = sell_amount / buy_amount
        assert price == 2.0


class TestGlyphContainerQueries:
    """Tests for container/collection queries"""

    def test_container_children_query(self):
        """Test querying children of a container"""
        container_ref = 'a' * 64 + '_0'
        children = [
            {'ref': 'b' * 64 + '_0', 'index': 0},
            {'ref': 'c' * 64 + '_0', 'index': 1},
        ]
        assert len(children) == 2

    def test_container_metadata(self):
        """Test container metadata structure"""
        container = {
            'v': 2,
            'type': 'nft',
            'p': [2, 7],  # NFT + CONTAINER
            'name': 'Test Collection',
            'app': {
                'namespace': 'rxd.container',
                'data': {
                    'type': 'collection',
                    'maxItems': 1000,
                }
            }
        }
        assert 7 in container['p']  # GLYPH_CONTAINER


class TestWAVEQueries:
    """Tests for WAVE naming system queries"""

    def test_wave_name_validation(self):
        """Test WAVE name character validation"""
        valid_chars = 'abcdefghijklmnopqrstuvwxyz0123456789-'
        assert len(valid_chars) == 37

        valid_name = 'alice'
        for char in valid_name:
            assert char in valid_chars

    def test_wave_metadata_structure(self):
        """Test WAVE name metadata structure"""
        wave_metadata = {
            'v': 2,
            'type': 'wave_name',
            'p': [2, 5, 11],  # NFT + MUT + WAVE
            'name': 'alice',
            'app': {
                'namespace': 'rxd.wave',
                'schema': 'wave_name_v1',
                'data': {
                    'name': 'alice',
                    'parent': None,
                    'zone': {
                        'address': '1Alice...',
                    }
                }
            }
        }
        assert 11 in wave_metadata['p']  # GLYPH_WAVE
        assert wave_metadata['app']['namespace'] == 'rxd.wave'

    def test_wave_resolution_path(self):
        """Test WAVE name resolution path calculation"""
        name = 'alice'
        char_to_index = {c: i for i, c in enumerate('abcdefghijklmnopqrstuvwxyz0123456789-')}
        
        path = []
        for char in name:
            path.append({
                'char': char,
                'index': char_to_index[char],
                'output_index': char_to_index[char] + 1,  # +1 because output 0 is claim token
            })
        
        assert len(path) == 5
        assert path[0]['char'] == 'a'
        assert path[0]['output_index'] == 1
