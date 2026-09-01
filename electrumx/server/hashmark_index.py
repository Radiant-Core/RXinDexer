"""
HashMark digest index for RXinDexer (`hashmark.lookup`).

Digest lookup is the one HashMark operation no existing Radiant infrastructure can serve:
`blockchain.ref.get` indexes Glyph refs, not data-output payloads, and every other scanner in this
tree is gated on its own magic (`gly`, `RMKT`, `RRYL`, `RSWP`) so a HashMark output leaves no trace
in the DB.  This index closes that gap, which is what lets HashMark drop its separate indexer
process rather than duplicating block-following, reorg handling and confirmation tracking.

Record layout, an OP_RETURN of at most MAX_SCRIPT_BYTES.  Two versions are indexed::

    v1 (HASHMARK_PROTOCOL.md) — 3 or 4 pushes
      OP_RETURN
        <push 8>  "HASHMARK"                      magic
        <push 2>  version(uint8) || algorithmId(uint8)
        <push N>  digest, N fixed by algorithmId  (sha256 = id 0x01, N = 32)
        <push L>  label, OPTIONAL, 1..128 bytes UTF-8

    v2 (docs/HASHMARK_V2_ATTESTATION.md) — 5 or 6 pushes
      OP_RETURN
        <push 8>  "HASHMARK"
        <push 2>  version(0x02) || algorithmId
        <push N>  digest
        <push 20> signerHash160 — the key the record commits to
        <push 65> compact recoverable signature over the canonical statement
        <push L>  label, OPTIONAL, 1..88 bytes UTF-8 for sha256

v2 moves the label from push 3 to push 5, which is exactly why an unknown version must never be
read as a known one: a v1 parser let loose on a v2 record would index a signature as a label.

The signature is NOT verified here.  Doing so needs secp256k1 and the chain's genesis hash, and
would change nothing: HashMark re-fetches every hit and checks the signature against the committed
signer itself.  A row is a pointer, and an unverified pointer is all this index has ever been.

Detection is a 10-byte prefix compare (`HASHMARK_PREFIX`) run against every output, cheap enough
that non-HashMark outputs — the overwhelming majority of OP_RETURNs on Radiant — cost one memcmp
and are skipped silently.

Key layout, one 'HMd' keyspace::

    HMd + algorithmId(1) + digest(N) + be_u32(height) + txid(32) + be_u16(vout) -> version || block_hash || label

The algorithm id leads the key so that digest length is fixed within any one prefix scan; a future
algorithm with a different N therefore cannot make a shorter digest look like the prefix of a longer
one.  Height precedes txid so a prefix iteration over (algorithm, digest) emerges in height-ascending
order for free — the API returns oldest first because the earliest confirmed mark is the meaningful
one.  txid+vout complete the key, giving each output exactly one row.

Trust model: a hit means only "this script is on chain at this height", never that the digest
describes what someone claims.  HashMark re-fetches and re-decodes every hit client-side and treats
this index as a search hint, never as proof — so a bug here can cause a MISSED result but must never
cause a false one.  That is why records carry the block hash they were mined in (§4): a row that
somehow outlived its block is then detectable by the client rather than silently re-attributed to
whatever block later occupies that height.
"""

import asyncio
import os
import re
import struct
from typing import Any, Dict, List, Optional, Tuple, Union

from electrumx.lib import util
from electrumx.lib.hash import hash_to_hex_str
from electrumx.lib.util import pack_be_uint32, unpack_be_uint32, encode_undo, decode_undo

MAGIC = b'HASHMARK'
# OP_RETURN, push-8, "HASHMARK" — the whole detection test.
HASHMARK_PREFIX = bytes([0x6A, 0x08]) + MAGIC

V1 = 1
V2 = 2
SUPPORTED_VERSIONS = (V1, V2)
# The version this index would expect a new record to use.  Kept separate from SUPPORTED_VERSIONS
# because "what we can read" and "what is current" are different questions.
LATEST_VERSION = V2

# v2 fields, both fixed-length.
SIGNER_HASH_BYTES = 20
SIGNATURE_BYTES = 65

# id -> (name, digest length in bytes)
ALGORITHMS: Dict[int, Tuple[str, int]] = {0x01: ('sha256', 32)}
ALGORITHM_IDS_BY_NAME = {name: alg_id for alg_id, (name, _n) in ALGORITHMS.items()}

