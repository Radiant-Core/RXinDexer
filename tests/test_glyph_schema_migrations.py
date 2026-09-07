"""
Tests for the in-place Glyph schema migrations v4->v5 (GCM) and v5->v6 (GMT).

The property that matters: a migrator must derive keys BYTE-IDENTICAL to the live write path in
GlyphIndex.process_tx. A divergence would silently produce rows that live writes never match and
queries never find — an index that looks populated but answers wrong.

GT/GM rows are CBOR-encoded, and cbor2 is not always present in a bare checkout, so the CBOR layer
is stubbed here. What remains under test is exactly the code these migrations added: the page/seek
walk, the metadata gating, and the key derivation.

Run: PYTHONPATH=. python3 -m pytest tests/test_glyph_schema_migrations.py
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import electrumx.server.glyph_index as gi  # noqa: E402
from electrumx.server.glyph_index import GlyphDBKeys, GlyphIndex  # noqa: E402
from electrumx.lib.hash import sha256  # noqa: E402

REF_A = bytes([0xA1]) * 36
REF_B = bytes([0xB2]) * 36
REF_C = bytes([0xC3]) * 36
CONTAINER = bytes([0xDD]) * 36


class _Batch:
    def __init__(self, store):
        self.store = store

    def put(self, key, value):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _StubUtxoDB:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def put(self, key, value):
        self.store[key] = value

    def write_batch(self):
        return _Batch(self.store)

    def iterator(self, prefix=b'', seek=None, reverse=False):
        keys = sorted(k for k in self.store if k.startswith(prefix))
        if seek is not None:
            keys = [k for k in keys if k >= seek]
        for key in keys:
            yield key, self.store[key]


class _Logger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def exception(self, *a, **k):
        pass


class _CBORFreeToken:
    """Stand-in for GlyphTokenInfo: the migrators only read `metadata_hash`."""

    MARKER = b'MH:'

    @classmethod
    def from_bytes(cls, data):
        if not data.startswith(cls.MARKER):
            raise ValueError('not a token row')
        return SimpleNamespace(metadata_hash=data[len(cls.MARKER):])


def _index(rows, metadata_by_hash, monkeypatch_target=gi):
    """Build a bare GlyphIndex over a stub DB. __init__ opens real storage, so bypass it and wire
    in only what the migrators touch."""
    idx = object.__new__(GlyphIndex)
    idx.db = SimpleNamespace(utxo_db=_StubUtxoDB())
    idx.logger = _Logger()
    idx.metadata_cache = {}
    for ref, meta_hash in rows:
        idx.db.utxo_db.store[GlyphDBKeys.TOKEN + ref] = _CBORFreeToken.MARKER + meta_hash
    idx.get_metadata = lambda h: metadata_by_hash.get(h)
    return idx


def _run(idx, migrator_name):
    saved = gi.GlyphTokenInfo.from_bytes
    gi.GlyphTokenInfo.from_bytes = _CBORFreeToken.from_bytes
    try:
        return getattr(idx, migrator_name)()
    finally:
        gi.GlyphTokenInfo.from_bytes = saved


def _written(idx, prefix):
    return sorted(k for k in idx.db.utxo_db.store if k.startswith(prefix))


# --------------------------------------------------------------------------- v4 -> v5 (GCM)

def test_gcm_migration_derives_the_live_key():
    idx = _index([(REF_A, b'h1')], {b'h1': {'in': [CONTAINER]}})
    assert _run(idx, '_migrate_4_to_5') == 1
    # Exactly the key the live flush path writes: GCM + container_ref + member_ref.
    assert _written(idx, GlyphDBKeys.CONTAINER_MEMBERS) == [
        GlyphDBKeys.CONTAINER_MEMBERS + CONTAINER + REF_A
    ]


def test_gcm_migration_unwraps_cbor_tags():
    tagged = SimpleNamespace(value=CONTAINER)   # what cbor2 hands back for a tagged bytestring
    idx = _index([(REF_A, b'h1')], {b'h1': {'in': [tagged]}})
    assert _run(idx, '_migrate_4_to_5') == 1
    assert _written(idx, GlyphDBKeys.CONTAINER_MEMBERS) == [
        GlyphDBKeys.CONTAINER_MEMBERS + CONTAINER + REF_A
    ]


def test_gcm_migration_ignores_malformed_in_fields():
    idx = _index(
        [(REF_A, b'h1'), (REF_B, b'h2'), (REF_C, b'h3')],
        {
            b'h1': {'in': []},                    # empty
            b'h2': {'in': [b'\x01' * 35]},        # wrong length
            b'h3': {'in': 'not-a-list'},          # wrong type
        },
    )
    assert _run(idx, '_migrate_4_to_5') == 0
    assert _written(idx, GlyphDBKeys.CONTAINER_MEMBERS) == []


def test_gcm_migration_is_idempotent():
    idx = _index([(REF_A, b'h1')], {b'h1': {'in': [CONTAINER]}})
    _run(idx, '_migrate_4_to_5')
    before = dict(idx.db.utxo_db.store)
    _run(idx, '_migrate_4_to_5')
    assert idx.db.utxo_db.store == before


# --------------------------------------------------------------------------- v5 -> v6 (GMT)

def test_gmt_migration_matches_live_normalisation():
    idx = _index([(REF_A, b'h1')], {b'h1': {'type': '  User  '}})
    assert _run(idx, '_migrate_5_to_6') == 1
    # Live path: sha256(raw.strip().lower())[:16] -- normalisation must match or nothing resolves.
    expected = GlyphDBKeys.BY_META_TYPE + sha256(b'user')[:16] + REF_A
    assert _written(idx, GlyphDBKeys.BY_META_TYPE) == [expected]


def test_gmt_migration_skips_empty_and_non_string_types():
    idx = _index(
        [(REF_A, b'h1'), (REF_B, b'h2'), (REF_C, b'h3')],
        {b'h1': {'type': '   '}, b'h2': {'type': 123}, b'h3': {}},
    )
    assert _run(idx, '_migrate_5_to_6') == 0
    assert _written(idx, GlyphDBKeys.BY_META_TYPE) == []


def test_gmt_migration_groups_refs_under_one_type():
    idx = _index(
        [(REF_A, b'h1'), (REF_B, b'h2')],
        {b'h1': {'type': 'container'}, b'h2': {'type': 'CONTAINER'}},
    )
    assert _run(idx, '_migrate_5_to_6') == 2
    type_hash = sha256(b'container')[:16]
    assert _written(idx, GlyphDBKeys.BY_META_TYPE) == sorted([
        GlyphDBKeys.BY_META_TYPE + type_hash + REF_A,
        GlyphDBKeys.BY_META_TYPE + type_hash + REF_B,
    ])


# --------------------------------------------------------------------------- shared driver

def test_tokens_without_metadata_are_skipped():
    # No metadata_hash, and a hash whose GM row is gone (denylist-scrubbed) -- both are skipped
    # rather than raising, since there is nothing to derive a key from.
    idx = _index([(REF_A, b''), (REF_B, b'missing')], {})
    assert _run(idx, '_migrate_4_to_5') == 0
    assert _run(idx, '_migrate_5_to_6') == 0


def test_unparseable_token_rows_do_not_abort_the_walk():
    idx = _index([(REF_A, b'h1')], {b'h1': {'type': 'user'}})
    idx.db.utxo_db.store[GlyphDBKeys.TOKEN + REF_B] = b'GARBAGE'   # fails from_bytes
    idx.db.utxo_db.store[GlyphDBKeys.TOKEN + b'\x00' * 4] = b'MH:h1'  # ref wrong length
    assert _run(idx, '_migrate_5_to_6') == 1


def test_page_walk_resumes_strictly_after_the_last_key():
    rows = [(bytes([i]) * 36, b'h%d' % i) for i in range(1, 6)]
    idx = _index(rows, {})
    seen = []
    seek = GlyphDBKeys.TOKEN
    saved = gi.GlyphTokenInfo.from_bytes
    gi.GlyphTokenInfo.from_bytes = _CBORFreeToken.from_bytes
    try:
        while True:
            items, seek = idx._read_token_page(seek, 2)
            seen.extend(ref for ref, _t in items)
            if seek is None:
                break
    finally:
        gi.GlyphTokenInfo.from_bytes = saved
    # Every row exactly once, in key order -- no duplicates from an inclusive seek, no gaps.
    assert seen == [ref for ref, _h in rows]


def test_migration_chain_is_registered_for_every_step():
    """Every version from 3 up to CURRENT must have an in-place migrator.

    A gap here is what produced "version 4 < 6 has no in-place migration" on deploy. Derived from
    CURRENT_SCHEMA_VERSION rather than hard-coded, so bumping the schema without adding a migrator
    fails this test instead of silently shipping a hard-fail to production.
    """
    from electrumx.server.glyph_index import CURRENT_SCHEMA_VERSION

    src = open(os.path.join(os.path.dirname(__file__), '..', 'electrumx', 'server',
                            'glyph_index.py'), encoding='utf-8').read()
    for v in range(3, CURRENT_SCHEMA_VERSION):
        step = f'{v}: self._migrate_{v}_to_{v + 1}'
        assert step in src, (
            f'schema v{v} -> v{v + 1} has no registered in-place migrator; '
            f'a node at v{v} would refuse to start')
        assert f'def _migrate_{v}_to_{v + 1}' in src, f'missing _migrate_{v}_to_{v + 1} definition'


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))


# --------------------------------------------------------------- v8 -> v9 (GH tx_idx widening)
#
# The v8 GH key packed tx_idx as '>H'. That held only while every writer recorded a ref's FIRST
# sighting; record_ref_hop writes a row per singleton movement, so the first mainnet block with
# more than 65,535 transactions raised struct.error inside advance_txs and terminated the server
# mid-sync (2026-09-06, resync at ~443,590 — every block since the last flush was lost).

def _v8_history_key(ref, height, tx_idx):
    """The pre-v9 key, packed the way the old pack_history_key did."""
    import struct
    return (GlyphDBKeys.HISTORY + ref
            + struct.pack('>I', height) + struct.pack('>H', tx_idx))


def _history_index(v8_rows):
    idx = object.__new__(GlyphIndex)
    idx.db = SimpleNamespace(utxo_db=_StubUtxoDB())
    idx.logger = _Logger()
    for ref, height, tx_idx, value in v8_rows:
        idx.db.utxo_db.store[_v8_history_key(ref, height, tx_idx)] = value
    return idx


def test_the_bug_a_block_over_65535_txs_no_longer_raises():
    """The exact failure: struct.error: 'H' format requires 0 <= number <= 65535."""
    key = gi.pack_history_key(REF_A, 443_591, 70_000)
    assert gi.unpack_history_key_tail(key) == (443_591, 70_000)


def test_tx_idx_survives_the_full_32_bit_range():
    for tx_idx in (0, 1, 65_535, 65_536, 1_000_000, 0xFFFFFFFF):
        key = gi.pack_history_key(REF_A, 443_591, tx_idx)
        assert gi.unpack_history_key_tail(key) == (443_591, tx_idx)


def test_v9_keys_still_sort_height_then_tx_idx():
    """Ordering is load-bearing: /tokens/{ref}/history and the location chain both rely on a
    forward prefix scan being chronological."""
    keys = [gi.pack_history_key(REF_A, h, t)
            for h, t in [(2, 0), (1, 70_000), (1, 3), (1, 65_536), (2, 70_000)]]
    assert sorted(keys) == [
        gi.pack_history_key(REF_A, 1, 3),
        gi.pack_history_key(REF_A, 1, 65_536),
        gi.pack_history_key(REF_A, 1, 70_000),
        gi.pack_history_key(REF_A, 2, 0),
        gi.pack_history_key(REF_A, 2, 70_000),
    ]


def test_v9_migration_rewrites_keys_and_preserves_values():
    rows = [(REF_A, 100, 0, b'v0'), (REF_A, 100, 7, b'v7'), (REF_B, 250, 65_535, b'vmax')]
    idx = _history_index(rows)
    assert idx._migrate_8_to_9() == 3
    store = idx.db.utxo_db.store
    for ref, height, tx_idx, value in rows:
        assert store[gi.pack_history_key(ref, height, tx_idx)] == value
        assert _v8_history_key(ref, height, tx_idx) not in store, 'old key must be removed'
    assert len(store) == 3, 'no strays'


def test_v9_migration_is_idempotent():
    """A run interrupted before the version stamp is repeated on the next start."""
    idx = _history_index([(REF_A, 100, 5, b'v5')])
    assert idx._migrate_8_to_9() == 1
    before = dict(idx.db.utxo_db.store)
    assert idx._migrate_8_to_9() == 0, 'already-widened keys must not be rewritten'
    assert idx.db.utxo_db.store == before


def test_v9_migration_handles_tx_idx_zero():
    """tx_idx == 0 is the one case where the old key is a proper PREFIX of the new one, so the
    walk re-reads the row it just wrote. Length, not content, is the test."""
    idx = _history_index([(REF_A, 100, 0, b'z')])
    assert idx._migrate_8_to_9() == 1
    assert idx.db.utxo_db.store == {gi.pack_history_key(REF_A, 100, 0): b'z'}


def test_v9_migration_leaves_other_keyspaces_alone():
    idx = _history_index([(REF_A, 100, 1, b'v')])
    idx.db.utxo_db.store[GlyphDBKeys.TOKEN + REF_B] = b'token'
    idx.db.utxo_db.store[GlyphDBKeys.OWNER + b'\x01' * 11] = b'owner'
    idx._migrate_8_to_9()
    assert idx.db.utxo_db.store[GlyphDBKeys.TOKEN + REF_B] == b'token'
    assert idx.db.utxo_db.store[GlyphDBKeys.OWNER + b'\x01' * 11] == b'owner'


def test_v9_migration_pages_past_its_batch_size():
    """PAGE is 20,000; the seek walk must not stall or double-count across page boundaries."""
    rows = [(REF_A, 100, i, bytes([i % 256])) for i in range(45_000)]
    idx = _history_index(rows)
    assert idx._migrate_8_to_9() == 45_000
    assert len(idx.db.utxo_db.store) == 45_000
    assert all(len(k) == len(GlyphDBKeys.HISTORY) + 36 + gi.HISTORY_TAIL_LEN
               for k in idx.db.utxo_db.store)


def test_v9_is_registered_in_the_migration_chain():
    """Unregistered, the deploy hard-fails with 'no in-place migration' and demands a full
    reindex — the same wall a v4 DB hit earlier. Drives the real _check_schema_version."""
    idx = _history_index([(REF_A, 100, 3, b'v')])
    idx.db.utxo_db.store[GlyphDBKeys.SCHEMA_VERSION] = bytes([8])
    idx._check_schema_version()
    assert idx.db.utxo_db.store[GlyphDBKeys.SCHEMA_VERSION] == bytes([9])
    assert gi.pack_history_key(REF_A, 100, 3) in idx.db.utxo_db.store
