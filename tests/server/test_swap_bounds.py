"""Regression tests for swap order amount bounds + flush hardening.

order.price/amount are parsed from attacker-controlled RSWP OP_RETURN payloads.
An out-of-uint64 value would make struct.pack('>Q', price) in SwapIndex.flush()
raise struct.error inside the shared RocksDB write batch, aborting the whole
flush (UTXO + glyph + wave + state) and wedging the indexer.  These tests cover
the ingest-time rejection (_order_amounts_in_range) and the defensive flush guard.
"""

from types import SimpleNamespace

from electrumx.server.swap_index import (
    SwapIndex, SwapOrderInfo, OrderStatus, OrderSide,
    _order_amounts_in_range, MAX_UINT64,
)


def _order(**kw):
    o = SwapOrderInfo()
    for k, v in kw.items():
        setattr(o, k, v)
    return o


# --- ingest-time bounds check -------------------------------------------------

def test_in_range_accepts_valid():
    assert _order_amounts_in_range(
        _order(price=1000, amount=5, remaining_amount=5, side=OrderSide.SELL)
    )


def test_in_range_rejects_overflow():
    assert not _order_amounts_in_range(_order(price=MAX_UINT64 + 1))
    assert not _order_amounts_in_range(_order(price=2 ** 70))
    assert not _order_amounts_in_range(_order(amount=2 ** 64))
    assert not _order_amounts_in_range(_order(remaining_amount=2 ** 64))


def test_in_range_rejects_negative_and_bad_side():
    assert not _order_amounts_in_range(_order(price=-1))
    assert not _order_amounts_in_range(_order(side=-1))
    assert not _order_amounts_in_range(_order(side=256))


def test_in_range_accepts_max_uint64_boundary():
    assert _order_amounts_in_range(_order(price=MAX_UINT64, amount=MAX_UINT64))


# --- flush hardening (defense-in-depth) --------------------------------------

class _FakeUtxoDB:
    def get(self, _key):
        return None


class _FakeDB:
    def __init__(self):
        self.utxo_db = _FakeUtxoDB()
        self.db_height = 100


class _FakeBatch:
    def __init__(self):
        self.puts = []

    def put(self, key, value):
        self.puts.append((key, value))

    def delete(self, _key):
        pass


def _swap_index():
    env = SimpleNamespace(swap_index=True, reorg_limit=0)
    return SwapIndex(_FakeDB(), env)


def test_flush_skips_overflow_order_without_raising():
    si = _swap_index()
    bad = _order(order_id=b'\x01' * 36, base_ref=b'\x02' * 36,
                 quote_ref=b'\x03' * 36, status=OrderStatus.OPEN,
                 side=OrderSide.SELL, price=2 ** 70,
                 maker_scripthash=b'\x04' * 11)
    si.order_cache[bad.order_id] = bad
    si.order_height[bad.order_id] = 100

    batch = _FakeBatch()
    si.flush(batch)  # must NOT raise struct.error

    # The malformed order writes no keys of its own.
    assert all(bad.order_id not in key for key, _ in batch.puts)


def test_flush_writes_valid_order():
    si = _swap_index()
    good = _order(order_id=b'\x11' * 36, base_ref=b'\x02' * 36,
                  quote_ref=b'\x03' * 36, status=OrderStatus.OPEN,
                  side=OrderSide.SELL, price=123456,
                  maker_scripthash=b'\x04' * 11)
    si.order_cache[good.order_id] = good
    si.order_height[good.order_id] = 100

    batch = _FakeBatch()
    si.flush(batch)

    assert any(good.order_id in key for key, _ in batch.puts)


def test_flush_isolates_bad_order_from_good_order():
    si = _swap_index()
    good = _order(order_id=b'\x11' * 36, base_ref=b'\x02' * 36,
                  quote_ref=b'\x03' * 36, status=OrderStatus.OPEN,
                  side=OrderSide.SELL, price=123456,
                  maker_scripthash=b'\x04' * 11)
    bad = _order(order_id=b'\x01' * 36, base_ref=b'\x02' * 36,
                 quote_ref=b'\x03' * 36, status=OrderStatus.OPEN,
                 side=OrderSide.BUY, price=2 ** 70,
                 maker_scripthash=b'\x05' * 11)
    for o in (good, bad):
        si.order_cache[o.order_id] = o
        si.order_height[o.order_id] = 100

    batch = _FakeBatch()
    si.flush(batch)  # one bad order must not stop the good one from flushing

    assert any(good.order_id in key for key, _ in batch.puts)
    assert all(bad.order_id not in key for key, _ in batch.puts)
