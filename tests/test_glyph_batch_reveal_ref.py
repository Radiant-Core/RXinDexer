"""
Regression test: a glyph's ref is the outpoint spent by its OWN envelope-carrying input.

Driven by real mainnet bytes (height 236,693), frozen in tests/fixtures/mainnet-dmint-batch/ and
integrity-checked against their txids by the first test here.

The transaction is a batch reveal:

  input 0  spends commit:0   ~52KB envelope, p:[1,4] "DEEZ NUTZ" -> the ft+dmint deploy
  inputs 1-10 spend commit:1..:10, plain p2pkh, NO envelope
  input 11 spends commit:11  56-byte envelope, p:[2], unnamed    -> a separate NFT
  outputs 0-9  mint singletons commit:1..:10 (the deploy's dmint mining contracts),
               each also carrying the deploy's normal ref commit:0
  output 10    mints singleton commit:11 (the NFT)
  output 11    re-creates the creator singleton ae4e54f9…:0 (by-evidence, not a mint)

The bug: _find_output_ref returns the FIRST singleton found scanning outputs in order, regardless
of which input the envelope came from, and it used to win over the input's prevout. So the
input-11 NFT was attributed ref commit:1 — a mining contract — which surfaced as an "Unnamed
token" row at :1 while the real NFT never appeared at :11. Single mints hid it, because there the
first singleton IS the right one.

Run: PYTHONPATH=. python3 -m pytest tests/test_glyph_batch_reveal_ref.py
"""
import glob
import io
import json
import os
import sys
from hashlib import sha256

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from electrumx.lib.glyph import contains_glyph_magic  # noqa: E402
from electrumx.lib.tx import Deserializer  # noqa: E402
from electrumx.server.glyph_index import GlyphIndex, pack_ref  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures', 'mainnet-dmint-batch')
MANIFEST = json.load(io.open(os.path.join(FIXTURES, 'manifest.json'), encoding='utf-8'))

COMMIT_TXID = MANIFEST['commitTxid']
REVEAL_TXID = MANIFEST['revealTxid']
# Internal (little-endian) byte order, as refs are stored.
COMMIT = bytes.fromhex(COMMIT_TXID)[::-1]


def _load(kind: str) -> bytes:
    path = [f for f in glob.glob(os.path.join(FIXTURES, '*.hex')) if kind in os.path.basename(f)]
    assert len(path) == 1, path
    return bytes.fromhex(io.open(path[0], encoding='utf-8').read().strip())


def _txid(raw: bytes) -> str:
    return sha256(sha256(raw).digest()).digest()[::-1].hex()


def commit_ref(n: int) -> bytes:
    """The 36-byte ref for commit:n, in RXinDexer's internal layout."""
    return pack_ref(COMMIT, n)


def manifest_ref(key: str) -> bytes:
    """Convert a manifest ref to RXinDexer's internal 36-byte layout.

    The two serialisations differ and must not be compared raw:
      manifest  -> display-order txid (64 hex) + BIG-endian vout      (as a human reads a txid)
      RXinDexer -> internal-order txid (reversed) + LITTLE-endian vout (as it appears on chain)
    """
    value = MANIFEST[key]
    txid_hex, vout_hex = value[:64], value[64:]
    return pack_ref(bytes.fromhex(txid_hex)[::-1], int(vout_hex, 16))


def reveal_tx():
    return Deserializer(_load('reveal')).read_tx()


def _index() -> GlyphIndex:
    # Only pure ref-extraction helpers are exercised; __init__ opens real storage.
    return object.__new__(GlyphIndex)


# --------------------------------------------------------------------------- fixture integrity

def test_fixtures_hash_to_their_txids():
    """If this fails the fixtures are corrupt and nothing below means anything."""
    assert _txid(_load('commit')) == COMMIT_TXID
    assert _txid(_load('reveal')) == REVEAL_TXID


# --------------------------------------------------------------------------- chain structure

def test_reveal_shape():
    tx = reveal_tx()
    assert len(tx.inputs) == 14
    assert len(tx.outputs) == 13


def test_only_inputs_0_and_11_carry_envelopes():
    """The crux: two envelopes, on inputs 0 and 11. Inputs 1-10 are plain p2pkh spends of the
    contract commits and carry no payload of their own."""
    tx = reveal_tx()
    carriers = [i for i, ti in enumerate(tx.inputs)
                if ti.script and contains_glyph_magic(ti.script)]
    assert carriers == [0, 11]
    for i in range(1, 11):
        assert not contains_glyph_magic(tx.inputs[i].script), f'input {i} should be plain p2pkh'


def test_envelope_inputs_spend_the_commits_they_reveal():
    tx = reveal_tx()
    assert pack_ref(tx.inputs[0].prev_hash, tx.inputs[0].prev_idx) == commit_ref(0)
    assert pack_ref(tx.inputs[11].prev_hash, tx.inputs[11].prev_idx) == commit_ref(11)


def test_outputs_mint_the_contract_singletons_and_the_nft():
    tx = reveal_tx()
    idx = _index()

    def singletons(vout):
        return [rb for rb, rt in idx._extract_refs_from_script(tx.outputs[vout].pk_script)
                if rt == 1]

    # Outputs 0-9 mint commit:1..:10 — the deploy's dmint mining contracts.
    for vout in range(10):
        assert singletons(vout) == [commit_ref(vout + 1)]
    # Output 10 mints the NFT's own outpoint.
    assert singletons(10) == [commit_ref(11)]
    # Output 11 re-creates the creator singleton — by-evidence, not a mint of this commit.
    assert singletons(11) == [manifest_ref('creatorRef')]


