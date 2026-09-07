"""
/glyphs/search: the exact/wildcard split, and what each mode can find.

Two problems, both reported from a consumer's search box.

1. Typing a partial name returned NOTHING. Exact mode is a `GN + sha256(name)` seek, so
   `sha256("surfer")` can never match a token called "Surfer on Acid" -- and a caller who did
   not know to pass `wildcard=true` got an empty list rather than a hint that the mode was
   wrong. An empty exact result now falls back to the scan, and `mode` reports which path
   answered. `wildcard=false` is the explicit opt-out for a strict lookup.

2. A Glyph v2 link record was unfindable by the name this API reports for it. Since
   _resolve_link_payload made /glyphs/{ref} answer "Surfer on Acid" for the link, a name search
   that excluded it contradicted the same index. Wildcard mode now matches the inherited name.
   Exact mode deliberately does not: it seeks a hashed index of each token's OWN name, and
   indexing inherited names would mean duplicate GN entries plus a migration.

Run: PYTHONPATH=. python3 -m pytest tests/server/test_glyph_search_modes.py
"""
import os
import sys
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from electrumx.server.rest_api import app, set_indexer  # noqa: E402

EXACT_HIT = [{'ref': 'a' * 72, 'name': 'Surfer on Acid'}]
WILD_HIT = {'pattern': '*surfer*', 'count': 1, 'scanned': 13197, 'truncated': False,
            'tokens': [{'ref': 'b' * 72, 'name': 'Surfer on Acid'}]}
WILD_EMPTY = {'pattern': '*nope*', 'count': 0, 'scanned': 13197, 'truncated': False,
              'tokens': []}


@pytest.fixture
def client():
    idx = Mock()
    idx.enabled = True
    idx.search_tokens = Mock(return_value=[])
    idx.search_tokens_wildcard = Mock(return_value=dict(WILD_HIT))
    idx.get_all_tokens_summary = Mock(return_value={'total': 0, 'tokens': []})
    db = Mock()
    db.db_height = 462281
    set_indexer(idx, db, Mock())
    yield TestClient(app), idx
    set_indexer(None, None, None)


# --------------------------------------------------------------- the fallback

def test_exact_miss_falls_back_and_says_so(client):
    c, idx = client
    body = c.get('/glyphs/search', params={'q': 'surfer'}).json()
    assert body['mode'] == 'wildcard', 'the caller must be told which path answered'
    assert body['count'] == 1
    idx.search_tokens.assert_called_once()
    idx.search_tokens_wildcard.assert_called_once()


def test_an_exact_hit_never_runs_the_scan(client):
    """The scan reads every GT row; an indexed hit must not pay for it."""
    c, idx = client
    idx.search_tokens.return_value = list(EXACT_HIT)
    body = c.get('/glyphs/search', params={'q': 'Surfer on Acid'}).json()
    assert body['mode'] == 'exact' and body['count'] == 1
    idx.search_tokens_wildcard.assert_not_called()


def test_wildcard_false_suppresses_the_fallback(client):
    """A caller that wants a strict lookup must be able to get an empty answer."""
    c, idx = client
    body = c.get('/glyphs/search', params={'q': 'surfer', 'wildcard': 'false'}).json()
    assert body['mode'] == 'exact'
    assert body['results'] == [] and body['count'] == 0
    idx.search_tokens_wildcard.assert_not_called()


def test_wildcard_true_skips_exact_entirely(client):
    c, idx = client
    body = c.get('/glyphs/search', params={'q': 'surfer', 'wildcard': 'true'}).json()
    assert body['mode'] == 'wildcard'
    idx.search_tokens.assert_not_called()


@pytest.mark.parametrize('q', ['sur*', 'surfe?', 'surf[ei]r'])
def test_a_pattern_still_goes_straight_to_wildcard(client, q):
    c, idx = client
    assert c.get('/glyphs/search', params={'q': q}).json()['mode'] == 'wildcard'
    idx.search_tokens.assert_not_called()


