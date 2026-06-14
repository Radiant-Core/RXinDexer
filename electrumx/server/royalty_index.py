"""
Royalty Listing Index for RXinDexer.

Discovers on-chain royalty-covenant sale listings (Photonic `royaltySaleScript`)
so a wallet can browse every NFT listed for sale across all sellers — the cross-
seller discovery the per-listing scripthash can't provide (each listing bakes its
terms in as literals, yielding a unique scripthash, so listings can't be
enumerated by owner the way soulbound/authority covenants can).

Detection is beacon-gated: the listing transaction emits a value-0 OP_RETURN
`RRYL` beacon (magic + version + ref) alongside the covenant output. The beacon
makes detection cheap and version-proofs the parser against future covenant-script
changes. Given a beacon, we locate the covenant output carrying the same ref and
parse its terms (price, seller payout, royalty recipients) directly from the
scriptPubKey with a strict opcode walker — the terms are on-chain literals, no
off-chain descriptor needed.

Lifecycle mirrors swap_index's close-on-spend: a listing's id IS the covenant
outpoint (`tx_hash + LE(vout)`); when that outpoint is spent (bought or
cancelled) the listing is removed from the ACTIVE_* indexes. Reorgs are unwound
via the undo machinery, mirroring predict_index / swap_index exactly.

CRITICAL sync invariant: the opcode layout parsed here MUST stay byte-for-byte in
lockstep with Photonic `royaltySaleScript()` (packages/lib/src/royaltyCovenant.ts).
There is no on-chain length marker, so a silent builder change would yield
silently-unparsed listings. A shared test-vectors fixture asserts parity on both
sides (see tests/royalty_vectors + the royaltyCovenant test).
"""

import struct
import time
from typing import Optional, Dict, Any, List, Tuple
from collections import defaultdict

from electrumx.lib import util
from electrumx.lib.hash import hash_to_hex_str, sha256, Base58
from electrumx.lib.util import pack_be_uint32, encode_undo, decode_undo
from electrumx.lib.script import OpCodes

try:
    import cbor2
    HAS_CBOR = True
except ImportError:
    HAS_CBOR = False


# OP_RETURN beacon emitted by buildRoyaltyListingTx (royaltyCovenant.ts).
RRYL_MAGIC = b'RRYL'
RRYL_VERSION = 0x01

MAX_UINT64 = 0xFFFFFFFFFFFFFFFF
MAX_ROYALTIES = 32          # bound recipient count (anti chain-halt / DoS)
MAX_SCRIPT_PUSH = 10000     # bound any single script/data push we accept
REF_LEN = 36


class RoyaltyStatus:
    ACTIVE = 0
    RESOLVED = 1            # covenant UTXO spent (bought or cancelled)


class RoyaltyDBKeys:
    """Database key prefixes for the royalty-listing index (distinct from
    SwapDBKeys SO/SP/SM/SH/SS/SF/SWU and PredictDBKeys PMm/PMh/PMu)."""
    LISTING = b'RLm'           # RLm + listing_id(36)            -> CBOR record
    ACTIVE_BY_REF = b'RLr'     # RLr + ref(36) + listing_id(36)  -> b''
    ACTIVE_BY_SELLER = b'RLs'  # RLs + seller_sh(32) + lid(36)   -> b''
    ACTIVE_ALL = b'RLa'        # RLa + be_u32(height) + lid(36)  -> b''  (global, newest-first)
    UNDO = b'RLu'              # RLu + be_u32(height)


# ───────────────────────────── script parsing ──────────────────────────────
def _parse_script_chunks(script: bytes) -> List[bytes]:
    """Split a script into push chunks (bare opcodes become 1-byte chunks).
    Mirrors predict_index/swap_index — used only for the OP_RETURN beacon."""
    chunks: List[bytes] = []
    pos = 0
    n = len(script)
    while pos < n:
        op = script[pos]
        pos += 1
        if op == OpCodes.OP_RETURN or op == 0x00:
            chunks.append(bytes([op]))
            continue
        if 1 <= op <= 75:
            length = op
        elif op == 0x4c:
            if pos >= n:
                break
            length = script[pos]
            pos += 1
        elif op == 0x4d:
            if pos + 2 > n:
                break
            length = struct.unpack('<H', script[pos:pos + 2])[0]
            pos += 2
        elif op == 0x4e:
            if pos + 4 > n:
                break
            length = struct.unpack('<I', script[pos:pos + 4])[0]
            pos += 4
        else:
            chunks.append(bytes([op]))
            continue
        if pos + length > n:
            break
        chunks.append(script[pos:pos + length])
        pos += length
    return chunks


