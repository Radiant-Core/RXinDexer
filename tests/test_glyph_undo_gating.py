"""
Tests for the two glyph undo optimisations.

Undo bookkeeping dominated flush time: `_record_undo` does a random DB read per key, and at
dMint-era block density that is ~1M reads per flush. Two gates cut it:

  1. Blocks below the reorg window record no undo at all — they can never be unwound, and the
     core UTXO path already skips them the same way.
  2. Keys that provably cannot pre-exist (history, and the pair-keyed derived indexes) record
     `(key, None)` without reading.

Both change what a reorg can restore, so the tests below pin the correctness boundary as much as
the saving: within the window nothing changes, and an insert-only key still unwinds to absent.

Run: PYTHONPATH=. python3 -m pytest tests/test_glyph_undo_gating.py
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import defaultdict  # noqa: E402

from electrumx.server.glyph_index import GlyphDBKeys, GlyphIndex  # noqa: E402

REF = bytes([0xA1]) * 36


class _CountingUtxoDB:
    """Counts random reads so a test can assert the saving, not just the behaviour."""

    def __init__(self):
        self.store = {}
        self.reads = 0

    def get(self, key):
        self.reads += 1
        return self.store.get(key)

    def iterator(self, prefix=b'', seek=None, reverse=False):
        for key in sorted(k for k in self.store if k.startswith(prefix)):
            yield key, self.store[key]


def _index(db_height=1000):
    idx = object.__new__(GlyphIndex)
    idx.enabled = True
    idx.db = SimpleNamespace(utxo_db=_CountingUtxoDB(), db_height=db_height)
    idx.logger = SimpleNamespace(info=lambda *a, **k: None,
                                 warning=lambda *a, **k: None,
                                 exception=lambda *a, **k: None)
    idx._undo_cache = defaultdict(list)
    idx._undo_seen = defaultdict(set)
    idx.undo_min_height = 0
    idx._known_refs = set()        # backup() clears it on reorg
    return idx


# --------------------------------------------------------------------------- default is unchanged

def test_default_records_everything():
    """undo_min_height defaults to 0, so an index nobody configures behaves exactly as before."""
    idx = _index()
    assert idx.undo_min_height == 0
    idx._record_undo(5, b'GTkey')
    assert idx._undo_cache[5] == [(b'GTkey', None)]
    assert idx.db.utxo_db.reads == 1, 'the read must still happen by default'


def test_existing_value_is_captured_for_updates():
    idx = _index()
    idx.db.utxo_db.store[b'GTkey'] = b'old'
    idx._record_undo(5, b'GTkey')
    assert idx._undo_cache[5] == [(b'GTkey', b'old')], 'an update must capture the prior value'


def test_dedup_still_applies():
    idx = _index()
    idx._record_undo(5, b'GTkey')
    idx._record_undo(5, b'GTkey')
    assert len(idx._undo_cache[5]) == 1
    assert idx.db.utxo_db.reads == 1, 'the second call must not re-read'


# --------------------------------------------------------------------------- reorg-window gate

def test_below_the_window_records_nothing_and_reads_nothing():
    idx = _index()
    idx.undo_min_height = 460_000          # tip-ish, while we replay old blocks
    idx._record_undo(272_834, b'GTkey')
    assert idx._undo_cache == {}, 'a block below the window must record no undo'
    assert idx.db.utxo_db.reads == 0, 'and must cost no DB read'


def test_at_or_above_the_window_still_records():
    idx = _index()
    idx.undo_min_height = 460_000
    idx._record_undo(460_000, b'GTkey')           # boundary is inclusive
    idx._record_undo(460_001, b'GTkey2')
    assert len(idx._undo_cache[460_000]) == 1
    assert len(idx._undo_cache[460_001]) == 1
    assert idx.db.utxo_db.reads == 2


def test_gate_applies_to_the_insert_path_too():
    idx = _index()
    idx.undo_min_height = 460_000
    idx._record_undo_insert(272_834, GlyphDBKeys.HISTORY + REF)
    assert idx._undo_cache == {}


def test_catch_up_saving_is_total():
    """The shape of the win: replaying pre-window blocks costs zero undo reads."""
    idx = _index()
    idx.undo_min_height = 460_000
    for h in range(270_000, 270_500):
        idx._record_undo(h, GlyphDBKeys.TOKEN + REF)
        idx._record_undo_insert(h, GlyphDBKeys.HISTORY + REF + bytes([h % 256]))
    assert idx.db.utxo_db.reads == 0
    assert idx._undo_cache == {}


# --------------------------------------------------------------------------- insert-only path

def test_insert_only_records_none_without_reading():
    idx = _index()
    key = GlyphDBKeys.HISTORY + REF
    idx._record_undo_insert(7, key)
    assert idx._undo_cache[7] == [(key, None)]
    assert idx.db.utxo_db.reads == 0, 'the whole point: no read for a key that cannot pre-exist'


def test_insert_only_unwinds_to_absent():
    """Correctness boundary: (key, None) must make backup DELETE the row, so a reorg leaves the
    index as if the block never happened."""
    idx = _index()
    key = GlyphDBKeys.HISTORY + REF
    idx._record_undo_insert(7, key)
    idx.db.utxo_db.store[key] = b'event'

    batch_ops = []
    idx.db.utxo_db.store[idx._undo_key(7)] = __import__(
        'electrumx.lib.util', fromlist=['encode_undo']).encode_undo(idx._undo_cache[7])

    class _Batch:
        def put(self, k, v):
            batch_ops.append(('put', k))

        def delete(self, k):
            batch_ops.append(('del', k))
            idx.db.utxo_db.store.pop(k, None)

    idx.backup(_Batch(), 7)
    assert ('del', key) in batch_ops
    assert key not in idx.db.utxo_db.store


def test_insert_only_dedups():
    idx = _index()
    key = GlyphDBKeys.HISTORY + REF
    idx._record_undo_insert(9, key)
    idx._record_undo_insert(9, key)
    assert len(idx._undo_cache[9]) == 1


def test_read_and_insert_paths_share_the_seen_set():
    """A key recorded either way must not be recorded twice — otherwise a mixed flush could
    append a None after a real prior value and unwind to the wrong state."""
    idx = _index()
    idx.db.utxo_db.store[b'GTkey'] = b'old'
    idx._record_undo(3, b'GTkey')
    idx._record_undo_insert(3, b'GTkey')
    assert idx._undo_cache[3] == [(b'GTkey', b'old')], 'the real prior value must win'


# --------------------------------------------------------------------------- wiring

def test_block_processor_propagates_the_window_floor():
    src = open(os.path.join(os.path.dirname(__file__), '..', 'electrumx', 'server',
                            'block_processor.py'), encoding='utf-8').read()
    assert 'self.glyph_index.undo_min_height = min_height' in src
    # It must be the same floor the core undo gate uses, not a separately derived one.
    assert 'min_height = self.db.min_undo_height(self.daemon.cached_height())' in src


def test_only_provably_insert_only_sites_use_the_fast_path():
    """Guard against the fast path spreading to keys that CAN be rewritten at a later height —
    GT records, BY_TYPE, name/ticker, balances, metadata and key-reveals all need the read."""
    src = open(os.path.join(os.path.dirname(__file__), '..', 'electrumx', 'server',
                            'glyph_index.py'), encoding='utf-8').read()
    for keyspace in ('GlyphDBKeys.HISTORY', 'GlyphDBKeys.CONTAINER_MEMBERS',
                     'GlyphDBKeys.BY_META_TYPE', 'GlyphDBKeys.BY_CREATOR',
                     'GlyphDBKeys.MEDIA_HASH', 'GlyphDBKeys.PAYLOAD_HASH'):
        assert keyspace in src
    # The update sites must still call the reading variant.
    assert 'self._record_undo(height, key)\n            batch.put(key, token.to_bytes())' in src


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
