"""
Prediction-market discovery index for RXinDexer (RadiantSwap `market.*`).

Scans every transaction for an RMKT market-creation beacon (a value-0 OP_RETURN emitted by
RadiantSwap's buildCreateMarket) and records the market so clients can DISCOVER markets without an
off-chain registry. This is distinct from market_index.py (trade analytics / OHLCV for RSWP swaps).

Trust model (mirrors the RadiantSwap SDK `verifyMarketBeacon`): the beacon is just an OP_RETURN
anyone can write, with no consensus binding. We believe a market only when:
  1. `marketRef` is INDUCIBLE by this tx — it equals one of the tx's spent outpoints (the exact seed
     Radiant consensus lets a singleton be minted from). `spent_outpoints` are `prev_hash+LE(vout)`,
     byte-identical to a 36-byte ref.
  2. output[0] is the Market singleton — a stateful `<push state> OP_STATESEPARATOR <code>` output
     whose code carries `marketRef`. Resolution params (expiry/grace/oracle/status) are read from
     THAT state section, never from the beacon's (forgeable) fields.
  3. output[1]/[2] carry the yes/no refs AND the marketRef (the share anchors).
Only then is the human `question` stored; expiry/grace/oracle are always the on-chain values.

Live status changes (resolve/finalize/revert) are NOT tracked here — expiry/grace/oracle are
immutable, so discovery is stable; clients refresh live status via `blockchain.ref.get` on the
singleton (as the RadiantSwap client already does). `market.*` serves the immutable record + the
creation status.
"""

import struct
import time
from typing import Optional, Dict, Any, List

from electrumx.lib import util
from electrumx.lib.hash import hash_to_hex_str, hex_str_to_hash
from electrumx.lib.util import pack_be_uint32, unpack_be_uint32, encode_undo, decode_undo
from electrumx.lib.script import OpCodes

OP_STATESEPARATOR = 0xbd
RMKT_MAGIC = b'RMKT'
RMKT_VERSION = 0x01
STATE_LEN = 42
STATE_LEN_OPT = 74


class PredictDBKeys:
    MARKET = b'PMm'       # PMm + marketRef(36) -> packed market record
    BY_HEIGHT = b'PMh'    # PMh + be_u32(height) + marketRef(36) -> b''  (newest-first listing)
    UNDO = b'PMu'         # PMu + be_u32(height) -> encode_undo([(key, prev|None)])


def _parse_script_chunks(script: bytes) -> List[bytes]:
    """Split a script into push chunks (bare opcodes become 1-byte chunks). Mirrors swap_index."""
    chunks: List[bytes] = []
    pos = 0
    n = len(script)
    while pos < n:
        op = script[pos]; pos += 1
        if op == OpCodes.OP_RETURN or op == 0x00:
            chunks.append(bytes([op])); continue
        if 1 <= op <= 75:
            length = op
        elif op == 0x4c:
            if pos >= n: break
            length = script[pos]; pos += 1
        elif op == 0x4d:
            if pos + 2 > n: break
            length = struct.unpack('<H', script[pos:pos + 2])[0]; pos += 2
        elif op == 0x4e:
            if pos + 4 > n: break
            length = struct.unpack('<I', script[pos:pos + 4])[0]; pos += 4
        else:
            chunks.append(bytes([op])); continue
        if pos + length > n: break
        chunks.append(script[pos:pos + length]); pos += length
    return chunks


def parse_market_beacon(script: bytes) -> Optional[Dict[str, Any]]:
    """Parse an RMKT OP_RETURN. Returns dict (refs/expiry/grace/oracle/question) or None.
    UNTRUSTED — see verify in process_tx."""
    try:
        if not script or script[0] != OpCodes.OP_RETURN:
            return None
        c = _parse_script_chunks(script)
        if len(c) < 10 or c[1] != RMKT_MAGIC:
            return None
        if len(c[2]) != 1 or c[2][0] != RMKT_VERSION:
            return None
        market_ref, yes_ref, no_ref = c[3], c[4], c[5]
        if len(market_ref) != 36 or len(yes_ref) != 36 or len(no_ref) != 36:
            return None
        if len(c[6]) != 4 or len(c[7]) != 4 or len(c[8]) != 33:
            return None
        question = c[9].decode('utf-8', errors='strict')
        if not question:
            return None
        return {
            'market_ref': market_ref, 'yes_ref': yes_ref, 'no_ref': no_ref,
            'b_expiry': struct.unpack('<I', c[6])[0], 'b_grace': struct.unpack('<I', c[7])[0],
            'b_oracle': c[8], 'question': question,
        }
    except Exception:
        return None


MARKER = b'\x00' * 20  # anchor state: 20 zero bytes (not a real pubkey-hash)


