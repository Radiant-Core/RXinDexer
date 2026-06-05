"""Tests for electrumx.lib.rswp_verify (RSWP swap-order signature auth, Part B).

What is tested WITHOUT a crypto backend (always runs here):
  * the Radiant ForkId sighash *preimage* byte construction — cross-validated
    against the stdlib reference vectors shipped with radiantjs
    (test/data/sighash_radiant.json), plus an explicit golden-bytes assertion;
  * the RSWP signature (P2PKH scriptSig) parser;
  * the opt-in / availability gate.

What is GATED on coincurve (skipped when the EC backend is missing):
  * verify_rswp_signature round-trip.
"""

import hashlib
import json
import os
import struct

import pytest

from electrumx.lib.rswp_verify import (
    build_rswp_sighash_preimage,
    parse_rswp_signature,
    verify_rswp_signature,
    signature_verification_enabled,
    double_sha256,
    compact_size,
    CryptoUnavailable,
    RSWP_SIGHASH_TYPE,
    SIGHASH_SINGLE,
    SIGHASH_FORKID,
    SIGHASH_ANYONECANPAY,
)


def _has_coincurve():
    try:
        import coincurve  # noqa: F401
        return True
    except Exception:
        return False


HAS_COINCURVE = _has_coincurve()

# Path to the radiantjs reference vectors (authoritative cross-check source).
_RADIANTJS_VECTORS = os.path.join(
    os.path.dirname(__file__), '..', '..', '..', 'radiantjs',
    'test', 'data', 'sighash_radiant.json'
)


def _sha256d(b):
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()


def _p2pkh(h160: bytes) -> bytes:
    assert len(h160) == 20
    return b'\x76\xa9\x14' + h160 + b'\x88\xac'


# --- constants ---------------------------------------------------------------
def test_rswp_sighash_type_value():
    assert RSWP_SIGHASH_TYPE == 0xC3
    assert RSWP_SIGHASH_TYPE == (SIGHASH_SINGLE | SIGHASH_ANYONECANPAY | SIGHASH_FORKID)


def test_compact_size_encodings():
    assert compact_size(0) == b'\x00'
    assert compact_size(0xfc) == b'\xfc'
    assert compact_size(0xfd) == b'\xfd\xfd\x00'
    assert compact_size(0x1234) == b'\xfd\x34\x12'
    assert compact_size(0x10000) == b'\xfe\x00\x00\x01\x00'


# --- preimage construction ----------------------------------------------------
def test_preimage_single_acp_golden_bytes():
    """Exact-byte golden test for a synthetic SINGLE|ANYONECANPAY|FORKID order.

    Backing UTXO: txid=aa.., vout=0, P2PKH(11..), value=1_000_000_000, seq=0xffffffff.
    Payout: P2PKH(22..), value=100_000_000. version=1, locktime=0.
    """
    backing_txid_internal = bytes.fromhex('aa' * 32)
    backing_spk = _p2pkh(bytes.fromhex('11' * 20))
    payout_spk = _p2pkh(bytes.fromhex('22' * 20))

    pre = build_rswp_sighash_preimage(
        version=1,
        backing_txid_internal=backing_txid_internal,
        backing_vout=0,
        backing_script_pubkey=backing_spk,
        backing_value=1_000_000_000,
        n_sequence=0xffffffff,
        payout_script=payout_spk,
        payout_value=100_000_000,
        n_locktime=0,
        sighash_type=RSWP_SIGHASH_TYPE,
    )

    # Reconstruct the expected preimage independently from the documented layout.
    out_serial = struct.pack('<q', 100_000_000) + compact_size(len(payout_spk)) + payout_spk
    out_summary = (struct.pack('<q', 100_000_000) + _sha256d(payout_spk)
                   + struct.pack('<I', 0) + b'\x00' * 32)
    expected = (
        struct.pack('<i', 1)
        + b'\x00' * 32                       # hashPrevouts (ANYONECANPAY)
        + b'\x00' * 32                       # hashSequence (SINGLE)
        + backing_txid_internal + struct.pack('<I', 0)
        + compact_size(len(backing_spk)) + backing_spk
        + struct.pack('<q', 1_000_000_000)
        + struct.pack('<I', 0xffffffff)
        + _sha256d(out_summary)              # hashOutputHashes
        + _sha256d(out_serial)               # hashOutputs
        + struct.pack('<I', 0)               # nLockTime
        + struct.pack('<I', RSWP_SIGHASH_TYPE)
    )
    assert pre == expected
    # digest is double-sha256 of the preimage
    assert double_sha256(pre) == _sha256d(pre)


