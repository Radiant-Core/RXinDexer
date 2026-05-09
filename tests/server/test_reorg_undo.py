import contextlib

import pytest


class FakeBatch:
    def __init__(self, store):
        self._store = store

    def put(self, key: bytes, value: bytes):
        self._store[key] = value

    def delete(self, key: bytes):
        self._store.pop(key, None)


class FakeUtxoDB:
    def __init__(self):
        self._store = {}

    def get(self, key: bytes):
        return self._store.get(key)

    def put(self, key: bytes, value: bytes):
        self._store[key] = value

    def iterator(self, prefix=b"", reverse=False, include_value=True):
        items = [(k, v) for k, v in self._store.items() if k.startswith(prefix)]
        items.sort(key=lambda kv: kv[0], reverse=reverse)
        if include_value:
            return iter(items)
        return iter([k for k, _v in items])

    @contextlib.contextmanager
    def write_batch(self):
        yield FakeBatch(self._store)


class FakeDB:
    def __init__(self):
        self.utxo_db = FakeUtxoDB()
        self.db_height = 100


class FakeEnv:
    glyph_index = True
    wave_index = True
    swap_index = True
    analytics_index = True
    wave_hot_names = 100
    wave_genesis_ref = None
    reorg_limit = 10
    coin = type("FakeCoin", (), {
        "VALUE_PER_COIN": 100_000_000,
        "P2PKH_VERBYTE": bytes.fromhex("00"),
        "P2SH_VERBYTES": [bytes.fromhex("05")],
    })()


def test_glyph_undo_backup_roundtrip():
    from electrumx.server.glyph_index import GlyphIndex, GlyphTokenInfo, GlyphDBKeys, pack_token_key
    from electrumx.lib.glyph import GlyphProtocol, get_token_type_id

    db = FakeDB()
    env = FakeEnv()
    idx = GlyphIndex(db, env)

    height = 100
    ref = b"\x11" * 36
    key = pack_token_key(ref)

    # Set an existing value to ensure undo restores it
    db.utxo_db._store[key] = b"old"

    token = GlyphTokenInfo()
    token.ref = ref
    token.protocols = [GlyphProtocol.GLYPH_FT]
    token.token_type = get_token_type_id(token.protocols)
    token.name = "Token"

    idx.token_cache[ref] = token
    idx.token_height[ref] = height

    batch = FakeBatch(db.utxo_db._store)
    idx.flush(batch)

    undo_key = GlyphDBKeys.UNDO + height.to_bytes(4, "big")
    assert undo_key in db.utxo_db._store
    assert db.utxo_db.get(key) != b"old"

    batch2 = FakeBatch(db.utxo_db._store)
    idx.backup(batch2, height)

    assert db.utxo_db.get(key) == b"old"
    assert undo_key not in db.utxo_db._store


def test_wave_undo_backup_roundtrip():
    from electrumx.server.wave_index import WaveIndex, WaveDBKeys

    db = FakeDB()
    env = FakeEnv()
    idx = WaveIndex(db, env)

    height = 200
    tree_key = b"\x22" * 37  # parent_ref(36) + idx(1)
    child_ref = b"\x33" * 36
    full_key = WaveDBKeys.TREE + tree_key

    db.utxo_db._store[full_key] = b"old_child"

    idx.tree_cache[tree_key] = child_ref
    idx.tree_height[tree_key] = height

    batch = FakeBatch(db.utxo_db._store)
    idx.flush(batch)

    undo_key = WaveDBKeys.UNDO + height.to_bytes(4, "big")
    assert undo_key in db.utxo_db._store
    assert db.utxo_db.get(full_key) == child_ref

    batch2 = FakeBatch(db.utxo_db._store)
    idx.backup(batch2, height)

    assert db.utxo_db.get(full_key) == b"old_child"
    assert undo_key not in db.utxo_db._store