def parse_stateful(script: bytes):
    """Split a stateful output `<push state> OP_STATESEPARATOR <code>` into (state, code), or None."""
    n = len(script)
    if n < 2:
        return None
    op = script[0]
    if 1 <= op <= 75:
        length, o = op, 1
    elif op == 0x4c:
        length, o = script[1], 2
    elif op == 0x4d:
        if n < 3:
            return None
        length, o = struct.unpack('<H', script[1:3])[0], 3
    else:
        return None
    if o + length >= n or script[o + length] != OP_STATESEPARATOR:
        return None
    return script[o:o + length], script[o + length + 1:]


def parse_singleton_state(script: bytes) -> Optional[Dict[str, Any]]:
    """Parse a Market singleton output and decode its 42/74-byte state, or None."""
    parsed = parse_stateful(script)
    if not parsed:
        return None
    state, code = parsed
    if len(state) not in (STATE_LEN, STATE_LEN_OPT):
        return None
    return {
        'status': state[0],
        'expiry': struct.unpack('<I', state[1:5])[0],
        'grace': struct.unpack('<I', state[5:9])[0],
        'oracle': state[9:42],
        'optimistic': len(state) == STATE_LEN_OPT,
        'code': code,
    }


def is_anchor_for(script: bytes, share_ref: bytes, market_ref: bytes) -> bool:
    """True iff `script` is a genuine ShareToken anchor: a stateful output with a 20-byte MARKER
    state whose code carries both the share ref and the market ref. Distinguishes a real anchor
    from the Market singleton (which is stateful too and contains all three refs in its code)."""
    parsed = parse_stateful(script)
    if not parsed:
        return False
    state, code = parsed
    return state == MARKER and share_ref in code and market_ref in code


class MarketRecord:
    __slots__ = ('market_ref', 'yes_ref', 'no_ref', 'expiry', 'grace', 'oracle',
                 'status', 'optimistic', 'question', 'beacon_params_match',
                 'create_txid', 'create_height')

    def __init__(self, **kw):
        for s in self.__slots__:
            setattr(self, s, kw.get(s))

    def to_bytes(self) -> bytes:
        q = (self.question or '').encode('utf-8')
        return b''.join([
            self.market_ref, self.yes_ref, self.no_ref,
            struct.pack('<IIBBB', self.expiry, self.grace, self.status,
                        1 if self.optimistic else 0, 1 if self.beacon_params_match else 0),
            self.oracle,                       # 33
            self.create_txid,                  # 32
            pack_be_uint32(self.create_height),
            struct.pack('<H', len(q)), q,
        ])

    @classmethod
    def from_bytes(cls, b: bytes) -> 'MarketRecord':
        o = 0
        market_ref = b[o:o + 36]; o += 36
        yes_ref = b[o:o + 36]; o += 36
        no_ref = b[o:o + 36]; o += 36
        expiry, grace, status, opt, bpm = struct.unpack('<IIBBB', b[o:o + 11]); o += 11
        oracle = b[o:o + 33]; o += 33
        create_txid = b[o:o + 32]; o += 32
        create_height = unpack_be_uint32(b[o:o + 4])[0]; o += 4
        qlen = struct.unpack('<H', b[o:o + 2])[0]; o += 2
        question = b[o:o + qlen].decode('utf-8', errors='replace') if qlen else None
        return cls(market_ref=market_ref, yes_ref=yes_ref, no_ref=no_ref, expiry=expiry,
                   grace=grace, status=status, optimistic=bool(opt), beacon_params_match=bool(bpm),
                   oracle=oracle, create_txid=create_txid, create_height=create_height,
                   question=question)

    def to_dict(self) -> Dict[str, Any]:
        # refs are reported in display (big-endian txid) form: reverse the 32-byte txid, keep vout.
        def ref_hex(r: bytes) -> str:
            return hash_to_hex_str(r[:32]) + '_' + str(struct.unpack('<I', r[32:36])[0])
        return {
            'market_ref': ref_hex(self.market_ref),
            'yes_ref': ref_hex(self.yes_ref),
            'no_ref': ref_hex(self.no_ref),
            'expiry': self.expiry,
            'grace': self.grace,
            'oracle': self.oracle.hex(),
            'optimistic': self.optimistic,
            'status_at_creation': self.status,   # live status: query blockchain.ref.get on market_ref
            'question': self.question,
            'beacon_params_match': self.beacon_params_match,
            'create_txid': hash_to_hex_str(self.create_txid),
            'create_height': self.create_height,
        }


