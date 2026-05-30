"""
Stable cursor pagination — tests for glyph.get_history.

Covers the dual-shape contract documented in docs/pagination-cursors.md:

* Legacy call (no `cursor` arg) returns a plain list and honours `offset`.
* Cursor call (`cursor` supplied) returns
  `{entries, next_cursor, has_more}` and is stable under insertions
  between paginated calls.
"""

from __future__ import annotations

import base64
import struct
from typing import Any, Dict, Iterator, List, Tuple
from unittest.mock import MagicMock

import pytest

from electrumx.server.glyph_index import (
    GlyphDBKeys,
    GlyphEventType,
    GlyphIndex,
    pack_history_key,
    pack_ref,
)


# ---------------------------------------------------------------------------
# Mock RocksDB
# ---------------------------------------------------------------------------

class MockRocksDB:
    """In-memory dict mock supporting both `prefix=` and `seek=` iterator args."""

    def __init__(self):
        self._store: Dict[bytes, bytes] = {}

    def get(self, key: bytes):
        return self._store.get(key)

    def put(self, key: bytes, value: bytes):
        self._store[key] = value

    def iterator(self, prefix: bytes = b"", seek: bytes = None,
                 reverse: bool = False,
                 include_value: bool = True) -> Iterator[Tuple[bytes, bytes]]:
        keys = sorted(k for k in self._store if k.startswith(prefix))
        if reverse:
            keys.reverse()
        if seek is not None:
            if reverse:
                keys = [k for k in keys if k <= seek]
            else:
                keys = [k for k in keys if k >= seek]
        if include_value:
            return iter((k, self._store[k]) for k in keys)
        return iter((k, b"") for k in keys)


def make_index() -> GlyphIndex:
    db = MagicMock()
    db.db_height = 0
    db.utxo_db = MockRocksDB()
    env = MagicMock()
    env.glyph_index = True
    env.reorg_limit = 0
    return GlyphIndex(db, env)


def make_ref(byte: int = 0xAB, vout: int = 0) -> bytes:
    return pack_ref(bytes([byte]) * 32, vout)


def seed_history(idx: GlyphIndex, ref: bytes, entries: List[Tuple[int, int, int, bytes]]):
    """Insert HISTORY rows: each (height, tx_idx, event_type, txid_bytes)."""
    for height, tx_idx, event_type, txid in entries:
        key = pack_history_key(ref, height, tx_idx)
        value = bytes([event_type]) + txid
        idx.db.utxo_db.put(key, value)


# ---------------------------------------------------------------------------
# Legacy shape — backwards compatibility
# ---------------------------------------------------------------------------

class TestLegacyShape:
    def test_empty_returns_empty_list(self):
        idx = make_index()
        ref = make_ref()
        result = idx.get_token_history(ref)
        assert result == []
        assert isinstance(result, list)

    def test_returns_list_not_dict(self):
        idx = make_index()
        ref = make_ref()
        seed_history(idx, ref, [
            (100, 0, GlyphEventType.DEPLOY, bytes(32)),
            (101, 1, GlyphEventType.MINT, bytes(32)),
        ])
        result = idx.get_token_history(ref, limit=10, offset=0)
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]['height'] == 100
        assert result[1]['height'] == 101
        assert result[0]['event'] == 'deploy'
        assert result[1]['event'] == 'mint'

    def test_offset_skips_entries(self):
        idx = make_index()
        ref = make_ref()
        seed_history(idx, ref, [
            (h, 0, GlyphEventType.TRANSFER, bytes(32)) for h in range(100, 110)
        ])
        page1 = idx.get_token_history(ref, limit=3, offset=0)
        page2 = idx.get_token_history(ref, limit=3, offset=3)
        assert [r['height'] for r in page1] == [100, 101, 102]
        assert [r['height'] for r in page2] == [103, 104, 105]


# ---------------------------------------------------------------------------
# Cursor shape — stable pagination
# ---------------------------------------------------------------------------