# v1 had no signature to pay for, so it could afford 40 more bytes of label.
MAX_LABEL_BYTES_V1 = 128
# v2, sha256: 223 - OP_RETURN(1) - magic(9) - header(3) - digest(33) - signer(21) - sig(66) - push(2)
MAX_LABEL_BYTES_V2 = 88
# The conservative relay ceiling both versions are designed against
# (MAX_OP_RETURN_RELAY).  v1 records reach 176 bytes; v2 records reach exactly this.
MAX_SCRIPT_BYTES = 223
MAX_PUSH_BYTES = 520

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

BACKFILL_CHUNK_BLOCKS = int(os.getenv('HASHMARK_BACKFILL_CHUNK_BLOCKS', '200'))

_HEX_RE = re.compile(r'^[0-9a-f]+$')


class HashMarkDBKeys:
    """Database key prefixes for the HashMark index."""
    DATA = b'HMd'               # see module docstring for the composite key layout
    UNDO = b'HMu'               # HMu + be_u32(height) -> encode_undo([(key, None)])
    LIVE_FROM = b'HMs'          # -> be_u32(first height covered by live indexing)
    BACKFILL_CURSOR = b'HMc'    # -> be_u32(next height to scan)
    BACKFILL_TARGET = b'HMt'    # -> be_u32(height the backfill scans up to, inclusive)
    BACKFILL_DONE = b'HMf'      # -> b'1' once the historic scan has completed


class HashMarkRecord:
    """A parsed, validated HashMark output."""

    __slots__ = ('version', 'algorithm_id', 'algorithm', 'digest', 'label',
                 'signer_hash160')

    def __init__(self, version: int, algorithm_id: int, algorithm: str,
                 digest: bytes, label: Optional[str],
                 signer_hash160: Optional[bytes] = None):
        self.version = version
        self.algorithm_id = algorithm_id
        self.algorithm = algorithm
        self.digest = digest          # raw bytes; hex only at the API boundary
        self.label = label
        # v2 only: the key the record commits to.  Stored so a client can tell two marks of one
        # file apart without fetching both transactions.  Never verified here (see module docstring).
        self.signer_hash160 = signer_hash160

    def __eq__(self, other):
        return (isinstance(other, HashMarkRecord)
                and self.version == other.version
                and self.algorithm_id == other.algorithm_id
                and self.digest == other.digest
                and self.label == other.label
                and self.signer_hash160 == other.signer_hash160)

    def __repr__(self):
        return (f'HashMarkRecord(version={self.version}, algorithm={self.algorithm!r}, '
                f'digest={self.digest.hex()!r}, label={self.label!r})')


def read_pushes(script: bytes, offset: int) -> Optional[List[bytes]]:
    """Read every push from `offset` to the end of `script`, or None if any is not minimal.

    Minimal encoding is what gives a record exactly one valid serialization, so two independent
    encoders produce identical bytes and records can be compared byte-for-byte.  OP_0 and
    OP_1..OP_16 are rejected: they would give a one-byte field a second spelling.
    """
    pushes: List[bytes] = []
    i = offset
    n = len(script)
    while i < n:
        op = script[i]
        i += 1
        if 0x01 <= op <= 0x4b:
            length = op
        elif op == 0x4c:                            # OP_PUSHDATA1
            if i >= n:
                return None
            length = script[i]
            i += 1
            if length <= 0x4b:
                return None                         # non-minimal
        elif op == 0x4d:                            # OP_PUSHDATA2
            if i + 1 >= n:
                return None
            length = script[i] | (script[i + 1] << 8)
            i += 2
            if length <= 0xff:
                return None                         # non-minimal
        else:
            return None                             # OP_0, OP_1..OP_16, OP_PUSHDATA4, …
        if length > MAX_PUSH_BYTES or i + length > n:
            return None
        pushes.append(script[i:i + length])
        i += length
    return pushes


