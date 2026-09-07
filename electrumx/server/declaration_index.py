"""
Canon declaration index for RXinDexer (`/declarations/*`).

A declaration is a signed JSON document a key publishes about refs it claims — "this ref is my
creator profile" — revealed on chain inside a transaction's **scriptSig**. That makes this the only
scanner in this tree that reads INPUTS rather than outputs, with two consequences worth stating up
front:

  * Nothing else in the DB can answer these queries. ElectrumX retains UTXOs, history and headers;
    input scripts survive only inside the reorg window (``write_raw_block`` deletes past it). So
    unlike the GCM/GMT/GA/GMH backfills, this one cannot be derived from stored CBOR — it reads
    blocks back from the daemon, the same shape as HashMarkIndex.backfill.

    That makes backfill cost per-BLOCK while declarations are per-DOCUMENT and rare, so two things
    bound it: ``DECLARATION_START_HEIGHT`` skips everything below the format's activation (a
    document cannot exist there), and ``scan_txid`` indexes one known transaction in two daemon
    calls. Scanning from height 0 is a fallback, not the expected path.
  * A declaration is discovered at spend time, so its height is the height of the revealing tx, not
    of whatever it talks about.

Document shape (``canon-declaration`` v1)::

    { "format": "canon-declaration", "version": 1, "network": "radiant-mainnet",
      "signer": "<base58 address>",
      "declares": [ { "kind": "creator", "ref": "<72 hex>", "action": "declare", "label": "..." } ],
      "issuedAt": "<ISO8601>", "expiresAt": "<ISO8601>",
      "signature": "<base64 compact recoverable sig over the canonical statement>" }

Detection: for each input, each scriptSig push of at least MIN_DOC_PUSH bytes is tested for the
``cnd1`` magic, then the remainder is parsed as JSON under a MAX_DOC_BYTES cap.

Trust model — the important part. Only SCHEMA-VALID documents are indexed. Signature validity is
computed when a native EC backend is present and stored as a HINT (``sig_valid``), never as a gate:

  * ``sig_valid`` may be ``None``, meaning "not checked here" (no ``coincurve``), which is NOT the
    same as invalid. Callers must treat None and False differently or they will silently drop
    valid declarations on a node without the optional dependency.
  * A row means "this document was revealed on chain at this height", never that its claim is
    true. Anyone can sign a document about a ref they do not own; the signature proves only that
    the named key authored the document. Canon re-verifies every hit and decides for itself — these
    endpoints return pointers and hints, never a verdict.
"""

import asyncio
import base64
import hashlib
import json
import os
import struct
from typing import Any, Dict, List, Optional, Tuple

from electrumx.lib import util
from electrumx.lib.hash import hash_to_hex_str, sha256, double_sha256
from electrumx.lib.util import pack_be_uint32, unpack_be_uint32, encode_undo, decode_undo

CANON_MAGIC = b'cnd1'
DOC_FORMAT = 'canon-declaration'
# Versions this index reads. Unlike the HashMark record — where v2 moved the label from push 3 to
# push 5, so a v1 parser would index a signature as a label — a declaration is JSON and keyed by
# name, so a version that only ADDS fields stays readable under these rules. Widen this set when a
# new version does that; add version-specific handling instead if one ever redefines an existing
# field's meaning, and keep the parsed `version` on the record so consumers can tell them apart.
SUPPORTED_VERSIONS = (1, 2)
DOC_VERSION = 1                 # the version a new document is expected to use
LATEST_VERSION = max(SUPPORTED_VERSIONS)

# A push must be at least this long to be worth testing: magic + the smallest plausible document.
MIN_DOC_PUSH = 40
# Cap the JSON body. Declarations are small; this bounds parse cost on a hostile scriptSig.
MAX_DOC_BYTES = 16 * 1024

VALID_ACTIONS = ('declare', 'revoke')
MAX_DECLARES = 256          # bound one document's entry count
MAX_LABEL_BYTES = 256
MAX_SIGNER_LEN = 90

# Bitcoin-compatible message signing, confirmed against Radiant-Core
# (src/validation.cpp: strMessageMagic).
MESSAGE_MAGIC = b'Bitcoin Signed Message:\n'

