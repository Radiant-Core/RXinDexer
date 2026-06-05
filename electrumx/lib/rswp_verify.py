"""RSWP swap-order signature verification (C3-auth, Part B).

This module reconstructs the Radiant ForkId sighash *preimage* that a maker
signs when advertising an RSWP swap order, and (optionally, behind an import
gate) ECDSA-verifies the maker's partial signature against it.

Authorization scheme (authoritative — from the Photonic-Wallet source)
----------------------------------------------------------------------
A maker advertises a swap by publishing a *partially-signed Radiant tx*
(``partiallySigned`` in ``packages/lib/src/transfer.tsx``):

  * the single signed input is the backing UTXO the order offers
    (``offeredTxid``/``offeredVout``);
  * the single output is the exact payout the maker requires — this is the
    ``priceTerms`` MultiTxOutV1 blob in the advertisement;
  * the sighash type is ``SIGHASH_SINGLE | SIGHASH_ANYONECANPAY | SIGHASH_FORKID``
    (``transfer.tsx`` L220-223), i.e. ``0x03 | 0x80 | 0x40 == 0xC3``.

The advertisement's ``signature`` field is that input's *scriptSig*
(``Swap.tsx`` L701: ``new rjs.Transaction(rawPsrt).inputs[0].script``), built by
``buildTx`` (``packages/lib/src/tx.ts`` L48-61) as a standard P2PKH unlock:

    <DER-signature || 1-byte sighash type>  <33-byte compressed pubkey>

Under SINGLE|ANYONECANPAY the signed preimage commits to:

  * the backing input's outpoint (txid + vout),
  * the backing UTXO's scriptPubKey (``scriptCode``) and value,
  * nSequence of that input,
  * the single matching output[0] = the priceTerms payout,
  * nLockTime and the sighash type.

So a valid signature proves the advertiser controls the backing UTXO AND has
committed to the exact payout the order claims — which is precisely the
authorization an indexer wants before admitting an order to the public
orderbook.

Radiant ForkId preimage (NOT vanilla BCH BIP143!)
-------------------------------------------------
Radiant inserts an extra ``hashOutputHashes`` field (a per-output value +
scriptPubKey-hash + pushRef-color-hash digest) immediately *before* the
classic ``hashOutputs`` field.  The exact layout, cross-validated against
``Radiant-Core/src/script/interpreter.cpp::SignatureHash`` and
``radiantjs/lib/transaction/sighash.js``, is reproduced here in pure stdlib so
it is testable without any native crypto:

    4   version (int32 LE)
    32  hashPrevouts        (zero under ANYONECANPAY)
    32  hashSequence        (zero under SINGLE/NONE or ANYONECANPAY)
    36  outpoint            (txid internal-order [reversed display] + vout LE)
    var scriptCode         (CompactSize len + backing scriptPubKey bytes)
    8   value (int64 LE)    (backing UTXO value)
    4   nSequence (uint32 LE)
    32  hashOutputHashes    (Radiant color/pushref output digest)
    32  hashOutputs         (classic single-output digest, for SINGLE = output[n_in])
    4   nLockTime (uint32 LE)
    4   sighashType (uint32 LE)

The signature digest is ``double_sha256(preimage)``.

Integration status / why this is NOT wired into block processing
-----------------------------------------------------------------
``SwapIndex.process_tx`` does NOT call this module.  Verifying a signature here
requires the backing UTXO's *scriptPubKey*, which the indexer does not persist
for ref-bearing outputs, and fetching it via a daemon RPC inside the
block-sync loop is an anti-pattern (synchronous network I/O per advertisement,
during the hot indexing path).  There are two clean integration options, both
out of scope for this change:

  1. Persist the scriptPubKey (and value) for ref-bearing outputs in a side
     table and reindex, then verify synchronously from disk.
  2. Asynchronously prefetch the backing prevouts (batch daemon
     ``getrawtransaction``) outside the sync loop and verify off the hot path.

Until the ECDSA step is validated in a ``coincurve``-capable environment, the
opt-in hook :func:`signature_verification_enabled` defaults to OFF so a missing
or mis-built crypto dependency can never cause legitimate orders to be
rejected.  ``coincurve`` is an *optional* dependency (see requirements.txt).
"""

import hashlib
import os
import struct
from typing import List, Optional, Tuple

# --- sighash type constants (match Radiant-Core sighashtype.h) ----------------
SIGHASH_ALL = 0x01
SIGHASH_NONE = 0x02
SIGHASH_SINGLE = 0x03
SIGHASH_FORKID = 0x40
SIGHASH_ANYONECANPAY = 0x80

# The sighash type an RSWP maker signs with (Photonic transfer.tsx L220-223).
RSWP_SIGHASH_TYPE = SIGHASH_SINGLE | SIGHASH_ANYONECANPAY | SIGHASH_FORKID  # 0xC3

ZERO32 = b'\x00' * 32


