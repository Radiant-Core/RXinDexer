"""
Glyph v2 link payloads: a token that inherits its name through `loc`.

A dMint mint writes a LINK record alongside the token itself -- a payload of just
``{p, by, loc}`` where ``loc`` is a vout in the SAME commit txid whose payload the record
inherits. Photonic's createLinkCommit calls it "a record of dmints", and its reader merges the
two as ``{...linked, ...own}``: the linked payload as the base, the link's own fields winning.

RXinDexer had no concept of `loc` -- the string appeared nowhere in the tree -- so it reported
`name: null` for these while the reference wallet showed a name. Found on mainnet 2026-09-07 via
204463d7..._33, a link record beside "Surfer on Acid" (vout 0) and its 32 mining contracts
(vouts 1-32). 244 tokens were affected, every one of them with a named sibling.

Resolved for DISPLAY only and deliberately not written into the token row: the link's identity
is its own payload, and stamping the inherited name into BY_NAME would list every dMint token's
name twice in search -- once for the token, once for its link record.

Run: PYTHONPATH=. python3 -m pytest tests/test_glyph_link_payload.py
"""
import os
import struct
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from electrumx.server.glyph_index import GlyphIndex, pack_ref  # noqa: E402

TXID = bytes([0x20]) * 32
TARGET = pack_ref(TXID, 0)          # "Surfer on Acid"
LINK = pack_ref(TXID, 33)           # its dMint link record
OTHER_TXID = bytes([0x99]) * 32


class _Token:
    """Minimal stand-in for GlyphTokenInfo: only what the resolver reads."""

    def __init__(self, ref, name=None, ticker=None, metadata_hash=b''):
        self.ref = ref
        self.name = name
        self.ticker = ticker
        self.metadata_hash = metadata_hash


def _index(tokens=None, metadata=None):
    idx = object.__new__(GlyphIndex)
    idx.logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None,
                                 exception=lambda *a, **k: None, debug=lambda *a, **k: None)
    toks = dict(tokens or {})
    meta = dict(metadata or {})
    idx.get_token = lambda ref: toks.get(ref)
    idx.get_metadata = lambda h: meta.get(h)
    return idx


def _wired(link_payload, target_name='Surfer on Acid', target_ticker='SoA'):
    """The mainnet shape: a link at vout 33 pointing back at a named token at vout 0."""
    return _index(
        tokens={
            LINK: _Token(LINK, metadata_hash=b'LINKHASH'),
            TARGET: _Token(TARGET, name=target_name, ticker=target_ticker,
                           metadata_hash=b'TARGETHASH'),
        },
        metadata={
            b'LINKHASH': link_payload,
            b'TARGETHASH': {'p': [1, 4], 'name': target_name, 'ticker': target_ticker},
        },
    )


def test_a_link_record_inherits_the_target_name():
    idx = _wired({'p': [2], 'by': [b'\x01' * 36], 'loc': 0})
    got = idx._resolve_link_payload(idx.get_token(LINK))
    assert got is not None
    assert got['name'] == 'Surfer on Acid'
    assert got['ticker'] == 'SoA'
    assert got['linked_ref'] == f'{TXID[::-1].hex()}_0'


def test_loc_is_a_vout_in_the_same_commit_txid():
    """Photonic builds the linked ref from the ref's OWN txid plus loc, not from a full outpoint
    in the payload. A loc pointing at a vout of some other transaction is not representable."""
    idx = _wired({'p': [2], 'loc': 0})
    idx.get_token = lambda ref: (
        _Token(LINK, metadata_hash=b'LINKHASH') if ref == LINK else
        _Token(ref, name='WRONG TX') if ref[:32] == OTHER_TXID else
        _Token(TARGET, name='Surfer on Acid', metadata_hash=b'TARGETHASH'))
    got = idx._resolve_link_payload(_Token(LINK, metadata_hash=b'LINKHASH'))
    assert got['name'] == 'Surfer on Acid'


def test_a_token_with_its_own_name_is_never_overridden():
    """The link's own fields win, per {...linked, ...own}. A named token must not be touched."""
    idx = _wired({'p': [2], 'loc': 0})
    named = _Token(LINK, name='Its Own Name', metadata_hash=b'LINKHASH')
    assert idx._resolve_link_payload(named) is None


