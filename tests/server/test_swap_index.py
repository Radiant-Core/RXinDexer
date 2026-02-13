"""
Swap Index Tests for RXinDexer

Tests for the RSWP swap protocol parsing and indexing functionality including:
- RSWP v1 (legacy) format parsing
- RSWP v2 (extended) format parsing
- Script chunk parsing
- Order indexing and caching
"""

import pytest
from unittest.mock import Mock, MagicMock
import struct


# RSWP Protocol constants
RSWP_MAGIC = b'RSWP'
RSWP_VERSION_1 = 0x01
RSWP_VERSION_2 = 0x02
FLAG_HAS_WANT = 0x01

# OP codes
OP_RETURN = 0x6a
OP_0 = 0x00


def build_push_data(data: bytes) -> bytes:
    """Build a Bitcoin script push for the given data."""
    length = len(data)
    if length == 0:
        return bytes([OP_0])
    elif length <= 75:
        return bytes([length]) + data
    elif length <= 255:
        return bytes([0x4c, length]) + data
    elif length <= 65535:
        return bytes([0x4d]) + struct.pack('<H', length) + data
    else:
        return bytes([0x4e]) + struct.pack('<I', length) + data


def build_rswp_v1_script(token_id: bytes, utxo_hash: bytes, utxo_index: int,
                          price_terms: bytes, signature: bytes) -> bytes:
    """Build a RSWP v1 format OP_RETURN script."""
    script = bytes([OP_RETURN])
    script += build_push_data(RSWP_MAGIC)
    script += build_push_data(bytes([RSWP_VERSION_1]))
    script += build_push_data(bytes([0x01]))  # Type (legacy field)
    script += build_push_data(token_id)
    script += build_push_data(utxo_hash)
    script += build_push_data(bytes([utxo_index]) if utxo_index < 256 else struct.pack('<I', utxo_index))
    script += build_push_data(price_terms)
    script += build_push_data(signature)
    return script


def build_rswp_v2_script(token_id: bytes, utxo_hash: bytes, utxo_index: int,
                          price_terms: bytes, signature: bytes,
                          flags: int = 0, offered_type: int = 0, terms_type: int = 0,
                          want_token_id: bytes = None) -> bytes:
    """Build a RSWP v2 format OP_RETURN script."""
    script = bytes([OP_RETURN])
    script += build_push_data(RSWP_MAGIC)
    script += build_push_data(bytes([RSWP_VERSION_2]))
    script += build_push_data(bytes([flags]))
    script += build_push_data(bytes([offered_type]))
    script += build_push_data(bytes([terms_type]))
    script += build_push_data(token_id)
    if flags & FLAG_HAS_WANT and want_token_id:
        script += build_push_data(want_token_id)
    script += build_push_data(utxo_hash)
    script += build_push_data(bytes([utxo_index]) if utxo_index < 256 else struct.pack('<I', utxo_index))
    script += build_push_data(price_terms)
    script += build_push_data(signature)
    return script


class TestRSWPScriptBuilding:
    """Test the script building helpers."""

    def test_build_push_data_small(self):
        """Test push data for small values."""
        data = b'RSWP'
        result = build_push_data(data)
        assert result == bytes([4]) + data

    def test_build_push_data_empty(self):
        """Test push data for empty/zero."""
        result = build_push_data(b'')
        assert result == bytes([OP_0])

    def test_build_rswp_v1_script(self):
        """Test building a v1 script."""
        token_id = bytes(32)
        utxo_hash = bytes(32)
        script = build_rswp_v1_script(token_id, utxo_hash, 0, b'\x01\x02', b'\x03\x04')
        
        assert script[0] == OP_RETURN
        assert RSWP_MAGIC in script
        assert bytes([RSWP_VERSION_1]) in script

    def test_build_rswp_v2_script(self):
        """Test building a v2 script."""
        token_id = bytes(32)
        utxo_hash = bytes(32)
        script = build_rswp_v2_script(token_id, utxo_hash, 0, b'\x01\x02', b'\x03\x04')
        
        assert script[0] == OP_RETURN
        assert RSWP_MAGIC in script
        assert bytes([RSWP_VERSION_2]) in script

    def test_build_rswp_v2_with_want_token(self):
        """Test building a v2 script with want token."""
        token_id = bytes(32)
        want_token_id = bytes([0xff] * 32)
        utxo_hash = bytes(32)
        script = build_rswp_v2_script(
            token_id, utxo_hash, 0, b'\x01\x02', b'\x03\x04',
            flags=FLAG_HAS_WANT, want_token_id=want_token_id
        )
        
        assert script[0] == OP_RETURN
        assert want_token_id in script