def test_preimage_matches_radiantjs_single_reference_vector():
    """Cross-validate the SINGLE|FORKID branch against radiantjs golden vectors.

    Vector #4 in sighash_radiant.json is a 1-in/2-out tx signed SIGHASH_SINGLE|
    FORKID on input 0 (committing only output 0).  Our builder models exactly
    that single-input / single-committed-output shape, so feeding it the same
    inputs must reproduce the reference digest.  (Vector is SINGLE without
    ANYONECANPAY -> hashPrevouts is the single input's prevout hash, not zero.)
    """
    if not os.path.exists(_RADIANTJS_VECTORS):
        pytest.skip('radiantjs reference vectors not present')
    vectors = json.load(open(_RADIANTJS_VECTORS))
    # [raw_tx, scriptcode, n_in, sighash_type, expected_BE, amount, desc]
    vec = next(v for v in vectors
               if v[3] == (SIGHASH_SINGLE | SIGHASH_FORKID) and v[2] == 0)

    scriptcode = bytes.fromhex(vec[1])
    sighash_type = vec[3]
    expected_be = bytes.fromhex(vec[4])
    amount = int(vec[5])

    # Reference fixture: in_a0 = txid 'aa'*32 vout 0 seq 0xffffffff;
    # output 0 = value 100_000_000 to P2PKH('22'*20); version 1; locktime 0.
    backing_txid_display = bytes.fromhex('aa' * 32)
    pre = build_rswp_sighash_preimage(
        version=1,
        backing_txid_internal=backing_txid_display[::-1],  # display -> internal
        backing_vout=0,
        backing_script_pubkey=scriptcode,
        backing_value=amount,
        n_sequence=0xffffffff,
        payout_script=_p2pkh(bytes.fromhex('22' * 20)),
        payout_value=100_000_000,
        n_locktime=0,
        sighash_type=sighash_type,
    )
    assert double_sha256(pre) == expected_be


def test_preimage_rejects_bad_txid_length():
    with pytest.raises(ValueError):
        build_rswp_sighash_preimage(
            version=1, backing_txid_internal=b'\x00' * 31, backing_vout=0,
            backing_script_pubkey=b'', backing_value=0, n_sequence=0,
            payout_script=b'', payout_value=0, n_locktime=0)


# --- scriptSig parser ---------------------------------------------------------
def _push(data: bytes) -> bytes:
    assert len(data) <= 75
    return bytes([len(data)]) + data


def test_parse_signature_valid_compressed():
    der = bytes([0x30, 0x06]) + bytes(6)  # plausible-looking DER body
    sig_with_type = der + bytes([RSWP_SIGHASH_TYPE])
    pubkey = b'\x02' + bytes(32)
    script = _push(sig_with_type) + _push(pubkey)

    parsed = parse_rswp_signature(script)
    assert parsed is not None
    der_out, sht, pk = parsed
    assert der_out == der
    assert sht == RSWP_SIGHASH_TYPE
    assert pk == pubkey