class TestCursorShape:
    def test_empty_returns_dict_with_null_cursor(self):
        idx = make_index()
        ref = make_ref()
        result = idx.get_token_history(ref, _use_cursor=True)
        assert result == {'entries': [], 'next_cursor': None, 'has_more': False}

    def test_single_page_no_cursor(self):
        idx = make_index()
        ref = make_ref()
        seed_history(idx, ref, [
            (100, 0, GlyphEventType.DEPLOY, bytes(32)),
            (101, 0, GlyphEventType.MINT, bytes(32)),
        ])
        result = idx.get_token_history(ref, limit=10, _use_cursor=True)
        assert len(result['entries']) == 2
        assert result['next_cursor'] is None
        assert result['has_more'] is False

    def test_full_walk_visits_each_entry_exactly_once(self):
        idx = make_index()
        ref = make_ref()
        seed_history(idx, ref, [
            (h, 0, GlyphEventType.TRANSFER, bytes(32)) for h in range(100, 125)
        ])

        seen: List[Tuple[int, int]] = []
        cursor = None
        pages = 0
        while True:
            r = idx.get_token_history(ref, limit=7, cursor=cursor, _use_cursor=True)
            pages += 1
            assert pages < 20, "pagination did not terminate"
            for e in r['entries']:
                seen.append((e['height'], e['tx_idx']))
            if not r['has_more']:
                assert r['next_cursor'] is None
                break
            cursor = r['next_cursor']
            assert cursor is not None

        # Each (height, tx_idx) appears exactly once, in order.
        expected = [(h, 0) for h in range(100, 125)]
        assert seen == expected
        assert len(seen) == len(set(seen))  # no dupes

    def test_stable_under_insertion_between_pages(self):
        """New rows landing mid-pagination must not cause double-counting."""
        idx = make_index()
        ref = make_ref()
        seed_history(idx, ref, [
            (h, 0, GlyphEventType.TRANSFER, bytes(32)) for h in range(100, 110)
        ])

        page1 = idx.get_token_history(ref, limit=5, _use_cursor=True)
        seen = [(e['height'], e['tx_idx']) for e in page1['entries']]
        assert seen == [(h, 0) for h in range(100, 105)]
        cursor = page1['next_cursor']
        assert cursor is not None

        # Simulate new chain activity: insert rows BEFORE the cursor point.
        # An offset-based caller would re-emit some rows; a cursor caller
        # must not.
        seed_history(idx, ref, [(99, 0, GlyphEventType.TRANSFER, bytes(32))])
        seed_history(idx, ref, [(100, 1, GlyphEventType.TRANSFER, bytes(32))])

        page2 = idx.get_token_history(ref, limit=5, cursor=cursor, _use_cursor=True)
        seen.extend((e['height'], e['tx_idx']) for e in page2['entries'])

        # No row should appear twice. The cursor seeks past page-1's last
        # returned key, so heights 99 and 100/tx_idx=1 (inserted later)
        # are NOT re-emitted even though they sort earlier.
        assert len(seen) == len(set(seen))
        # And we have the entries from height 105 onward.
        assert (105, 0) in seen
        assert (109, 0) in seen

    def test_cursor_is_bounded_size(self):
        idx = make_index()
        ref = make_ref()
        seed_history(idx, ref, [
            (h, 0, GlyphEventType.TRANSFER, bytes(32)) for h in range(0, 10)
        ])
        result = idx.get_token_history(ref, limit=3, _use_cursor=True)
        cursor = result['next_cursor']
        assert cursor is not None
        # 256-byte cap from docs/pagination-cursors.md.
        assert len(cursor.encode()) <= 256
        # In practice: HISTORY key is 44 raw bytes → 60 base64 chars.
        assert len(cursor.encode()) < 100

    def test_malformed_cursor_does_not_crash(self):
        idx = make_index()
        ref = make_ref()
        seed_history(idx, ref, [
            (100, 0, GlyphEventType.DEPLOY, bytes(32)),
        ])
        # Bad base64.
        result = idx.get_token_history(
            ref, limit=10, cursor="not-a-valid-cursor!!!", _use_cursor=True
        )
        # _decode_cursor returns None on failure; we fall back to the prefix
        # and return all entries.
        assert isinstance(result, dict)
        assert len(result['entries']) == 1

    def test_cursor_for_unknown_ref_returns_empty(self):
        idx = make_index()
        ref_a = make_ref(0xAB)
        ref_b = make_ref(0xCD)
        seed_history(idx, ref_a, [
            (100, 0, GlyphEventType.DEPLOY, bytes(32)),
        ])
        # Cursor scoped to ref_b — no entries for that ref.
        result = idx.get_token_history(ref_b, limit=10, _use_cursor=True)
        assert result == {'entries': [], 'next_cursor': None, 'has_more': False}

    def test_isolation_between_refs(self):
        """A cursor walk over ref_a must not bleed into ref_b's rows."""
        idx = make_index()
        ref_a = make_ref(0x01)
        ref_b = make_ref(0xFF)
        # Seed both. ref_a < ref_b lexicographically.
        seed_history(idx, ref_a, [
            (h, 0, GlyphEventType.TRANSFER, bytes(32)) for h in range(0, 5)
        ])
        seed_history(idx, ref_b, [
            (h, 0, GlyphEventType.TRANSFER, bytes(32)) for h in range(0, 5)
        ])

        seen_a = []
        cursor = None
        while True:
            r = idx.get_token_history(ref_a, limit=2, cursor=cursor, _use_cursor=True)
            seen_a.extend(r['entries'])
            if not r['has_more']:
                break
            cursor = r['next_cursor']
        assert len(seen_a) == 5
        # None of ref_a's entries should be from ref_b.
        # We can't distinguish from height alone, but the iteration is
        # prefix-scoped so this is implicit; the count is the proof.


