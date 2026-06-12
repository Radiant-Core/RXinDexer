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
        """Test parsing RSWP v2 advertisement with want token.

        offered_type == 0 makes this a BUY, and the pair is normalized
        token-as-base: base_ref comes from the WANT side, quote_ref from the
        offered side, so the bid keys into the same orderbook pair as the
        asks for the wanted token.
        """
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
        assert order.side == 0  # BUY (offered_type == 0)
        assert order.base_ref[:32] == want_token_id  # base = WANT side for a bid
        assert order.quote_ref[:32] == token_id      # quote = offered side

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


def _p2pkh(pkh20: bytes) -> bytes:
    """OP_DUP OP_HASH160 <20> OP_EQUALVERIFY OP_CHECKSIG"""
    return bytes([0x76, 0xa9, 0x14]) + pkh20 + bytes([0x88, 0xac])


def _multi_txout(outputs) -> bytes:
    """Encode [(value, script), ...] as a MultiTxOutV1 blob (count < 253, len < 253)."""
    blob = bytes([len(outputs)])
    for value, script in outputs:
        blob += struct.pack('<Q', value) + bytes([len(script)]) + script
    return blob


class TestPriceTermsDecoding:
    """MultiTxOutV1 priceTerms decode → real amount / price / maker / side.

    These vectors double as the cross-repo spec for RadiantSwap's TS encoder
    (RadiantSwap/tests/rswp.test.ts builds byte-identical advertisements), so
    the encoder and this parser cannot drift silently.
    """

    PKH = bytes([0xab]) * 20

    @pytest.fixture
    def swap_index(self):
        from electrumx.server.swap_index import SwapIndex

        db = Mock()
        db.utxo_db = MagicMock()
        db.utxo_db.get = Mock(return_value=None)
        db.utxo_db.iterator = Mock(return_value=iter([]))
        db.db_height = 100
        env = Mock()
        env.swap_index = True
        env.reorg_limit = 10
        env.coin = Mock(P2PKH_VERBYTE=b'\x00', P2SH_VERBYTES=[b'\x05'])
        return SwapIndex(db, env)

    def test_parse_multi_txout_single_p2pkh(self):
        from electrumx.server.swap_index import parse_multi_txout

        script = _p2pkh(self.PKH)
        blob = _multi_txout([(10_000, script)])
        assert parse_multi_txout(blob) == [(10_000, script)]

    def test_parse_multi_txout_multiple_outputs_sum(self):
        from electrumx.server.swap_index import parse_multi_txout

        s1, s2 = _p2pkh(self.PKH), _p2pkh(bytes([0xcd]) * 20)
        blob = _multi_txout([(7_000, s1), (3_000, s2)])
        assert parse_multi_txout(blob) == [(7_000, s1), (3_000, s2)]

    def test_parse_multi_txout_legacy_fallback(self):
        """A bare value(8 LE) + script blob (no framing) decodes via fallback."""
        from electrumx.server.swap_index import parse_multi_txout

        script = _p2pkh(self.PKH)
        blob = struct.pack('<Q', 5_000) + script
        assert parse_multi_txout(blob) == [(5_000, script)]

    def test_parse_multi_txout_garbage_returns_none(self):
        from electrumx.server.swap_index import parse_multi_txout

        assert parse_multi_txout(b'') is None
        assert parse_multi_txout(b'\x01\x02\x03\x04') is None

    def test_maker_from_bare_p2pkh(self):
        from electrumx.lib.hash import sha256
        from electrumx.server.swap_index import maker_from_script

        script = _p2pkh(self.PKH)
        coin = Mock(P2PKH_VERBYTE=b'\x00', P2SH_VERBYTES=[b'\x05'])
        sh, addr = maker_from_script(script, coin)
        assert sh == sha256(script)[::-1]
        assert addr is not None
        # Without a coin only the scripthash is resolvable.
        sh2, addr2 = maker_from_script(script)
        assert sh2 == sh and addr2 is None

    def test_maker_from_mainnet_verified_address(self):
        """Round-trip the maker address of the mainnet-verified order 5fbd060e…

        (height 428525): priceTerms count=1 value=10000, maker
        1L6UJfojmZEciBo83yB1cCMASZyQ8zMKuw.
        """
        from electrumx.lib.hash import Base58
        from electrumx.server.swap_index import maker_from_script

        address = '1L6UJfojmZEciBo83yB1cCMASZyQ8zMKuw'
        pkh = Base58.decode_check(address)[1:]  # strip verbyte
        coin = Mock(P2PKH_VERBYTE=b'\x00', P2SH_VERBYTES=[b'\x05'])
        _sh, addr = maker_from_script(_p2pkh(pkh), coin)
        assert addr == address

    def test_maker_from_ft_token_script(self):
        """An ftScript payout (P2PKH + ref machinery) resolves to the embedded P2PKH."""
        from electrumx.lib.hash import sha256
        from electrumx.server.swap_index import maker_from_script

        OP_STATESEPARATOR = 0xbd
        OP_PUSHINPUTREF = 208
        base = _p2pkh(self.PKH)
        ft_script = (base + bytes([OP_STATESEPARATOR, OP_PUSHINPUTREF])
                     + bytes(36) + bytes([0x75]))
        sh, _addr = maker_from_script(ft_script)
        assert sh == sha256(base)[::-1]

    def test_v2_order_decodes_price_amount_maker(self, swap_index):
        """Full v2 advertisement → non-zero price/amount + resolvable maker."""
        token_id = bytes([0xaa]) * 32
        utxo_hash = bytes([0xbb]) * 32
        payout = _p2pkh(self.PKH)
        price_terms = _multi_txout([(123_456_789, payout)])

        script = build_rswp_v2_script(
            token_id, utxo_hash, 2, price_terms, b'\xde' * 8,
            flags=FLAG_HAS_WANT, offered_type=2, terms_type=1,
            want_token_id=bytes(32),
        )
        order = swap_index._parse_rswp_advertisement(
            script, bytes([0xcc]) * 32, 0, 200, 1234567890)

        from electrumx.lib.hash import sha256
        assert order is not None
        assert order.amount == 123_456_789
        assert order.remaining_amount == 123_456_789
        assert order.price == 123_456_789
        assert order.maker_scripthash == sha256(payout)[::-1]
        assert order.maker_address is not None
        assert order.order_id == utxo_hash + struct.pack('<I', 2)

    @pytest.mark.parametrize('offered_type,expected_side', [
        (0, 0),  # RXD offered  -> BUY (bidding for the want token)
        (1, 1),  # NFT offered  -> SELL
        (2, 1),  # FT offered   -> SELL (shares!)
        (3, 1),  # VAULT        -> SELL
    ])
    def test_v2_side_follows_contract_type(self, swap_index, offered_type,
                                           expected_side):
        script = build_rswp_v2_script(
            bytes([0xaa]) * 32, bytes([0xbb]) * 32, 0,
            _multi_txout([(10_000, _p2pkh(self.PKH))]), b'\xde' * 8,
            flags=0, offered_type=offered_type, terms_type=1,
        )
        order = swap_index._parse_rswp_advertisement(
            script, bytes([0xcc]) * 32, 0, 200, 1234567890)
        assert order is not None and order.side == expected_side

    def test_v2_garbage_terms_still_indexes_with_zero_amount(self, swap_index):
        """Unparseable priceTerms must not reject the order (or crash)."""
        script = build_rswp_v2_script(
            bytes([0xaa]) * 32, bytes([0xbb]) * 32, 0, b'\x01\x02\x03\x04',
            b'\xde' * 8, flags=0, offered_type=2, terms_type=1,
        )
        order = swap_index._parse_rswp_advertisement(
            script, bytes([0xcc]) * 32, 0, 200, 1234567890)
        assert order is not None
        assert order.amount == 0 and order.maker_scripthash == b''

    def test_v2_absent_want_defaults_quote_to_rxd_zero_ref(self, swap_index):
        """Canonical encoders omit the want push for RXD; the order must still
        land on the (token, RXD-zero) orderbook pair, identically to an
        explicit zero want id."""
        args = (bytes([0xaa]) * 32, bytes([0xbb]) * 32, 0,
                _multi_txout([(10_000, _p2pkh(self.PKH))]), b'\xde' * 8)
        omitted = swap_index._parse_rswp_advertisement(
            build_rswp_v2_script(*args, flags=0, offered_type=2, terms_type=1),
            bytes([0xcc]) * 32, 0, 200, 1234567890)
        explicit = swap_index._parse_rswp_advertisement(
            build_rswp_v2_script(*args, flags=FLAG_HAS_WANT, offered_type=2,
                                 terms_type=1, want_token_id=bytes(32)),
            bytes([0xcc]) * 32, 0, 200, 1234567890)
        assert omitted is not None and explicit is not None
        assert omitted.quote_ref == bytes(36)
        assert omitted.quote_ref == explicit.quote_ref
        assert swap_index._pair_key(omitted) == swap_index._pair_key(explicit)
        assert swap_index._pair_key(omitted) is not None

    def test_v3_expiry_height_parsed(self, swap_index):
        """v3 = v2 + <expiryHeight:4LE> (flags 0x02) between want id and outpoint."""
        token_id = bytes([0xaa]) * 32
        utxo_hash = bytes([0xbb]) * 32
        terms = _multi_txout([(10_000, _p2pkh(self.PKH))])

        script = bytes([OP_RETURN])
        script += build_push_data(RSWP_MAGIC)
        script += build_push_data(bytes([0x03]))         # version 3
        script += build_push_data(bytes([0x02]))         # flags: HAS_EXPIRY
        script += build_push_data(bytes([0x02]))         # offeredType FT
        script += build_push_data(bytes([0x01]))         # const marker
        script += build_push_data(token_id)
        script += build_push_data(struct.pack('<I', 444_000))  # expiry height
        script += build_push_data(utxo_hash)
        script += build_push_data(bytes([0x00]))
        script += build_push_data(terms)
        script += build_push_data(b'\xde' * 8)

        order = swap_index._parse_rswp_advertisement(
            script, bytes([0xcc]) * 32, 0, 200, 1234567890)
        assert order is not None
        assert order.expiry_height == 444_000
        assert order.amount == 10_000
        assert order.quote_ref == bytes(36)  # absent want -> RXD

    def test_v2_with_expiry_flag_is_rejected(self, swap_index):
        """The expiry flag (0x02) is a v3 field; a v2 ad carrying it is malformed."""
        script = build_rswp_v2_script(
            bytes([0xaa]) * 32, bytes([0xbb]) * 32, 0,
            _multi_txout([(10_000, _p2pkh(self.PKH))]), b'\xde' * 8,
            flags=0x02, offered_type=2, terms_type=1,
        )
        assert swap_index._parse_rswp_advertisement(
            script, bytes([0xcc]) * 32, 0, 200, 1234567890) is None

    def test_unknown_version_is_skipped(self, swap_index):
        """A v4 ad must be skipped, never misparsed (forward incompatibility)."""
        script = bytearray(build_rswp_v2_script(
            bytes([0xaa]) * 32, bytes([0xbb]) * 32, 0,
            _multi_txout([(10_000, _p2pkh(self.PKH))]), b'\xde' * 8,
            flags=0, offered_type=2, terms_type=1,
        ))
        script[7] = 0x04  # version byte (OP_RETURN + push4"RSWP" + push1 -> offset 7)
        assert swap_index._parse_rswp_advertisement(
            bytes(script), bytes([0xcc]) * 32, 0, 200, 1234567890) is None