def test_parse_signature_uncompressed_pubkey():
    sig_with_type = bytes([0x30, 0x02, 0x00, 0x00]) + bytes([RSWP_SIGHASH_TYPE])
    pubkey = b'\x04' + bytes(64)
    script = bytes([0x4c, len(sig_with_type)]) + sig_with_type + _push65(pubkey)
    parsed = parse_rswp_signature(script)
    assert parsed is not None
    assert parsed[2] == pubkey


def _push65(data: bytes) -> bytes:
    # 65 > 75 is False, so a direct push works for 65 bytes.
    return bytes([len(data)]) + data


def test_parse_signature_rejects_non_two_pushes():
    # Only one push present.
    assert parse_rswp_signature(_push(b'\x30\x02\x00\x00\xc3')) is None
    # Empty.
    assert parse_rswp_signature(b'') is None


def test_parse_signature_rejects_bad_pubkey():
    sig_with_type = bytes([0x30, 0x02, 0x00, 0x00, RSWP_SIGHASH_TYPE])
    bad_pubkey = b'\x05' + bytes(32)  # wrong prefix for 33-byte key
    script = _push(sig_with_type) + _push(bad_pubkey)
    assert parse_rswp_signature(script) is None


def test_parse_signature_rejects_trailing_bytes():
    sig_with_type = bytes([0x30, 0x02, 0x00, 0x00, RSWP_SIGHASH_TYPE])
    pubkey = b'\x02' + bytes(32)
    script = _push(sig_with_type) + _push(pubkey) + b'\x99'
    assert parse_rswp_signature(script) is None


# --- opt-in gate --------------------------------------------------------------
def test_verification_default_off(monkeypatch):
    monkeypatch.delenv('SWAP_VERIFY_SIGNATURES', raising=False)
    assert signature_verification_enabled() is False


def test_verification_off_without_backend_even_if_flagged(monkeypatch):
    monkeypatch.setenv('SWAP_VERIFY_SIGNATURES', '1')
    if HAS_COINCURVE:
        assert signature_verification_enabled() is True
    else:
        # Flag set but no backend -> still off (degrade to accept, never reject).
        assert signature_verification_enabled() is False


@pytest.mark.skipif(HAS_COINCURVE,
                    reason='exercise the no-backend error path only when coincurve absent')
def test_verify_raises_without_backend():
    with pytest.raises(CryptoUnavailable):
        verify_rswp_signature(b'\x00' * 100, b'\x30\x02\x00\x00', b'\x02' + bytes(32))


# --- ECDSA round-trip (GATED: requires coincurve) -----------------------------
@pytest.mark.skipif(not HAS_COINCURVE, reason='coincurve not installed')
def test_verify_signature_roundtrip():
    from coincurve import PrivateKey

    priv = PrivateKey()
    pub = priv.public_key.format(compressed=True)

    pre = build_rswp_sighash_preimage(
        version=1,
        backing_txid_internal=bytes.fromhex('aa' * 32),
        backing_vout=0,
        backing_script_pubkey=_p2pkh(bytes.fromhex('11' * 20)),
        backing_value=1_000_000_000,
        n_sequence=0xffffffff,
        payout_script=_p2pkh(bytes.fromhex('22' * 20)),
        payout_value=100_000_000,
        n_locktime=0,
        sighash_type=RSWP_SIGHASH_TYPE,
    )
    digest = double_sha256(pre)
    der = priv.sign(digest, hasher=None)

    assert verify_rswp_signature(pre, der, pub) is True
    # Wrong digest -> invalid.
    bad = build_rswp_sighash_preimage(
        version=1,
        backing_txid_internal=bytes.fromhex('aa' * 32),
        backing_vout=0,
        backing_script_pubkey=_p2pkh(bytes.fromhex('11' * 20)),
        backing_value=999,
        n_sequence=0xffffffff,
        payout_script=_p2pkh(bytes.fromhex('22' * 20)),
        payout_value=100_000_000,
        n_locktime=0,
        sighash_type=RSWP_SIGHASH_TYPE,
    )
    assert verify_rswp_signature(bad, der, pub) is False