class TestSwapIndexParsing:
    """Tests for SwapIndex RSWP parsing methods."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = Mock()
        db.utxo_db = MagicMock()
        db.utxo_db.get = Mock(return_value=None)
        db.utxo_db.iterator = Mock(return_value=iter([]))
        db.db_height = 100
        return db

    @pytest.fixture
    def mock_env(self):
        """Create a mock environment."""
        env = Mock()
        env.swap_index = True
        env.reorg_limit = 10
        return env

    @pytest.fixture
    def swap_index(self, mock_db, mock_env):
        """Create a SwapIndex instance."""
        from electrumx.server.swap_index import SwapIndex
        return SwapIndex(mock_db, mock_env)

    def test_swap_index_init(self, swap_index):
        """Test SwapIndex initialization."""
        assert swap_index.enabled is True
        assert swap_index.order_cache == {}

    def test_swap_index_disabled(self, mock_db, mock_env):
        """Test SwapIndex when disabled."""
        from electrumx.server.swap_index import SwapIndex
        mock_env.swap_index = False
        index = SwapIndex(mock_db, mock_env)
        assert index.enabled is False

    def test_parse_script_chunks_simple(self, swap_index):
        """Test parsing simple script chunks."""
        # OP_RETURN + 4-byte push "RSWP"
        script = bytes([OP_RETURN, 4]) + RSWP_MAGIC
        chunks = swap_index._parse_script_chunks(script)
        
        assert len(chunks) == 2
        assert chunks[0] == bytes([OP_RETURN])
        assert chunks[1] == RSWP_MAGIC

    def test_parse_script_chunks_op_pushdata1(self, swap_index):
        """Test parsing with OP_PUSHDATA1."""
        data = bytes(100)  # 100 zero bytes
        script = bytes([OP_RETURN, 0x4c, 100]) + data
        chunks = swap_index._parse_script_chunks(script)
        
        assert len(chunks) == 2
        assert chunks[1] == data

    def test_parse_script_chunks_op_1_to_16(self, swap_index):
        """Test parsing OP_1 through OP_16."""
        # OP_RETURN + OP_5 (0x55)
        script = bytes([OP_RETURN, 0x55])
        chunks = swap_index._parse_script_chunks(script)
        
        assert len(chunks) == 2
        assert chunks[1] == bytes([5])  # OP_5 - 0x50 = 5

    def test_parse_script_int_single_byte(self, swap_index):
        """Test parsing single byte integer."""
        assert swap_index._parse_script_int(bytes([42])) == 42

    def test_parse_script_int_two_bytes(self, swap_index):
        """Test parsing two byte integer."""
        assert swap_index._parse_script_int(bytes([0x00, 0x01])) == 256

    def test_parse_script_int_empty(self, swap_index):
        """Test parsing empty data returns 0."""
        assert swap_index._parse_script_int(b'') == 0

    def test_parse_rswp_v1(self, swap_index):
        """Test parsing RSWP v1 advertisement."""
        token_id = bytes([0xaa] * 32)
        utxo_hash = bytes([0xbb] * 32)
        utxo_index = 1
        price_terms = b'\x01\x02\x03\x04'
        signature = b'\x05\x06\x07\x08'
        
        script = build_rswp_v1_script(token_id, utxo_hash, utxo_index, price_terms, signature)
        
        tx_hash = bytes([0xcc] * 32)
        order = swap_index._parse_rswp_advertisement(script, tx_hash, 0, 100, 1234567890)
        
        assert order is not None
        assert order.tx_hash == tx_hash
        assert order.height == 100
        assert order.base_ref[:32] == token_id
        # order_id = utxo_hash + packed utxo_index
        expected_order_id = utxo_hash + struct.pack('<I', utxo_index)
        assert order.order_id == expected_order_id

    def test_parse_rswp_v2_basic(self, swap_index):
        """Test parsing RSWP v2 advertisement without want token."""
        token_id = bytes([0xaa] * 32)
        utxo_hash = bytes([0xbb] * 32)
        utxo_index = 2
        price_terms = b'\x01\x02\x03\x04'
        signature = b'\x05\x06\x07\x08'
        
        script = build_rswp_v2_script(
            token_id, utxo_hash, utxo_index, price_terms, signature,
            flags=0, offered_type=1, terms_type=0
        )
        
        tx_hash = bytes([0xcc] * 32)
        order = swap_index._parse_rswp_advertisement(script, tx_hash, 0, 200, 1234567890)
        
        assert order is not None
        assert order.tx_hash == tx_hash
        assert order.height == 200
        assert order.base_ref[:32] == token_id
        assert order.side == 1  # SELL (offered_type == 1)

    def test_parse_rswp_v2_with_want_token(self, swap_index):
        """Test parsing RSWP v2 advertisement with want token."""
        token_id = bytes([0xaa] * 32)
        want_token_id = bytes([0xdd] * 32)
        utxo_hash = bytes([0xbb] * 32)
        utxo_index = 3
        price_terms = b'\x01\x02\x03\x04'
        signature = b'\x05\x06\x07\x08'
        
        script = build_rswp_v2_script(
            token_id, utxo_hash, utxo_index, price_terms, signature,
            flags=FLAG_HAS_WANT, offered_type=0, terms_type=1,
            want_token_id=want_token_id
        )
        
        tx_hash = bytes([0xcc] * 32)
        order = swap_index._parse_rswp_advertisement(script, tx_hash, 0, 300, 1234567890)
        
        assert order is not None
        assert order.base_ref[:32] == token_id
        assert order.quote_ref[:32] == want_token_id
        assert order.side == 0  # BUY (offered_type == 0)

    def test_parse_non_rswp_script(self, swap_index):
        """Test that non-RSWP scripts return None."""
        # Regular OP_RETURN with different data
        script = bytes([OP_RETURN, 4]) + b'TEST'
        
        order = swap_index._parse_rswp_advertisement(script, bytes(32), 0, 100, 0)
        assert order is None

    def test_parse_invalid_version(self, swap_index):
        """Test that invalid version returns None."""
        script = bytes([OP_RETURN])
        script += build_push_data(RSWP_MAGIC)
        script += build_push_data(bytes([0x99]))  # Invalid version
        script += build_push_data(bytes(32))  # Some data
        
        order = swap_index._parse_rswp_advertisement(script, bytes(32), 0, 100, 0)
        assert order is None

    def test_parse_truncated_v1(self, swap_index):
        """Test that truncated v1 script returns None."""
        # Missing required fields
        script = bytes([OP_RETURN])
        script += build_push_data(RSWP_MAGIC)
        script += build_push_data(bytes([RSWP_VERSION_1]))
        script += build_push_data(bytes([0x01]))  # Type only, missing rest
        
        order = swap_index._parse_rswp_advertisement(script, bytes(32), 0, 100, 0)
        assert order is None


class TestSwapIndexOrdering:
    """Tests for SwapIndex order management."""

    @pytest.fixture
    def mock_db(self):
        db = Mock()
        db.utxo_db = MagicMock()
        db.utxo_db.get = Mock(return_value=None)
        db.utxo_db.iterator = Mock(return_value=iter([]))
        db.db_height = 100
        return db

    @pytest.fixture
    def mock_env(self):
        env = Mock()
        env.swap_index = True
        env.reorg_limit = 10
        return env

    @pytest.fixture
    def swap_index(self, mock_db, mock_env):
        from electrumx.server.swap_index import SwapIndex
        return SwapIndex(mock_db, mock_env)

    def test_order_caching(self, swap_index):
        """Test that orders are cached during processing."""
        from electrumx.server.swap_index import SwapOrderInfo
        
        order = SwapOrderInfo()
        order.order_id = bytes(36)
        order.tx_hash = bytes(32)
        
        swap_index.order_cache[order.order_id] = order
        
        assert order.order_id in swap_index.order_cache
        assert swap_index.order_cache[order.order_id] == order

    def test_status_name(self, swap_index):
        """Test status name conversion."""
        from electrumx.server.swap_index import OrderStatus
        
        assert swap_index._status_name(OrderStatus.OPEN) == 'open'
        assert swap_index._status_name(OrderStatus.PARTIAL) == 'partial'
        assert swap_index._status_name(OrderStatus.FILLED) == 'filled'
        assert swap_index._status_name(OrderStatus.CANCELLED) == 'cancelled'
        assert swap_index._status_name(OrderStatus.EXPIRED) == 'expired'
        assert swap_index._status_name(999) == 'unknown'

    def test_format_ref(self, swap_index):
        """Test ref formatting."""
        # 32-byte txid + 4-byte vout
        txid = bytes([0xab] * 32)
        vout = 5
        ref = txid + struct.pack('<I', vout)
        
        formatted = swap_index._format_ref(ref)
        
        assert formatted is not None
        assert formatted.endswith('_5')
        assert len(formatted) == 64 + 2  # 64 hex chars + "_5"

    def test_format_ref_invalid(self, swap_index):
        """Test ref formatting with invalid data."""
        assert swap_index._format_ref(None) is None
        assert swap_index._format_ref(b'') is None
        assert swap_index._format_ref(bytes(10)) is None  # Too short


class TestMempoolSwapParsing:
    """Tests for mempool RSWP parsing."""

    @pytest.fixture
    def mempool_glyph_index(self):
        """Create a MempoolGlyphIndex instance."""
        from electrumx.server.mempool_glyph import MempoolGlyphIndex
        
        env = Mock()
        env.mempool_glyph_index = True
        env.mempool_swap_index = True
        
        return MempoolGlyphIndex(env)

    def test_mempool_parse_script_chunks(self, mempool_glyph_index):
        """Test mempool script chunk parsing."""
        script = bytes([OP_RETURN, 4]) + RSWP_MAGIC
        chunks = mempool_glyph_index._parse_script_chunks(script)
        
        assert len(chunks) == 2
        assert chunks[1] == RSWP_MAGIC

    def test_mempool_parse_int(self, mempool_glyph_index):
        """Test mempool integer parsing."""
        assert mempool_glyph_index._parse_int(bytes([42])) == 42
        assert mempool_glyph_index._parse_int(bytes([0x00, 0x01])) == 256
        assert mempool_glyph_index._parse_int(b'') == 0

    def test_mempool_parse_rswp_v1(self, mempool_glyph_index):
        """Test mempool RSWP v1 parsing."""
        token_id = bytes([0xaa] * 32)
        utxo_hash = bytes([0xbb] * 32)
        
        script = build_rswp_v1_script(token_id, utxo_hash, 1, b'\x01\x02', b'\x03\x04')
        
        tx_hash = bytes([0xcc] * 32)
        order = mempool_glyph_index._parse_rswp_mempool(script, tx_hash, 0)
        
        assert order is not None
        assert order.tx_hash == tx_hash
        assert order.base_ref[:32] == token_id

    def test_mempool_parse_rswp_v2(self, mempool_glyph_index):
        """Test mempool RSWP v2 parsing."""
        token_id = bytes([0xaa] * 32)
        utxo_hash = bytes([0xbb] * 32)
        
        script = build_rswp_v2_script(
            token_id, utxo_hash, 2, b'\x01\x02', b'\x03\x04',
            flags=0, offered_type=1
        )
        
        tx_hash = bytes([0xcc] * 32)
        order = mempool_glyph_index._parse_rswp_mempool(script, tx_hash, 0)
        
        assert order is not None
        assert order.side == 1  # SELL

    def test_mempool_parse_non_rswp(self, mempool_glyph_index):
        """Test that non-RSWP returns None."""
        script = bytes([OP_RETURN, 4]) + b'TEST'
        
        order = mempool_glyph_index._parse_rswp_mempool(script, bytes(32), 0)
        assert order is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