class _BookStore:
    """Dict-backed stand-in for db.utxo_db with a real prefix iterator, so
    process_tx -> flush -> get_orderbook runs end-to-end against 'disk'."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def iterator(self, prefix=b'', reverse=False, seek=None,
                 include_value=True):
        items = sorted((k, v) for k, v in self.store.items()
                       if k.startswith(prefix))
        if reverse:
            items = list(reversed(items))
        for k, v in items:
            yield (k, v) if include_value else (k, None)


class _BookBatch:
    """Write batch applying puts/deletes straight to the _BookStore."""

    def __init__(self, store):
        self._store = store

    def put(self, key, value):
        self._store[key] = value

    def delete(self, key):
        self._store.pop(key, None)


class TestBuyOrderBookKeying:
    """RXD-offered bids must land in the SAME OPEN_BY_PAIR book as the asks
    for the token they want (regtest repro 2026-06-12, ad tx d64a0791…).

    A canonical RadiantSwap bid advertisement carries offeredType 0x00 with a
    zero offered-token id, the wanted token as the HAS_WANT push (reversed
    sha256 of its 36-byte ref), priceTerms = one MultiTxOutV1 output paying
    the maker's token lock, and a plain P2PKH RXD coin as backing outpoint.
    Keying the bid on its offered (zero) ref filed it under the (zero, token)
    pair, so swap.get_orders(token, rxd) returned empty bids[] while the
    matching SELL ad listed fine in asks[].
    """

    PKH = bytes([0xab]) * 20
    # The wanted token's on-disk 36-byte ref and its advertised (reversed
    # sha256) want push — exactly what Photonic's assetToSwapTokenId emits.
    WANT_REF36 = bytes([0xaa]) * 32 + struct.pack('<I', 0)

    @property
    def want_push(self):
        return _sha256(self.WANT_REF36)[::-1]

    @pytest.fixture
    def swap_index(self):
        from electrumx.server.swap_index import SwapIndex

        db = Mock()
        db.utxo_db = _BookStore()
        db.db_height = 100
        env = Mock()
        env.swap_index = True
        env.reorg_limit = 10
        env.coin = Mock(P2PKH_VERBYTE=b'\x00', P2SH_VERBYTES=[b'\x05'])
        return SwapIndex(db, env)

    def _maker_token_lock(self):
        """ftScript paying the maker: P2PKH + state separator + ref machinery."""
        OP_STATESEPARATOR = 0xbd
        OP_PUSHINPUTREF = 0xd0
        return (_p2pkh(self.PKH) + bytes([OP_STATESEPARATOR, OP_PUSHINPUTREF])
                + self.WANT_REF36 + bytes([0x75]))

    def _bid_script(self, utxo_hash=bytes([0xbb]) * 32, utxo_index=1,
                    photons=1_000):
        """Full canonical v2 bid ad: RXD offered, WANT_REF36's token wanted."""
        return build_rswp_v2_script(
            bytes(32),                      # offered token id = zeros (RXD)
            utxo_hash, utxo_index,
            _multi_txout([(photons, self._maker_token_lock())]),
            b'\xde' * 8,
            flags=FLAG_HAS_WANT, offered_type=0, terms_type=1,
            want_token_id=self.want_push,
        )

    def _ask_script(self, utxo_hash=bytes([0xee]) * 32, utxo_index=2,
                    photons=50_000):
        """Matching v2 SELL ad: same token offered, RXD wanted (want omitted)."""
        return build_rswp_v2_script(
            self.want_push,                 # offered token id = the token
            utxo_hash, utxo_index,
            _multi_txout([(photons, _p2pkh(self.PKH))]),
            b'\xde' * 8,
            flags=0, offered_type=2, terms_type=1,
        )

    def test_full_bid_ad_parses_onto_want_token_book(self, swap_index):
        """The regression: a bid's base_ref must be the WANT side."""
        from electrumx.lib.hash import sha256

        order = swap_index._parse_rswp_advertisement(
            self._bid_script(), bytes([0xcc]) * 32, 0, 200, 1234567890)

        assert order is not None
        assert order.side == 0  # BUY
        assert order.base_ref == self.want_push + struct.pack('<I', 0)
        assert order.quote_ref == bytes(36)  # RXD zero ref
        assert order.amount == 1_000
        assert order.price == 1_000
        # Maker resolves from the P2PKH embedded in the token lock payout.
        assert order.maker_scripthash == sha256(_p2pkh(self.PKH))[::-1]
        assert order.order_id == bytes([0xbb]) * 32 + struct.pack('<I', 1)

    def test_bid_and_ask_share_one_orderbook_prefix(self, swap_index):
        """Bid and matching ask differ ONLY in the side byte of the pair key."""
        from electrumx.server.swap_index import SwapDBKeys, OrderSide

        bid = swap_index._parse_rswp_advertisement(
            self._bid_script(), bytes([0xcc]) * 32, 0, 200, 1234567890)
        ask = swap_index._parse_rswp_advertisement(
            self._ask_script(), bytes([0xcd]) * 32, 0, 200, 1234567890)
        assert bid is not None and ask is not None

        assert bid.base_ref == ask.base_ref
        assert bid.quote_ref == ask.quote_ref

        bid_key = swap_index._pair_key(bid)
        ask_key = swap_index._pair_key(ask)
        book = len(SwapDBKeys.OPEN_BY_PAIR) + 36 + 36  # prefix up to side byte
        assert bid_key[:book] == ask_key[:book]
        assert bid_key[book] == OrderSide.BUY
        assert ask_key[book] == OrderSide.SELL

    def test_bid_lists_in_get_orderbook_bids(self, swap_index):
        """End-to-end repro: process both ads, flush, query the (token, RXD)
        book — the bid must appear in bids[] alongside the ask in asks[]."""

        class _Out:
            def __init__(self, s):
                self.pk_script = s

        class _Tx:
            def __init__(self, s):
                self.outputs = [_Out(s)]

        swap_index.process_tx(bytes([0xcc]) * 32, _Tx(self._bid_script()),
                              200, 0)
        swap_index.process_tx(bytes([0xcd]) * 32, _Tx(self._ask_script()),
                              200, 1)
        swap_index.flush(_BookBatch(swap_index.db.utxo_db.store))

        book = swap_index.get_orderbook(
            self.want_push + struct.pack('<I', 0), bytes(36))

        assert len(book['bids']) == 1
        assert len(book['asks']) == 1
        assert book['bids'][0]['side'] == 'buy'
        assert book['bids'][0]['order_id'] == \
            (bytes([0xbb]) * 32 + struct.pack('<I', 1))[::-1].hex()
        assert book['asks'][0]['side'] == 'sell'

    def test_bid_validation_checks_offered_side_not_want(self, swap_index):
        """A bid offers RXD: its plain backing coin must never be required to
        carry the WANTED token — even when the coin happens to hold some
        unrelated ref (present-but-mismatched b'ri' record)."""
        from electrumx.server.swap_index import _advertised_token_hash

        bid = swap_index._parse_rswp_advertisement(
            self._bid_script(), bytes([0xcc]) * 32, 0, 200, 1234567890)
        assert bid is not None

        # Offered side is RXD -> nothing to verify on-chain.
        assert _advertised_token_hash(bid) is None

        # Even a present b'ri' record carrying a foreign token must not
        # reject the bid (the check applies to the OFFERED asset only).
        foreign = bytes([0x77]) * 32 + struct.pack('<I', 4)
        swap_index.db.utxo_db.store[b'ri' + bid.order_id] = \
            _ri_record((foreign, 0))
        assert swap_index._backing_utxo_offers_token(bid) is True


