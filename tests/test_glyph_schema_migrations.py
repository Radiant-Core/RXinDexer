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
    # A gap here is what produced "version 4 < 6 has no in-place migration" on deploy.
    src = open(os.path.join(os.path.dirname(__file__), '..', 'electrumx', 'server',
                            'glyph_index.py'), encoding='utf-8').read()
    for step in ('3: self._migrate_3_to_4', '4: self._migrate_4_to_5', '5: self._migrate_5_to_6'):
        assert step in src, f'missing migration step: {step}'
    assert 'CURRENT_SCHEMA_VERSION = 6' in src


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