def test_no_metadata_means_nothing_to_resolve():
    idx = _wired({'p': [2], 'loc': 0})
    assert idx._resolve_link_payload(_Token(LINK, metadata_hash=b'')) is None


def test_a_payload_without_loc_is_not_a_link():
    idx = _wired({'p': [2], 'by': [b'\x01' * 36]})
    assert idx._resolve_link_payload(idx.get_token(LINK)) is None


@pytest.mark.parametrize('loc', ['0', None, 1.5, [0], {'v': 0}, -1, 2 ** 32])
def test_a_non_vout_loc_is_ignored(loc):
    """Only an integer in range is a vout. A string or float loc is some other use of the key."""
    idx = _wired({'p': [2], 'loc': loc})
    assert idx._resolve_link_payload(idx.get_token(LINK)) is None


def test_a_boolean_loc_is_ignored():
    """bool is an int subclass, so `loc: true` would otherwise resolve to vout 1."""
    idx = _wired({'p': [2], 'loc': True})
    assert idx._resolve_link_payload(idx.get_token(LINK)) is None


def test_a_self_link_resolves_to_nothing():
    """loc pointing at the record's own vout would otherwise recurse or self-satisfy."""
    idx = _wired({'p': [2], 'loc': 33})
    assert idx._resolve_link_payload(idx.get_token(LINK)) is None


def test_a_link_to_a_missing_token_is_not_an_error():
    idx = _index(
        tokens={LINK: _Token(LINK, metadata_hash=b'LINKHASH')},
        metadata={b'LINKHASH': {'p': [2], 'loc': 0}},
    )
    assert idx._resolve_link_payload(idx.get_token(LINK)) is None


def test_a_link_to_an_unnamed_token_yields_no_name():
    """Two link records pointing at each other must not invent a name."""
    idx = _wired({'p': [2], 'loc': 0}, target_name=None, target_ticker=None)
    got = idx._resolve_link_payload(idx.get_token(LINK))
    assert got is not None and got['name'] is None and got['ticker'] is None


def test_a_malformed_ref_is_rejected_before_slicing():
    assert _wired({'p': [2], 'loc': 0})._resolve_link_payload(
        _Token(b'\x01' * 10, metadata_hash=b'LINKHASH')) is None


def test_the_inherited_name_is_not_written_back_into_the_token():
    """The whole reason this is a read-time resolution: BY_NAME must not gain a second entry for
    every dMint token's name."""
    idx = _wired({'p': [2], 'loc': 0})
    token = idx.get_token(LINK)
    idx._resolve_link_payload(token)
    assert token.name is None, 'resolution must not mutate the record'


def test_resolution_is_only_attempted_for_an_unnamed_token_with_metadata():
    """Cost control: the lookup must not fire on every token in a list page."""
    calls = []
    idx = _wired({'p': [2], 'loc': 0})
    inner = idx.get_metadata
    idx.get_metadata = lambda h: (calls.append(h), inner(h))[1]
    idx._resolve_link_payload(_Token(LINK, name='Named', metadata_hash=b'LINKHASH'))
    idx._resolve_link_payload(_Token(LINK, metadata_hash=b''))
    assert calls == [], 'neither a named token nor one without metadata may read metadata'


def test_loc_zero_is_a_valid_target():
    """vout 0 is the common case -- the token a dMint link points back at -- so a falsy-int
    check would break every real link record."""
    idx = _wired({'p': [2], 'loc': 0})
    assert idx._resolve_link_payload(idx.get_token(LINK))['name'] == 'Surfer on Acid'


def test_glyph_parser_still_has_no_loc_concept():
    """Documents where this lives. The parser is untouched; `loc` is resolved at display time
    from payloads already stored, so no reindex or schema change was needed."""
    src = open(os.path.join(os.path.dirname(__file__), '..', 'electrumx', 'lib', 'glyph.py'),
               encoding='utf-8').read()
    assert "'loc'" not in src


def test_pack_ref_round_trip_matches_the_resolver_arithmetic():
    """Guards the byte order the resolver relies on: same txid, vout as LE uint32."""
    assert LINK[:32] == TXID and struct.unpack('<I', LINK[32:36])[0] == 33
    assert pack_ref(TXID, 0) == TXID + struct.pack('<I', 0)


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
