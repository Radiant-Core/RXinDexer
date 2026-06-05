import contextlib
import struct


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


class FakeCoin:
    VALUE_PER_COIN = 100_000_000
    P2PKH_VERBYTE = bytes.fromhex("00")
    P2SH_VERBYTES = [bytes.fromhex("05")]


class FakeDB:
    def __init__(self):
        self.utxo_db = FakeUtxoDB()
        self.db_height = 100

    def fs_tx_hash(self, tx_num):
        return tx_num.to_bytes(32, "little"), 10 + tx_num


class FakeEnv:
    analytics_index = True
    reorg_limit = 10
    coin = FakeCoin()


def test_analytics_undo_backup_roundtrip():
    from electrumx.server.analytics_index import AnalyticsDBKeys, AnalyticsIndex

    db = FakeDB()
    env = FakeEnv()
    idx = AnalyticsIndex(db, env)

    height = 120
    hashX = b"\x11" * 11
    balance_key = AnalyticsDBKeys.BALANCE + hashX
    db.utxo_db._store[balance_key] = struct.pack("<Q", 50 * env.coin.VALUE_PER_COIN)

    idx.process_block(
        height,
        spends=[],
        adds=[(b"\xaa" * 32, 0, hashX, 75 * env.coin.VALUE_PER_COIN, "addr1")],
    )

    idx.flush(FakeBatch(db.utxo_db._store))

    undo_key = AnalyticsDBKeys.UNDO + height.to_bytes(4, "big")
    assert undo_key in db.utxo_db._store
    assert struct.unpack("<Q", db.utxo_db.get(balance_key))[0] == 125 * env.coin.VALUE_PER_COIN

    idx.backup(FakeBatch(db.utxo_db._store), height)

    assert struct.unpack("<Q", db.utxo_db.get(balance_key))[0] == 50 * env.coin.VALUE_PER_COIN
    assert undo_key not in db.utxo_db._store


def test_analytics_process_block_updates_summaries():
    from electrumx.server.analytics_index import AnalyticsIndex
    from electrumx.lib.hash import Base58
    from electrumx.lib.script import ScriptPubKey

    db = FakeDB()
    env = FakeEnv()
    idx = AnalyticsIndex(db, env)

    hashX1 = b"\x01" * 11
    hashX2 = b"\x02" * 11
    tx1 = b"\xaa" * 32
    tx2 = b"\xbb" * 32
    addr_two_hash160 = b"\x22" * 20
    addr_two = Base58.encode_check(env.coin.P2PKH_VERBYTE + addr_two_hash160)

    idx.process_block(
        144,
        spends=[],
        adds=[
            (tx1, 0, hashX1, 2 * env.coin.VALUE_PER_COIN, "addr-one"),
            (tx2, 1, hashX2, 20 * env.coin.VALUE_PER_COIN, ScriptPubKey.P2PKH_script(addr_two_hash160)),
        ],
    )
    idx.flush(FakeBatch(db.utxo_db._store))

    top = idx.get_top_addresses(limit=10, offset=0)
    assert top["total"] == 2
    assert top["rows"][0]["address"] == addr_two
    assert top["rows"][0]["balance"] == 20 * env.coin.VALUE_PER_COIN

    # get_balance_distribution() returns {bucket: {"count": N, "amount": sats}}.
    distribution = idx.get_balance_distribution()
    assert distribution["10-100"]["count"] == 1
    assert distribution["1-10"]["count"] == 1

    aging = idx.get_supply_aging()
    assert aging["<1d"] == 22 * env.coin.VALUE_PER_COIN

    movement = idx.get_movement(days=1)
    assert movement["series"][-1]["new_addresses"] == 2
    assert movement["series"][-1]["active_addresses"] == 2
    assert movement["series"][-1]["coins_moved"] == 0

    stats = idx.get_stats()
    assert stats["enabled"] is True
    assert stats["last_processed_height"] == 144
    assert stats["rich_list_entries"] == 2