def parse_royalty_beacon(script: bytes) -> Optional[Dict[str, Any]]:
    """Parse an RRYL OP_RETURN beacon. Returns {version, ref(36)} or None.
    UNTRUSTED — the matching covenant output is what actually carries the terms."""
    try:
        if not script or script[0] != OpCodes.OP_RETURN:
            return None
        c = _parse_script_chunks(script)
        if len(c) < 4 or c[1] != RRYL_MAGIC:
            return None
        # The version is a tiny int; libauth/consensus minimal-push encodes
        # 1..16 as OP_1..OP_16 (0x51..0x60), so _parse_script_chunks yields the
        # bare opcode byte. Accept both that and a literal 1-byte data push.
        if len(c[2]) != 1:
            return None
        vb = c[2][0]
        version = vb - 0x50 if 0x51 <= vb <= 0x60 else vb
        ref = c[3]
        if len(ref) != REF_LEN:
            return None
        return {'version': version, 'ref': ref}
    except Exception:
        return None


def _read_data(s: bytes, pos: int, n: int) -> Tuple[bytes, int]:
    """Read a length-prefixed data push at pos. Returns (data, new_pos)."""
    if pos >= n:
        raise ValueError('eof')
    op = s[pos]
    if 0x01 <= op <= 0x4b:
        length = op
        start = pos + 1
    elif op == 0x4c:
        if pos + 1 >= n:
            raise ValueError('truncated pushdata1')
        length = s[pos + 1]
        start = pos + 2
    elif op == 0x4d:
        if pos + 3 > n:
            raise ValueError('truncated pushdata2')
        length = struct.unpack_from('<H', s, pos + 1)[0]
        start = pos + 3
    elif op == 0x4e:
        if pos + 5 > n:
            raise ValueError('truncated pushdata4')
        length = struct.unpack_from('<I', s, pos + 1)[0]
        start = pos + 5
    else:
        raise ValueError('expected data push')
    if length > MAX_SCRIPT_PUSH:
        raise ValueError('push too large')
    end = start + length
    if end > n:
        raise ValueError('truncated push body')
    return s[start:end], end


def _scriptnum(data: bytes) -> int:
    """Decode a minimal little-endian CScriptNum (the form pushMinimal emits)."""
    if not data:
        return 0
    result = 0
    for i, b in enumerate(data):
        result |= b << (8 * i)
    if data[-1] & 0x80:
        result &= ~(0x80 << (8 * (len(data) - 1)))
        return -result
    return result


def _read_num(s: bytes, pos: int, n: int) -> Tuple[int, int]:
    """Read a number push: OP_0, OP_1..OP_16, OP_1NEGATE, or a CScriptNum push."""
    if pos >= n:
        raise ValueError('eof')
    op = s[pos]
    if op == 0x00:                  # OP_0 / OP_FALSE
        return 0, pos + 1
    if 0x51 <= op <= 0x60:          # OP_1 .. OP_16
        return op - 0x50, pos + 1
    if op == 0x4f:                  # OP_1NEGATE
        return -1, pos + 1
    data, npos = _read_data(s, pos, n)
    return _scriptnum(data), npos


