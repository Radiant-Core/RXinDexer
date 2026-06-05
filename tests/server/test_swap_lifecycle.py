"""Lifecycle tests for swap orders: close-on-spend + reorg reopen.

An order's ``order_id`` is byte-for-byte the backing outpoint it advertises
(``utxoHash + <utxoIndex LE u32>``).  When that outpoint is spent, the order can
no longer be filled, so SwapIndex closes it: rewrites the ORDER record with a
terminal status and deletes the OPEN_BY_PAIR / OPEN_BY_MAKER index entries.  A
chain reorg must undo all of that and reopen the order.

These tests drive ``process_tx``/``flush``/``backup`` against an in-memory DB +
batch that actually applies puts/deletes, so we can assert the on-disk state the
same way RocksDB would see it (including the reorg round-trip).
"""

import struct

from electrumx.server.swap_index import (
    SwapIndex, SwapOrderInfo, SwapDBKeys, OrderStatus, OrderSide,
)


# --- in-memory DB / batch (apply puts+deletes so we can read state back) ------

class _MemUtxoDB:
    """Minimal stand-in for db.utxo_db backed by a dict."""

    def __init__(self, store):
        self._store = store

    def get(self, key):
        return self._store.get(key)

    def iterator(self, prefix=b'', reverse=False, seek=None,
                 include_value=True):
        items = sorted((k, v) for k, v in self._store.items()
                       if k.startswith(prefix))
        if reverse:
            items = list(reversed(items))
        for k, v in items:
            yield (k, v) if include_value else (k, None)


class _MemDB:
    def __init__(self):
        self.store = {}
        self.utxo_db = _MemUtxoDB(self.store)
        self.db_height = 100


class _MemBatch:
    """A write batch that applies to the backing store on each call.

    Real RocksDB batches buffer until commit, but applying eagerly is fine for
    these single-threaded tests and lets a later flush/backup observe the
    effect.  Records the op log so tests can assert puts vs deletes.
    """

    def __init__(self, store):
        self._store = store
        self.puts = []
        self.deletes = []

    def put(self, key, value):
        self.puts.append((key, value))
        self._store[key] = value

    def delete(self, key):
        self.deletes.append(key)
        self._store.pop(key, None)


def _env():
    from types import SimpleNamespace
    return SimpleNamespace(swap_index=True, reorg_limit=10)


def _swap_index():
    return SwapIndex(_MemDB(), _env())


# --- fake tx with inputs/outputs ----------------------------------------------

class _Out:
    def __init__(self, pk_script):
        self.pk_script = pk_script


class _Tx:
    def __init__(self, outputs=()):
        self.outputs = list(outputs)


def _outpoint(seed):
    """Build a 36-byte outpoint (== order_id) from a byte seed."""
    return bytes([seed]) * 32 + struct.pack('<I', seed)


def _make_open_order(order_id, *, side=OrderSide.SELL, price=123456,
                     maker=b'\x04' * 11, base=None, quote=None):
    o = SwapOrderInfo()
    o.order_id = order_id
    o.tx_hash = order_id[:32]
    o.base_ref = base if base is not None else b'\x02' * 36
    o.quote_ref = quote if quote is not None else b'\x03' * 36
    o.side = side
    o.price = price
    o.amount = price
    o.remaining_amount = price
    o.status = OrderStatus.OPEN
    o.maker_scripthash = maker
    return o


def _stage_open(si, order, height):
    si.order_cache[order.order_id] = order
    si.order_height[order.order_id] = height


def _flush(si):
    batch = _MemBatch(si.db.store)
    si.flush(batch)
    return batch


# ---------------------------------------------------------------------------
# open path
# ---------------------------------------------------------------------------

def test_open_order_present_in_indexes_after_flush():
    si = _swap_index()
    order = _make_open_order(_outpoint(0x11))
    _stage_open(si, order, 100)

    pair_key = si._pair_key(order)
    maker_key = si._maker_key(order)
    order_key = SwapDBKeys.ORDER + order.order_id

    _flush(si)

    assert si.db.store.get(order_key) is not None
    assert pair_key in si.db.store
    assert maker_key in si.db.store
    # The stored ORDER row is OPEN.
    reread = SwapOrderInfo.from_bytes(si.db.store[order_key])
    assert reread.status == OrderStatus.OPEN