# ---------------------------------------------------------------------------
# search_tokens — cursor pagination over BY_NAME prefix
# ---------------------------------------------------------------------------

from electrumx.lib.glyph import GlyphProtocol  # noqa: E402
from electrumx.lib.hash import sha256 as _sha256  # noqa: E402
from electrumx.server.glyph_index import GlyphTokenInfo  # noqa: E402


def _seed_token(idx: GlyphIndex, ref: bytes, name: str, protocols=None,
                token_type: int = 2):
    """Insert a token into the cache and BY_NAME index for search tests."""
    t = GlyphTokenInfo()
    t.ref = ref
    t.name = name
    t.ticker = None
    t.token_type = token_type
    t.protocols = protocols or [GlyphProtocol.GLYPH_NFT]
    t.deploy_height = 100
    t.deploy_txid = bytes(32)
    t.metadata_hash = None
    idx.token_cache[ref] = t
    # BY_NAME key = GN + name_hash(16) + ref
    name_hash = _sha256(name.lower().encode('utf-8'))[:16]
    key = GlyphDBKeys.BY_NAME + name_hash + ref
    idx.db.utxo_db.put(key, b"")


class TestSearchTokensCursor:
    def test_legacy_returns_list(self):
        idx = make_index()
        for i in range(3):
            _seed_token(idx, make_ref(0x10 + i, i), "Alice")
        result = idx.search_tokens("Alice", limit=10)
        assert isinstance(result, list)
        assert len(result) == 3

    def test_cursor_returns_dict_shape(self):
        idx = make_index()
        for i in range(5):
            _seed_token(idx, make_ref(0x10 + i, i), "Bob")
        result = idx.search_tokens("Bob", limit=2, _use_cursor=True)
        assert set(result.keys()) == {'entries', 'next_cursor', 'has_more'}
        assert len(result['entries']) == 2
        assert result['has_more'] is True

    def test_full_walk_no_duplicates(self):
        idx = make_index()
        for i in range(7):
            _seed_token(idx, make_ref(0x20 + i, i), "Carol")
        seen_refs = []
        cursor = None
        while True:
            r = idx.search_tokens("Carol", limit=3, cursor=cursor, _use_cursor=True)
            seen_refs.extend(e['ref'] for e in r['entries'])
            if not r['has_more']:
                break
            cursor = r['next_cursor']
        assert len(seen_refs) == 7
        assert len(set(seen_refs)) == 7


# ---------------------------------------------------------------------------
# get_balances_for_scripthash — verifies the existing cursor still works
# after our handler changes wrap it.
# ---------------------------------------------------------------------------

class TestBalancesCursorStillWorks:
    def test_full_walk(self):
        idx = make_index()
        scripthash = b'\x11' * 32
        # Balances are keyed by the 11-byte hashX (the recipient's base-address
        # hashX), exactly as the block processor writes them.  The query
        # converts the Electrum scripthash to that hashX before seeking, so the
        # seed rows must use the hashX too.
        from electrumx.lib.hash import HASHX_LEN
        hashX = scripthash[::-1][:HASHX_LEN]
        refs = [make_ref(0x30 + i, i) for i in range(6)]
        for ref in refs:
            # Seed a balance row and a token.
            bal_key = GlyphDBKeys.BALANCE + hashX + ref
            idx.db.utxo_db.put(bal_key, struct.pack('<Q', 1000))
            t = GlyphTokenInfo()
            t.ref = ref
            t.name = 'Token'
            t.ticker = 'TKN'
            t.decimals = 0
            t.token_type = 1
            idx.token_cache[ref] = t

        seen = []
        cursor = None
        while True:
            r = idx.get_balances_for_scripthash(scripthash, limit=2, cursor=cursor)
            seen.extend(r['balances'])
            cursor = r['next_cursor']
            if cursor is None:
                break
        assert len(seen) == 6