def parse_royalty_sale_script(s: bytes) -> Optional[Dict[str, Any]]:
    """Strict opcode walker for `royaltySaleScript` (royaltyCovenant.ts).

    Layout:
        d8 <ref:36> 75 63                              ; SINGLETON ref, DROP, IF
          76 a9 <push pkh:20> 88 ac                    ;   cancel: P2PKH(seller)
        67                                             ; ELSE
          00 cd <push sellerScript> 88                 ;   out[0].script == sellerScript
          00 cc <num price> a2 69                      ;   out[0].value >= price
          { idx(2+i) cd <push rScript> 88
            idx(2+i) cc <num rValue> a2 69 }*          ;   royalties at out[2..]
          51                                           ;   OP_1 (true)
        68                                             ; ENDIF

    Returns {ref, pkh, seller_script, price, royalties:[(script,value)]} or None.
    Defensive: any structural mismatch / bound violation returns None (never raise).
    """
    try:
        n = len(s)
        if n < 6 or s[0] != 0xd8:                       # OP_PUSHINPUTREFSINGLETON
            return None
        pos = 1
        if pos + REF_LEN > n:
            return None
        ref = s[pos:pos + REF_LEN]
        pos += REF_LEN
        if pos + 2 > n or s[pos] != 0x75 or s[pos + 1] != 0x63:  # OP_DROP OP_IF
            return None
        pos += 2

        # cancel branch: OP_DUP OP_HASH160 <pkh20> OP_EQUALVERIFY OP_CHECKSIG
        if pos + 2 > n or s[pos] != 0x76 or s[pos + 1] != 0xa9:
            return None
        pos += 2
        pkh, pos = _read_data(s, pos, n)
        if len(pkh) != 20:
            return None
        if pos + 3 > n or s[pos] != 0x88 or s[pos + 1] != 0xac or s[pos + 2] != 0x67:
            return None  # OP_EQUALVERIFY OP_CHECKSIG OP_ELSE
        pos += 3

        # buy branch — seller payout enforcement
        v, pos = _read_num(s, pos, n)
        if v != 0:                                      # idx(0)
            return None
        if pos >= n or s[pos] != 0xcd:                  # OP_OUTPUTBYTECODE
            return None
        pos += 1
        seller_script, pos = _read_data(s, pos, n)
        if pos >= n or s[pos] != 0x88:                  # OP_EQUALVERIFY
            return None
        pos += 1
        v, pos = _read_num(s, pos, n)
        if v != 0:                                      # idx(0)
            return None
        if pos >= n or s[pos] != 0xcc:                  # OP_OUTPUTVALUE
            return None
        pos += 1
        price, pos = _read_num(s, pos, n)
        if pos + 2 > n or s[pos] != 0xa2 or s[pos + 1] != 0x69:  # OP_GREATERTHANOREQUAL OP_VERIFY
            return None
        pos += 2

        royalties: List[Tuple[bytes, int]] = []
        while pos < n and s[pos] != 0x51:               # until OP_1 terminator
            if len(royalties) >= MAX_ROYALTIES:
                return None
            out_idx, pos = _read_num(s, pos, n)
            if pos >= n or s[pos] != 0xcd:
                return None
            pos += 1
            r_script, pos = _read_data(s, pos, n)
            if pos >= n or s[pos] != 0x88:
                return None
            pos += 1
            out_idx2, pos = _read_num(s, pos, n)
            if out_idx2 != out_idx:
                return None
            if pos >= n or s[pos] != 0xcc:
                return None
            pos += 1
            r_value, pos = _read_num(s, pos, n)
            if pos + 2 > n or s[pos] != 0xa2 or s[pos + 1] != 0x69:
                return None
            pos += 2
            if not (1 <= r_value <= MAX_UINT64):
                return None
            royalties.append((r_script, r_value))

        if pos + 2 > n or s[pos] != 0x51 or s[pos + 1] != 0x68:  # OP_1 OP_ENDIF
            return None
        pos += 2
        if pos != n:
            return None
        if not royalties:
            return None
        if not (1 <= price <= MAX_UINT64):
            return None
        return {
            'ref': ref,
            'pkh': pkh,
            'seller_script': seller_script,
            'price': price,
            'royalties': royalties,
        }
    except Exception:
        return None