DEFAULT_LIMIT = 100
MAX_LIMIT = 500

BACKFILL_CHUNK_BLOCKS = int(os.getenv('DECLARATION_BACKFILL_CHUNK_BLOCKS', '200'))


class DeclarationDBKeys:
    """Key prefixes. All 3 bytes under a fresh 'DC' family, so none is a prefix of another."""
    DOC = b'DCd'            # DCd + doc_hash(32) -> json record (earliest reveal wins)
    BY_REF = b'DCr'         # DCr + ref(36) + be_u32(height) + be_u16(tx_index) + doc_hash(32) -> kind|action
    BY_SIGNER = b'DCs'      # DCs + signer_hash(16) + be_u32(height) + be_u16(tx_index) + doc_hash(32) -> b''
    UNDO = b'DCu'           # DCu + be_u32(height) -> encode_undo([(key, prev|None)])
    LIVE_FROM = b'DCl'      # -> be_u32(first height covered by live indexing)
    BACKFILL_CURSOR = b'DCc'
    BACKFILL_TARGET = b'DCt'
    BACKFILL_DONE = b'DCf'


def _signer_hash(signer: str) -> bytes:
    """Fixed-width key component for a variable-length address string."""
    return sha256(signer.encode('utf-8'))[:16]


def read_pushes(script: bytes) -> List[bytes]:
    """Collect data pushes from a script, skipping non-push opcodes.

    Deliberately lenient (unlike the HashMark reader, which enforces minimal encoding): a scriptSig
    is written by arbitrary spending code and the document is identified by its magic and validated
    by its schema, so there is nothing a non-minimal push could forge here.
    """
    pushes: List[bytes] = []
    i = 0
    n = len(script)
    while i < n:
        op = script[i]
        i += 1
        if 1 <= op <= 0x4b:
            length = op
        elif op == 0x4c:
            if i >= n:
                break
            length = script[i]
            i += 1
        elif op == 0x4d:
            if i + 2 > n:
                break
            length = struct.unpack('<H', script[i:i + 2])[0]
            i += 2
        elif op == 0x4e:
            if i + 4 > n:
                break
            length = struct.unpack('<I', script[i:i + 4])[0]
            i += 4
        else:
            continue
        if i + length > n:
            break
        pushes.append(script[i:i + length])
        i += length
    return pushes


def _iso_to_epoch(value: Any) -> int:
    """ISO8601 -> unix seconds, or 0 when absent/unparseable (stored as a hint only)."""
    if not isinstance(value, str) or not value:
        return 0
    try:
        from datetime import datetime
        text = value.replace('Z', '+00:00')
        return int(datetime.fromisoformat(text).timestamp())
    except Exception:
        return 0