class PredictionMarketIndex:
    """Discovery index for RadiantSwap prediction markets (RMKT beacons)."""

    def __init__(self, db, env):
        self.logger = util.class_logger(__name__, self.__class__.__name__)
        self.db = db
        self.env = env
        self.enabled = getattr(env, 'predict_index', True)
        self.market_cache: Dict[bytes, MarketRecord] = {}
        self._undo_cache: Dict[int, List] = {}
        self._undo_seen: Dict[int, set] = {}
        reorg_limit = getattr(env, 'reorg_limit', 0)
        cur = getattr(db, 'db_height', -1)
        self._last_undo_pruned = (max(0, cur - reorg_limit + 1) - 1) if reorg_limit else -1
        if self.enabled:
            self.logger.info('Prediction-market (RMKT) indexing enabled')

    def set_logger(self, logger):
        if logger:
            self.logger = logger

    # ---- block processing ----
    def process_tx(self, tx_hash: bytes, tx, height: int, tx_idx: int,
                   glyph_envelope: Dict[str, Any] = None, spent_outpoints: set = None):
        if not self.enabled:
            return
        # find an RMKT beacon among outputs
        beacon = None
        for txout in tx.outputs:
            b = parse_market_beacon(txout.pk_script)
            if b:
                beacon = b
                break
        if not beacon:
            return

        market_ref = beacon['market_ref']
        # 1) inducibility: marketRef must be a spent outpoint of this tx
        if not spent_outpoints or market_ref not in spent_outpoints:
            self.logger.info('RMKT beacon in %s rejected: marketRef not inducible by tx',
                             hash_to_hex_str(tx_hash))
            return
        # 2) out0 must be the Market singleton carrying marketRef; read params from its state
        if len(tx.outputs) < 3:
            return
        s0 = parse_singleton_state(tx.outputs[0].pk_script)
        if not s0 or market_ref not in tx.outputs[0].pk_script:
            self.logger.info('RMKT beacon in %s rejected: out0 is not the deployed singleton',
                             hash_to_hex_str(tx_hash))
            return
        # 3) out1/out2 must be genuine MARKER anchors carrying the yes/no refs + marketRef
        o1, o2 = tx.outputs[1].pk_script, tx.outputs[2].pk_script
        if not (is_anchor_for(o1, beacon['yes_ref'], market_ref)
                and is_anchor_for(o2, beacon['no_ref'], market_ref)):
            self.logger.info('RMKT beacon in %s rejected: out1/out2 are not the yes/no anchors',
                             hash_to_hex_str(tx_hash))
            return

        params_match = (beacon['b_expiry'] == s0['expiry']
                        and beacon['b_grace'] == s0['grace']
                        and beacon['b_oracle'] == s0['oracle'])
        rec = MarketRecord(
            market_ref=market_ref, yes_ref=beacon['yes_ref'], no_ref=beacon['no_ref'],
            expiry=s0['expiry'], grace=s0['grace'], oracle=s0['oracle'],   # from CHAIN STATE
            status=s0['status'], optimistic=s0['optimistic'],
            question=beacon['question'], beacon_params_match=params_match,
            create_txid=tx_hash, create_height=height,
        )
        self.market_cache[market_ref] = rec

    # ---- undo / flush / backup ----
    def _undo_key(self, height: int) -> bytes:
        return PredictDBKeys.UNDO + pack_be_uint32(height)

    def _record_undo(self, height: int, key: bytes):
        seen = self._undo_seen.setdefault(height, set())
        if key in seen:
            return
        seen.add(key)
        self._undo_cache.setdefault(height, []).append((key, self.db.utxo_db.get(key)))

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
        for market_ref, rec in self.market_cache.items():
            h = rec.create_height
            mkey = PredictDBKeys.MARKET + market_ref
            hkey = PredictDBKeys.BY_HEIGHT + pack_be_uint32(h) + market_ref
            self._record_undo(h, mkey)
            self._record_undo(h, hkey)
            batch.put(mkey, rec.to_bytes())
            batch.put(hkey, b'')
        for h, entries in self._undo_cache.items():
            if entries:
                batch.put(self._undo_key(h), encode_undo(entries))
        self.market_cache.clear()
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
        return len(self.market_cache) * 260 + undo_entries * 100

    # ---- queries ----
    def get_market(self, market_ref: bytes) -> Optional[Dict[str, Any]]:
        rec = self.market_cache.get(market_ref)
        if rec is not None:
            return rec.to_dict()
        data = self.db.utxo_db.get(PredictDBKeys.MARKET + market_ref)
        if not data:
            return None
        return MarketRecord.from_bytes(data).to_dict()

    def list_markets(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        out: List[Dict[str, Any]] = []
        skipped = 0
        # newest-first by height
        for key, _ in self.db.utxo_db.iterator(prefix=PredictDBKeys.BY_HEIGHT, reverse=True):
            if skipped < offset:
                skipped += 1
                continue
            market_ref = key[len(PredictDBKeys.BY_HEIGHT) + 4:]
            data = self.db.utxo_db.get(PredictDBKeys.MARKET + market_ref)
            if data:
                out.append(MarketRecord.from_bytes(data).to_dict())
            if len(out) >= limit:
                break
        return out