def parse_hashmark(script: bytes) -> Union[HashMarkRecord, str]:
    """Parse an output script.  Returns a HashMarkRecord, or a failure reason string.

    ``NOT_HASHMARK`` means the output never claimed to be one (another protocol, or a
    non-minimally-encoded magic push).  Every other reason means it claimed to be a HashMark and
    failed validation — those are real, and are not indexed.
    """
    if not script or script[0] != 0x6A:
        return 'NOT_HASHMARK'
    pushes = read_pushes(script, 1)
    if pushes is None or not pushes or pushes[0] != MAGIC:
        return 'NOT_HASHMARK'

    # From here the output claims to be a HashMark, so failures are real.
    if len(script) > MAX_SCRIPT_BYTES:
        return 'INVALID'
    # The header has to be read before the shape can be checked: the push count and the position
    # of the label both depend on the version.
    if len(pushes) < 3 or len(pushes[1]) != 2:
        return 'INVALID'

    version, algorithm_id = pushes[1][0], pushes[1][1]
    if version not in SUPPORTED_VERSIONS:
        # Do NOT index an unknown version under a known version's rules: a later version may
        # redefine every field after the header — v2 already moves the label from push 3 to push 5
        # — so guessing would produce confidently wrong answers.
        return 'UNKNOWN_VERSION'
    if algorithm_id not in ALGORITHMS:
        return 'UNKNOWN_ALGORITHM'

    name, digest_len = ALGORITHMS[algorithm_id]
    if len(pushes[2]) != digest_len:
        return 'INVALID'

    if version == V1:
        expected, label_at, max_label = (3, 4), 3, MAX_LABEL_BYTES_V1
    else:
        expected, label_at, max_label = (5, 6), 5, MAX_LABEL_BYTES_V2
    if len(pushes) not in expected:
        return 'INVALID'

    signer_hash160 = None
    if version == V2:
        if len(pushes[3]) != SIGNER_HASH_BYTES:
            return 'INVALID'
        if len(pushes[4]) != SIGNATURE_BYTES:
            return 'INVALID'
        signer_hash160 = pushes[3]

    label = None
    if len(pushes) > label_at:
        raw = pushes[label_at]
        # An empty label is unrepresentable: encoding one needs OP_0, which read_pushes rejects.
        if len(raw) > max_label:
            return 'INVALID'
        try:
            label = raw.decode('utf-8')             # strict: no replacement chars
        except UnicodeDecodeError:
            return 'INVALID'
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in label):
            return 'INVALID'                        # control characters

    return HashMarkRecord(version, algorithm_id, name, pushes[2], label, signer_hash160)


def _data_key(record: HashMarkRecord, height: int, tx_hash: bytes, vout: int) -> bytes:
    return (HashMarkDBKeys.DATA
            + bytes([record.algorithm_id])
            + record.digest
            + pack_be_uint32(height)
            + tx_hash
            + struct.pack('>H', min(vout, 0xFFFF)))


def _data_value(record: HashMarkRecord, block_hash: bytes) -> bytes:
    """version(1) || block_hash(32) || [signer(20) when version >= 2] || label

    The leading version byte is what makes this extensible in place: a row written before v2
    existed says 1, so it is read the old way, and no rescan is needed to keep serving it.
    """
    label = record.label.encode('utf-8') if record.label else b''
    signer = record.signer_hash160 if record.version >= V2 and record.signer_hash160 else b''
    return bytes([record.version]) + block_hash + signer + label


def _split_key(key: bytes, digest_len: int) -> Tuple[int, bytes, int]:
    """Decode (height, tx_hash, vout) from the tail of a data key."""
    pos = len(HashMarkDBKeys.DATA) + 1 + digest_len
    height = unpack_be_uint32(key[pos:pos + 4])[0]
    tx_hash = key[pos + 4:pos + 36]
    vout = struct.unpack('>H', key[pos + 36:pos + 38])[0]
    return height, tx_hash, vout


def _split_value(raw: bytes) -> Tuple[int, bytes, Optional[str]]:
    """Decode (version, block_hash, signer_hash160, label) from a stored value."""
    version = raw[0]
    block_hash = raw[1:33]
    if version >= V2:
        signer = raw[33:33 + SIGNER_HASH_BYTES]
        label_raw = raw[33 + SIGNER_HASH_BYTES:]
        if len(signer) != SIGNER_HASH_BYTES:
            signer = None
    else:
        signer = None
        label_raw = raw[33:]
    label = label_raw.decode('utf-8', errors='replace') if label_raw else None
    return version, block_hash, signer, label