class TestMempoolPriceTermsDecoding:
    """The mempool RSWP parser shares the MultiTxOutV1 decode + side mapping."""

    PKH = bytes([0xab]) * 20

    @pytest.fixture
    def mempool_glyph_index(self):
        from electrumx.server.mempool_glyph import MempoolGlyphIndex

        env = Mock()
        env.mempool_glyph_index = True
        env.mempool_swap_index = True
        return MempoolGlyphIndex(env)

    def test_mempool_v2_decodes_price_amount_maker(self, mempool_glyph_index):
        from electrumx.lib.hash import sha256

        payout = _p2pkh(self.PKH)
        script = build_rswp_v2_script(
            bytes([0xaa]) * 32, bytes([0xbb]) * 32, 2,
            _multi_txout([(123_456_789, payout)]), b'\xde' * 8,
            flags=FLAG_HAS_WANT, offered_type=2, terms_type=1,
            want_token_id=bytes(32),
        )
        order = mempool_glyph_index._parse_rswp_mempool(
            script, bytes([0xcc]) * 32, 0)

        assert order is not None
        assert order.price == 123_456_789
        assert order.amount == 123_456_789
        assert order.maker_scripthash == sha256(payout)[::-1]
        assert order.side == 1  # FT offered -> SELL

    def test_mempool_v2_rxd_offer_is_buy(self, mempool_glyph_index):
        script = build_rswp_v2_script(
            bytes([0xaa]) * 32, bytes([0xbb]) * 32, 0,
            _multi_txout([(10_000, _p2pkh(self.PKH))]), b'\xde' * 8,
            flags=0, offered_type=0, terms_type=1,
        )
        order = mempool_glyph_index._parse_rswp_mempool(
            script, bytes([0xcc]) * 32, 0)
        assert order is not None and order.side == 0

    def test_mempool_bid_keys_same_pair_as_ask(self, mempool_glyph_index):
        """Unconfirmed bids must land in the same swap_by_pair bucket as the
        asks for the wanted token (mirrors TestBuyOrderBookKeying)."""
        want_ref36 = bytes([0xaa]) * 32 + struct.pack('<I', 0)
        want_push = _sha256(want_ref36)[::-1]
        bid_script = build_rswp_v2_script(
            bytes(32), bytes([0xbb]) * 32, 1,
            _multi_txout([(1_000, _p2pkh(self.PKH))]), b'\xde' * 8,
            flags=FLAG_HAS_WANT, offered_type=0, terms_type=1,
            want_token_id=want_push,
        )
        ask_script = build_rswp_v2_script(
            want_push, bytes([0xee]) * 32, 2,
            _multi_txout([(50_000, _p2pkh(self.PKH))]), b'\xde' * 8,
            flags=0, offered_type=2, terms_type=1,
        )
        bid = mempool_glyph_index._parse_rswp_mempool(
            bid_script, bytes([0xcc]) * 32, 0)
        ask = mempool_glyph_index._parse_rswp_mempool(
            ask_script, bytes([0xcd]) * 32, 0)
        assert bid is not None and ask is not None
        assert bid.side == 0 and ask.side == 1
        assert bid.base_ref == ask.base_ref == want_push + struct.pack('<I', 0)
        assert bid.quote_ref == ask.quote_ref == bytes(36)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