# ---------------------------------------------------------------------------
# close-on-spend
# ---------------------------------------------------------------------------

def test_spend_backing_outpoint_closes_order():
    si = _swap_index()
    order = _make_open_order(_outpoint(0x11))
    pair_key = si._pair_key(order)
    maker_key = si._maker_key(order)
    order_key = SwapDBKeys.ORDER + order.order_id

    _stage_open(si, order, 100)
    _flush(si)
    assert pair_key in si.db.store and maker_key in si.db.store

    # A later block spends the backing outpoint.
    spend_tx_hash = b'\xee' * 32
    si.process_tx(spend_tx_hash, _Tx(), 101, 0, None,
                  spent_outpoints={order.order_id})
    batch = _flush(si)

    # ORDER rewritten with terminal status.
    closed = SwapOrderInfo.from_bytes(si.db.store[order_key])
    assert closed.status == OrderStatus.FILLED
    assert closed.cancel_height == 101
    assert closed.cancel_txid == spend_tx_hash
    # Both index keys deleted.
    assert pair_key not in si.db.store
    assert maker_key not in si.db.store
    assert pair_key in batch.deletes
    assert maker_key in batch.deletes


def test_close_is_idempotent_on_double_spend():
    si = _swap_index()
    order = _make_open_order(_outpoint(0x22))
    _stage_open(si, order, 100)
    _flush(si)

    # First spend closes it.
    si.process_tx(b'\xaa' * 32, _Tx(), 101, 0, None,
                  spent_outpoints={order.order_id})
    _flush(si)
    closed = SwapOrderInfo.from_bytes(
        si.db.store[SwapDBKeys.ORDER + order.order_id])
    assert closed.status == OrderStatus.FILLED
    first_height = closed.cancel_height

    # A second spend of the same outpoint must be a no-op (nothing re-staged).
    si.process_tx(b'\xbb' * 32, _Tx(), 102, 0, None,
                  spent_outpoints={order.order_id})
    assert order.order_id not in si.order_cache  # not re-staged
    batch = _flush(si)
    assert batch.deletes == []  # nothing to do
    still = SwapOrderInfo.from_bytes(
        si.db.store[SwapDBKeys.ORDER + order.order_id])
    assert still.status == OrderStatus.FILLED
    assert still.cancel_height == first_height  # unchanged


def test_same_block_create_and_spend_never_indexes_open():
    si = _swap_index()
    order = _make_open_order(_outpoint(0x33))
    pair_key = si._pair_key(order)
    maker_key = si._maker_key(order)

    # Order created this block (staged in cache), then a later tx in the SAME
    # block spends its backing outpoint before any flush.
    _stage_open(si, order, 100)
    si.process_tx(b'\xcc' * 32, _Tx(), 100, 5, None,
                  spent_outpoints={order.order_id})
    _flush(si)

    # It must never have appeared in the OPEN_BY_* indexes.
    assert pair_key not in si.db.store
    assert maker_key not in si.db.store
    closed = SwapOrderInfo.from_bytes(
        si.db.store[SwapDBKeys.ORDER + order.order_id])
    assert closed.status == OrderStatus.FILLED


def test_unknown_outpoint_spend_writes_nothing():
    si = _swap_index()
    unknown = _outpoint(0x44)

    si.process_tx(b'\xdd' * 32, _Tx(), 101, 0, None,
                  spent_outpoints={unknown})
    batch = _flush(si)

    # No record (no tombstone) for an unknown outpoint.
    assert SwapDBKeys.ORDER + unknown not in si.db.store
    assert batch.puts == []
    assert batch.deletes == []
    assert unknown not in si.order_cache


