"""Tests for the multi-ref v3 output scanner's detection logic
(contrib/scan_multiref_v3_outputs.py).  The scan answers whether a reindex is
needed after the v3-txid ref-ordering fix, so its gating (v3-only, >=2 DISTINCT
refs) must be exact.
"""

import importlib.util
import os

from electrumx.lib.script import OpCodes

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), 'contrib', 'scan_multiref_v3_outputs.py')
_spec = importlib.util.spec_from_file_location('scan_multiref', _PATH)
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)

OP_PUSHINPUTREF = OpCodes.OP_PUSHINPUTREF


def _spk(*refs):
    return (b''.join(bytes([OP_PUSHINPUTREF]) + r for r in refs)).hex()


def _tx(version, *output_scripts_hex, txid='tx'):
    return {
        'version': version,
        'txid': txid,
        'vout': [{'n': i, 'scriptPubKey': {'hex': h}}
                 for i, h in enumerate(output_scripts_hex)],
    }


REF_A = b'\xaa' * 36
REF_B = b'\xbb' * 36


def test_output_distinct_ref_count():
    assert scan.output_distinct_ref_count(bytes.fromhex(_spk(REF_A, REF_B))) == 2
    assert scan.output_distinct_ref_count(bytes.fromhex(_spk(REF_A))) == 1
    assert scan.output_distinct_ref_count(bytes.fromhex(_spk(REF_A, REF_A))) == 1  # dedup
    assert scan.output_distinct_ref_count(b'\x76') == 0  # OP_DUP, no refs


def test_v3_two_distinct_refs_is_affected():
    hits = scan.scan_block({'tx': [_tx(3, _spk(REF_A, REF_B), txid='t1')]})
    assert hits == [('t1', 0, 2)]


def test_v3_single_or_duplicate_ref_not_affected():
    assert scan.scan_block({'tx': [_tx(3, _spk(REF_A))]}) == []
    assert scan.scan_block({'tx': [_tx(3, _spk(REF_A, REF_A))]}) == []  # not distinct


def test_v2_multi_ref_not_affected():
    # v2 txids are plain double_sha256 — the ref ordering fix does not touch them.
    assert scan.scan_block({'tx': [_tx(2, _spk(REF_A, REF_B))]}) == []


def test_mixed_block_counts_only_affected():
    block = {'tx': [
        _tx(3, _spk(REF_A, REF_B), txid='hit'),       # affected
        _tx(2, _spk(REF_A, REF_B), txid='v2'),        # wrong version
        _tx(3, _spk(REF_A), txid='single'),           # single ref
        _tx(3, '76', _spk(REF_A, REF_B), txid='hit2'),  # affected on vout 1
    ]}
    hits = scan.scan_block(block)
    assert ('hit', 0, 2) in hits
    assert ('hit2', 1, 2) in hits
    assert len(hits) == 2