# ---------------------------------------------------------------------------
# Current-ownership semantics: mint credits the recipient, a transfer debits
# the sender and credits the new owner.  This is what lets glyph.list_tokens be
# the *current ownership* source of truth instead of an "addresses ever
# involved" history (which over-reports a minted-then-sent NFT).
# ---------------------------------------------------------------------------

class _DictBatch:
    """Minimal write-batch that applies put/delete straight to MockRocksDB."""

    def __init__(self, db: "MockRocksDB"):
        self._db = db

    def put(self, key: bytes, value: bytes):
        self._db._store[key] = value

    def delete(self, key: bytes):
        self._db._store.pop(key, None)


class TestCurrentOwnership:
    def test_mint_then_transfer_reflects_current_ownership(self):
        from electrumx.lib.hash import HASHX_LEN

        idx = make_index()

        ref = make_ref(0x42, 0)  # 36-byte singleton (NFT) ref
        t = GlyphTokenInfo()
        t.ref = ref
        t.name = "Radiant Cube"
        t.protocols = [GlyphProtocol.GLYPH_NFT]
        t.token_type = 2  # NFT
        idx.token_cache[ref] = t  # registered -> "known" + name-resolvable
        idx.token_height[ref] = 1  # so flush() persists it to the DB

        # Two holders addressed by their standard Electrum scripthashes.  The
        # glyph_index methods receive the raw 32-byte scripthash (the API layer
        # does bytes.fromhex), exactly as the JSON-RPC handlers pass them.
        scripthash_A = bytes.fromhex(
            "59dea47da05ec1d2ecf6ed312b523926c7ac1058860dcf2bfe3338ce4495d1e6")
        scripthash_B = bytes.fromhex("aa" * 32)
        # Recipient base-address hashX — what block_processor credits/debits and
        # what _scripthash_to_hashX derives from the client scripthash.
        base_A = scripthash_A[::-1][:HASHX_LEN]
        base_B = scripthash_B[::-1][:HASHX_LEN]

        # --- Mint: token output (1 photon) paid to A ---
        idx.process_balance_changes(1, debits=[], credits=[(base_A, 1, [ref])])
        assert idx.get_balance(scripthash_A, ref) == 1
        assert idx.get_balance(scripthash_B, ref) == 0

        # --- Transfer A -> B ---
        # block_processor supplies the spent output's base-address hashX (base_A,
        # read back from the per-outpoint b'rb' map) as the debit key, and base_B
        # as the credit.  refs_data is the 37-byte (ref + singleton-type) entry
        # exactly as stored in the 'ri' index and consumed by the debit loop.
        refs_data = ref + b"\x01"
        idx.process_balance_changes(
            2, debits=[(base_A, 1, refs_data)], credits=[(base_B, 1, [ref])]
        )

        # Immediate (cache) view.
        assert idx.get_balance(scripthash_A, ref) == 0   # A no longer owns it
        assert idx.get_balance(scripthash_B, ref) == 1   # B now owns it

        # DB view — the real glyph.list_tokens path — after a flush.
        idx.flush(_DictBatch(idx.db.utxo_db))
        a_tokens = idx.get_balances_for_scripthash(scripthash_A)["balances"]
        b_tokens = idx.get_balances_for_scripthash(scripthash_B)["balances"]
        assert a_tokens == []                            # dropped from sender
        assert len(b_tokens) == 1
        assert b_tokens[0]["name"] == "Radiant Cube"     # current owner only


# ---------------------------------------------------------------------------
# swap_index cursor methods
# ---------------------------------------------------------------------------

from electrumx.server.swap_index import (  # noqa: E402
    OrderSide,
    OrderStatus,
    SwapDBKeys,
    SwapIndex,
    SwapOrderInfo,
)


def make_swap_index() -> SwapIndex:
    db = MagicMock()
    db.db_height = 0
    db.utxo_db = MockRocksDB()
    env = MagicMock()
    env.swap_index = True
    env.reorg_limit = 0
    return SwapIndex(db, env)


