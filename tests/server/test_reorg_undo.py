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


class FakeEnv:
    glyph_index = True
    wave_index = True
    swap_index = True
    wave_hot_names = 100
    wave_genesis_ref = None


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