def test_multiple_orders_and_spends_in_one_tx():
    si = _swap_index()
    o1 = _make_open_order(_outpoint(0x51), maker=b'\x0a' * 11)
    o2 = _make_open_order(_outpoint(0x52), maker=b'\x0b' * 11)
    for o in (o1, o2):
        _stage_open(si, o, 100)
    _flush(si)

    # One tx spends both backing outpoints + one unknown one.
    si.process_tx(b'\xff' * 32, _Tx(), 101, 0, None,
                  spent_outpoints={o1.order_id, o2.order_id, _outpoint(0x99)})
    _flush(si)

    for o in (o1, o2):
        closed = SwapOrderInfo.from_bytes(
            si.db.store[SwapDBKeys.ORDER + o.order_id])
        assert closed.status == OrderStatus.FILLED
        assert si._pair_key(o) not in si.db.store
        assert si._maker_key(o) not in si.db.store


# ---------------------------------------------------------------------------
# reorg
# ---------------------------------------------------------------------------

def test_reorg_reopens_closed_order():
    si = _swap_index()
    order = _make_open_order(_outpoint(0x61))
    pair_key = si._pair_key(order)
    maker_key = si._maker_key(order)
    order_key = SwapDBKeys.ORDER + order.order_id

    # H=100: open.
    _stage_open(si, order, 100)
    _flush(si)
    open_cbor = si.db.store[order_key]
    assert pair_key in si.db.store and maker_key in si.db.store

    # H=101: spent -> closed.
    si.process_tx(b'\xee' * 32, _Tx(), 101, 0, None,
                  spent_outpoints={order.order_id})
    _flush(si)
    assert pair_key not in si.db.store and maker_key not in si.db.store
    assert SwapOrderInfo.from_bytes(si.db.store[order_key]).status \
        == OrderStatus.FILLED

    # Reorg unwinds H=101: order reopened, indexes restored.
    backup_batch = _MemBatch(si.db.store)
    si.backup(backup_batch, 101)

    assert si.db.store[order_key] == open_cbor  # ORDER restored to OPEN cbor
    assert SwapOrderInfo.from_bytes(si.db.store[order_key]).status \
        == OrderStatus.OPEN
    assert si.db.store.get(pair_key) == b''   # OPEN_BY_PAIR restored to b''
    assert si.db.store.get(maker_key) == b''  # OPEN_BY_MAKER restored to b''


def test_reorg_of_same_block_create_and_close_leaves_nothing():
    si = _swap_index()
    order = _make_open_order(_outpoint(0x71))
    pair_key = si._pair_key(order)
    maker_key = si._maker_key(order)
    order_key = SwapDBKeys.ORDER + order.order_id

    # Both create and close happen at H=100, in one flush.
    _stage_open(si, order, 100)
    si.process_tx(b'\xcc' * 32, _Tx(), 100, 5, None,
                  spent_outpoints={order.order_id})
    _flush(si)
    assert SwapOrderInfo.from_bytes(si.db.store[order_key]).status \
        == OrderStatus.FILLED
    assert pair_key not in si.db.store and maker_key not in si.db.store

    # Reorg unwinds H=100: prev on-disk value for every touched key was None,
    # so the order vanishes entirely with no leaked index keys.
    backup_batch = _MemBatch(si.db.store)
    si.backup(backup_batch, 100)

    assert order_key not in si.db.store
    assert pair_key not in si.db.store
    assert maker_key not in si.db.store


# ---------------------------------------------------------------------------
# bounds regression: closed order reconstructs the exact written key
# ---------------------------------------------------------------------------

def test_closed_order_reconstructs_exact_pair_key_buy_and_sell():
    si = _swap_index()
    for side in (OrderSide.BUY, OrderSide.SELL):
        seed = 0x80 + side
        order = _make_open_order(_outpoint(seed), side=side, price=987654)
        key_at_open = si._pair_key(order)

        _stage_open(si, order, 100)
        _flush(si)
        assert key_at_open in si.db.store

        # Close: the close path must delete the SAME key bytes (BUY inverts
        # price, SELL does not) so no phantom orderbook key is left behind.
        si.process_tx(bytes([seed]) * 32, _Tx(), 101, 0, None,
                      spent_outpoints={order.order_id})
        # The re-staged (now FILLED) order reconstructs the identical key.
        assert si._pair_key(si.order_cache[order.order_id]) == key_at_open
        _flush(si)
        assert key_at_open not in si.db.store