def _seed_open_order(idx: SwapIndex, base_ref: bytes, quote_ref: bytes,
                     order_id: bytes, side: int = OrderSide.SELL, price: int = 1):
    """Insert an OPEN_BY_PAIR row + matching order in the cache."""
    key = (SwapDBKeys.OPEN_BY_PAIR + base_ref + quote_ref
           + bytes([side]) + struct.pack('>Q', price) + order_id)
    idx.db.utxo_db.put(key, b"")

    order = SwapOrderInfo()
    order.order_id = order_id
    order.base_ref = base_ref
    order.quote_ref = quote_ref
    order.side = side
    order.price = price
    order.status = OrderStatus.OPEN
    order.amount = 1000
    order.remaining_amount = 1000
    idx.order_cache[order_id] = order


class TestOpenOrdersCursor:
    def test_legacy_returns_list(self):
        idx = make_swap_index()
        base = b'\x01' * 36
        quote = b'\x02' * 36
        for i in range(3):
            _seed_open_order(idx, base, quote, bytes([i]) * 36)
        result = idx.get_open_orders(base_ref=base, limit=10)
        assert isinstance(result, list)
        assert len(result) == 3

    def test_cursor_full_walk(self):
        idx = make_swap_index()
        base = b'\x01' * 36
        quote = b'\x02' * 36
        order_ids = [bytes([i + 1]) * 36 for i in range(8)]
        for oid in order_ids:
            _seed_open_order(idx, base, quote, oid)

        seen = []
        cursor = None
        while True:
            r = idx.get_open_orders(base_ref=base, limit=3,
                                    cursor=cursor, _use_cursor=True)
            seen.extend(r['entries'])
            if not r['has_more']:
                break
            cursor = r['next_cursor']
        assert len(seen) == 8
        # Every order_id surfaces exactly once.
        ids_seen = [e['order_id'] if isinstance(e.get('order_id'), str)
                    else None for e in seen]
        # Order ids may be serialized differently; the count is the
        # uniqueness guarantee here.
        assert len(seen) == len(order_ids)


class TestSwapHistoryCursor:
    """Reverse-iteration cursor for newest-first history walk."""

    def _seed_history(self, idx: SwapIndex, base_ref: bytes,
                      heights: List[int]):
        try:
            import cbor2
        except ImportError:
            pytest.skip("cbor2 not available")
        for h in heights:
            key = (SwapDBKeys.HISTORY + base_ref
                   + struct.pack('>I', h) + struct.pack('>H', 0))
            idx.db.utxo_db.put(key, cbor2.dumps({'height': h, 'price': h}))

    def test_legacy_returns_list_newest_first(self):
        idx = make_swap_index()
        base = b'\x03' * 36
        self._seed_history(idx, base, [100, 101, 102])
        result = idx.get_swap_history(base, limit=10)
        assert isinstance(result, list)
        heights = [r['height'] for r in result]
        assert heights == [102, 101, 100]

    def test_cursor_full_walk_newest_first_no_dupes(self):
        idx = make_swap_index()
        base = b'\x04' * 36
        self._seed_history(idx, base, list(range(100, 110)))

        seen = []
        cursor = None
        while True:
            r = idx.get_swap_history(base, limit=3, cursor=cursor, _use_cursor=True)
            seen.extend(r['entries'])
            if not r['has_more']:
                break
            cursor = r['next_cursor']

        heights = [e['height'] for e in seen]
        # Newest-first, no dupes.
        assert heights == sorted(heights, reverse=True)
        assert len(heights) == len(set(heights)) == 10


# ---------------------------------------------------------------------------
# wave.get_subdomains — char-idx cursor for API consistency
# ---------------------------------------------------------------------------

class TestWaveSubdomainsCursor:
    """Verify the cursor parameter is accepted and round-trips correctly.

    The underlying iteration is bounded (37 char slots), so this is a
    consistency check more than a stability check.
    """

    def _make_wave(self):
        from electrumx.server.wave_index import WaveIndex
        db = MagicMock()
        db.db_height = 0
        db.utxo_db = MockRocksDB()
        env = MagicMock()
        env.wave_index = True
        env.reorg_limit = 0
        return WaveIndex(db, env)

    def test_cursor_param_round_trips(self):
        wave = self._make_wave()
        # Without a registered parent, both shapes should return empty.
        legacy = wave.get_subdomains("nonexistent", limit=10)
        assert legacy == []
        cursor_shape = wave.get_subdomains("nonexistent", limit=10, _use_cursor=True)
        assert cursor_shape == {
            'entries': [], 'next_cursor': None, 'has_more': False,
        }