def parse_declaration(push: bytes) -> Optional[Dict[str, Any]]:
    """Parse and schema-check one ``cnd1`` push. Returns a normalised dict, or None.

    None means "not a declaration, or not a valid one" — both are simply not indexed. The schema
    check is what gates indexing; the signature is only ever a hint (see module docstring).
    """
    if len(push) < MIN_DOC_PUSH or not push.startswith(CANON_MAGIC):
        return None
    body = push[len(CANON_MAGIC):]
    if len(body) > MAX_DOC_BYTES:
        return None
    try:
        doc = json.loads(body.decode('utf-8'))
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None

    if doc.get('format') != DOC_FORMAT:
        return None
    version = doc.get('version')
    # `is True` guards against JSON `true`, which equals 1 under Python's bool/int equivalence and
    # would otherwise sneak through as version 1.
    if version is True or version not in SUPPORTED_VERSIONS:
        return None
    signer = doc.get('signer')
    if not isinstance(signer, str) or not (1 <= len(signer) <= MAX_SIGNER_LEN):
        return None
    network = doc.get('network')
    if not isinstance(network, str) or not network:
        return None
    signature = doc.get('signature')
    if not isinstance(signature, str) or not signature:
        return None

    # `declares` may be empty when the document only revokes; the combined entry count is what
    # must be non-empty (checked after the revokes loop below).
    declares = doc.get('declares')
    if not isinstance(declares, list) or len(declares) > MAX_DECLARES:
        return None

    entries = []
    for raw in declares:
        if not isinstance(raw, dict):
            return None
        kind = raw.get('kind')
        ref_hex = raw.get('ref')
        if not isinstance(kind, str) or not kind or len(kind) > 32:
            return None
        if not isinstance(ref_hex, str) or len(ref_hex) != 72:
            return None
        try:
            ref = bytes.fromhex(ref_hex)
        except ValueError:
            return None
        if len(ref) != 36:
            return None
        action = raw.get('action', 'declare')
        if action not in VALID_ACTIONS:
            return None
        label = raw.get('label')
        if label is not None:
            if not isinstance(label, str) or len(label.encode('utf-8')) > MAX_LABEL_BYTES:
                return None
        entries.append({'ref': ref, 'kind': kind, 'action': action, 'label': label})

    # Top-level `revokes`: a bare array of refs, no per-entry object. A revoke carries no kind or
    # label — it withdraws whatever was declared, so there is nothing to restate.
    revokes = doc.get('revokes', [])
    if not isinstance(revokes, list) or len(revokes) > MAX_DECLARES:
        return None
    for ref_hex in revokes:
        if not isinstance(ref_hex, str) or len(ref_hex) != 72:
            return None
        try:
            ref = bytes.fromhex(ref_hex)
        except ValueError:
            return None
        if len(ref) != 36:
            return None
        entries.append({'ref': ref, 'kind': None, 'action': 'revoke', 'label': None})

    # A document that neither declares nor revokes anything says nothing worth indexing.
    if not entries:
        return None

    return {
        # The document's identity is the pushed bytes verbatim: unambiguous, and independent of any
        # canonical-JSON rules the writer may or may not follow.
        'doc_hash': sha256(body),
        'version': version,
        'signer': signer,
        'network': network,
        'signature': signature,
        'issued_at': _iso_to_epoch(doc.get('issuedAt')),
        'expires_at': _iso_to_epoch(doc.get('expiresAt')),
        'entries': entries,
        'raw': body,
    }


def _message_hash(message: bytes) -> bytes:
    """The double-SHA256 a Bitcoin-style signmessage signs, magic and lengths included."""
    def varint(n: int) -> bytes:
        if n < 0xfd:
            return bytes([n])
        if n <= 0xffff:
            return b'\xfd' + struct.pack('<H', n)
        return b'\xfe' + struct.pack('<I', n)
    preimage = (varint(len(MESSAGE_MAGIC)) + MESSAGE_MAGIC
                + varint(len(message)) + message)
    return double_sha256(preimage)


def signature_verification_enabled() -> bool:
    """Whether a native EC backend is present. When False, sig_valid is None, not False."""
    try:
        import coincurve  # noqa: F401
        return True
    except ImportError:
        return False


def verify_signmessage(signer_address: str, message: bytes, signature_b64: str,
                       p2pkh_verbyte: bytes) -> Optional[bool]:
    """Verify a Bitcoin-style signmessage signature. None = could not check.

    Recovers the pubkey from the 65-byte compact signature, derives its P2PKH address and compares
    to ``signer_address``. Returns None (not False) when ``coincurve`` is absent or the signature is
    structurally unreadable in a way that means "unknown" rather than "forged", so callers never
    mistake an unchecked signature for a bad one.
    """
    try:
        raw = base64.b64decode(signature_b64, validate=True)
    except Exception:
        return False
    if len(raw) != 65:
        return False

    try:
        from coincurve import PublicKey
    except ImportError:
        return None

    header = raw[0]
    if not 27 <= header <= 34:
        return False
    recid = (header - 27) & 3
    compressed = (header - 27) >= 4

    try:
        # coincurve wants r||s||recid and hashes the message itself unless given a digest.
        pub = PublicKey.from_signature_and_message(
            raw[1:] + bytes([recid]), _message_hash(message), hasher=None)
        encoded = pub.format(compressed=compressed)
    except Exception:
        return False

    from electrumx.lib.hash import ripemd160
    h160 = ripemd160(hashlib.sha256(encoded).digest())
    from electrumx.lib.hash import Base58
    try:
        derived = Base58.encode_check(p2pkh_verbyte + h160)
    except Exception:
        return None
    return derived == signer_address