# ───────────────────────────── record ──────────────────────────────────────
class RoyaltyListingInfo:
    __slots__ = ('listing_id', 'tx_hash', 'vout', 'height', 'timestamp', 'ref',
                 'seller_scripthash', 'seller_address', 'seller_script',
                 'cov_script', 'price', 'royalties', 'value', 'status',
                 'cancel_height', 'cancel_txid')

    def __init__(self):
        self.listing_id = b''
        self.tx_hash = b''
        self.vout = 0
        self.height = 0
        self.timestamp = 0
        self.ref = b''
        self.seller_scripthash = b''
        self.seller_address = None
        self.seller_script = b''
        self.cov_script = b''
        self.price = 0
        self.royalties: List[Tuple[bytes, int]] = []
        self.value = 0
        self.status = RoyaltyStatus.ACTIVE
        self.cancel_height = 0
        self.cancel_txid = None

    def to_bytes(self) -> bytes:
        if not HAS_CBOR:
            raise RuntimeError('cbor2 required for royalty indexing')
        data = {
            'lid': self.listing_id,
            'txh': self.tx_hash,
            'v': self.vout,
            'h': self.height,
            'ts': self.timestamp,
            'rf': self.ref,
            'ssh': self.seller_scripthash,
            'sa': self.seller_address,
            'ss': self.seller_script,
            'cs': self.cov_script,
            'pr': self.price,
            'ry': [[s, v] for s, v in self.royalties],
            'val': self.value,
            'st': self.status,
            'ch': self.cancel_height,
            'ct': self.cancel_txid,
        }
        return cbor2.dumps(data)

    @classmethod
    def from_bytes(cls, data: bytes) -> 'RoyaltyListingInfo':
        if not HAS_CBOR:
            raise RuntimeError('cbor2 required for royalty indexing')
        rec = cls()
        d = cbor2.loads(data)
        rec.listing_id = d.get('lid', b'')
        rec.tx_hash = d.get('txh', b'')
        rec.vout = d.get('v', 0)
        rec.height = d.get('h', 0)
        rec.timestamp = d.get('ts', 0)
        rec.ref = d.get('rf', b'')
        rec.seller_scripthash = d.get('ssh', b'')
        rec.seller_address = d.get('sa')
        rec.seller_script = d.get('ss', b'')
        rec.cov_script = d.get('cs', b'')
        rec.price = d.get('pr', 0)
        rec.royalties = [(s, v) for s, v in d.get('ry', [])]
        rec.value = d.get('val', 0)
        rec.status = d.get('st', RoyaltyStatus.ACTIVE)
        rec.cancel_height = d.get('ch', 0)
        rec.cancel_txid = d.get('ct')
        return rec

    def to_dict(self) -> Dict[str, Any]:
        return {
            'listing_id': self.listing_id.hex() if self.listing_id else None,
            'txid': hash_to_hex_str(self.tx_hash) if self.tx_hash else None,
            'vout': self.vout,
            'height': self.height,
            'timestamp': self.timestamp,
            'ref': _format_ref(self.ref),
            # Raw 36-byte LE ref hex (the on-chain singleton operand) so a buyer
            # can rebuild the purchase terms without re-deriving byte order.
            'ref_le': self.ref.hex() if self.ref else None,
            'seller_address': self.seller_address,
            'seller_script': self.seller_script.hex() if self.seller_script else None,
            'price': self.price,
            'royalties': [{'script': s.hex(), 'value': v} for s, v in self.royalties],
            'royalty_total': sum(v for _, v in self.royalties),
            'value': self.value,
            'covenant_script': self.cov_script.hex() if self.cov_script else None,
            'status': 'active' if self.status == RoyaltyStatus.ACTIVE else 'resolved',
        }


def _format_ref(ref: bytes) -> Optional[str]:
    """Format a 36-byte LE ref to display form `<txid_be>_<vout>` (matches swap)."""
    if not ref or len(ref) < REF_LEN:
        return None
    return hash_to_hex_str(ref[:32]) + '_' + str(struct.unpack('<I', ref[32:36])[0])