def test_contract_outputs_also_carry_the_deploy_normal_ref():
    """Why _ref_minted_in_outputs must accept normal refs too: the FT deploy's own ref (commit:0)
    appears as a normal ref (0xd0) on the minted outputs, never as a singleton."""
    tx = reveal_tx()
    idx = _index()
    normals = [rb for rb, rt in idx._extract_refs_from_script(tx.outputs[0].pk_script) if rt == 0]
    assert normals == [commit_ref(0)]


# --------------------------------------------------------------------------- the bug

def test_find_output_ref_alone_mis_attributes_the_nft():
    """Pins the defective behaviour so a regression is unambiguous: scanning outputs hands the
    input-11 NFT the ref commit:1, a mining contract."""
    tx = reveal_tx()
    idx = _index()
    got = idx._find_output_ref(bytes.fromhex(REVEAL_TXID)[::-1], tx, {'p': [2]})
    assert got == commit_ref(1), 'expected the historical mis-attribution to commit:1'
    assert got != commit_ref(11)


# --------------------------------------------------------------------------- the fix

def test_minted_check_accepts_both_envelope_prevouts():
    tx = reveal_tx()
    idx = _index()
    assert idx._ref_minted_in_outputs(tx, commit_ref(0)) is True    # normal ref on outputs 0-9
    assert idx._ref_minted_in_outputs(tx, commit_ref(11)) is True   # singleton on output 10


def test_minted_check_rejects_a_ref_this_tx_does_not_mint():
    tx = reveal_tx()
    idx = _index()
    # commit:12 is spent by input 13 as plain change and is never minted here.
    assert idx._ref_minted_in_outputs(tx, commit_ref(12)) is False


def test_resolved_refs_follow_the_envelope_carrying_input():
    """The fix, applied exactly as the reveal loop does: each envelope resolves to its own input's
    prevout, because the tx mints it."""
    tx = reveal_tx()
    idx = _index()

    resolved = {}
    for vin_idx, txin in enumerate(tx.inputs):
        if not txin.script or not contains_glyph_magic(txin.script):
            continue
        ref = pack_ref(txin.prev_hash, txin.prev_idx)
        if idx._ref_minted_in_outputs(tx, ref):
            final_ref = ref
        else:
            final_ref = idx._find_output_ref(b'\x00' * 32, tx, {'p': [2]}) or ref
        resolved[vin_idx] = final_ref

    assert resolved[0] == commit_ref(0), 'the DEEZ NUTZ deploy belongs at :0'
    assert resolved[11] == commit_ref(11), 'the unnamed NFT belongs at :11, not :1'
    assert commit_ref(1) not in resolved.values(), 'no envelope may resolve to a mining contract'


def test_manifest_refs_match_what_the_fix_produces():
    """Cross-check against the reference implementation's own expectations."""
    assert manifest_ref('nftRef') == commit_ref(11)
    assert manifest_ref('contractRef') == commit_ref(1)


def test_reveal_loop_prefers_the_prevout_over_the_output_scan():
    """Source guard: the loop must consult _ref_minted_in_outputs before falling back, so a future
    edit cannot quietly restore `output_ref if output_ref else ref`."""
    src = io.open(os.path.join(os.path.dirname(__file__), '..', 'electrumx', 'server',
                               'glyph_index.py'), encoding='utf-8').read()
    assert 'if self._ref_minted_in_outputs(tx, ref):' in src
    assert 'final_ref = output_ref if output_ref else ref' not in src


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))


# --------------------------------------------------------------------------- contract detection

def test_sibling_singletons_exclude_the_co_revealed_nft():
    """The DEEZ tx mints :1..:10 as mining contracts and :11 as a separate NFT. Only :1..:10 are
    the deploy's plumbing; counting :11 inflated live_contracts, could make contract_ref point at
    an NFT, listed that NFT under /dmint/contracts, and hid it from the recency feeds."""
    tx = reveal_tx()
    idx = _index()

    siblings = idx._find_sibling_singletons(tx, commit_ref(0))
    vouts = sorted(int.from_bytes(r[32:36], 'little') for r in siblings)
    assert vouts == list(range(1, 11)), f'expected the ten contracts, got {vouts}'
    assert commit_ref(11) not in siblings, 'the co-revealed NFT is not a mining contract'


def test_revealed_token_refs_are_the_envelope_prevouts():
    tx = reveal_tx()
    idx = _index()
    revealed = idx._revealed_token_refs(tx)
    assert revealed == {commit_ref(0), commit_ref(11)}


def test_contract_count_is_ten_not_eleven():
    """live_contracts is derived from this set and feeds burn detection."""
    tx = reveal_tx()
    idx = _index()
    assert len(idx._find_all_contract_refs(tx, commit_ref(0))) == 10


def test_contract_ref_cannot_be_the_nft():
    """contract_ref is sorted(contract_refs)[0]; the NFT must not be a candidate."""
    tx = reveal_tx()
    idx = _index()
    refs = idx._find_all_contract_refs(tx, commit_ref(0))
    assert sorted(refs)[0] != commit_ref(11)


def test_the_nft_sees_the_contracts_as_siblings_but_not_the_deploy():
    """From the NFT's side: the ten contracts share its txid and are not revealed tokens, so they
    are siblings of it too — but the FT deploy at :0 IS a revealed token and must be excluded."""
    tx = reveal_tx()
    idx = _index()
    siblings = idx._find_sibling_singletons(tx, commit_ref(11))
    assert commit_ref(0) not in siblings, 'the FT deploy is a token, not the NFT plumbing'
    assert commit_ref(11) not in siblings