def test_swap_undo_backup_roundtrip():
    from electrumx.server.swap_index import SwapIndex, SwapOrderInfo, SwapDBKeys, OrderStatus, OrderSide

    db = FakeDB()
    env = FakeEnv()
    idx = SwapIndex(db, env)

    height = 300
    order_id = b"\x44" * 36

    order = SwapOrderInfo()
    order.order_id = order_id
    order.base_ref = b"\x55" * 36
    order.quote_ref = b"\x66" * 36
    order.side = OrderSide.SELL
    order.status = OrderStatus.OPEN
    order.price = 123
    order.maker_scripthash = b"\x77" * 32

    order_key = SwapDBKeys.ORDER + order_id
    db.utxo_db._store[order_key] = b"old_order"

    idx.order_cache[order_id] = order
    idx.order_height[order_id] = height

    batch = FakeBatch(db.utxo_db._store)
    idx.flush(batch)

    undo_key = SwapDBKeys.UNDO + height.to_bytes(4, "big")
    assert undo_key in db.utxo_db._store
    assert db.utxo_db.get(order_key) != b"old_order"

    batch2 = FakeBatch(db.utxo_db._store)
    idx.backup(batch2, height)

    assert db.utxo_db.get(order_key) == b"old_order"
    assert undo_key not in db.utxo_db._store


def test_analytics_undo_backup_roundtrip():
    from electrumx.server.analytics_index import AnalyticsDBKeys, AnalyticsIndex

    db = FakeDB()
    env = FakeEnv()
    idx = AnalyticsIndex(db, env)

    height = 400
    hashX = b"\x88" * 11
    balance_key = AnalyticsDBKeys.BALANCE + hashX
    db.utxo_db._store[balance_key] = (25 * env.coin.VALUE_PER_COIN).to_bytes(8, "little")

    idx.process_block(
        height,
        spends=[],
        adds=[(b"\x99" * 32, 0, hashX, 10 * env.coin.VALUE_PER_COIN, "analytics-addr")],
    )

    batch = FakeBatch(db.utxo_db._store)
    idx.flush(batch)

    undo_key = AnalyticsDBKeys.UNDO + height.to_bytes(4, "big")
    assert undo_key in db.utxo_db._store
    assert int.from_bytes(db.utxo_db.get(balance_key), "little") == 35 * env.coin.VALUE_PER_COIN

    batch2 = FakeBatch(db.utxo_db._store)
    idx.backup(batch2, height)

    assert int.from_bytes(db.utxo_db.get(balance_key), "little") == 25 * env.coin.VALUE_PER_COIN
    assert undo_key not in db.utxo_db._store


# ── R22: encode_undo / decode_undo round-trip ────────────────────────────────

def test_encode_decode_undo_roundtrip():
    """R22: encode_undo → decode_undo produces identical entries."""
    from electrumx.lib.util import encode_undo, decode_undo

    entries = [
        (b'\x01' * 10, b'\xff' * 20),
        (b'\x02' * 5, None),
        (b'', b''),
        (b'\x00' * 36, b'\xab\xcd' * 100),
    ]
    raw = encode_undo(entries)
    assert isinstance(raw, bytes)
    result = decode_undo(raw)
    assert result == entries


def test_encode_decode_undo_empty():
    """R22: empty entry list encodes to empty bytes and decodes cleanly."""
    from electrumx.lib.util import encode_undo, decode_undo

    assert encode_undo([]) == b''
    assert decode_undo(b'') == []


def test_encode_decode_undo_none_sentinel():
    """R22: None prev_value survives round-trip (not confused with empty bytes)."""
    from electrumx.lib.util import encode_undo, decode_undo

    entries = [(b'key', None), (b'key2', b'')]
    result = decode_undo(encode_undo(entries))
    assert result[0] == (b'key', None)
    assert result[1] == (b'key2', b'')


# ── R1: balance delete undo ───────────────────────────────────────────────────

