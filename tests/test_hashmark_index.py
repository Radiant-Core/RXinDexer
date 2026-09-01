"""
Tests for the HashMark digest index.

Covers the parser against the test vectors in the HashMark spec (§7), plus a round-trip through
the index itself: flush -> lookup ordering -> reorg unwind.  Run:
PYTHONPATH=. python3 -m pytest tests/test_hashmark_index.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from electrumx.server.hashmark_index import (  # noqa: E402
    HashMarkIndex, HashMarkRecord, HashMarkDBKeys,
    parse_hashmark, read_pushes, parse_digest_arg, resolve_algorithm,
    MAGIC, HASHMARK_PREFIX,
)

DIGEST = bytes([0xAA]) * 32
DIGEST_HEX = DIGEST.hex()


def script(*parts: bytes) -> bytes:
    return b''.join(parts)


def push(data: bytes) -> bytes:
    """Minimal push encoding, as a HashMark writer must produce."""
    if len(data) <= 0x4B:
        return bytes([len(data)]) + data
    if len(data) <= 0xFF:
        return bytes([0x4C, len(data)]) + data
    return bytes([0x4D, len(data) & 0xFF, len(data) >> 8]) + data


OP_RETURN = b'\x6a'
HEADER_V1_SHA256 = push(bytes([1, 1]))


def valid_script(digest=DIGEST, label=None, header=HEADER_V1_SHA256):
    parts = [OP_RETURN, push(MAGIC), header, push(digest)]
    if label is not None:
        parts.append(push(label))
    return script(*parts)


SIGNER = bytes([0x26]) * 20
SIGNATURE = bytes([0x1F]) + bytes([0xAB]) * 64
HEADER_V2_SHA256 = push(bytes([2, 1]))


def v2_script(digest=DIGEST, label=None, header=HEADER_V2_SHA256,
              signer=SIGNER, signature=SIGNATURE):
    parts = [OP_RETURN, push(MAGIC), header, push(digest), push(signer), push(signature)]
    if label is not None:
        parts.append(push(label))
    return script(*parts)


# --------------------------------------------------------------------------- parser: accept

def test_prefix_is_the_ten_documented_bytes():
    assert HASHMARK_PREFIX == bytes.fromhex('6a0848415348' '4d41524b')
    assert len(HASHMARK_PREFIX) == 10
    assert valid_script().startswith(HASHMARK_PREFIX)


def test_record_with_no_label():
    raw = valid_script()
    assert len(raw) == 46, 'spec §7: unlabelled record is 46 bytes'
    rec = parse_hashmark(raw)
    assert isinstance(rec, HashMarkRecord), rec
    assert rec.version == 1
    assert rec.algorithm_id == 1
    assert rec.algorithm == 'sha256'
    assert rec.digest.hex() == DIGEST_HEX
    assert rec.label is None


def test_record_with_label():
    rec = parse_hashmark(valid_script(label=b'hi'))
    assert isinstance(rec, HashMarkRecord), rec
    assert rec.digest.hex() == DIGEST_HEX
    assert rec.label == 'hi'


def test_max_length_label_is_accepted():
    rec = parse_hashmark(valid_script(label=b'x' * 128))
    assert isinstance(rec, HashMarkRecord), rec
    assert rec.label == 'x' * 128
    # The spec's stated 176-byte ceiling is exactly this record.
    assert len(valid_script(label=b'x' * 128)) == 176


# --------------------------------------------------------------------------- parser: v2

def test_v2_record_with_no_label():
    raw = v2_script()
    assert len(raw) == 133, 'unlabelled v2 record: 46 + 21 signer + 66 signature'
    rec = parse_hashmark(raw)
    assert isinstance(rec, HashMarkRecord), rec
    assert rec.version == 2
    assert rec.digest == DIGEST
    assert rec.signer_hash160 == SIGNER
    assert rec.label is None


def test_v2_reads_the_label_from_push_five():
    # The whole reason an unknown version must not be guessed at: in v1 this position holds the
    # digest's neighbour, in v2 it holds a signature.
    rec = parse_hashmark(v2_script(label=b'Contract draft'))
    assert isinstance(rec, HashMarkRecord), rec
    assert rec.label == 'Contract draft'
    assert rec.signer_hash160 == SIGNER


def test_v2_label_cap_is_lower_than_v1():
    # v2 spends 87 bytes on the attestation, so the label budget shrinks to 88.
    assert isinstance(parse_hashmark(v2_script(label=b'x' * 88)), HashMarkRecord)
    assert parse_hashmark(v2_script(label=b'x' * 89)) == 'INVALID'
    # v1 keeps its own, larger cap.
    assert isinstance(parse_hashmark(valid_script(label=b'x' * 128)), HashMarkRecord)


def test_v2_rejects_a_wrong_sized_signer_or_signature():
    assert parse_hashmark(v2_script(signer=bytes([0x26]) * 19)) == 'INVALID'
    assert parse_hashmark(v2_script(signature=bytes([0xAB]) * 64)) == 'INVALID'


def test_v2_rejects_a_v1_push_count():
    # Missing the signature entirely.
    raw = script(OP_RETURN, push(MAGIC), HEADER_V2_SHA256, push(DIGEST), push(SIGNER))
    assert parse_hashmark(raw) == 'INVALID'


def test_v2_signature_is_not_verified_here():
    # A signature of the right shape but no cryptographic meaning is still indexed: this index is
    # a search hint, and the client recovers the key and checks it against the commitment itself.
    rec = parse_hashmark(v2_script(signature=bytes([0x1F]) + bytes(64)))
    assert isinstance(rec, HashMarkRecord), rec


# --------------------------------------------------------------------------- parser: reject

def test_short_digest_is_invalid():
    assert parse_hashmark(valid_script(digest=bytes([0xAA]) * 31)) == 'INVALID'


def test_future_version_is_not_indexed_as_a_known_one():
    # 3, not 2: v2 is a format this index reads, so a v1-shaped record claiming version 2 is a
    # malformed v2 record rather than an unknown version.  A version from the future is the case
    # this test is about.
    assert parse_hashmark(valid_script(header=push(bytes([3, 1])))) == 'UNKNOWN_VERSION'


def test_a_v2_shaped_record_claiming_v1_is_invalid():
    # The reverse mistake: right pushes, wrong version byte.  Reading it as v1 would index a
    # signature as a label.
    raw = v2_script(header=HEADER_V1_SHA256)
    assert parse_hashmark(raw) == 'INVALID'


def test_unknown_algorithm():
    assert parse_hashmark(valid_script(header=push(bytes([1, 2])))) == 'UNKNOWN_ALGORITHM'


def test_non_minimal_push_is_not_a_hashmark():
    # PUSHDATA1 used for a 32-byte digest that fits a direct push.
    raw = script(OP_RETURN, push(MAGIC), HEADER_V1_SHA256, bytes([0x4C, 0x20]), DIGEST)
    assert parse_hashmark(raw) == 'NOT_HASHMARK'


def test_another_protocol_is_not_a_hashmark():
    raw = bytes.fromhex('6a036d735706736e6b00336b')
    assert parse_hashmark(raw) == 'NOT_HASHMARK'
    assert not raw.startswith(HASHMARK_PREFIX)


def test_small_int_opcodes_are_rejected():
    # OP_1 (0x51) would give a one-byte field a second spelling.
    raw = script(OP_RETURN, push(MAGIC), b'\x51', push(DIGEST))
    assert parse_hashmark(raw) == 'NOT_HASHMARK'


def test_oversized_label_is_invalid():
    assert parse_hashmark(valid_script(label=b'x' * 129)) == 'INVALID'


def test_empty_label_cannot_be_encoded():
    # An empty 4th push can only be spelled OP_0, which the minimal-push rule rejects outright —
    # so "label present but empty" is unrepresentable rather than merely invalid.
    assert parse_hashmark(valid_script(label=b'')) == 'NOT_HASHMARK'


def test_control_characters_and_bad_utf8_in_label_are_invalid():
    assert parse_hashmark(valid_script(label=b'a\x01b')) == 'INVALID'
    assert parse_hashmark(valid_script(label=b'\xff\xfe')) == 'INVALID'


def test_two_pushes_or_five_pushes_are_invalid():
    assert parse_hashmark(script(OP_RETURN, push(MAGIC), HEADER_V1_SHA256)) == 'INVALID'
    assert parse_hashmark(valid_script(label=b'hi') + push(b'extra')) == 'INVALID'


def test_non_op_return_output():
    assert parse_hashmark(b'\x76\xa9\x14' + b'\x00' * 20 + b'\x88\xac') == 'NOT_HASHMARK'
    assert parse_hashmark(b'') == 'NOT_HASHMARK'


def test_read_pushes_rejects_truncated_push():
    assert read_pushes(b'\x20\xaa\xbb', 0) is None


# --------------------------------------------------------------------------- argument validation

def test_digest_argument_is_strict():
    assert parse_digest_arg(DIGEST_HEX, 1) == DIGEST
    assert parse_digest_arg(DIGEST_HEX.upper(), 1) is None      # uppercase
    assert parse_digest_arg(DIGEST_HEX[:32], 1) is None         # partial -> no enumeration
    assert parse_digest_arg(DIGEST_HEX + 'aa', 1) is None
    assert parse_digest_arg('zz' * 32, 1) is None
    assert parse_digest_arg(DIGEST_HEX, 99) is None
    assert parse_digest_arg(None, 1) is None


def test_algorithm_resolution():
    assert resolve_algorithm('sha256') == 1
    assert resolve_algorithm(1) == 1
    assert resolve_algorithm('1') == 1
    assert resolve_algorithm('sha512') is None
    assert resolve_algorithm(2) is None
    assert resolve_algorithm(True) is None      # bool is an int subclass; must not pass as id 1


# --------------------------------------------------------------------------- index round-trip

class _Out:
    def __init__(self, script_bytes):
        self.pk_script = script_bytes


class _Tx:
    def __init__(self, scripts):
        self.outputs = [_Out(s) for s in scripts]


class _Batch:
    """Collects puts/deletes and applies them to the stub store on exit, like a RocksDB batch."""

    def __init__(self, store):
        self.store = store
        self.ops = []

    def put(self, key, value):
        self.ops.append(('put', key, value))

    def delete(self, key):
        self.ops.append(('del', key, None))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for op, key, value in self.ops:
            if op == 'put':
                self.store[key] = value
            else:
                self.store.pop(key, None)
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
        if reverse:
            keys = list(reversed(keys))
        for key in keys:
            yield key, self.store[key]


class _StubDB:
    def __init__(self):
        self.utxo_db = _StubUtxoDB()
        self.db_height = 500000


class _Env:
    hashmark_index = True
    reorg_limit = 200


def _index():
    return HashMarkIndex(_StubDB(), _Env())


def _mark(idx, txid_byte, height, label=None, digest=DIGEST, vout_scripts=None):
    tx = _Tx(vout_scripts or [valid_script(digest=digest, label=label)])
    idx.process_tx(bytes([txid_byte]) * 32, tx, height, bytes([height & 0xFF]) * 32)


def _flush(idx):
    with idx.db.utxo_db.write_batch() as batch:
        idx.flush(batch)


def test_lookup_returns_marks_oldest_first():
    idx = _index()
    # Recorded out of order; the key layout must still yield height-ascending results.
    _mark(idx, 0x22, 400)
    _mark(idx, 0x11, 100, label=b'first')
    _mark(idx, 0x33, 900)
    _flush(idx)

    hits = idx.lookup(DIGEST, 1, 20)
    assert [h['height'] for h in hits] == [100, 400, 900]
    assert hits[0]['label'] == 'first'
    assert 'label' not in hits[1], 'absent label must be omitted, not null'
    assert hits[0]['txid'] == (bytes([0x11]) * 32)[::-1].hex()
    assert hits[0]['block_hash'] == (bytes([100]) * 32)[::-1].hex()
    assert hits[0]['algorithm'] == 'sha256'
    assert hits[0]['version'] == 1
    assert hits[0]['digest'] == DIGEST_HEX
    assert hits[0]['output_index'] == 0


def test_lookup_returns_the_committed_signer_for_v2_marks():
    idx = _index()
    # The same file marked twice: once under v1, once under v2 by a key. Both are real, neither
    # supersedes the other, and only the v2 row can say who made it.
    _mark(idx, 0x11, 100)
    idx.process_tx(bytes([0x22]) * 32, _Tx([v2_script(label=b'signed')]), 400,
                   bytes([400 & 0xFF]) * 32)
    _flush(idx)

    hits = idx.lookup(DIGEST, 1, 20)
    assert [h['version'] for h in hits] == [1, 2]
    assert 'signer_hash160' not in hits[0], 'a v1 row has no signer to report'
    assert hits[1]['signer_hash160'] == SIGNER.hex()
    assert hits[1]['label'] == 'signed'


def test_v1_rows_written_before_v2_existed_still_read_back():
    # The stored value is version-led, so an old row is read the old way and no rescan is needed.
    idx = _index()
    _mark(idx, 0x11, 100, label=b'old')
    _flush(idx)
    hits = idx.lookup(DIGEST, 1, 20)
    assert hits[0]['version'] == 1
    assert hits[0]['label'] == 'old'
    assert 'signer_hash160' not in hits[0]


def test_unmarked_digest_returns_empty_list():
    idx = _index()
    _mark(idx, 0x11, 100)
    _flush(idx)
    assert idx.lookup(bytes([0xBB]) * 32, 1, 20) == []


def test_limit_is_honoured():
    idx = _index()
    for i in range(10):
        _mark(idx, 0x10 + i, 100 + i)
    _flush(idx)
    assert len(idx.lookup(DIGEST, 1, 3)) == 3
    assert [h['height'] for h in idx.lookup(DIGEST, 1, 3)] == [100, 101, 102]


def test_same_tx_multiple_outputs_are_separate_rows():
    idx = _index()
    tx = _Tx([valid_script(label=b'a'), b'\x6a\x04test', valid_script(label=b'b')])
    idx.process_tx(bytes([0x11]) * 32, tx, 100, bytes([1]) * 32)
    _flush(idx)
    hits = idx.lookup(DIGEST, 1, 20)
    assert [h['output_index'] for h in hits] == [0, 2]


def test_invalid_outputs_are_not_indexed():
    idx = _index()
    tx = _Tx([
        valid_script(header=push(bytes([2, 1]))),      # UNKNOWN_VERSION
        valid_script(digest=bytes([0xAA]) * 31),       # INVALID
        bytes.fromhex('6a036d735706736e6b00336b'),     # another protocol
    ])
    idx.process_tx(bytes([0x11]) * 32, tx, 100, bytes([1]) * 32)
    _flush(idx)
    assert idx.lookup(DIGEST, 1, 20) == []
    assert idx._pending_rows == 0


def test_reorg_removes_only_the_disconnected_block():
    idx = _index()
    _mark(idx, 0x11, 100)
    _mark(idx, 0x22, 101)
    _flush(idx)
    assert len(idx.lookup(DIGEST, 1, 20)) == 2

    with idx.db.utxo_db.write_batch() as batch:
        idx.backup(batch, 101)

    hits = idx.lookup(DIGEST, 1, 20)
    assert [h['height'] for h in hits] == [100], 'block 100 must survive an unwind of block 101'
    assert idx.db.utxo_db.get(HashMarkDBKeys.UNDO + b'\x00\x00\x00\x65') is None


def test_reorg_then_reindex_does_not_duplicate():
    idx = _index()
    _mark(idx, 0x11, 100)
    _flush(idx)
    with idx.db.utxo_db.write_batch() as batch:
        idx.backup(batch, 100)
    assert idx.lookup(DIGEST, 1, 20) == []

    # Same tx re-mined at a new height on the new chain.
    _mark(idx, 0x11, 100)
    _flush(idx)
    assert len(idx.lookup(DIGEST, 1, 20)) == 1


def test_disabled_index_records_nothing():
    class _Off(_Env):
        hashmark_index = False

    idx = HashMarkIndex(_StubDB(), _Off())
    _mark(idx, 0x11, 100)
    _flush(idx)
    assert idx.db.utxo_db.store == {}


def test_fresh_sync_needs_no_backfill():
    # A from-genesis resync (what the DB_VERSIONS bump forces) indexes every block live, so the
    # historic scan must resolve to an empty range rather than re-reading the chain.
    db = _StubDB()
    db.db_height = -1
    idx = HashMarkIndex(db, _Env())
    _mark(idx, 0x11, 0)
    _flush(idx)
    assert idx.db.utxo_db.get(HashMarkDBKeys.LIVE_FROM) == b'\x00\x00\x00\x00'
    assert idx._backfill_target(500000) == -1


def test_enabling_on_a_synced_db_backfills_everything_below():
    db = _StubDB()
    db.db_height = 500000
    idx = HashMarkIndex(db, _Env())
    _mark(idx, 0x11, 500001)
    _flush(idx)
    # Live coverage starts at 500001, so the scan must cover 0..500000.
    assert idx._backfill_target(500001) == 500000


def test_live_from_watermark_is_written_once():
    db = _StubDB()
    db.db_height = 100
    idx = HashMarkIndex(db, _Env())
    _mark(idx, 0x11, 101)
    _flush(idx)
    assert idx._backfill_target(0) == 100

    # A later flush at a higher height must not move the watermark forward.
    db.db_height = 200
    idx2 = HashMarkIndex(db, _Env())
    _mark(idx2, 0x22, 201)
    _flush(idx2)
    assert idx2._backfill_target(0) == 100


def test_stats_reports_backfill_progress():
    idx = _index()
    stats = idx.stats()
    assert stats['enabled'] is True
    assert stats['backfill_complete'] is False
    assert stats['algorithms'] == {'sha256': 1}
    assert stats['protocol_version'] == 2
    assert stats['protocol_versions'] == [1, 2]


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
