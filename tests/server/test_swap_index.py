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
import hashlib


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


def _sha256(b):
    return hashlib.sha256(b).digest()


def _ri_record(*refs_with_type):
    """Build a b'ri' record: concatenated 37-byte (36-byte ref + 1 type) entries."""
    out = b''
    for ref36, t in refs_with_type:
        assert len(ref36) == 36
        out += ref36 + bytes([t])
    return out


def _advertised_base_ref_for_ref(ref36):
    """Given an on-disk 36-byte ref, return the base_ref the RSWP parser would
    produce for an order offering that token.

    Photonic ``assetToSwapTokenId`` = sha256(ref36), pushed byte-REVERSED into
    the OP_RETURN; the parser stores that reversed value verbatim as
    base_ref[:32], then appends LE(0).
    """
    token_hash = _sha256(ref36)
    return token_hash[::-1] + struct.pack('<I', 0)


class TestBackingUtxoOfferedRefCheck:
    """Part A (C3-auth): reject orders whose backing UTXO demonstrably does not
    carry the offered token (present-but-mismatched b'ri' record)."""

    @pytest.fixture
    def mock_env(self):
        env = Mock()
        env.swap_index = True
        env.reorg_limit = 10
        return env

    def _swap_index(self, mock_env, ri_store):
        from electrumx.server.swap_index import SwapIndex
        db = Mock()
        db.utxo_db = MagicMock()
        db.utxo_db.get = Mock(side_effect=lambda k: ri_store.get(k))
        db.utxo_db.iterator = Mock(return_value=iter([]))
        db.db_height = 100
        return SwapIndex(db, mock_env)

    def _order(self, order_id, base_ref):
        from electrumx.server.swap_index import SwapOrderInfo, OrderStatus, OrderSide
        o = SwapOrderInfo()
        o.order_id = order_id
        o.base_ref = base_ref
        o.side = OrderSide.SELL
        o.status = OrderStatus.OPEN
        return o

    def test_absent_record_is_accepted(self, mock_env):
        """No b'ri' record -> accept (may be same-block, not yet flushed)."""
        si = self._swap_index(mock_env, {})
        order_id = b'\xbb' * 32 + struct.pack('<I', 1)
        ref36 = b'\xaa' * 32 + struct.pack('<I', 0)
        order = self._order(order_id, _advertised_base_ref_for_ref(ref36))
        assert si._backing_utxo_offers_token(order) is True

    def test_present_and_matching_is_accepted(self, mock_env):
        ref36 = b'\xaa' * 32 + struct.pack('<I', 0)
        order_id = b'\xbb' * 32 + struct.pack('<I', 1)
        ri_store = {b'ri' + order_id: _ri_record((ref36, 0))}
        si = self._swap_index(mock_env, ri_store)
        order = self._order(order_id, _advertised_base_ref_for_ref(ref36))
        assert si._backing_utxo_offers_token(order) is True

    def test_present_and_matching_among_multiple_refs(self, mock_env):
        """Match must succeed even when the backing UTXO carries several refs."""
        wanted = b'\xaa' * 32 + struct.pack('<I', 0)
        other1 = b'\xcc' * 32 + struct.pack('<I', 2)
        other2 = b'\xdd' * 32 + struct.pack('<I', 7)
        order_id = b'\xbb' * 32 + struct.pack('<I', 1)
        ri_store = {b'ri' + order_id: _ri_record((other1, 0), (wanted, 1), (other2, 0))}
        si = self._swap_index(mock_env, ri_store)
        order = self._order(order_id, _advertised_base_ref_for_ref(wanted))
        assert si._backing_utxo_offers_token(order) is True

    def test_present_but_mismatched_is_rejected(self, mock_env):
        """Record exists but holds a different token -> REJECT."""
        on_disk = b'\xcc' * 32 + struct.pack('<I', 3)
        offered = b'\xaa' * 32 + struct.pack('<I', 0)
        order_id = b'\xbb' * 32 + struct.pack('<I', 1)
        ri_store = {b'ri' + order_id: _ri_record((on_disk, 0))}
        si = self._swap_index(mock_env, ri_store)
        order = self._order(order_id, _advertised_base_ref_for_ref(offered))
        assert si._backing_utxo_offers_token(order) is False

    def test_rxd_offer_zero_token_is_accepted(self, mock_env):
        """RXD offer (token id == 32 zero bytes) -> nothing to check, accept,
        even if a b'ri' record happens to exist."""
        order_id = b'\xbb' * 32 + struct.pack('<I', 1)
        some_ref = b'\xcc' * 32 + struct.pack('<I', 0)
        ri_store = {b'ri' + order_id: _ri_record((some_ref, 0))}
        si = self._swap_index(mock_env, ri_store)
        base_ref = (b'\x00' * 32) + struct.pack('<I', 0)
        order = self._order(order_id, base_ref)
        assert si._backing_utxo_offers_token(order) is True

    def test_malformed_short_base_ref_is_accepted(self, mock_env):
        """A short/empty base_ref can't be checked -> accept (no false reject)."""
        order_id = b'\xbb' * 32 + struct.pack('<I', 1)
        si = self._swap_index(mock_env, {b'ri' + order_id: _ri_record(
            (b'\xcc' * 32 + struct.pack('<I', 0), 0))})
        order = self._order(order_id, b'\x01\x02')
        assert si._backing_utxo_offers_token(order) is True

    def test_process_tx_skips_caching_on_mismatch(self, mock_env):
        """End-to-end: a parsed order whose backing UTXO mismatches is never
        admitted to order_cache."""
        on_disk = b'\xcc' * 32 + struct.pack('<I', 3)
        offered = b'\xaa' * 32 + struct.pack('<I', 0)
        utxo_hash = b'\xbb' * 32
        utxo_index = 1
        order_id = utxo_hash + struct.pack('<I', utxo_index)
        ri_store = {b'ri' + order_id: _ri_record((on_disk, 0))}
        si = self._swap_index(mock_env, ri_store)

        token_id = _sha256(offered)[::-1]  # advertised (reversed) token id
        # priceTerms: single RXD payout to a P2PKH (so amount > 0, in range)
        p2pkh = bytes([0x76, 0xa9, 0x14]) + bytes(20) + bytes([0x88, 0xac])
        price_terms = bytes([1]) + struct.pack('<Q', 10000) + bytes([len(p2pkh)]) + p2pkh
        script = build_rswp_v2_script(
            token_id, utxo_hash, utxo_index, price_terms, b'\x05\x06\x07\x08',
            flags=0, offered_type=1)

        class _Out:
            def __init__(self, s):
                self.pk_script = s

        class _Tx:
            outputs = [_Out(script)]

        si.process_tx(b'\x99' * 32, _Tx(), 200, 0)
        assert order_id not in si.order_cache

    def test_process_tx_caches_on_match(self, mock_env):
        on_disk = b'\xaa' * 32 + struct.pack('<I', 0)
        utxo_hash = b'\xbb' * 32
        utxo_index = 1
        order_id = utxo_hash + struct.pack('<I', utxo_index)
        ri_store = {b'ri' + order_id: _ri_record((on_disk, 1))}
        si = self._swap_index(mock_env, ri_store)

        token_id = _sha256(on_disk)[::-1]
        p2pkh = bytes([0x76, 0xa9, 0x14]) + bytes(20) + bytes([0x88, 0xac])
        price_terms = bytes([1]) + struct.pack('<Q', 10000) + bytes([len(p2pkh)]) + p2pkh
        script = build_rswp_v2_script(
            token_id, utxo_hash, utxo_index, price_terms, b'\x05\x06\x07\x08',
            flags=0, offered_type=1)

        class _Out:
            def __init__(self, s):
                self.pk_script = s

        class _Tx:
            outputs = [_Out(script)]

        si.process_tx(b'\x99' * 32, _Tx(), 200, 0)
        assert order_id in si.order_cache


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