def test_balance_delete_undo_on_reorg():
    """R1: zeroed balance entries (balance_deletes) are restored on reorg."""
    from electrumx.server.glyph_index import GlyphIndex, GlyphDBKeys
    import struct

    db = FakeDB()
    env = FakeEnv()
    idx = GlyphIndex(db, env)

    height = 500
    scripthash = b'\xaa' * 11  # HASHX_LEN = 11
    ref = b'\xbb' * 36

    # Pre-populate a balance and holder key
    balance_key = GlyphDBKeys.BALANCE + scripthash + ref
    holder_key = GlyphDBKeys.HOLDER_BY_REF + ref + scripthash
    old_balance = struct.pack('<Q', 1000)
    db.utxo_db._store[balance_key] = old_balance
    db.utxo_db._store[holder_key] = old_balance

    # Queue the keys for deletion (simulating a zero-balance flush)
    idx.balance_deletes = {balance_key, holder_key}
    idx.balance_height[balance_key] = height
    idx.balance_height[holder_key] = height

    batch = FakeBatch(db.utxo_db._store)
    idx.flush(batch)

    # After flush: keys should be deleted
    assert db.utxo_db.get(balance_key) is None
    assert db.utxo_db.get(holder_key) is None

    # Undo entry must exist
    undo_key = GlyphDBKeys.UNDO + height.to_bytes(4, 'big')
    assert undo_key in db.utxo_db._store

    # Reorg: backup restores the keys
    batch2 = FakeBatch(db.utxo_db._store)
    idx.backup(batch2, height)

    assert db.utxo_db.get(balance_key) == old_balance
    assert db.utxo_db.get(holder_key) == old_balance
    assert undo_key not in db.utxo_db._store


# ── R2: key reveal reorg ─────────────────────────────────────────────────────

def test_key_reveal_reorg():
    """R2: key reveal flushed atomically with undo; reorg removes it."""
    from electrumx.server.glyph_index import GlyphIndex, GlyphDBKeys

    db = FakeDB()
    env = FakeEnv()
    idx = GlyphIndex(db, env)

    height = 600
    ref = b'\xcc' * 36
    reveal_data = b'\xde\xad\xbe\xef' * 10

    # Queue a key reveal (as flush() will do)
    idx.key_reveal_cache[ref] = reveal_data
    idx.key_reveal_height[ref] = height

    batch = FakeBatch(db.utxo_db._store)
    idx.flush(batch)

    reveal_key = GlyphDBKeys.KEY_REVEALS + ref
    assert db.utxo_db.get(reveal_key) == reveal_data

    undo_key = GlyphDBKeys.UNDO + height.to_bytes(4, 'big')
    assert undo_key in db.utxo_db._store

    # Reorg: reveal should be removed
    batch2 = FakeBatch(db.utxo_db._store)
    idx.backup(batch2, height)

    assert db.utxo_db.get(reveal_key) is None
    assert undo_key not in db.utxo_db._store


# ── R21: schema version ───────────────────────────────────────────────────────

def test_schema_version_written_on_fresh_db():
    """R21: GVER=2 is written to a fresh DB on GlyphIndex init."""
    from electrumx.server.glyph_index import GlyphIndex, GlyphDBKeys, CURRENT_SCHEMA_VERSION

    db = FakeDB()
    env = FakeEnv()
    GlyphIndex(db, env)

    raw = db.utxo_db.get(GlyphDBKeys.SCHEMA_VERSION)
    assert raw is not None
    assert int.from_bytes(raw, 'big') == CURRENT_SCHEMA_VERSION


def test_schema_version_mismatch_raises():
    """R21: startup raises RuntimeError when DB schema version < current."""
    import pytest
    from electrumx.server.glyph_index import GlyphIndex, GlyphDBKeys

    db = FakeDB()
    # Write schema version 1 (old)
    db.utxo_db._store[GlyphDBKeys.SCHEMA_VERSION] = (1).to_bytes(4, 'big')

    env = FakeEnv()
    with pytest.raises(RuntimeError, match='reindex'):
        GlyphIndex(db, env)