# ───────────────────────────── index ───────────────────────────────────────
class RoyaltyIndex:
    """Royalty-listing discovery index. Mirrors predict_index (beacon discovery)
    + swap_index (close-on-spend, undo) lifecycle."""

    def __init__(self, db, env):
        self.logger = util.class_logger(__name__, self.__class__.__name__)
        self.db = db
        self.env = env
        self.enabled = getattr(env, 'royalty_index', False)
        self.coin = getattr(env, 'coin', None)

        self.listing_cache: Dict[bytes, RoyaltyListingInfo] = {}
        self.listing_height: Dict[bytes, int] = {}
        self._undo_cache: Dict[int, List[Tuple[bytes, Optional[bytes]]]] = defaultdict(list)
        self._undo_seen: Dict[int, set] = defaultdict(set)

        current_height = getattr(db, 'db_height', -1)
        reorg_limit = getattr(env, 'reorg_limit', 0)
        min_keep = max(0, current_height - reorg_limit + 1) if reorg_limit else 0
        self._last_undo_pruned = min_keep - 1

        if self.enabled:
            self.logger.info('Royalty-listing indexing enabled')

    # ---- ingest ----
    def process_tx(self, tx_hash: bytes, tx, height: int, tx_idx: int,
                   glyph_envelope: Dict[str, Any] = None,
                   spent_outpoints: set = None):
        if not self.enabled:
            return
        ts = int(time.time())

        # Close-on-spend FIRST (mirrors swap_index): any spent outpoint that is a
        # known listing's covenant UTXO closes that listing (bought or cancelled).
        if spent_outpoints:
            for outpoint in spent_outpoints:
                self._close_listing_if_open(outpoint, tx_hash, height, ts)

        beacon = None
        for txout in tx.outputs:
            b = parse_royalty_beacon(txout.pk_script)
            if b is not None:
                beacon = b
                break
        if beacon is None:
            return
        if beacon['version'] != RRYL_VERSION:
            self.logger.debug('royalty beacon in %s: unsupported version %d',
                              hash_to_hex_str(tx_hash), beacon['version'])
            return

        ref = beacon['ref']
        # Locate the covenant output carrying the beacon's ref in this same tx.
        for vout_idx, txout in enumerate(tx.outputs):
            parsed = parse_royalty_sale_script(txout.pk_script)
            if not parsed or parsed['ref'] != ref:
                continue
            rec = self._build_record(tx_hash, vout_idx, txout, parsed, height, ts)
            if rec is None:
                continue
            self.listing_cache[rec.listing_id] = rec
            self.listing_height[rec.listing_id] = height
            self.logger.debug('Indexed royalty listing %s', _format_ref(ref))
            return  # one listing per beacon

        self.logger.debug('royalty beacon in %s has no matching covenant output',
                          hash_to_hex_str(tx_hash))

    def _build_record(self, tx_hash, vout_idx, txout, parsed, height, ts):
        # Bound numeric fields before they can reach struct.pack at flush time.
        if not (1 <= parsed['price'] <= MAX_UINT64):
            return None
        value = getattr(txout, 'value', 0)
        if not isinstance(value, int) or value < 0 or value > MAX_UINT64:
            return None
        rec = RoyaltyListingInfo()
        rec.listing_id = tx_hash + struct.pack('<I', vout_idx)
        rec.tx_hash = tx_hash
        rec.vout = vout_idx
        rec.height = height
        rec.timestamp = ts
        rec.ref = parsed['ref']
        rec.cov_script = bytes(txout.pk_script)
        rec.seller_script = parsed['seller_script']
        # Reconstruct the canonical P2PKH cancel script and key the by-seller index
        # on its scripthash (so a wallet can find its own listings by address).
        cancel_p2pkh = bytes([0x76, 0xa9, 0x14]) + parsed['pkh'] + bytes([0x88, 0xac])
        rec.seller_scripthash = sha256(cancel_p2pkh)
        rec.seller_address = self._address_from_pkh(parsed['pkh'])
        rec.price = parsed['price']
        rec.royalties = parsed['royalties']
        rec.value = value
        rec.status = RoyaltyStatus.ACTIVE
        return rec

    def _address_from_pkh(self, pkh: bytes) -> Optional[str]:
        if self.coin is None or len(pkh) != 20:
            return None
        try:
            return Base58.encode_check(self.coin.P2PKH_VERBYTE + pkh)
        except Exception:
            return None

    def _close_listing_if_open(self, outpoint: bytes, tx_hash: bytes,
                               height: int, ts: int):
        rec = self.listing_cache.get(outpoint)
        if rec is None:
            rec = self._get_listing(outpoint)
        if rec is None or rec.status != RoyaltyStatus.ACTIVE:
            return
        rec.status = RoyaltyStatus.RESOLVED
        rec.cancel_height = height
        rec.cancel_txid = tx_hash
        # Re-stage at the close height so flush rewrites the record + drops the
        # ACTIVE_* keys, and backup() at this height reopens it on reorg.
        self.listing_cache[outpoint] = rec
        self.listing_height[outpoint] = height

    # ---- key builders (shared by open + close so bytes never drift) ----
    def _ref_key(self, rec: RoyaltyListingInfo) -> bytes:
        return RoyaltyDBKeys.ACTIVE_BY_REF + rec.ref + rec.listing_id

    def _seller_key(self, rec: RoyaltyListingInfo) -> Optional[bytes]:
        if not rec.seller_scripthash:
            return None
        return RoyaltyDBKeys.ACTIVE_BY_SELLER + rec.seller_scripthash + rec.listing_id

    def _all_key(self, rec: RoyaltyListingInfo) -> bytes:
        # Keyed on CREATION height (rec.height) so open + close reconstruct it
        # identically even though the close is recorded at a later height.
        return RoyaltyDBKeys.ACTIVE_ALL + pack_be_uint32(rec.height) + rec.listing_id

    # ---- undo / flush / backup (mirror swap_index/predict_index) ----
    def _undo_key(self, height: int) -> bytes:
        return RoyaltyDBKeys.UNDO + pack_be_uint32(height)

    def _record_undo(self, height: int, key: bytes):
        if key in self._undo_seen[height]:
            return
        self._undo_seen[height].add(key)
        self._undo_cache[height].append((key, self.db.utxo_db.get(key)))

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

    def flush(self, batch):
        if not self.enabled:
            return
        self._prune_old_undo_keys(batch)
        for listing_id, rec in self.listing_cache.items():
            undo_height = self.listing_height.get(listing_id)
            if undo_height is None:
                continue
            terminal = rec.status != RoyaltyStatus.ACTIVE
            # Build keys/value BEFORE touching the batch so a malformed record is
            # logged + skipped, never aborting the shared write batch.
            try:
                value = rec.to_bytes()
                ref_key = self._ref_key(rec)
                seller_key = self._seller_key(rec)
                all_key = self._all_key(rec)
            except Exception:
                self.logger.warning('Skipping malformed royalty listing %s during flush',
                                    listing_id.hex(), exc_info=True)
                continue

            listing_key = RoyaltyDBKeys.LISTING + listing_id
            self._record_undo(undo_height, listing_key)
            batch.put(listing_key, value)

            if terminal:
                for k in (ref_key, seller_key, all_key):
                    if k is None:
                        continue
                    self._record_undo(undo_height, k)
                    batch.delete(k)
            else:
                for k in (ref_key, seller_key, all_key):
                    if k is None:
                        continue
                    self._record_undo(undo_height, k)
                    batch.put(k, b'')

        for h, entries in sorted(self._undo_cache.items()):
            if entries:
                batch.put(self._undo_key(h), encode_undo(entries))
        self.listing_cache.clear()
        self.listing_height.clear()
        self._undo_cache.clear()
        self._undo_seen.clear()

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
        undo_entries = sum(len(v) for v in self._undo_cache.values())
        return len(self.listing_cache) * 400 + undo_entries * 120

    # ---- queries ----
    def _get_listing(self, listing_id: bytes) -> Optional[RoyaltyListingInfo]:
        rec = self.listing_cache.get(listing_id)
        if rec is not None:
            return rec
        data = self.db.utxo_db.get(RoyaltyDBKeys.LISTING + listing_id)
        if not data:
            return None
        return RoyaltyListingInfo.from_bytes(data)

    def get_listings(self, ref: bytes = None, seller_scripthash: bytes = None,
                     limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Active listings: for one NFT (ref), one seller, or global (newest-first)."""
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        if ref:
            prefix = RoyaltyDBKeys.ACTIVE_BY_REF + ref
            reverse = False
        elif seller_scripthash:
            prefix = RoyaltyDBKeys.ACTIVE_BY_SELLER + seller_scripthash
            reverse = False
        else:
            prefix = RoyaltyDBKeys.ACTIVE_ALL
            reverse = True  # global browse: newest-first
        out: List[Dict[str, Any]] = []
        skipped = 0
        for key, _ in self.db.utxo_db.iterator(prefix=prefix, reverse=reverse):
            if skipped < offset:
                skipped += 1
                continue
            listing_id = key[-36:]
            rec = self._get_listing(listing_id)
            if rec and rec.status == RoyaltyStatus.ACTIVE:
                out.append(rec.to_dict())
            if len(out) >= limit:
                break
        return out