def test_a_genuine_miss_still_reports_empty(client):
    """Falling back must not invent results -- 'no such token' has to remain expressible."""
    c, idx = client
    idx.search_tokens_wildcard.return_value = dict(WILD_EMPTY)
    body = c.get('/glyphs/search', params={'q': 'nope'}).json()
    assert body['mode'] == 'wildcard' and body['count'] == 0 and body['tokens'] == []


def test_the_fallback_forwards_the_filters(client):
    c, idx = client
    c.get('/glyphs/search', params={'q': 'surfer', 'protocols': '1,4',
                                    'limit': 10, 'offset': 5})
    kwargs = idx.search_tokens_wildcard.call_args.kwargs
    assert kwargs['protocols'] == [1, 4] and kwargs['limit'] == 10 and kwargs['offset'] == 5


def test_scanned_and_truncated_survive_the_fallback(client):
    """They are how a caller tells a complete scan from a capped one, so the fallback must not
    drop them."""
    c, _idx = client
    body = c.get('/glyphs/search', params={'q': 'surfer'}).json()
    assert body['scanned'] == 13197 and body['truncated'] is False


# ------------------------------------------------- link records in wildcard mode

def test_wildcard_matches_a_name_inherited_through_loc():
    """The consistency requirement: /glyphs/{ref} reports the inherited name, so a name search
    has to be able to find it."""
    from types import SimpleNamespace
    from electrumx.server.glyph_index import GlyphDBKeys, GlyphIndex, GlyphTokenInfo, pack_ref

    TXID = bytes([0x20]) * 32
    target, link = pack_ref(TXID, 0), pack_ref(TXID, 33)

    def _tok(ref, **kw):
        t = GlyphTokenInfo()
        t.ref = ref
        for k, v in kw.items():
            setattr(t, k, v)
        return t

    rows = {
        GlyphDBKeys.TOKEN + target: _tok(target, name='Surfer on Acid', ticker='SoA',
                                         protocols=[1, 4], token_type=4,
                                         metadata_hash=b'TARGETHASH'),
        GlyphDBKeys.TOKEN + link: _tok(link, protocols=[2], token_type=2,
                                       metadata_hash=b'LINKHASH'),
    }
    encoded = {k: object() for k in rows}

    class _DB:
        def iterator(self, prefix=b'', **kw):
            for k in sorted(rows):
                if k.startswith(prefix):
                    yield k, encoded[k]

        def get(self, key):
            return None

    idx = object.__new__(GlyphIndex)
    idx.db = SimpleNamespace(utxo_db=_DB(), db_height=1)
    idx.logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None,
                                 exception=lambda *a, **k: None, debug=lambda *a, **k: None)
    idx._plumbing_cache = {}
    idx.contract_to_token_cache = {}
    idx.token_cache = {}
    idx.get_token = lambda ref: next(
        (t for k, t in rows.items() if k[2:] == ref), None)
    idx.get_metadata = lambda h: ({'p': [2], 'loc': 0} if h == b'LINKHASH' else
                                  {'name': 'Surfer on Acid', 'ticker': 'SoA'})
    idx._token_to_dict = lambda t, **kw: {'ref': t.ref.hex(), 'name': t.name}

    import electrumx.server.glyph_index as gi
    saved = gi.GlyphTokenInfo.from_bytes
    gi.GlyphTokenInfo.from_bytes = lambda data: next(t for k, t in rows.items()
                                                     if encoded[k] is data)
    try:
        out = idx.search_tokens_wildcard('surfer')
    finally:
        gi.GlyphTokenInfo.from_bytes = saved

    refs = {r['ref'] for r in out['tokens']}
    assert target.hex() in refs, 'the token itself'
    assert link.hex() in refs, 'and its link record, by the name it inherits'
    assert out['count'] == 2


def test_exact_mode_is_deliberately_own_names_only():
    """Documents the divergence rather than leaving it to be discovered: exact seeks a hashed
    index of each token's own name, so inherited names are not findable there."""
    src = open(os.path.join(os.path.dirname(__file__), '..', '..', 'electrumx', 'server',
                            'glyph_index.py'), encoding='utf-8').read()
    exact = src[src.index('def search_tokens(self'):]
    exact = exact[:exact.index('def ', 10)]
    assert '_resolve_link_payload' not in exact


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