class DeclarationIndex:
    """Index of Canon declarations revealed in transaction scriptSigs."""

    def __init__(self, db, env):
        self.logger = util.class_logger(__name__, self.__class__.__name__)
        self.db = db
        self.env = env
        self.enabled = getattr(env, 'declaration_index', False)
        # Protocol activation height. The backfill needs a daemon rescan (input scripts are not
        # retained), so scanning from 0 costs a full chain re-read to find documents that cannot
        # exist below the height the format was introduced. Idiomatic here — cf. the coin's
        # GENESIS_ACTIVATION and rest_api's _V2_ACTIVATION_HEIGHTS.
        self.start_height = max(0, int(getattr(env, 'declaration_start_height', 0) or 0))
        self.verify_signatures = (getattr(env, 'declaration_verify_signatures', True)
                                  and signature_verification_enabled())
        # height -> [(key, value)]
        self._pending: Dict[int, List[Tuple[bytes, bytes]]] = {}
        self._pending_rows = 0
        self._undo_pending: Dict[int, List] = {}
        self._live_from: Optional[int] = None
        reorg_limit = getattr(env, 'reorg_limit', 0)
        cur = getattr(db, 'db_height', -1)
        self._last_undo_pruned = (max(0, cur - reorg_limit + 1) - 1) if reorg_limit else -1
        if self.enabled and not self.verify_signatures:
            self.logger.warning(
                'Declaration indexing enabled without signature verification '
                '(coincurve missing or disabled): sig_valid will be reported as null, '
                'which means UNCHECKED, not invalid')

    def set_logger(self, logger):
        if logger:
            self.logger = logger

    # ---- scanning ----
    def _doc_record(self, doc, tx_hash, height, tx_index) -> Dict[str, Any]:
        sig_valid = None
        if self.verify_signatures:
            try:
                verbyte = getattr(self.env.coin, 'P2PKH_VERBYTE', b'\x00')
                sig_valid = verify_signmessage(doc['signer'], doc['raw'],
                                               doc['signature'], verbyte)
            except Exception:
                self.logger.exception('declaration signature check failed for %s',
                                      doc['doc_hash'].hex())
                sig_valid = None
        return {
            'reveal_txid': hash_to_hex_str(tx_hash),
            'version': doc['version'],
            'height': height,
            'tx_index': tx_index,
            'signer': doc['signer'],
            'network': doc['network'],
            'issued_at': doc['issued_at'],
            'expires_at': doc['expires_at'],
            'sig_valid': sig_valid,
            'entries': [
                {'ref': e['ref'].hex(), 'kind': e['kind'], 'action': e['action'],
                 'label': e['label']}
                for e in doc['entries']
            ],
        }

    def scan_tx(self, tx_hash: bytes, tx, height: int, tx_index: int,
                out: Dict[int, List[Tuple[bytes, bytes]]]) -> int:
        """Collect declaration rows from a tx's INPUT scripts. Returns documents indexed."""
        found = 0
        for txin in tx.inputs:
            if txin.is_generation():
                continue
            script = txin.script
            if not script or CANON_MAGIC not in script:
                continue        # cheap reject before any push parsing
            for push in read_pushes(script):
                doc = parse_declaration(push)
                if doc is None:
                    continue
                record = self._doc_record(doc, tx_hash, height, tx_index)
                doc_hash = doc['doc_hash']
                rows = out.setdefault(height, [])
                rows.append((DeclarationDBKeys.DOC + doc_hash,
                             json.dumps(record, separators=(',', ':')).encode('utf-8')))
                pos = pack_be_uint32(height) + struct.pack('>H', min(tx_index, 0xFFFF))
                for e in doc['entries']:
                    rows.append((
                        DeclarationDBKeys.BY_REF + e['ref'] + pos + doc_hash,
                        # A revoke has no kind, so encode it as empty rather than letting
                        # f-string interpolation store the literal text "None".
                        f"{e['kind'] or ''}|{e['action']}".encode('utf-8'),
                    ))
                rows.append((
                    DeclarationDBKeys.BY_SIGNER + _signer_hash(doc['signer']) + pos + doc_hash,
                    b'',
                ))
                found += 1
        return found

    def process_tx(self, tx_hash: bytes, tx, height: int, tx_index: int = 0):
        if not self.enabled:
            return
        self._pending_rows += self.scan_tx(tx_hash, tx, height, tx_index, self._pending)

    # ---- undo / flush / backup ----
    def _undo_key(self, height: int) -> bytes:
        return DeclarationDBKeys.UNDO + pack_be_uint32(height)

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
        if self._live_from is not None:
            return
        stored = self.db.utxo_db.get(DeclarationDBKeys.LIVE_FROM)
        if stored:
            self._live_from = unpack_be_uint32(stored)[0]
            return
        self._live_from = max(0, getattr(self.db, 'db_height', -1) + 1)
        batch.put(DeclarationDBKeys.LIVE_FROM, pack_be_uint32(self._live_from))

    def _write_rows(self, batch, height, rows, undo_sink=None):
        """Write one height's rows. DOC keys keep the EARLIEST reveal; index keys are inserts.

        The spec's "unique on doc_hash, keep the earliest (height, tx_index)" is enforced here: a
        re-reveal of the same document adds its BY_REF/BY_SIGNER rows (so it stays discoverable at
        that height) but never overwrites the original DOC record.
        """
        for key, value in rows:
            is_doc = key.startswith(DeclarationDBKeys.DOC)
            prev = self.db.utxo_db.get(key) if is_doc else None
            if is_doc and prev is not None:
                continue        # earliest reveal wins
            if undo_sink is None:
                self._record_undo_entry(height, key, prev)
            else:
                undo_sink.setdefault(height, []).append((key, prev))
            batch.put(key, value)

    def _record_undo_entry(self, height, key, prev):
        self._undo_pending.setdefault(height, []).append((key, prev))

    def flush(self, batch):
        if not self.enabled:
            return
        self._undo_pending: Dict[int, List] = {}
        self._record_live_from(batch)
        self._prune_old_undo_keys(batch)
        for height, rows in self._pending.items():
            try:
                self._write_rows(batch, height, rows)
            except Exception:
                self.logger.exception(
                    'declaration_index: skipping un-writable rows at height %d', height)
                continue
        for height, entries in self._undo_pending.items():
            if entries:
                batch.put(self._undo_key(height), encode_undo(entries))
        self._pending.clear()
        self._pending_rows = 0
        self._undo_pending = {}

    def backup(self, batch, height: int):
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
        return self._pending_rows * 1024

    # ---- backfill (daemon rescan; input scripts are not retained in the DB) ----
    def _backfill_state(self) -> Tuple[bool, int, int]:
        done = bool(self.db.utxo_db.get(DeclarationDBKeys.BACKFILL_DONE))
        cursor_raw = self.db.utxo_db.get(DeclarationDBKeys.BACKFILL_CURSOR)
        target_raw = self.db.utxo_db.get(DeclarationDBKeys.BACKFILL_TARGET)
        return (done,
                unpack_be_uint32(cursor_raw)[0] if cursor_raw else 0,
                unpack_be_uint32(target_raw)[0] if target_raw else -1)

    def _backfill_target(self, height: int) -> int:
        stored = self.db.utxo_db.get(DeclarationDBKeys.LIVE_FROM)
        if stored:
            return unpack_be_uint32(stored)[0] - 1
        return height

    async def backfill(self, height: int, daemon, caught_up_event=None):
        """Rescan historic blocks from the daemon for declarations.

        This cannot reuse the in-place GM-metadata migrations the other indexes use: declarations
        live in input scripts, which the DB does not retain outside the reorg window. Structure
        mirrors HashMarkIndex.backfill — checkpointed, yields between chunks, never propagates.
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
            self.logger.exception('Declaration backfill failed; will resume on next startup')

    async def _backfill_impl(self, height: int, daemon):
        done, next_height, target = self._backfill_state()
        if done:
            return
        if target < 0:
            target = self._backfill_target(height)
            if target < 0:
                with self.db.utxo_db.write_batch() as batch:
                    batch.put(DeclarationDBKeys.BACKFILL_DONE, b'1')
                self.logger.info('Declarations covered the chain live; no backfill needed')
                return
            next_height = self.start_height
            if next_height > target:
                with self.db.utxo_db.write_batch() as batch:
                    batch.put(DeclarationDBKeys.BACKFILL_DONE, b'1')
                self.logger.info(
                    'Declaration start height %d is above the backfill target %d; nothing to scan',
                    next_height, target)
                return
            with self.db.utxo_db.write_batch() as batch:
                batch.put(DeclarationDBKeys.BACKFILL_TARGET, pack_be_uint32(target))
                batch.put(DeclarationDBKeys.BACKFILL_CURSOR, pack_be_uint32(next_height))
            self.logger.info('Starting declaration backfill: heights %d..%d (%d blocks)',
                             next_height, target, target - next_height + 1)
        else:
            self.logger.info('Resuming declaration backfill at height %d (target %d)',
                             next_height, target)

        reorg_limit = getattr(self.env, 'reorg_limit', 0)
        undo_from = max(0, target - reorg_limit + 1) if reorg_limit else target + 1

        total = 0
        while next_height <= target:
            count = min(BACKFILL_CHUNK_BLOCKS, target - next_height + 1)
            hex_hashes = await daemon.block_hex_hashes(next_height, count)
            raw_blocks = await daemon.raw_blocks(hex_hashes)

            pending: Dict[int, List[Tuple[bytes, bytes]]] = {}
            for offset, raw_block in enumerate(raw_blocks):
                block_height = next_height + offset
                try:
                    block = self.env.coin.block(raw_block)
                except Exception:
                    self.logger.exception('declaration backfill: undecodable block at %d',
                                          block_height)
                    continue
                for tx_index, (tx, tx_hash) in enumerate(block.transactions):
                    total += self.scan_tx(tx_hash, tx, block_height, tx_index, pending)

            undo: Dict[int, List] = {}
            with self.db.utxo_db.write_batch() as batch:
                for h, rows in pending.items():
                    self._write_rows(batch, h, rows,
                                     undo_sink=undo if h >= undo_from else {})
                for h, entries in undo.items():
                    existing = self.db.utxo_db.get(self._undo_key(h))
                    merged = (decode_undo(existing) if existing else []) + entries
                    batch.put(self._undo_key(h), encode_undo(merged))
                next_height += count
                batch.put(DeclarationDBKeys.BACKFILL_CURSOR, pack_be_uint32(next_height))

            self.logger.debug('Declaration backfill: height %d/%d, %d documents',
                              next_height - 1, target, total)
            await asyncio.sleep(0)

        with self.db.utxo_db.write_batch() as batch:
            batch.put(DeclarationDBKeys.BACKFILL_DONE, b'1')
            batch.delete(DeclarationDBKeys.BACKFILL_CURSOR)
        self.logger.info('Declaration backfill complete: %d documents up to height %d',
                         total, target)

    async def scan_txid(self, txid_hex: str, daemon) -> Dict[str, Any]:
        """Index the declarations in one known transaction, immediately.

        Two daemon calls (the tx, then its block for the height and position) instead of a chain
        rescan. This exists because the backfill's cost is per-BLOCK while declarations are
        per-DOCUMENT and vanishingly rare: when you already know the txid, scanning 459,000 blocks
        to find it is the wrong shape of work.

        Confirmed transactions only — an unconfirmed one has no height, and this index deliberately
        stores nothing that could be mistaken for confirmed.
        """
        if not self.enabled:
            return {'error': 'Declaration indexing not enabled'}

        info = await daemon.getrawtransaction(txid_hex, True)
        if not isinstance(info, dict):
            return {'error': 'transaction not found'}
        block_hash = info.get('blockhash')
        if not block_hash:
            return {'error': 'transaction is unconfirmed; only confirmed reveals are indexed'}
        raw_hex = info.get('hex')
        if not raw_hex:
            return {'error': 'daemon returned no raw transaction'}

        block = await daemon.deserialised_block(block_hash)
        height = block.get('height')
        if height is None:
            return {'error': 'could not resolve block height'}
        try:
            tx_index = list(block.get('tx') or []).index(txid_hex)
        except ValueError:
            tx_index = 0

        tx = self.env.coin.DESERIALIZER(bytes.fromhex(raw_hex)).read_tx()
        tx_hash = bytes.fromhex(txid_hex)[::-1]     # display order -> internal

        pending: Dict[int, List[Tuple[bytes, bytes]]] = {}
        found = self.scan_tx(tx_hash, tx, height, tx_index, pending)
        if found:
            undo: Dict[int, List] = {}
            with self.db.utxo_db.write_batch() as batch:
                for h, rows in pending.items():
                    self._write_rows(batch, h, rows, undo_sink=undo)
                for h, entries in undo.items():
                    existing = self.db.utxo_db.get(self._undo_key(h))
                    merged = (decode_undo(existing) if existing else []) + entries
                    batch.put(self._undo_key(h), encode_undo(merged))
        self.logger.info('Scanned %s at height %d: %d declaration(s)', txid_hex, height, found)
        return {'txid': txid_hex, 'height': height, 'tx_index': tx_index,
                'declarations_indexed': found}

    # ---- queries ----
    def _rows_for_prefix(self, prefix: bytes, key_tail: int, limit: int,
                         cursor: Optional[bytes]) -> Tuple[List[Dict[str, Any]], Optional[bytes]]:
        """Walk a BY_REF/BY_SIGNER prefix in height-ascending order (height leads the key tail)."""
        rows = []
        next_cursor = None
        seek = cursor or prefix
        for key, value in self.db.utxo_db.iterator(prefix=prefix, seek=seek):
            if len(rows) >= limit:
                next_cursor = key
                break
            tail = key[len(prefix):]
            if len(tail) != key_tail:
                continue
            height = unpack_be_uint32(tail[:4])[0]
            tx_index = struct.unpack('>H', tail[4:6])[0]
            doc_hash = tail[6:38]
            doc = self.get_document(doc_hash)
            if not doc:
                continue
            kind = action = None
            if value:
                try:
                    kind, action = value.decode('utf-8').split('|', 1)
                    # Empty kind means a revoke, which never had one — report null, not ''.
                    kind = kind or None
                except Exception:
                    pass
            rows.append({
                'reveal_txid': doc['reveal_txid'],
                'height': height,
                'tx_index': tx_index,
                'signer': doc['signer'],
                'kind': kind,
                'action': action,
                'version': doc.get('version'),
                'doc_hash': doc_hash.hex(),
                # A HINT: null means unchecked (no EC backend), not invalid. Canon re-verifies.
                'sig_valid': doc.get('sig_valid'),
            })
        return rows, next_cursor

    def get_document(self, doc_hash: bytes) -> Optional[Dict[str, Any]]:
        raw = self.db.utxo_db.get(DeclarationDBKeys.DOC + doc_hash)
        if not raw:
            return None
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception:
            return None

    def get_by_ref(self, ref: bytes, limit: int = DEFAULT_LIMIT,
                   cursor: Optional[bytes] = None) -> Dict[str, Any]:
        """Declarations naming a ref, height-ascending."""
        limit = max(1, min(int(limit), MAX_LIMIT))
        prefix = DeclarationDBKeys.BY_REF + ref
        rows, nxt = self._rows_for_prefix(prefix, 4 + 2 + 32, limit, cursor)
        return {'ref': ref.hex(), 'rows': rows,
                'next_cursor': nxt.hex() if nxt else None}

    def get_by_signer(self, signer: str, limit: int = DEFAULT_LIMIT,
                      cursor: Optional[bytes] = None) -> Dict[str, Any]:
        """Declarations by a signing address, height-ascending."""
        limit = max(1, min(int(limit), MAX_LIMIT))
        prefix = DeclarationDBKeys.BY_SIGNER + _signer_hash(signer)
        rows, nxt = self._rows_for_prefix(prefix, 4 + 2 + 32, limit, cursor)
        return {'signer': signer, 'rows': rows,
                'next_cursor': nxt.hex() if nxt else None}

    def stats(self) -> Dict[str, Any]:
        done, next_height, target = self._backfill_state()
        return {
            'enabled': self.enabled,
            'signature_verification': self.verify_signatures,
            'sig_valid_semantics': 'null means UNCHECKED, not invalid',
            'backfill_complete': done,
            'backfill_target_height': target if target >= 0 else None,
            'backfill_next_height': None if done else next_height,
            'pending_rows': self._pending_rows,
            'start_height': self.start_height,
            'doc_format': DOC_FORMAT,
            'supported_versions': list(SUPPORTED_VERSIONS),
        }