# --- crypto availability sentinel --------------------------------------------
class CryptoUnavailable(RuntimeError):
    """Raised when ECDSA verification is requested but no backend is importable.

    The byte-level preimage construction and scriptSig parsing in this module
    are pure-Python and always available; only :func:`verify_rswp_signature`
    needs a native EC backend (``coincurve``).
    """


def _has_coincurve() -> bool:
    try:
        import coincurve  # noqa: F401
        return True
    except Exception:
        return False


def signature_verification_enabled() -> bool:
    """True iff swap signature verification is opted in AND a backend exists.

    Gated by the ``SWAP_VERIFY_SIGNATURES`` env flag (default OFF).  Even when
    the flag is set, returns False if ``coincurve`` is not importable, so a
    misconfigured deployment degrades to "do not verify" (accept) rather than
    "reject everything".
    """
    flag = os.environ.get('SWAP_VERIFY_SIGNATURES', '')
    if flag.strip().lower() not in ('1', 'true', 'yes', 'on'):
        return False
    return _has_coincurve()


# --- low-level encoders -------------------------------------------------------
def _sha256(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def double_sha256(b: bytes) -> bytes:
    """SHA-256 of SHA-256 (Radiant CHash256 / the sighash digest)."""
    return _sha256(_sha256(b))


def compact_size(n: int) -> bytes:
    """Bitcoin CompactSize (varint) encoding."""
    if n < 0:
        raise ValueError('compact_size: negative length')
    if n < 0xfd:
        return bytes([n])
    if n <= 0xffff:
        return b'\xfd' + struct.pack('<H', n)
    if n <= 0xffffffff:
        return b'\xfe' + struct.pack('<I', n)
    return b'\xff' + struct.pack('<Q', n)


def _output_summary_bytes(value: int, script: bytes) -> bytes:
    """One output's contribution to Radiant ``hashOutputHashes``.

    Vanilla (no push-ref) path: ``totalRefs=0`` and ``refsHash`` = 32 zero
    bytes.  Token/ftScript payouts may carry push refs; to stay deterministic
    and self-contained this implementation handles the vanilla case (the common
    RXD payout) exactly and documents the token case as a follow-up — it is not
    exercised on the (default-off) verification path.
    """
    script_hash = double_sha256(script)
    return (
        struct.pack('<q', value)
        + script_hash
        + struct.pack('<I', 0)   # totalRefs (vanilla)
        + ZERO32                 # refsHash  (vanilla)
    )


def _serialize_output(value: int, script: bytes) -> bytes:
    """Classic CTxOut serialization: value(8 LE) + CompactSize(len) + script."""
    return struct.pack('<q', value) + compact_size(len(script)) + script


def build_rswp_sighash_preimage(
    *,
    version: int,
    backing_txid_internal: bytes,
    backing_vout: int,
    backing_script_pubkey: bytes,
    backing_value: int,
    n_sequence: int,
    payout_script: bytes,
    payout_value: int,
    n_locktime: int,
    sighash_type: int = RSWP_SIGHASH_TYPE,
) -> bytes:
    """Construct the Radiant ForkId sighash preimage an RSWP maker signs.

    Models the SINGLE|ANYONECANPAY|FORKID case (the only one RSWP uses): the
    single signed input is the backing UTXO, and the single committed output is
    the priceTerms payout (treated as output index 0, matching the
    partially-signed advertisement tx where input 0 pairs with output 0).

    Parameters
    ----------
    backing_txid_internal:
        The backing UTXO txid in *internal* byte order (the form stored in the
        indexer / RocksDB; NOT display/BE).  It is written to the preimage as-is
        (the reference reverses the *display* txid to reach this same order).
    backing_script_pubkey:
        The full scriptPubKey of the backing UTXO (the sighash ``scriptCode``).
    payout_script / payout_value:
        The single output the maker requires (decoded from the priceTerms
        MultiTxOutV1 blob).

    Returns the preimage bytes; ``double_sha256(preimage)`` is the value the
    DER signature is verified against.
    """
    if len(backing_txid_internal) != 32:
        raise ValueError('backing_txid_internal must be 32 bytes')

    base = sighash_type & 0x1f
    anyone = bool(sighash_type & SIGHASH_ANYONECANPAY)

    hash_prevouts = ZERO32
    hash_sequence = ZERO32
    hash_outputs = ZERO32
    hash_output_hashes = ZERO32

    # ANYONECANPAY zeroes hashPrevouts; we never have other inputs to commit.
    if not anyone:
        # Single backing input only (advertisement signs one input).
        prevout = backing_txid_internal + struct.pack('<I', backing_vout)
        hash_prevouts = double_sha256(prevout)

    if not anyone and base not in (SIGHASH_SINGLE, SIGHASH_NONE):
        hash_sequence = double_sha256(struct.pack('<I', n_sequence))

    if base not in (SIGHASH_SINGLE, SIGHASH_NONE):
        hash_outputs = double_sha256(_serialize_output(payout_value, payout_script))
        hash_output_hashes = double_sha256(
            _output_summary_bytes(payout_value, payout_script))
    elif base == SIGHASH_SINGLE:
        # n_in == 0 maps to the single payout output[0].
        hash_outputs = double_sha256(_serialize_output(payout_value, payout_script))
        hash_output_hashes = double_sha256(
            _output_summary_bytes(payout_value, payout_script))
    # SIGHASH_NONE leaves both output digests zero.

    preimage = (
        struct.pack('<i', version)
        + hash_prevouts
        + hash_sequence
        + backing_txid_internal + struct.pack('<I', backing_vout)
        + compact_size(len(backing_script_pubkey)) + backing_script_pubkey
        + struct.pack('<q', backing_value)
        + struct.pack('<I', n_sequence)
        + hash_output_hashes
        + hash_outputs
        + struct.pack('<I', n_locktime)
        + struct.pack('<I', sighash_type & 0xffffffff)
    )
    return preimage


# --- scriptSig parsing --------------------------------------------------------
def _read_push(script: bytes, pos: int) -> Tuple[Optional[bytes], int]:
    """Read one canonical data push at ``pos``; return (data, next_pos).

    Returns (None, pos) if the opcode at ``pos`` is not a data push or the push
    is truncated.  Only the push encodings used by a standard P2PKH scriptSig
    (direct push + OP_PUSHDATA1) need to be handled, but PUSHDATA2/4 are
    accepted for robustness.
    """
    if pos >= len(script):
        return None, pos
    op = script[pos]
    pos += 1
    if 1 <= op <= 75:
        dlen = op
    elif op == 0x4c:  # OP_PUSHDATA1
        if pos >= len(script):
            return None, pos
        dlen = script[pos]
        pos += 1
    elif op == 0x4d:  # OP_PUSHDATA2
        if pos + 2 > len(script):
            return None, pos
        dlen = struct.unpack_from('<H', script, pos)[0]
        pos += 2
    elif op == 0x4e:  # OP_PUSHDATA4
        if pos + 4 > len(script):
            return None, pos
        dlen = struct.unpack_from('<I', script, pos)[0]
        pos += 4
    else:
        return None, pos
    if pos + dlen > len(script):
        return None, pos
    return script[pos:pos + dlen], pos + dlen


def parse_rswp_signature(sig_script: bytes) -> Optional[Tuple[bytes, int, bytes]]:
    """Parse the RSWP ``signature`` chunk = a P2PKH input scriptSig.

    Layout: ``<DER||sighashbyte>  <compressed pubkey>`` (Photonic tx.ts L59-61).

    Returns ``(der_sig, sighash_type, pubkey)`` where ``der_sig`` is the bare
    DER ECDSA signature (the trailing sighash byte stripped), ``sighash_type``
    is that stripped byte, and ``pubkey`` is the 33-byte compressed pubkey;
    or ``None`` if the script is not two well-formed pushes / the sig push is
    empty / the pubkey is not a plausible 33- or 65-byte EC point.
    """
    if not sig_script:
        return None
    sig_push, pos = _read_push(sig_script, 0)
    if sig_push is None or len(sig_push) < 2:
        return None
    pubkey, pos = _read_push(sig_script, pos)
    if pubkey is None:
        return None
    if pos != len(sig_script):
        return None  # Trailing bytes — not a clean 2-push scriptSig.
    if len(pubkey) == 33:
        if pubkey[0] not in (0x02, 0x03):
            return None
    elif len(pubkey) == 65:
        if pubkey[0] != 0x04:
            return None
    else:
        return None
    der_sig = sig_push[:-1]
    sighash_type = sig_push[-1]
    return der_sig, sighash_type, pubkey


# --- ECDSA verify (import-gated) ----------------------------------------------
def verify_rswp_signature(preimage: bytes, der_sig: bytes, pubkey: bytes) -> bool:
    """ECDSA-verify a maker's RSWP signature over ``preimage``.

    Computes ``double_sha256(preimage)`` and verifies the DER signature with
    the compressed/uncompressed ``pubkey`` using ``coincurve`` (libsecp256k1).

    Raises :class:`CryptoUnavailable` if no EC backend is importable — callers
    that cannot tolerate that should gate on
    :func:`signature_verification_enabled` first.  Returns True/False for a
    valid/invalid signature; never raises on a merely-bad signature.
    """
    try:
        from coincurve import PublicKey
    except Exception as exc:  # pragma: no cover - exercised only without backend
        raise CryptoUnavailable(
            'coincurve is required for RSWP signature verification; install the '
            'optional dependency (see requirements.txt) or leave '
            'SWAP_VERIFY_SIGNATURES off'
        ) from exc

    digest = double_sha256(preimage)
    try:
        pk = PublicKey(pubkey)
        # coincurve verifies sha256(message) by default; pass the precomputed
        # 32-byte digest via a pass-through hasher so it ECDSA-verifies exactly
        # double_sha256(preimage).
        return pk.verify(der_sig, digest, hasher=None)
    except Exception:
        return False