# ---------------------------------------------------------------------------
# M1 (DoS): the rich-list / get_stats full keyspace scan must be cached so an
# attacker rotating `offset` cannot force a fresh scan on every request.
# ---------------------------------------------------------------------------

class _CountingUtxoDB(FakeUtxoDB):
    """FakeUtxoDB that counts how many times the BALANCE prefix is scanned."""

    def __init__(self):
        super().__init__()
        self.balance_scan_count = 0

    def iterator(self, prefix=b"", reverse=False, include_value=True):
        from electrumx.server.analytics_index import AnalyticsDBKeys
        if prefix == AnalyticsDBKeys.BALANCE:
            self.balance_scan_count += 1
        return super().iterator(prefix=prefix, reverse=reverse, include_value=include_value)


def _seed_balances(db, env, n=50):
    from electrumx.server.analytics_index import AnalyticsDBKeys
    for i in range(n):
        hashX = bytes([i % 256]) * 11
        key = AnalyticsDBKeys.BALANCE + hashX + i.to_bytes(2, "big")
        db.utxo_db._store[key] = struct.pack("<Q", (i + 1) * env.coin.VALUE_PER_COIN)


def test_top_addresses_scan_cached_across_offset_rotation():
    """Rotating `offset` must reuse the cached scan, not re-scan the keyspace."""
    from electrumx.server.analytics_index import AnalyticsIndex

    db = FakeDB()
    db.utxo_db = _CountingUtxoDB()
    env = FakeEnv()
    _seed_balances(db, env, n=50)

    idx = AnalyticsIndex(db, env)

    # Rotate offset across many requests (the attacker pattern).
    for off in range(0, 30, 5):
        idx.get_top_addresses(limit=10, offset=off)

    # get_stats() reuses the same cached pool — still no extra scan.
    idx.get_stats()

    # Despite 6 paginated calls + get_stats, the BALANCE keyspace is scanned once.
    assert db.utxo_db.balance_scan_count == 1


def test_top_addresses_offset_clamped_to_pool():
    """An over-cap offset is clamped at the index layer (defence in depth)."""
    from electrumx.server import analytics_index as ai

    db = FakeDB()
    db.utxo_db = _CountingUtxoDB()
    env = FakeEnv()
    _seed_balances(db, env, n=10)

    idx = ai.AnalyticsIndex(db, env)
    huge = ai.TOP_ADDRESSES_MAX_OFFSET + 1_000_000
    result = idx.get_top_addresses(limit=10, offset=huge)
    # Clamped offset never exceeds the cap and never triggers a second scan.
    assert result["offset"] == ai.TOP_ADDRESSES_MAX_OFFSET
    assert db.utxo_db.balance_scan_count == 1


def test_movement_stops_scanning_past_window():
    """get_movement must not read day-keys beyond current_day (bounded scan)."""
    from electrumx.server.analytics_index import AnalyticsDBKeys, AnalyticsIndex
    import json as _json

    db = FakeDB()
    env = FakeEnv()
    idx = AnalyticsIndex(db, env)

    # last_processed_height=144 -> current_day = 1. Seed days 0..5; days 2..5
    # are "future" relative to the window and must not be decoded.
    idx._set_summary(0, b"last_processed_height", 144)
    idx.flush(FakeBatch(db.utxo_db._store))
    for day in range(0, 6):
        key = AnalyticsDBKeys.DAILY + day.to_bytes(4, "big")
        db.utxo_db._store[key] = _json.dumps(
            {"coins_moved": day, "active_addresses": day, "new_addresses": day}
        ).encode()

    # Sentinel: a non-JSON value on a future day would raise if it were read.
    db.utxo_db._store[AnalyticsDBKeys.DAILY + (5).to_bytes(4, "big")] = b"NOT_JSON"

    result = idx.get_movement(days=2)  # window = days [0, 1]
    assert result["days"] == 2
    returned_days = {item["day"] for item in result["series"]}
    assert returned_days == {0, 1}
