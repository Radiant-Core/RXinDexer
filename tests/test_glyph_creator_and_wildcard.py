"""
Tests for the creator attribution index (GA / v7) and wildcard name+ticker search.

Both were added because no data source could answer their question before: `by` was never read
anywhere in the tree, and BY_NAME stores sha256(name) so no partial match was possible.

GT rows are CBOR, and cbor2 is not always present in a bare checkout, so the token codec is stubbed
where needed. What stays under test is the code these features added: the GA key derivation (live
path and v7 backfill deriving identical keys), and the wildcard matcher's semantics and ordering.

Run: PYTHONPATH=. python3 -m pytest tests/test_glyph_creator_and_wildcard.py
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import electrumx.server.glyph_index as gi  # noqa: E402
from electrumx.server.glyph_index import GlyphDBKeys, GlyphIndex  # noqa: E402

CREATOR = bytes([0xC0]) * 36
OTHER_CREATOR = bytes([0xC1]) * 36
WORK_A = bytes([0xA1]) * 36
WORK_B = bytes([0xB2]) * 36


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


class _TokenStub:
    """Stands in for GlyphTokenInfo for the CBOR-free paths."""

    MARKER = b'MH:'

    def __init__(self, name=None, ticker=None, protocols=None, metadata_hash=b''):
        self.name = name
        self.ticker = ticker
        self.protocols = protocols or [2]
        self.metadata_hash = metadata_hash

    @classmethod
    def from_bytes(cls, data):
        if not data.startswith(cls.MARKER):
            raise ValueError('not a token row')
        meta_hash = data[len(cls.MARKER):]
        return SimpleNamespace(metadata_hash=meta_hash)


def _bare_index():
    idx = object.__new__(GlyphIndex)
    idx.db = SimpleNamespace(utxo_db=_StubUtxoDB(), db_height=500000)
    idx.logger = _Logger()
    idx.metadata_cache = {}
    return idx


# --------------------------------------------------------------------------- v7 backfill (GA)

def _migration_index(rows, metadata_by_hash):
    idx = _bare_index()
    for ref, meta_hash in rows:
        idx.db.utxo_db.store[GlyphDBKeys.TOKEN + ref] = _TokenStub.MARKER + meta_hash
    idx.get_metadata = lambda h: metadata_by_hash.get(h)
    return idx


def _run_migration(idx, name='_migrate_6_to_7'):
    saved = gi.GlyphTokenInfo.from_bytes
    gi.GlyphTokenInfo.from_bytes = _TokenStub.from_bytes
    try:
        return getattr(idx, name)()
    finally:
        gi.GlyphTokenInfo.from_bytes = saved


def _ga_keys(idx):
    return sorted(k for k in idx.db.utxo_db.store if k.startswith(GlyphDBKeys.BY_CREATOR))


def test_backfill_derives_the_live_ga_key():
    idx = _migration_index([(WORK_A, b'h1')], {b'h1': {'by': [CREATOR]}})
    assert _run_migration(idx) == 1
    assert _ga_keys(idx) == [GlyphDBKeys.BY_CREATOR + CREATOR + WORK_A]


def test_backfill_unwraps_cbor_tags():
    tagged = SimpleNamespace(value=CREATOR)
    idx = _migration_index([(WORK_A, b'h1')], {b'h1': {'by': [tagged]}})
    assert _run_migration(idx) == 1
    assert _ga_keys(idx) == [GlyphDBKeys.BY_CREATOR + CREATOR + WORK_A]


def test_backfill_ignores_malformed_by_fields():
    idx = _migration_index(
        [(WORK_A, b'h1'), (WORK_B, b'h2')],
        {b'h1': {'by': [b'\x01' * 35]},   # wrong length
         b'h2': {'by': 'not-a-list'}},    # wrong type
    )
    assert _run_migration(idx) == 0
    assert _ga_keys(idx) == []


def test_backfill_is_idempotent():
    idx = _migration_index([(WORK_A, b'h1')], {b'h1': {'by': [CREATOR]}})
    _run_migration(idx)
    before = dict(idx.db.utxo_db.store)
    _run_migration(idx)
    assert idx.db.utxo_db.store == before


def test_ga_prefix_introduces_no_aliasing():
    # 'GBY' (the originally proposed prefix) would sit under BALANCE's 'GB', whose scans seek on
    # GB + hashX(11). Assert the chosen prefix collides with nothing.
    prefixes = [v for k, v in vars(GlyphDBKeys).items() if isinstance(v, bytes)]
    ga = GlyphDBKeys.BY_CREATOR
    for other in prefixes:
        if other == ga:
            continue
        assert not ga.startswith(other), f'{ga!r} sits under {other!r}'
        assert not other.startswith(ga), f'{other!r} sits under {ga!r}'


def test_v7_migrator_is_registered():
    # Asserts the v7 STEP, not the global schema version — that moves every time a new index is
    # added, and test_migration_chain_is_registered_for_every_step already checks the whole chain
    # against CURRENT_SCHEMA_VERSION.
    src = open(os.path.join(os.path.dirname(__file__), '..', 'electrumx', 'server',
                            'glyph_index.py'), encoding='utf-8').read()
    assert '6: self._migrate_6_to_7' in src
    assert 'def _migrate_6_to_7' in src


# --------------------------------------------------------------------------- creator queries

def test_get_creator_works_returns_only_that_creator():
    idx = _bare_index()
    store = idx.db.utxo_db.store
    store[GlyphDBKeys.BY_CREATOR + CREATOR + WORK_A] = b''
    store[GlyphDBKeys.BY_CREATOR + CREATOR + WORK_B] = b''
    store[GlyphDBKeys.BY_CREATOR + OTHER_CREATOR + WORK_A] = b''
    idx.get_token = lambda ref: _TokenStub(name='n')
    idx._token_to_dict = lambda t, **k: {'ref': 'x'}
    idx._decode_cursor = lambda c: None
    idx._encode_cursor = lambda k: 'cur'

    out = idx.get_creator_works(CREATOR, limit=100)
    assert len(out['tokens']) == 2
    assert out['attribution'] == 'self-asserted', 'callers must be told this is a claim'
    assert out['next_cursor'] is None
    assert idx.count_creator_works(CREATOR) == 2
    assert idx.count_creator_works(OTHER_CREATOR) == 1


def test_get_creator_works_pages_with_a_cursor():
    idx = _bare_index()
    for i in range(5):
        idx.db.utxo_db.store[GlyphDBKeys.BY_CREATOR + CREATOR + bytes([i]) * 36] = b''
    idx.get_token = lambda ref: _TokenStub(name='n')
    idx._token_to_dict = lambda t, **k: {'ref': 'x'}
    idx._decode_cursor = lambda c: None
    idx._encode_cursor = lambda k: 'cur'

    out = idx.get_creator_works(CREATOR, limit=2)
    assert len(out['tokens']) == 2
    assert out['next_cursor'] == 'cur'


# --------------------------------------------------------------------------- wildcard search

def _search_index(tokens):
    """tokens: list of (ref, name, ticker, protocols)."""
    idx = _bare_index()
    store = idx.db.utxo_db.store
    lookup = {}
    for ref, name, ticker, protos in tokens:
        store[GlyphDBKeys.TOKEN + ref] = b'TOK:' + ref
        lookup[ref] = _TokenStub(name=name, ticker=ticker, protocols=protos)
    idx._token_to_dict = lambda t, **k: {'name': t.name, 'ticker': t.ticker}
    return idx, lookup


def _run_search(idx, lookup, *args, **kwargs):
    saved = gi.GlyphTokenInfo.from_bytes
    gi.GlyphTokenInfo.from_bytes = lambda data: lookup[data[4:]]
    try:
        return idx.search_tokens_wildcard(*args, **kwargs)
    finally:
        gi.GlyphTokenInfo.from_bytes = saved


TOKENS = [
    (bytes([1]) * 36, 'gatorcoin', 'GATOR', [1]),
    (bytes([2]) * 36, 'gatorade', 'GADE', [1]),
    (bytes([3]) * 36, 'alligator', 'ALLI', [2]),
    (bytes([4]) * 36, 'photonlink', 'PHOTON', [2]),
    (bytes([5]) * 36, None, 'GATORX', [2]),
]


def test_bare_text_is_a_substring_search():
    idx, lookup = _search_index(TOKENS)
    out = _run_search(idx, lookup, 'gator')
    names = [t['name'] for t in out['tokens']]
    # Includes the infix match, which no ordered index over hashed names could ever return.
    assert 'alligator' in names
    assert 'gatorcoin' in names and 'gatorade' in names
    assert out['pattern'] == '*gator*'


def test_prefix_wildcard():
    idx, lookup = _search_index(TOKENS)
    out = _run_search(idx, lookup, 'gator*')
    names = [t['name'] for t in out['tokens']]
    assert 'gatorcoin' in names and 'gatorade' in names
    assert 'alligator' not in names, 'gator* must not match an infix'


def test_ticker_is_searched_too():
    idx, lookup = _search_index(TOKENS)
    # This token has NO name; it can only be found via its ticker.
    out = _run_search(idx, lookup, 'gatorx')
    assert [t['ticker'] for t in out['tokens']] == ['GATORX']


def test_matching_is_case_insensitive_on_both_fields():
    idx, lookup = _search_index(TOKENS)
    assert _run_search(idx, lookup, 'GATORCOIN')['count'] == 1
    assert _run_search(idx, lookup, 'photon')['count'] == 1


def test_question_mark_and_class_patterns():
    idx, lookup = _search_index(TOKENS)
    # '?' is exactly one char, and the pattern is anchored: 8 chars cannot match a 9-char name.
    assert _run_search(idx, lookup, 'gator?in')['count'] == 0
    # ...but 9 chars can, with '?' standing in for the 'c'.
    assert _run_search(idx, lookup, 'gator?oin')['count'] == 1
    assert _run_search(idx, lookup, 'gatorcoi?')['count'] == 1
    assert _run_search(idx, lookup, '[ag]lligator')['count'] == 1


def test_protocol_filter_applies():
    idx, lookup = _search_index(TOKENS)
    out = _run_search(idx, lookup, '*gator*', protocols=[1])
    names = sorted(t['name'] for t in out['tokens'])
    assert names == ['gatorade', 'gatorcoin'], 'alligator is protocol 2'


def test_results_are_alphabetical_and_page_stably():
    idx, lookup = _search_index(TOKENS)
    full = _run_search(idx, lookup, '*a*', limit=100)
    # Ordering is case-insensitive (the sort key is the lowercased name, falling back to ticker),
    # so compare on the same footing rather than raw case.
    ordered = [(t['name'] or t['ticker']).lower() for t in full['tokens']]
    assert ordered == sorted(ordered), 'ordering must not depend on ref order'

    page1 = _run_search(idx, lookup, '*a*', limit=2, offset=0)
    page2 = _run_search(idx, lookup, '*a*', limit=2, offset=2)
    seen = [t['name'] for t in page1['tokens']] + [t['name'] for t in page2['tokens']]
    assert len(seen) == len(set(seen)), 'pages must not overlap'
    assert page1['has_more'] is True


def test_empty_pattern_returns_nothing():
    idx, lookup = _search_index(TOKENS)
    out = _run_search(idx, lookup, '   ')
    assert out['count'] == 0 and out['tokens'] == []


def test_scan_cap_is_reported():
    idx, lookup = _search_index(TOKENS)
    saved = gi.MAX_WILDCARD_SCAN
    gi.MAX_WILDCARD_SCAN = 2
    try:
        out = _run_search(idx, lookup, '*gator*')
        assert out['truncated'] is True, 'a capped answer must say so'
        assert out['scanned'] == 2
    finally:
        gi.MAX_WILDCARD_SCAN = saved


def test_untruncated_scan_reports_false():
    idx, lookup = _search_index(TOKENS)
    out = _run_search(idx, lookup, '*gator*')
    assert out['truncated'] is False
    assert out['scanned'] == len(TOKENS)


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))


# --------------------------------------------------------------------------- media hash (v8 / GMH)

from electrumx.lib.hash import sha256 as _sha256  # noqa: E402

MEDIA = b'\x89PNG\r\n\x1a\n' + b'pretend-image-bytes' * 4
MEDIA_HASH = _sha256(MEDIA)


def test_embed_and_remote_forms_of_one_artwork_collide():
    """The whole point of a single-sha256 keyspace: an embedded copy and a remote copy of the same
    file must land on the SAME key, so re-uploads are detectable across both carriage forms."""
    from electrumx.server.glyph_index import media_hashes_from_metadata

    embedded = media_hashes_from_metadata({'main': {'t': 'image/png', 'b': MEDIA}})
    remote = media_hashes_from_metadata({'remote': {'t': 'image/png', 'u': 'ipfs://x',
                                                    'h': MEDIA_HASH}})
    assert embedded == {MEDIA_HASH}
    assert remote == {MEDIA_HASH}
    assert embedded == remote


def test_media_hash_is_single_not_double_sha256():
    from electrumx.server.glyph_index import media_hashes_from_metadata
    got = media_hashes_from_metadata({'em': {'b': MEDIA}})
    assert got == {_sha256(MEDIA)}
    assert got != {_sha256(_sha256(MEDIA))}, 'double-hashing would break remote-h collision'


def test_cbor_tag_hex_and_bytes_payloads_agree():
    from electrumx.server.glyph_index import media_hashes_from_metadata, unwrap_cbor_bytes
    assert unwrap_cbor_bytes(MEDIA) == MEDIA
    assert unwrap_cbor_bytes(SimpleNamespace(value=MEDIA)) == MEDIA
    assert unwrap_cbor_bytes(SimpleNamespace(value=MEDIA.hex())) == MEDIA
    assert unwrap_cbor_bytes(SimpleNamespace(value='not-hex')) is None
    assert unwrap_cbor_bytes(None) is None
    # All three spellings of the same bytes must produce one key.
    for payload in (MEDIA, SimpleNamespace(value=MEDIA), SimpleNamespace(value=MEDIA.hex())):
        assert media_hashes_from_metadata({'main': {'b': payload}}) == {MEDIA_HASH}


def test_token_with_both_embed_and_remote_yields_both_hashes():
    """The token-field extraction is an elif, so only one wins there; the index must not inherit
    that, or a token carrying both would be findable by only one hash."""
    from electrumx.server.glyph_index import media_hashes_from_metadata
    other = _sha256(b'a different file')
    got = media_hashes_from_metadata({
        'embed': {'b': MEDIA},
        'remote': {'u': 'ipfs://y', 'h': other},
    })
    assert got == {MEDIA_HASH, other}


def test_no_media_yields_no_keys():
    from electrumx.server.glyph_index import media_hashes_from_metadata
    assert media_hashes_from_metadata({}) == set()
    assert media_hashes_from_metadata({'main': {'t': 'image/png'}}) == set()   # no bytes
    assert media_hashes_from_metadata({'main': 'not-a-dict'}) == set()
    assert media_hashes_from_metadata({'remote': {'h': b'\x01' * 31}}) == set()  # wrong length
    assert media_hashes_from_metadata(None) == set()


def test_v8_backfill_writes_gmh_and_gdh():
    idx = _migration_index([(WORK_A, b'h1')], {b'h1': {'main': {'b': MEDIA}}})
    assert _run_migration(idx, '_migrate_7_to_8') == 2   # one GMH + one GDH
    store = idx.db.utxo_db.store
    assert GlyphDBKeys.MEDIA_HASH + MEDIA_HASH + WORK_A in store
    assert GlyphDBKeys.PAYLOAD_HASH + b'h1' + WORK_A in store


def test_v8_backfill_is_idempotent():
    idx = _migration_index([(WORK_A, b'h1')], {b'h1': {'main': {'b': MEDIA}}})
    _run_migration(idx, '_migrate_7_to_8')
    before = dict(idx.db.utxo_db.store)
    _run_migration(idx, '_migrate_7_to_8')
    assert idx.db.utxo_db.store == before


def test_media_lookup_and_bounded_count():
    idx = _bare_index()
    store = idx.db.utxo_db.store
    for i in range(3):
        store[GlyphDBKeys.MEDIA_HASH + MEDIA_HASH + bytes([i]) * 36] = b''
    store[GlyphDBKeys.MEDIA_HASH + _sha256(b'other') + WORK_B] = b''
    idx.get_token = lambda ref: _TokenStub(name='n')
    idx._token_to_dict = lambda t, **k: {'ref': 'x'}
    idx._decode_cursor = lambda c: None
    idx._encode_cursor = lambda k: 'cur'

    out = idx.get_tokens_by_media_hash(MEDIA_HASH, limit=100)
    assert len(out['tokens']) == 3
    # .hex(), not reversed txid-style hex, or no client-computed sha256 would ever match.
    assert out['media_sha256'] == MEDIA_HASH.hex()
    assert idx.count_tokens_by_media_hash(MEDIA_HASH) == 3


def test_duplicate_count_is_capped():
    idx = _bare_index()
    for i in range(80):
        idx.db.utxo_db.store[GlyphDBKeys.MEDIA_HASH + MEDIA_HASH + bytes([i]) * 36] = b''
    n = idx.count_tokens_by_media_hash(MEDIA_HASH)
    assert n == 51, 'must stop at the cap rather than scanning all 80'


def test_media_prefixes_do_not_alias_a_scanned_prefix():
    """GMH sits under METADATA's 'GM', which is only ever exact-get. GDH deliberately avoids 'GP'
    (BY_PROTO), which IS prefix-scanned as GP + proto(1)."""
    assert GlyphDBKeys.MEDIA_HASH == b'GMH'
    assert GlyphDBKeys.PAYLOAD_HASH == b'GDH'
    assert not GlyphDBKeys.PAYLOAD_HASH.startswith(GlyphDBKeys.BY_PROTO)


def test_schema_v8_registered():
    from electrumx.server.glyph_index import CURRENT_SCHEMA_VERSION
    assert CURRENT_SCHEMA_VERSION == 8
    src = open(os.path.join(os.path.dirname(__file__), '..', 'electrumx', 'server',
                            'glyph_index.py'), encoding='utf-8').read()
    assert '7: self._migrate_7_to_8' in src
