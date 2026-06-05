"""Regression tests for the v3-txid push-input-ref ordering.

Radiant Core collects an output's push-input-refs into a std::set<uint288> and
hashes them in set order.  uint288 ordering is base_blob::Compare, which compares
the 36 bytes MSB-first IN REVERSE ("the data is little endian", src/uint256.h) —
i.e. each ref is ordered as a little-endian 288-bit integer (last script byte is
most significant).  Python's default bytes sort is big-endian, so an output with
>=2 distinct refs would otherwise hash them in a different order and yield a
DIFFERENT v3 txid than the node.  See electrumx/lib/tx.py
calculate_pushrefs_count_and_hash.
"""

import struct

import electrumx.lib.tx as tx_lib
from electrumx.lib.hash import double_sha256
from electrumx.lib.script import OpCodes

OP_PUSHINPUTREF = OpCodes.OP_PUSHINPUTREF  # 0xd0, followed by a 36-byte ref


def _refs_script(refs):
    return b''.join(bytes([OP_PUSHINPUTREF]) + r for r in refs)


def test_multi_ref_output_orders_refs_little_endian_like_core():
    # Two distinct 36-byte refs whose big-endian and little-endian orderings are
    # the reverse of each other.
    ref_hi_first = b'\xff' + b'\x00' * 35   # big-endian large; little-endian small
    ref_lo_first = b'\x00' * 35 + b'\xff'   # big-endian small; little-endian large

    # Pushed in this (script) order.
    script = _refs_script([ref_hi_first, ref_lo_first])
    result = tx_lib.Deserializer(b'\x00').calculate_pushrefs_count_and_hash(script)

    # Core orders by little-endian uint288 (compare last byte first):
    #   ref_hi_first (ends 0x00) < ref_lo_first (ends 0xff)
    expected = struct.pack('<I', 2) + double_sha256(ref_hi_first + ref_lo_first)
    assert result == expected

    # Must differ from the naive big-endian ordering (the pre-fix behaviour),
    # proving the ordering actually matters for multi-ref outputs.
    naive = struct.pack('<I', 2) + double_sha256(
        b''.join(sorted([ref_hi_first, ref_lo_first]))
    )
    assert result != naive


def test_ref_order_is_independent_of_script_order():
    # Same two refs, pushed in the opposite script order -> identical hash,
    # because Core (and now we) sort before hashing.
    a = b'\xaa' + b'\x11' * 35
    b = b'\x11' * 35 + b'\xaa'
    r1 = tx_lib.Deserializer(b'\x00').calculate_pushrefs_count_and_hash(_refs_script([a, b]))
    r2 = tx_lib.Deserializer(b'\x00').calculate_pushrefs_count_and_hash(_refs_script([b, a]))
    assert r1 == r2


def test_single_ref_output_unchanged_by_fix():
    # 0/1-ref outputs (the overwhelmingly common case) are unaffected: a one-
    # element sort is a no-op regardless of key.
    ref = b'\x12' * 36
    result = tx_lib.Deserializer(b'\x00').calculate_pushrefs_count_and_hash(
        bytes([OP_PUSHINPUTREF]) + ref
    )
    assert result == struct.pack('<I', 1) + double_sha256(ref)


def test_no_ref_output_uses_zero_ref_hash():
    # A non-push, non-ref script (OP_DUP) -> zero refs -> zeroRef sentinel.
    result = tx_lib.Deserializer(b'\x00').calculate_pushrefs_count_and_hash(b'\x76')
    assert result == struct.pack('<I', 0) + b'\x00' * 32


def test_duplicate_refs_are_deduped():
    # Core dedups via std::set; count is the UNIQUE count.
    ref = b'\x07' * 36
    result = tx_lib.Deserializer(b'\x00').calculate_pushrefs_count_and_hash(
        _refs_script([ref, ref])
    )
    assert result == struct.pack('<I', 1) + double_sha256(ref)