def scan_tx(tx_hash: bytes, tx, height: int, block_hash: bytes,
            out: Dict[int, List[Tuple[bytes, bytes]]]) -> int:
    """Add every valid HashMark output of `tx` to `out` (height -> [(key, value)]).

    Shared by the live path and the backfill so both derive identical rows from identical scripts.
    Returns the number of outputs indexed.
    """
    indexed = 0
    for vout, txout in enumerate(tx.outputs):
        script = txout.pk_script
        if not script or not script.startswith(HASHMARK_PREFIX):
            continue
        record = parse_hashmark(script)
        if isinstance(record, str):
            continue        # claimed-but-invalid, or another protocol: not indexed
        out.setdefault(height, []).append(
            (_data_key(record, height, tx_hash, vout), _data_value(record, block_hash))
        )
        indexed += 1
    return indexed


class HashMarkIndex:
    """Digest -> transaction index over HashMark OP_RETURN outputs."""

    def __init__(self, db, env):
        self.logger = util.class_logger(__name__, self.__class__.__name__)
        self.db = db
        self.env = env
        self.enabled = getattr(env, 'hashmark_index', False)
        # height -> [(key, value)] accumulated between flushes
        self._pending: Dict[int, List[Tuple[bytes, bytes]]] = {}
        self._pending_rows = 0
        self._live_from: Optional[int] = None
        reorg_limit = getattr(env, 'reorg_limit', 0)
        cur = getattr(db, 'db_height', -1)
        self._last_undo_pruned = (max(0, cur - reorg_limit + 1) - 1) if reorg_limit else -1

    def set_logger(self, logger):
        if logger:
            self.logger = logger

    # ---- block processing ----
    def process_tx(self, tx_hash: bytes, tx, height: int, block_hash: bytes):
        """Record every valid HashMark output of a confirmed transaction.

        Mempool transactions are deliberately not indexed: an unconfirmed mark must never be
        mistaken for a confirmed one, and every row here carries a real height and block hash.
        """
        if not self.enabled:
            return
        self._pending_rows += scan_tx(tx_hash, tx, height, block_hash, self._pending)

    # ---- undo / flush / backup ----
    def _undo_key(self, height: int) -> bytes:
        return HashMarkDBKeys.UNDO + pack_be_uint32(height)

    def _prune_old_undo_keys(self, batch):
        reorg_limit = getattr(self.env, 'reorg_limit', 0)
        if not reorg_limit:
            return
        prune_to = max(0, self.db.db_height - reorg_limit + 1) - 1
        if prune_to <= self._last_undo_pruned:
            return
        for h in range(self._last_undo_pruned + 1, prune_to + 1):
            batch.delete(self._undo_key(h))
        self._last_undo_pruned = prune_to

    def _record_live_from(self, batch):
        """Persist, once, the first height this index covered live.

        Everything from that height up arrives through process_tx, so only what lies BELOW it needs
        a historic scan.  On a fresh sync db_height is -1 at the first flush, giving a watermark of
        0 and an empty backfill range — which is what stops a from-genesis resync (as the DB_VERSIONS
        bump in eec29a3 forces) from pointlessly re-reading the whole chain from the daemon.
        """
        if self._live_from is not None:
            return
        stored = self.db.utxo_db.get(HashMarkDBKeys.LIVE_FROM)
        if stored:
            self._live_from = unpack_be_uint32(stored)[0]
            return
        self._live_from = max(0, getattr(self.db, 'db_height', -1) + 1)
        batch.put(HashMarkDBKeys.LIVE_FROM, pack_be_uint32(self._live_from))

    def flush(self, batch):
        if not self.enabled:
            return
        self._record_live_from(batch)
        self._prune_old_undo_keys(batch)
        for height, rows in self._pending.items():
            try:
                for key, value in rows:
                    batch.put(key, value)
                # Every row is a fresh insert — the key embeds txid+vout, so a height's rows can
                # only ever be added, never overwritten.  Undo therefore just deletes them back out.
                batch.put(self._undo_key(height), encode_undo([(key, None) for key, _v in rows]))
            except Exception:
                # Never let this overlay abort the shared write batch — that would halt every other
                # indexer at this block.  Mirrors predict_index/swap_index flush discipline.
                self.logger.exception('hashmark_index: skipping un-writable rows at height %d',
                                      height)
                continue
        self._pending.clear()
        self._pending_rows = 0

    def backup(self, batch, height: int):
        """Unwind one disconnected block: the table is a pure projection of chain data, so the
        rows it wrote are simply deleted and re-added when the new chain is processed."""
        if not self.enabled:
            return
        raw = self.db.utxo_db.get(self._undo_key(height))
        if not raw:
            return
        for key, prev in decode_undo(raw):
            if prev is None:
                batch.delete(key)
            else:
                batch.put(key, prev)
        batch.delete(self._undo_key(height))

    def memory_estimate(self) -> int:
        if not self.enabled:
            return 0
        return self._pending_rows * 260

    # ---- backfill ----
    def _backfill_state(self) -> Tuple[bool, int, int]:
        """Return (done, next_height, target) from the on-disk checkpoint."""
        done = bool(self.db.utxo_db.get(HashMarkDBKeys.BACKFILL_DONE))
        cursor_raw = self.db.utxo_db.get(HashMarkDBKeys.BACKFILL_CURSOR)
        target_raw = self.db.utxo_db.get(HashMarkDBKeys.BACKFILL_TARGET)
        next_height = unpack_be_uint32(cursor_raw)[0] if cursor_raw else 0
        target = unpack_be_uint32(target_raw)[0] if target_raw else -1
        return done, next_height, target

    def _backfill_target(self, height: int) -> int:
        """Highest height the historic scan must cover: everything below where live indexing began.

        Falls back to the current tip when no watermark exists yet — that is a node enabling the
        index and reaching caught-up without having flushed a block, where nothing has been indexed
        live and the whole chain does need scanning.
        """
        stored = self.db.utxo_db.get(HashMarkDBKeys.LIVE_FROM)
        if stored:
            return unpack_be_uint32(stored)[0] - 1
        return height

    async def backfill(self, height: int, daemon, caught_up_event=None):
        """Scan historic blocks for HashMark outputs, resuming from the last checkpoint.

        Unlike analytics_index's backfill this cannot read what it needs from the DB — ElectrumX
        stores no raw scripts for unspendable outputs — so it re-reads blocks from the daemon.  It
        runs as a background task after the node is caught up and serving, yields to the event loop
        between chunks, and checkpoints after every chunk so an interrupted run resumes instead of
        restarting.  Exceptions are logged, never propagated: a backfill failure must not kill the
        block-processing task group.
        """
        if not self.enabled:
            return
        if caught_up_event is not None:
            await caught_up_event.wait()
        try:
            await self._backfill_impl(height, daemon)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception('HashMark backfill failed; will resume on next startup')

    async def _backfill_impl(self, height: int, daemon):
        done, next_height, target = self._backfill_state()
        if done:
            return
        if target < 0:
            # First run: the scan stops exactly where live indexing began, so the two paths meet
            # and leave neither gap nor overlap.  On a from-genesis sync that range is empty.
            target = self._backfill_target(height)
            if target < 0:
                with self.db.utxo_db.write_batch() as batch:
                    batch.put(HashMarkDBKeys.BACKFILL_DONE, b'1')
                self.logger.info('HashMark index covered the chain live; no backfill needed')
                return
            with self.db.utxo_db.write_batch() as batch:
                batch.put(HashMarkDBKeys.BACKFILL_TARGET, pack_be_uint32(target))
                batch.put(HashMarkDBKeys.BACKFILL_CURSOR, pack_be_uint32(0))
            next_height = 0
            self.logger.info('Starting HashMark backfill: heights 0..%d', target)
        else:
            self.logger.info('Resuming HashMark backfill at height %d (target %d)',
                             next_height, target)

        # Backfilled blocks this close to the tip are still reorgable, so they need undo records
        # exactly as live-indexed blocks do.
        reorg_limit = getattr(self.env, 'reorg_limit', 0)
        undo_from = max(0, target - reorg_limit + 1) if reorg_limit else target + 1

        total_rows = 0
        while next_height <= target:
            count = min(BACKFILL_CHUNK_BLOCKS, target - next_height + 1)
            hex_hashes = await daemon.block_hex_hashes(next_height, count)
            raw_blocks = await daemon.raw_blocks(hex_hashes)

            pending: Dict[int, List[Tuple[bytes, bytes]]] = {}
            for offset, raw_block in enumerate(raw_blocks):
                block_height = next_height + offset
                try:
                    block = self.env.coin.block(raw_block)
                    block_hash = self.env.coin.header_hash(block.header)
                except Exception:
                    self.logger.exception('hashmark backfill: undecodable block at height %d',
                                          block_height)
                    continue
                for tx, tx_hash in block.transactions:
                    total_rows += scan_tx(tx_hash, tx, block_height, block_hash, pending)

            with self.db.utxo_db.write_batch() as batch:
                for h, rows in pending.items():
                    for key, value in rows:
                        batch.put(key, value)
                    if h >= undo_from:
                        existing = self.db.utxo_db.get(self._undo_key(h))
                        merged = ((decode_undo(existing) if existing else [])
                                  + [(key, None) for key, _v in rows])
                        batch.put(self._undo_key(h), encode_undo(merged))
                next_height += count
                batch.put(HashMarkDBKeys.BACKFILL_CURSOR, pack_be_uint32(next_height))

            self.logger.debug('HashMark backfill checkpoint: height %d/%d, %d records indexed',
                              next_height - 1, target, total_rows)
            await asyncio.sleep(0)

        with self.db.utxo_db.write_batch() as batch:
            batch.put(HashMarkDBKeys.BACKFILL_DONE, b'1')
            batch.delete(HashMarkDBKeys.BACKFILL_CURSOR)
        self.logger.info('HashMark backfill complete: %d records indexed up to height %d',
                         total_rows, target)

    # ---- queries ----
    def lookup(self, digest: bytes, algorithm_id: int = 0x01,
               limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
        """Return the marks for one digest, oldest first.  An empty list means no match — a
        digest that was never marked is a normal answer, not an error."""
        if algorithm_id not in ALGORITHMS:
            return []
        name, digest_len = ALGORITHMS[algorithm_id]
        if len(digest) != digest_len:
            return []
        limit = max(1, min(int(limit), MAX_LIMIT))

        prefix = HashMarkDBKeys.DATA + bytes([algorithm_id]) + digest
        results: List[Dict[str, Any]] = []
        # Keys sort height-ascending within a (algorithm, digest) prefix, so forward iteration is
        # already oldest-first and stops as soon as the page is full.
        for key, raw in self.db.utxo_db.iterator(prefix=prefix):
            if len(raw) < 33:
                continue
            height, tx_hash, vout = _split_key(key, digest_len)
            version, block_hash, signer, label = _split_value(raw)
            entry = {
                'txid': hash_to_hex_str(tx_hash),
                'output_index': vout,
                'height': height,
                'block_hash': hash_to_hex_str(block_hash),
                'version': version,
                'algorithm': name,
                'digest': digest.hex(),
            }
            # v2 only, and still only a hint: the client recovers the key from the signature and
            # checks it against this commitment itself.
            if signer is not None:
                entry['signer_hash160'] = signer.hex()
            if label is not None:
                entry['label'] = label
            results.append(entry)
            if len(results) >= limit:
                break
        return results

    def stats(self) -> Dict[str, Any]:
        """Index status.  `backfill_complete` is what tells a client whether an empty lookup means
        'never marked' or 'not scanned yet'."""
        done, next_height, target = self._backfill_state()
        return {
            'enabled': self.enabled,
            'backfill_complete': done,
            'backfill_target_height': target if target >= 0 else None,
            'backfill_next_height': None if done else next_height,
            'pending_rows': self._pending_rows,
            'algorithms': {name: alg_id for alg_id, (name, _n) in ALGORITHMS.items()},
            # Kept for clients written against the v1-only index; `protocol_versions` is the
            # honest answer now that more than one is readable.
            'protocol_version': LATEST_VERSION,
            'protocol_versions': list(SUPPORTED_VERSIONS),
        }


def parse_digest_arg(digest_hex: Any, algorithm_id: int) -> Optional[bytes]:
    """Validate a client-supplied digest.

    Only an exact, lowercase, full-length hex digest is accepted.  Prefix or partial matching is
    deliberately not offered: it would let a caller enumerate the index.
    """
    if algorithm_id not in ALGORITHMS:
        return None
    _name, digest_len = ALGORITHMS[algorithm_id]
    if not isinstance(digest_hex, str) or len(digest_hex) != digest_len * 2:
        return None
    if not _HEX_RE.match(digest_hex):
        return None
    return bytes.fromhex(digest_hex)


def resolve_algorithm(algorithm: Any) -> Optional[int]:
    """Accept either an algorithm id (1) or its name ('sha256'); None if unknown."""
    if isinstance(algorithm, bool):
        return None
    if isinstance(algorithm, int):
        return algorithm if algorithm in ALGORITHMS else None
    if isinstance(algorithm, str):
        if algorithm in ALGORITHM_IDS_BY_NAME:
            return ALGORITHM_IDS_BY_NAME[algorithm]
        if algorithm.isdigit():
            alg_id = int(algorithm)
            return alg_id if alg_id in ALGORITHMS else None
    return None
