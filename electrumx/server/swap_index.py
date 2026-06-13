"""
Swap Index for RXinDexer

This module provides database storage and indexing for on-chain swap
advertisements (RSWP protocol). Tracks open orders, filled orders,
and swap history.

Designed to serve explorers, wallets, DEX interfaces, and market data APIs.
"""

import base64
import struct
import time
from typing import Optional, Dict, Any, List, Tuple
from collections import defaultdict

from electrumx.lib import util
from electrumx.lib.hash import hash_to_hex_str, hex_str_to_hash, sha256, Base58, Base58Error
from electrumx.lib.util import pack_be_uint32, encode_undo, decode_undo
from electrumx.lib.script import OpCodes, Script, ScriptError
from electrumx.server.metrics import swap_parse_errors_total as _swap_parse_errors

try:
    import cbor2
    HAS_CBOR = True
except ImportError:
    HAS_CBOR = False


def _encode_cursor(raw_key: bytes) -> str:
    """Encode raw RocksDB seek key to opaque base64 cursor.

    Shared helper for cursor pagination. See docs/pagination-cursors.md.
    """
    return base64.b64encode(raw_key).decode()


def _decode_cursor(cursor: Optional[str]) -> Optional[bytes]:
    """Decode opaque cursor back to seek key. Returns None on failure."""
    if not cursor:
        return None
    try:
        return base64.b64decode(cursor)
    except Exception:
        return None


# Database key prefixes for Swap data
class SwapDBKeys:
    """Database key prefixes for Swap index."""
    ORDER = b'SO'           # SO + order_id -> order info
    OPEN_BY_PAIR = b'SP'    # SP + base_ref + quote_ref + price + order_id -> (for orderbook)
    OPEN_BY_MAKER = b'SM'   # SM + maker_scripthash + order_id -> (for user orders)
    HISTORY = b'SH'         # SH + base_ref + height + tx_idx -> event
    STATS = b'SS'           # SS + base_ref + quote_ref -> pair stats
    FILL = b'SF'            # SF + order_id + height + tx_idx -> fill info
    UNDO = b'SWU'           # SWU + height(be) -> repr([(key, prev_value_or_None), ...])


# Order status
class OrderStatus:
    OPEN = 0
    PARTIAL = 1
    FILLED = 2
    CANCELLED = 3
    EXPIRED = 4


# Order side
class OrderSide:
    BUY = 0
    SELL = 1


# Maximum value representable in the on-disk uint64 price/amount keys.  Order
# amounts are parsed from attacker-controlled OP_RETURN payloads, so any value
# outside this range is rejected before it can reach struct.pack('>Q', ...) in
# flush() — an out-of-range value there raises struct.error and would abort the
# entire shared write batch (UTXO + glyph + wave + state), wedging the indexer.
MAX_UINT64 = 0xFFFFFFFFFFFFFFFF


def _order_amounts_in_range(order) -> bool:
    """True iff every numeric field that is packed or keyed fits a uint64.

    Guards the flush-time ``struct.pack('>Q', price)`` and ``bytes([side])``
    calls against negative/oversized values decoded from untrusted scriptnums.
    """
    for value in (order.price, order.amount, order.remaining_amount,
                  order.filled_amount, order.min_fill):
        if not isinstance(value, int) or value < 0 or value > MAX_UINT64:
            return False
    return isinstance(order.side, int) and 0 <= order.side <= 255


# Length of one ref entry in a b'ri' record: 36-byte ref (txid_internal + LE
# vout) + 1 type byte (0=normal/FT, 1=singleton/NFT).  block_processor writes
# the b'ri'+outpoint value as a flat concatenation of these entries.
REF_ENTRY_LEN = 37
REF_LEN = 36


def _advertised_token_hash(order) -> Optional[bytes]:
    """Return the 32-byte SHA-256 id of the token the order OFFERS, or None.

    The RSWP advertisement does NOT carry the offered token's raw 36-byte ref;
    it carries ``offeredTokenId = sha256(ref_36)`` pushed byte-REVERSED (Photonic
    ``assetToSwapTokenId`` in swapBroadcast.ts + ``buildSwapAdvertisementScript``
    in Swap.tsx, which ``.reverse()``-es the 32-byte hash before the push).  The
    parser stores that on-script (reversed) value verbatim in the ref's first 32
    bytes, so to recover the canonical ``sha256(ref_36)`` we reverse it back.

    The backing-UTXO check only makes sense for the OFFERED asset (the thing
    the backing outpoint must actually hold).  Pair refs are normalized
    token-as-base, so for a SELL the offered asset is ``base_ref``; for a BUY
    the maker offers the quote side (RXD for canonical bids) and ``base_ref``
    is the WANTED token — which the backing coin does not and must not carry.

    Returns None for an RXD offer (token id == 32 zero bytes), which has no
    token ref to back-check, and for a malformed/short ref.
    """
    offered_ref = (order.quote_ref if order.side == OrderSide.BUY
                   else order.base_ref)
    if not offered_ref or len(offered_ref) < 32:
        return None
    token_hash = offered_ref[:32][::-1]
    if token_hash == b'\x00' * 32:
        return None  # RXD offer — nothing to verify on-chain.
    return token_hash


def _backing_utxo_carries_token(ri_record: bytes, token_hash: bytes) -> bool:
    """True iff a b'ri' record contains a ref whose sha256 == ``token_hash``.

    ``ri_record`` is the on-disk ``b'ri'+outpoint`` value: a flat concatenation
    of 37-byte entries (36-byte ref + 1 type byte).  The advertised token id is
    ``sha256(ref_36)`` (see :func:`_advertised_token_hash`), so we hash each raw
    ref entry and compare.  This is the only deterministic bridge between the
    two representations — the indexer never persists ``sha256(ref)`` keyed data.
    """
    n = len(ri_record)
    off = 0
    while off + REF_ENTRY_LEN <= n:
        ref_36 = ri_record[off:off + REF_LEN]
        if sha256(ref_36) == token_hash:
            return True
        off += REF_ENTRY_LEN
    return False


def parse_multi_txout(blob: bytes):
    """Decode an RSWP MultiTxOutV1 priceTerms blob.

    Layout (see Photonic ``encodePriceTermsOutputs`` / ``parsePriceTerms``):
        <CompactSize count> { <value: 8-byte LE> <CompactSize scriptLen> <script> }*count
    Legacy fallback: a bare ``<value:8-LE><script:rest>`` single output.
    Returns a list of ``(value:int, script:bytes)`` or ``None``.

    Module-level so the mempool RSWP parser (mempool_glyph.py) shares the
    exact same decode as the confirmed index.
    """
    if not blob:
        return None

    def read_compact(b, o):
        n = b[o]
        if n < 253:
            return n, o + 1
        if n == 253:
            return struct.unpack_from('<H', b, o + 1)[0], o + 3
        if n == 254:
            return struct.unpack_from('<I', b, o + 1)[0], o + 5
        return struct.unpack_from('<Q', b, o + 1)[0], o + 9

    try:
        outputs = []
        o = 0
        count, o = read_compact(blob, o)
        if count <= 0 or count > 1000:
            raise ValueError('bad count')
        for _ in range(count):
            if o + 8 > len(blob):
                raise ValueError('truncated value')
            value = struct.unpack_from('<Q', blob, o)[0]
            o += 8
            slen, o = read_compact(blob, o)
            if o + slen > len(blob):
                raise ValueError('truncated script')
            outputs.append((value, blob[o:o + slen]))
            o += slen
        if o != len(blob) or not outputs:
            raise ValueError('trailing bytes')
        return outputs
    except Exception:
        # Legacy single-output fallback: value(8 LE) + rest = script
        if len(blob) >= 9:
            return [(struct.unpack_from('<Q', blob, 0)[0], blob[8:])]
        return None


def maker_from_script(script: bytes, coin=None):
    """Resolve a payout output script to (electrum_scripthash_32, address).

    The maker is the recipient of the priceTerms payout; the script is a
    p2pkh (RXD) or an ftScript (token) that embeds the maker P2PKH. Returns
    (b'', None) if no standard address can be extracted.  ``coin`` supplies
    the address verbytes; with ``coin=None`` only the scripthash is returned.
    """
    try:
        base = Script.base_locking_script(script)
    except (ScriptError, Exception):
        return b'', None
    # Standard templates: P2PKH (OP_DUP OP_HASH160 <20> OP_EQUALVERIFY OP_CHECKSIG) / P2SH
    address = None
    try:
        if (len(base) == 25 and base[0] == OpCodes.OP_DUP
                and base[1] == OpCodes.OP_HASH160 and base[2] == 0x14
                and base[23] == OpCodes.OP_EQUALVERIFY and base[24] == OpCodes.OP_CHECKSIG):
            if coin is not None:
                address = Base58.encode_check(coin.P2PKH_VERBYTE + base[3:23])
        elif (len(base) == 23 and base[0] == OpCodes.OP_HASH160
                and base[1] == 0x14 and base[22] == OpCodes.OP_EQUAL):
            if coin is not None:
                address = Base58.encode_check(coin.P2SH_VERBYTES[0] + base[2:22])
        else:
            return b'', None
    except (Base58Error, Exception):
        address = None
    # Electrum scripthash convention: sha256(script) reversed.
    return sha256(base)[::-1], address


class SwapOrderInfo:
    """
    Represents an indexed swap order.

    Stores all fields needed by DEX interfaces and market data APIs.
    """
    __slots__ = (
        # Order identity
        'order_id', 'tx_hash', 'vout', 'height', 'timestamp',
        # Maker info
        'maker_scripthash', 'maker_address',
        # Trading pair
        'base_ref', 'quote_ref', 'base_ticker', 'quote_ticker',
        # Order details
        'side', 'price', 'amount', 'filled_amount', 'remaining_amount',
        'min_fill', 'fee_rate',
        # Status
        'status', 'expiry_height', 'cancel_height', 'cancel_txid',
        # Execution
        'fill_count', 'last_fill_height', 'avg_fill_price',
    )
    
    def __init__(self):
        # Order identity
        self.order_id = b''  # Unique order ID (tx_hash + vout)
        self.tx_hash = b''
        self.vout = 0
        self.height = 0
        self.timestamp = 0
        # Maker info
        self.maker_scripthash = b''
        self.maker_address = None
        # Trading pair
        self.base_ref = b''  # Base token ref (what you're selling/buying)
        self.quote_ref = b''  # Quote token ref (what you're pricing in)
        self.base_ticker = None
        self.quote_ticker = None
        # Order details
        self.side = OrderSide.SELL  # 0=BUY, 1=SELL
        self.price = 0  # Price in quote token units (scaled by 10^8)
        self.amount = 0  # Total order amount in base token
        self.filled_amount = 0
        self.remaining_amount = 0
        self.min_fill = 0  # Minimum fill amount
        self.fee_rate = 0  # Fee rate in basis points
        # Status
        self.status = OrderStatus.OPEN
        self.expiry_height = 0  # 0 = no expiry
        self.cancel_height = 0
        self.cancel_txid = None
        # Execution
        self.fill_count = 0
        self.last_fill_height = 0
        self.avg_fill_price = 0
    
    def to_bytes(self) -> bytes:
        """Serialize order info to CBOR bytes."""
        if not HAS_CBOR:
            raise RuntimeError('cbor2 required for Swap indexing')
        
        data = {
            'oid': self.order_id,
            'txh': self.tx_hash,
            'v': self.vout,
            'h': self.height,
            'ts': self.timestamp,
            'ms': self.maker_scripthash,
            'ma': self.maker_address,
            'br': self.base_ref,
            'qr': self.quote_ref,
            'bt': self.base_ticker,
            'qt': self.quote_ticker,
            'sd': self.side,
            'pr': self.price,
            'am': self.amount,
            'fa': self.filled_amount,
            'ra': self.remaining_amount,
            'mf': self.min_fill,
            'fr': self.fee_rate,
            'st': self.status,
            'eh': self.expiry_height,
            'ch': self.cancel_height,
            'ct': self.cancel_txid,
            'fc': self.fill_count,
            'lfh': self.last_fill_height,
            'afp': self.avg_fill_price,
        }
        data = {k: v for k, v in data.items() if v is not None and v != 0 and v != b''}
        # 'sd' (side) MUST always persist: OrderSide.BUY == 0 would otherwise be
        # stripped and read back as the SELL default, flipping the order side.
        # That side byte is part of the OPEN_BY_PAIR key, so a dropped BUY would
        # make the close path reconstruct a SELL key and leak a phantom orderbook
        # entry on close.  Likewise 'st' (status): OrderStatus.OPEN == 0 happens
        # to round-trip via the from_bytes default, but pin it for clarity.
        data['sd'] = self.side
        data['st'] = self.status
        return cbor2.dumps(data)
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'SwapOrderInfo':
        """Deserialize order info from CBOR bytes."""
        if not HAS_CBOR:
            raise RuntimeError('cbor2 required for Swap indexing')
        
        order = cls()
        d = cbor2.loads(data)
        
        order.order_id = d.get('oid', b'')
        order.tx_hash = d.get('txh', b'')
        order.vout = d.get('v', 0)
        order.height = d.get('h', 0)
        order.timestamp = d.get('ts', 0)
        order.maker_scripthash = d.get('ms', b'')
        order.maker_address = d.get('ma')
        order.base_ref = d.get('br', b'')
        order.quote_ref = d.get('qr', b'')
        order.base_ticker = d.get('bt')
        order.quote_ticker = d.get('qt')
        order.side = d.get('sd', OrderSide.SELL)
        order.price = d.get('pr', 0)
        order.amount = d.get('am', 0)
        order.filled_amount = d.get('fa', 0)
        order.remaining_amount = d.get('ra', 0)
        order.min_fill = d.get('mf', 0)
        order.fee_rate = d.get('fr', 0)
        order.status = d.get('st', OrderStatus.OPEN)
        order.expiry_height = d.get('eh', 0)
        order.cancel_height = d.get('ch', 0)
        order.cancel_txid = d.get('ct')
        order.fill_count = d.get('fc', 0)
        order.last_fill_height = d.get('lfh', 0)
        order.avg_fill_price = d.get('afp', 0)
        
        return order


class SwapFillInfo:
    """Represents a fill event on an order."""
    __slots__ = ('order_id', 'tx_hash', 'height', 'timestamp',
                 'taker_scripthash', 'fill_amount', 'fill_price',
                 'base_amount', 'quote_amount', 'fee_amount')
    
    def __init__(self):
        self.order_id = b''
        self.tx_hash = b''
        self.height = 0
        self.timestamp = 0
        self.taker_scripthash = b''
        self.fill_amount = 0
        self.fill_price = 0
        self.base_amount = 0
        self.quote_amount = 0
        self.fee_amount = 0


class PairStats:
    """Trading pair statistics."""
    __slots__ = ('base_ref', 'quote_ref', 'last_price', 'high_24h', 'low_24h',
                 'volume_24h_base', 'volume_24h_quote', 'trade_count_24h',
                 'open_order_count', 'total_bid_depth', 'total_ask_depth')
    
    def __init__(self):
        self.base_ref = b''
        self.quote_ref = b''
        self.last_price = 0
        self.high_24h = 0
        self.low_24h = 0
        self.volume_24h_base = 0
        self.volume_24h_quote = 0
        self.trade_count_24h = 0
        self.open_order_count = 0
        self.total_bid_depth = 0
        self.total_ask_depth = 0


class SwapIndex:
    """
    Swap order index manager.
    
    Handles indexing of swap advertisements and fills during block processing
    and provides query methods for the API.
    """
    
    def __init__(self, db, env):
        self.logger = util.class_logger(__name__, self.__class__.__name__)
        self.db = db
        self.env = env
        self.enabled = getattr(env, 'swap_index', True)
        
        # In-memory caches
        self.order_cache: Dict[bytes, SwapOrderInfo] = {}
        self.order_height: Dict[bytes, int] = {}
        self.stats_cache: Dict[bytes, PairStats] = {}
        self.history_cache: List[Tuple[int, bytes, bytes]] = []

        self._undo_cache: Dict[int, List[Tuple[bytes, Optional[bytes]]]] = defaultdict(list)
        self._undo_seen: Dict[int, set] = defaultdict(set)

        # Undo retention: keep at most env.reorg_limit heights of undo data.
        current_height = getattr(db, 'db_height', -1)
        reorg_limit = getattr(env, 'reorg_limit', 0)
        min_keep = max(0, current_height - reorg_limit + 1) if reorg_limit else 0
        self._last_undo_pruned = min_keep - 1
        
        if self.enabled:
            self.logger.info('Swap order indexing enabled')
    
    def process_tx(self, tx_hash: bytes, tx, height: int, tx_idx: int,
                   glyph_envelope: Dict[str, Any] = None,
                   spent_outpoints: set = None):
        """
        Process a transaction for swap orders.

        Detects RSWP protocol advertisements in OP_RETURN outputs.
        Format based on Radiant-Core swapindex.cpp implementation.

        Lifecycle (close-on-spend): an order's ``order_id`` is byte-for-byte the
        backing outpoint it advertises (``utxoHash + <utxoIndex LE u32>``).  When
        that outpoint is spent, the order can no longer be filled, so we close it.
        ``spent_outpoints`` is the per-tx set of spent outpoints already computed
        by the block processor in the identical ``prev_hash + LE(prev_idx)`` form,
        so a spent outpoint equals an order_id under an O(1) lookup with no
        transform.  Reorg unwinding reopens closed orders via the undo machinery.

        Authorization (C3-auth): before an order is admitted to
        ``order_cache`` we run :meth:`_backing_utxo_offers_token` (Part A), which
        rejects orders whose advertised backing UTXO demonstrably does not carry
        the offered token (present-but-mismatched ``b'ri'`` record; absent
        records are accepted to avoid same-block false positives).

        RSWP *signature* verification (Part B) lives in
        ``electrumx.lib.rswp_verify`` and is intentionally NOT wired into this
        block-processing path: sourcing the backing scriptPubKey here is
        unresolved (it is not persisted, and a daemon RPC inside the sync loop is
        an anti-pattern), and the ECDSA step cannot be validated without an
        optional ``coincurve`` build.  It is exposed as an opt-in hook
        (``SWAP_VERIFY_SIGNATURES`` env flag, default off) so it can never reject
        legitimate orders until validated in a crypto-capable environment.  See
        that module's docstring for the two integration options.
        """
        if not self.enabled:
            return

        timestamp = int(time.time())

        # Close-on-spend: walk every outpoint this tx consumed.  Any one that is
        # an open order's backing UTXO closes that order.  Done BEFORE the output
        # scan so a same-block create+spend (order minted then immediately spent
        # in a later tx of the same block) is closed from the cache and never
        # leaks into the OPEN_BY_* indexes.
        if spent_outpoints:
            for outpoint in spent_outpoints:
                self._close_order_if_open(outpoint, tx_hash, height, tx_idx,
                                          timestamp)

        for vout_idx, txout in enumerate(tx.outputs):
            script = txout.pk_script
            
            # Check for OP_RETURN
            if not script or script[0] != OpCodes.OP_RETURN:
                continue
            
            # Parse RSWP advertisement
            order = self._parse_rswp_advertisement(script, tx_hash, vout_idx, height, timestamp)
            if order:
                if not _order_amounts_in_range(order):
                    self.logger.warning(
                        'Rejecting swap order %s: price/amount out of uint64 range '
                        '(price=%r amount=%r side=%r)',
                        hash_to_hex_str(order.order_id), order.price, order.amount,
                        order.side
                    )
                    continue
                if not self._backing_utxo_offers_token(order):
                    continue
                self.order_cache[order.order_id] = order
                self.order_height[order.order_id] = height
                self.logger.debug(f'Indexed swap order: {hash_to_hex_str(order.order_id)}')

    def _close_order_if_open(self, order_id: bytes, tx_hash: bytes,
                             height: int, tx_idx: int, timestamp: int):
        """Close the order whose backing outpoint == ``order_id``, if any.

        Resolves the order from the in-memory cache first (covers same-block
        create+spend) and falls back to the on-disk ORDER record.  Spending an
        outpoint that is not a known order is a no-op: we deliberately write NO
        tombstone, because ``order_id`` here is an attacker-controlled spent
        outpoint and recording one record per spend would be unbounded state.

        Idempotent: an order already in a terminal status (FILLED/CANCELLED/
        EXPIRED) is left untouched, which is required for reorg replay and for a
        tx that lists the same outpoint twice / two txs spending sibling outputs.
        """
        # Same-block create+spend: the freshly minted order lives in the cache.
        order = self.order_cache.get(order_id)
        if order is None:
            order = self.get_order(order_id)
        if order is None:
            return  # Unknown outpoint — never create a record for it.

        if order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED,
                            OrderStatus.EXPIRED):
            return  # Already closed — idempotent.

        order.status = OrderStatus.FILLED
        order.cancel_height = height
        order.cancel_txid = tx_hash
        # Re-stage into the flush caches so flush() rewrites the ORDER record
        # with terminal status and removes the OPEN_BY_* index keys.  Record the
        # close at THIS height so reorg backup() at this height reopens it.
        self.order_cache[order_id] = order
        self.order_height[order_id] = height

    def _backing_utxo_offers_token(self, order: SwapOrderInfo) -> bool:
        """Authorization (C3-auth, Part A): does the backing UTXO carry the
        offered token?

        An order's ``order_id`` IS the backing outpoint it advertises
        (``utxoHash + LE(utxoIndex)``), which is exactly the key shape
        block_processor uses for its ref side table (``b'ri' + outpoint`` ->
        concatenated 37-byte ``ref(36)+type(1)`` entries).  We look that record
        up and require it to contain a ref whose ``sha256`` equals the offered
        token id (see :func:`_advertised_token_hash` /
        :func:`_backing_utxo_carries_token`).

        Decision rule (MUST stay deterministic + false-positive-free, because a
        wrong reject would silently diverge nodes at different flush boundaries):

          * record ABSENT  -> ACCEPT.  The backing UTXO may have been created in
            this same block and not yet flushed to ``b'ri'``; rejecting here
            would be a false positive.  (It would also reject RXD-only backing
            UTXOs, which legitimately have no ref record.)
          * offered asset is RXD/zero (canonical BUY bids) or the offered ref
            is malformed -> ACCEPT (nothing to check on-chain; a bid's backing
            coin offers plain RXD value, never the wanted token).
          * record PRESENT but contains no ref hashing to the offered token id
            -> REJECT: the advertised UTXO demonstrably does not carry the
            offered token.

        Returns True to admit the order, False to skip it.
        """
        token_hash = _advertised_token_hash(order)
        if token_hash is None:
            return True  # RXD offer / malformed base_ref — nothing to verify.

        ri_record = self.db.utxo_db.get(b'ri' + order.order_id)
        if not ri_record:
            return True  # Absent (possibly same-block, not yet flushed) — accept.

        if _backing_utxo_carries_token(ri_record, token_hash):
            return True

        self.logger.warning(
            'Rejecting swap order %s: backing UTXO does not carry offered token '
            '(token_hash=%s, ri entries=%d)',
            hash_to_hex_str(order.order_id), token_hash.hex(),
            len(ri_record) // REF_ENTRY_LEN,
        )
        return False

    def _pair_key(self, order: SwapOrderInfo) -> Optional[bytes]:
        """Build the OPEN_BY_PAIR orderbook key for ``order``, or None.

        Returns None when the order lacks base_ref/quote_ref.  Used by BOTH the
        open path (put b'') and the close path (delete) so the exact same bytes
        are reconstructed and the index key can never drift / leak.  BUY orders
        invert the price (MAX_UINT64 - price) so higher bids sort first; SELL
        orders key on price directly.  May raise on an out-of-range price; the
        caller computes it inside the flush guard before mutating the batch.
        """
        if not (order.base_ref and order.quote_ref):
            return None
        if order.side == OrderSide.BUY:
            price_key = struct.pack('>Q', MAX_UINT64 - order.price)
        else:
            price_key = struct.pack('>Q', order.price)
        return (
            SwapDBKeys.OPEN_BY_PAIR
            + order.base_ref
            + order.quote_ref
            + bytes([order.side])
            + price_key
            + order.order_id
        )

    def _maker_key(self, order: SwapOrderInfo) -> Optional[bytes]:
        """Build the OPEN_BY_MAKER key for ``order``, or None if no maker.

        Used by BOTH open (put) and close (delete) paths so the bytes match.
        """
        if not order.maker_scripthash:
            return None
        return SwapDBKeys.OPEN_BY_MAKER + order.maker_scripthash + order.order_id

    def _undo_key(self, height: int) -> bytes:
        return SwapDBKeys.UNDO + pack_be_uint32(height)

    def _record_undo(self, height: int, key: bytes):
        if not self.enabled:
            return
        if key in self._undo_seen[height]:
            return
        self._undo_seen[height].add(key)
        prev_value = self.db.utxo_db.get(key)
        self._undo_cache[height].append((key, prev_value))

    def backup(self, batch, height: int):
        """Revert Swap keys written at the given height (reorg unwind)."""
        if not self.enabled:
            return
        raw = self.db.utxo_db.get(self._undo_key(height))
        if not raw:
            return
        entries = decode_undo(raw)  # R22
        for key, prev in entries:
            if prev is None:
                batch.delete(key)
            else:
                batch.put(key, prev)
        batch.delete(self._undo_key(height))

    def _prune_old_undo_keys(self, batch):
        """Delete undo keys that are older than the reorg window."""
        reorg_limit = getattr(self.env, 'reorg_limit', 0)
        if not reorg_limit:
            return

        min_keep = max(0, self.db.db_height - reorg_limit + 1)
        prune_to = min_keep - 1
        if prune_to <= self._last_undo_pruned:
            return

        for height in range(self._last_undo_pruned + 1, prune_to + 1):
            batch.delete(self._undo_key(height))
        self._last_undo_pruned = prune_to
    
    def _parse_rswp_advertisement(self, script: bytes, tx_hash: bytes, 
                                   vout: int, height: int, timestamp: int) -> Optional[SwapOrderInfo]:
        """
        Parse RSWP swap advertisement from OP_RETURN script.
        
        Protocol format:
        v1: OP_RETURN <"RSWP"> <version=1> <type> <tokenID> <utxoHash> <utxoIndex> <priceTerms> <signature>
        v2: OP_RETURN <"RSWP"> <version=2> <flags> <offeredType> <termsType> <tokenID> [wantTokenID] <utxoHash> <utxoIndex> <priceTerms...> <signature>
        """
        try:
            chunks = self._parse_script_chunks(script)
            if len(chunks) < 2:
                return None
            
            # First chunk is OP_RETURN (already verified)
            # Second chunk should be protocol ID "RSWP"
            if len(chunks) < 3:
                return None
            
            protocol_id = chunks[1]
            if protocol_id != b'RSWP':
                return None
            
            # Parse version
            if len(chunks) < 4:
                return None
            
            version_data = chunks[2]
            if len(version_data) != 1:
                return None
            version = version_data[0]
            
            order = SwapOrderInfo()
            order.tx_hash = tx_hash
            order.vout = vout
            order.height = height
            order.timestamp = timestamp
            
            if version in (2, 3):
                return self._parse_rswp_v2(chunks, order, version)
            elif version == 1:
                return self._parse_rswp_v1(chunks, order)
            else:
                # Unknown version: skip, never misparse (forward incompatibility
                # is by design — see the v3 coordination note in Photonic's
                # buildSwapAdvertisementScript).
                return None
                
        except Exception as e:
            self.logger.debug(f'RSWP parse error: {e}')
            _swap_parse_errors.inc()  # R20
            return None
    
    def _parse_rswp_v2(self, chunks: List[bytes], order: SwapOrderInfo,
                       version: int = 2) -> Optional[SwapOrderInfo]:
        """
        Parse RSWP v2/v3 format (extended).

        v2: <"RSWP"> <0x02> <flags> <offeredType> <0x01> <tokenID·rev>
            [wantTokenID·rev] <utxoHash·rev> <utxoIndex> <priceTerms> <signature>
        v3 (Photonic swap-offer expiry): identical, but version byte 0x03 and an
            optional <expiryHeight: 4-byte LE> between the want id and the
            outpoint when flags & 0x02.

        The want push is OMITTED by canonical encoders when the wanted asset is
        native RXD (the zero token id) — an absent want therefore means RXD,
        not "no pair": quote_ref defaults to the zero ref so token/RXD orders
        land in the orderbook pair index.
        """
        FLAG_HAS_WANT = 0x01
        FLAG_HAS_EXPIRY = 0x02

        # Minimum chunks: RSWP(1) + ver(2) + flags(3) + offeredType(4) + termsType(5) + tokenID(6) + utxoHash(7) + utxoIndex(8) + terms(9) + sig(10)
        if len(chunks) < 10:
            return None

        idx = 3  # Start after version

        # Flags
        if len(chunks[idx]) != 1:
            return None
        flags = chunks[idx][0]
        idx += 1

        # Expiry is a v3 field; a v2 ad carrying the flag is malformed.
        if flags & FLAG_HAS_EXPIRY and version != 3:
            return None

        # Offered type
        if len(chunks[idx]) != 1:
            return None
        offered_type = chunks[idx][0]
        idx += 1

        # Constant 0x01 marker (Photonic `buildSwapAdvertisementScript` emits a
        # literal 0x01 push here — it is NOT a terms-type selector. The actual
        # terms live in the priceTerms MultiTxOutV1 blob below.)
        if len(chunks[idx]) != 1:
            return None
        idx += 1

        # Token ID (32 bytes)
        if len(chunks[idx]) != 32:
            return None
        token_id = chunks[idx]
        idx += 1

        # Want Token ID (32 bytes, optional based on flag)
        want_token_id = None
        if flags & FLAG_HAS_WANT:
            if idx >= len(chunks) or len(chunks[idx]) != 32:
                return None
            want_token_id = chunks[idx]
            idx += 1

        # Expiry height (v3, optional based on flag): absolute block height,
        # 4-byte little-endian, between the want id and the outpoint.
        expiry_height = 0
        if flags & FLAG_HAS_EXPIRY:
            if idx >= len(chunks) or len(chunks[idx]) != 4:
                return None
            expiry_height = struct.unpack('<I', chunks[idx])[0]
            idx += 1

        # UTXO Hash (32 bytes)
        if idx >= len(chunks) or len(chunks[idx]) != 32:
            return None
        utxo_hash = chunks[idx]
        idx += 1
        
        # UTXO Index (variable length integer)
        if idx >= len(chunks):
            return None
        utxo_index_data = chunks[idx]
        utxo_index = self._parse_script_int(utxo_index_data)
        if utxo_index is None:
            return None
        idx += 1
        
        # Remaining chunks are price terms (all but last) and signature (last)
        if idx >= len(chunks):
            return None
        
        remaining = chunks[idx:]
        if len(remaining) < 2:
            return None

        # Layout after utxoIndex is exactly: <priceTerms push> <signature push>.
        price_terms_blob = remaining[0]
        signature = remaining[-1]

        # Build order
        order.order_id = utxo_hash + struct.pack('<I', utxo_index)
        offered_ref = token_id + struct.pack('<I', 0)
        # Absent want push == native RXD (canonical encoders omit the zero id),
        # so default the want to the zero ref: explicit-zero-want and
        # omitted-want ads converge on the same orderbook pair key.
        want_ref = (want_token_id or b'\x00' * 32) + struct.pack('<I', 0)
        order.expiry_height = expiry_height
        # offeredType is Photonic's ContractType (RXD=0, NFT=1, FT=2, VAULT=3).
        # Offering RXD is a bid FOR the want token (BUY); offering any token is
        # an ask of that token (SELL).  The previous `== 1` test misfiled every
        # FT listing as a buy, putting fungible-token sells on the wrong side of
        # the orderbook.  Orders indexed before this fix keep their stored side
        # until a reindex (close-path key reconstruction uses the persisted
        # side, so no live migration is needed for correctness).
        order.side = OrderSide.BUY if offered_type == 0 else OrderSide.SELL
        # Pair orientation: the orderbook is keyed token-as-base / counter-
        # asset-as-quote, so a bid and its matching asks share one
        # OPEN_BY_PAIR book.  A SELL offers the base token (wanting the
        # quote); a BUY offers the quote (RXD) and WANTS the base token, so
        # for BUY orders base_ref comes from the want side.  Keying a bid on
        # its offered (zero) ref instead put it in a (zero, token) book that
        # no orderbook query ever scanned: get_orderbook(token, rxd) returned
        # empty bids[] while the matching asks listed fine.
        if order.side == OrderSide.BUY:
            order.base_ref = want_ref
            order.quote_ref = offered_ref
        else:
            order.base_ref = offered_ref
            order.quote_ref = want_ref
        order.status = OrderStatus.OPEN

        # priceTerms is a MultiTxOutV1 blob: the exact payout outputs a taker
        # must create to fill the order. Decode it for the requested amount and
        # the resolvable maker (recipient of the payout).
        self._apply_price_terms(price_terms_blob, order)

        return order

    def _parse_multi_txout(self, blob: bytes):
        """Decode an RSWP MultiTxOutV1 priceTerms blob (see parse_multi_txout)."""
        return parse_multi_txout(blob)

    def _maker_from_script(self, script: bytes):
        """Resolve a payout script to (scripthash, address) (see maker_from_script)."""
        return maker_from_script(script, getattr(self.env, 'coin', None))

    def _apply_price_terms(self, price_terms_blob: bytes, order: SwapOrderInfo):
        """Populate amount / price / maker from the priceTerms MultiTxOutV1 blob.

        RSWP orders are fixed-payment swaps (offer this UTXO, pay me these exact
        outputs), so there is no separate per-unit price: ``amount`` is the total
        requested payout and ``price`` mirrors it. The maker is the recipient.
        """
        outputs = self._parse_multi_txout(price_terms_blob)
        if not outputs:
            return
        total = sum(v for v, _ in outputs)
        order.amount = total
        order.remaining_amount = total
        order.price = total
        # Maker = recipient of the first standard payout output.
        for _, out_script in outputs:
            sh, addr = self._maker_from_script(out_script)
            if sh:
                order.maker_scripthash = sh
                order.maker_address = addr
                break
    
    def _parse_rswp_v1(self, chunks: List[bytes], order: SwapOrderInfo) -> Optional[SwapOrderInfo]:
        """
        Parse RSWP v1 format (legacy).
        
        Format: <"RSWP"> <version=1> <type> <tokenID> <utxoHash> <utxoIndex> <priceTerms> <signature>
        """
        # Minimum chunks: RSWP(1) + ver(2) + type(3) + tokenID(4) + utxoHash(5) + utxoIndex(6) + terms(7) + sig(8)
        if len(chunks) < 9:
            return None
        
        idx = 3  # Start after version
        
        # Type (legacy field)
        if len(chunks[idx]) != 1:
            return None
        legacy_type = chunks[idx][0]
        idx += 1
        
        # Token ID (32 bytes)
        if len(chunks[idx]) != 32:
            return None
        token_id = chunks[idx]
        idx += 1
        
        # UTXO Hash (32 bytes)
        if len(chunks[idx]) != 32:
            return None
        utxo_hash = chunks[idx]
        idx += 1
        
        # UTXO Index
        utxo_index_data = chunks[idx]
        utxo_index = self._parse_script_int(utxo_index_data)
        if utxo_index is None:
            return None
        idx += 1
        
        # Price terms
        if idx >= len(chunks):
            return None
        price_terms = chunks[idx]
        idx += 1
        
        # Signature
        if idx >= len(chunks):
            return None
        signature = chunks[idx]
        
        # Build order
        order.order_id = utxo_hash + struct.pack('<I', utxo_index)
        order.base_ref = token_id + struct.pack('<I', 0)
        order.side = OrderSide.SELL  # v1 defaults to sell
        order.status = OrderStatus.OPEN
        
        return order
    
    def _parse_script_chunks(self, script: bytes) -> List[bytes]:
        """Parse a Bitcoin script into data chunks."""
        chunks = []
        pos = 0
        
        while pos < len(script):
            opcode = script[pos]
            pos += 1
            
            # OP_RETURN or other non-push opcodes
            if opcode == OpCodes.OP_RETURN or opcode == OpCodes.OP_0:
                chunks.append(bytes([opcode]))
                continue
            
            # Direct push (1-75 bytes)
            if opcode >= 1 and opcode <= 75:
                length = opcode
                if pos + length <= len(script):
                    chunks.append(script[pos:pos + length])
                    pos += length
                else:
                    break
            # OP_PUSHDATA1
            elif opcode == 0x4c:
                if pos < len(script):
                    length = script[pos]
                    pos += 1
                    if pos + length <= len(script):
                        chunks.append(script[pos:pos + length])
                        pos += length
                    else:
                        break
                else:
                    break
            # OP_PUSHDATA2
            elif opcode == 0x4d:
                if pos + 2 <= len(script):
                    length = struct.unpack('<H', script[pos:pos + 2])[0]
                    pos += 2
                    if pos + length <= len(script):
                        chunks.append(script[pos:pos + length])
                        pos += length
                    else:
                        break
                else:
                    break
            # OP_PUSHDATA4
            elif opcode == 0x4e:
                if pos + 4 <= len(script):
                    length = struct.unpack('<I', script[pos:pos + 4])[0]
                    pos += 4
                    if pos + length <= len(script):
                        chunks.append(script[pos:pos + length])
                        pos += length
                    else:
                        break
                else:
                    break
            # OP_0 through OP_16 (push small integers)
            elif opcode == 0x00:  # OP_0
                chunks.append(b'')
            elif opcode >= 0x51 and opcode <= 0x60:  # OP_1 through OP_16
                chunks.append(bytes([opcode - 0x50]))
            else:
                # Other opcode, store as-is
                chunks.append(bytes([opcode]))
        
        return chunks
    
    def _parse_script_int(self, data: bytes) -> Optional[int]:
        """Parse a script integer (variable length, little-endian)."""
        if not data:
            return 0
        if len(data) == 1:
            return data[0]
        if len(data) == 2:
            return struct.unpack('<H', data)[0]
        if len(data) <= 4:
            padded = data + b'\x00' * (4 - len(data))
            return struct.unpack('<I', padded)[0]
        return None

    def memory_estimate(self) -> int:
        '''Approximate bytes held by unflushed in-memory caches.

        Used by block_processor.check_cache_size() to trigger a flush before
        these caches grow large enough to OOM the process.
        '''
        if not self.enabled:
            return 0
        undo_entries = sum(len(v) for v in self._undo_cache.values())
        return (
            len(self.order_cache) * 350
            + len(self.order_height) * 140
            + len(self.stats_cache) * 250
            + len(self.history_cache) * 250
            + undo_entries * 120
        )

    def flush(self, batch):
        """Flush cached swap data to the database."""
        if not self.enabled:
            return
        # Important: record undo entries for keys touched during this flush
        # first, then persist undo records at the end.

        self._prune_old_undo_keys(batch)

        # Flush orders and associated indexes
        for order_id, order in self.order_cache.items():
            height = self.order_height.get(order_id)
            if height is None:
                continue

            # Build every value that can raise on a malformed order BEFORE
            # touching the batch.  order.price/side come from attacker-controlled
            # OP_RETURN payloads; an out-of-range price makes struct.pack('>Q')
            # raise struct.error, which inside the shared write_batch would abort
            # the entire flush (UTXO + glyph + wave + state).  Compute keys first,
            # then commit — so a bad order is logged and skipped, not fatal, and
            # leaves no partial writes.  (Orders are already bounds-checked at
            # ingest in process_tx; this is defense-in-depth.)
            #
            # A closed order reconstructs the SAME pair_key/maker_key it was
            # written with (same base_ref/quote_ref/side/price/maker), so the
            # delete targets exactly the bytes the open path put — the bounds
            # invariant still holds and no phantom index key can be left behind.
            terminal = order.status in (OrderStatus.FILLED,
                                        OrderStatus.CANCELLED,
                                        OrderStatus.EXPIRED)
            try:
                order_bytes = order.to_bytes()
                # The orderbook key is only valid for live orders; for a closed
                # order we still need the exact same key to delete it.
                pair_key = self._pair_key(order)
                maker_key = self._maker_key(order)
            except Exception:
                self.logger.warning(
                    'Skipping malformed swap order %s during flush',
                    hash_to_hex_str(order_id), exc_info=True
                )
                continue

            order_key = SwapDBKeys.ORDER + order_id
            # Always rewrite the ORDER record (open/partial -> live row; terminal
            # -> closed row).  Record undo first so reorg backup() restores the
            # previous on-disk ORDER value (e.g. the OPEN cbor) at this height.
            self._record_undo(height, order_key)
            batch.put(order_key, order_bytes)

            if terminal:
                # Close path: remove the orderbook + maker index entries so the
                # order stops appearing as open.  Every key goes through
                # _record_undo at the CLOSE height so backup() restores the
                # OPEN_BY_* entry (its prior b'' value) and reopens the order.
                if pair_key is not None:
                    self._record_undo(height, pair_key)
                    batch.delete(pair_key)
                if maker_key is not None:
                    self._record_undo(height, maker_key)
                    batch.delete(maker_key)
            else:
                # Open/partial path: (re)publish the orderbook + maker indexes.
                if pair_key is not None:
                    self._record_undo(height, pair_key)
                    batch.put(pair_key, b'')
                if maker_key is not None:
                    self._record_undo(height, maker_key)
                    batch.put(maker_key, b'')

        # Flush history
        for height, key, value in self.history_cache:
            self._record_undo(height, key)
            batch.put(key, value)

        # Persist undo information last so it includes keys written above.
        for height, entries in sorted(self._undo_cache.items()):
            batch.put(self._undo_key(height), encode_undo(entries))  # R22
        self._undo_cache.clear()
        self._undo_seen.clear()

        # Clear caches
        self.order_cache.clear()
        self.order_height.clear()
        self.history_cache.clear()
    
    # ========================================================================
    # Query Methods (API)
    # ========================================================================
    
    def get_order(self, order_id: bytes) -> Optional[SwapOrderInfo]:
        """Get order info by ID."""
        if order_id in self.order_cache:
            return self.order_cache[order_id]
        
        key = SwapDBKeys.ORDER + order_id
        data = self.db.utxo_db.get(key)
        if data:
            return SwapOrderInfo.from_bytes(data)
        return None
    
    def _is_expired(self, order) -> bool:
        """AUDIT-FIX: a v3 order with an absolute expiry_height that the chain has reached is no
        longer fillable (the maker's pre-signed order is stale). The OrderStatus.EXPIRED enum was
        never assigned, so without this an expired order lingered in the open book and a taker could
        be steered to fill it. Excluded from every open-order read path below."""
        eh = getattr(order, 'expiry_height', 0)
        return bool(eh) and eh <= getattr(self.db, 'db_height', 0)

    def get_orderbook(self, base_ref: bytes, quote_ref: bytes,
                      side: int = None, limit: int = 50) -> Dict[str, List[Dict]]:
        """
        Get orderbook for a trading pair.
        
        Returns bids and asks sorted by price.
        """
        bids = []
        asks = []
        
        # Get asks (sells) - lowest price first
        if side is None or side == OrderSide.SELL:
            prefix = SwapDBKeys.OPEN_BY_PAIR + base_ref + quote_ref + bytes([OrderSide.SELL])
            for key, _ in self.db.utxo_db.iterator(prefix=prefix):
                if len(asks) >= limit:
                    break
                order_id = key[-36:]  # Last 36 bytes is order_id
                order = self.get_order(order_id)
                if order and order.status in (OrderStatus.OPEN, OrderStatus.PARTIAL) and not self._is_expired(order):
                    asks.append(self._order_to_dict(order))
        
        # Get bids (buys) - highest price first (inverted in key)
        if side is None or side == OrderSide.BUY:
            prefix = SwapDBKeys.OPEN_BY_PAIR + base_ref + quote_ref + bytes([OrderSide.BUY])
            for key, _ in self.db.utxo_db.iterator(prefix=prefix):
                if len(bids) >= limit:
                    break
                order_id = key[-36:]
                order = self.get_order(order_id)
                if order and order.status in (OrderStatus.OPEN, OrderStatus.PARTIAL) and not self._is_expired(order):
                    bids.append(self._order_to_dict(order))
        
        return {'bids': bids, 'asks': asks}
    
    def get_open_orders(self, base_ref: bytes = None, limit: int = 100,
                        offset: int = 0,
                        cursor: Optional[str] = None,
                        _use_cursor: bool = False):
        """Get open orders, optionally filtered by base token.

        Legacy shape (``_use_cursor=False``): plain ``List[Dict]``.
        Cursor shape (``_use_cursor=True``):
        ``{entries, next_cursor, has_more}`` with a stable seek-key cursor.
        See docs/pagination-cursors.md.
        """
        if base_ref:
            prefix = SwapDBKeys.OPEN_BY_PAIR + base_ref
        else:
            prefix = SwapDBKeys.OPEN_BY_PAIR

        if _use_cursor:
            entries = []
            seek = _decode_cursor(cursor) or prefix
            next_cursor = None
            for key, _ in self.db.utxo_db.iterator(prefix=prefix, seek=seek):
                if len(entries) >= limit:
                    next_cursor = _encode_cursor(key)
                    break
                order_id = key[-36:]
                order = self.get_order(order_id)
                if order and order.status in (OrderStatus.OPEN, OrderStatus.PARTIAL) and not self._is_expired(order):
                    entries.append(self._order_to_dict(order))
            return {
                'entries': entries,
                'next_cursor': next_cursor,
                'has_more': next_cursor is not None,
            }

        results = []
        count = 0
        for key, _ in self.db.utxo_db.iterator(prefix=prefix):
            if count < offset:
                count += 1
                continue
            if len(results) >= limit:
                break

            order_id = key[-36:]
            order = self.get_order(order_id)
            if order and order.status in (OrderStatus.OPEN, OrderStatus.PARTIAL) and not self._is_expired(order):
                results.append(self._order_to_dict(order))
            count += 1

        return results
    
    def get_user_orders(self, scripthash: bytes, status: int = None,
                        limit: int = 100) -> List[Dict]:
        """Get orders for a specific user."""
        results = []
        prefix = SwapDBKeys.OPEN_BY_MAKER + scripthash
        
        for key, _ in self.db.utxo_db.iterator(prefix=prefix):
            if len(results) >= limit:
                break
            
            order_id = key[len(prefix):]
            order = self.get_order(order_id)
            if order:
                if status is None or order.status == status:
                    results.append(self._order_to_dict(order))
        
        return results
    
    def get_swap_history(self, base_ref: bytes, limit: int = 100,
                         offset: int = 0,
                         cursor: Optional[str] = None,
                         _use_cursor: bool = False):
        """Get trade history for a token (newest-first).

        Legacy shape (``_use_cursor=False``): plain ``List[Dict]``.
        Cursor shape (``_use_cursor=True``):
        ``{entries, next_cursor, has_more}``. The cursor encodes the
        next-unread key in the reverse scan; pagination is stable under
        new history rows landing during the walk.
        See docs/pagination-cursors.md.
        """
        prefix = SwapDBKeys.HISTORY + base_ref

        if _use_cursor:
            entries = []
            seek = _decode_cursor(cursor)
            it_kwargs = {'prefix': prefix, 'reverse': True}
            if seek is not None:
                it_kwargs['seek'] = seek
            next_cursor = None
            for key, value in self.db.utxo_db.iterator(**it_kwargs):
                if len(entries) >= limit:
                    next_cursor = _encode_cursor(key)
                    break
                if HAS_CBOR:
                    try:
                        entries.append(cbor2.loads(value))
                    except Exception:
                        pass
            return {
                'entries': entries,
                'next_cursor': next_cursor,
                'has_more': next_cursor is not None,
            }

        results = []
        count = 0
        for key, value in self.db.utxo_db.iterator(prefix=prefix, reverse=True):
            if count < offset:
                count += 1
                continue
            if len(results) >= limit:
                break

            if HAS_CBOR:
                try:
                    entry = cbor2.loads(value)
                    results.append(entry)
                except Exception:
                    pass
            count += 1

        return results
    
    def get_swap_count(self, base_ref: bytes) -> int:
        """Get total swap count for a token."""
        count = 0
        prefix = SwapDBKeys.HISTORY + base_ref
        
        for _ in self.db.utxo_db.iterator(prefix=prefix, include_value=False):
            count += 1
        
        return count
    
    def get_pair_stats(self, base_ref: bytes, quote_ref: bytes) -> Optional[Dict]:
        """Get trading statistics for a pair."""
        key = SwapDBKeys.STATS + base_ref + quote_ref
        data = self.db.utxo_db.get(key)
        
        if data and HAS_CBOR:
            try:
                stats = cbor2.loads(data)
                return {
                    'base_ref': hash_to_hex_str(base_ref[:32]) + '_' + str(struct.unpack('<I', base_ref[32:36])[0]),
                    'quote_ref': hash_to_hex_str(quote_ref[:32]) + '_' + str(struct.unpack('<I', quote_ref[32:36])[0]),
                    'last_price': stats.get('lp', 0),
                    'high_24h': stats.get('h24', 0),
                    'low_24h': stats.get('l24', 0),
                    'volume_24h_base': stats.get('vb24', 0),
                    'volume_24h_quote': stats.get('vq24', 0),
                    'trade_count_24h': stats.get('tc24', 0),
                    'open_orders': stats.get('oo', 0),
                    'bid_depth': stats.get('bd', 0),
                    'ask_depth': stats.get('ad', 0),
                }
            except Exception:
                pass
        return None
    
    def _order_to_dict(self, order: SwapOrderInfo) -> Dict[str, Any]:
        """Convert order info to API dict."""
        return {
            'order_id': hash_to_hex_str(order.order_id) if order.order_id else None,
            'tx_hash': hash_to_hex_str(order.tx_hash) if order.tx_hash else None,
            'vout': order.vout,
            'height': order.height,
            'timestamp': order.timestamp,
            'maker_scripthash': order.maker_scripthash.hex() if order.maker_scripthash else None,
            'maker_address': order.maker_address,
            'base_ref': self._format_ref(order.base_ref),
            'quote_ref': self._format_ref(order.quote_ref),
            'base_ticker': order.base_ticker,
            'quote_ticker': order.quote_ticker,
            'side': 'buy' if order.side == OrderSide.BUY else 'sell',
            'price': order.price,
            'amount': order.amount,
            'filled_amount': order.filled_amount,
            'remaining_amount': order.remaining_amount,
            'percent_filled': (order.filled_amount / order.amount * 100) if order.amount > 0 else 0,
            'min_fill': order.min_fill,
            'fee_rate': order.fee_rate,
            # REDTEAM-FIX: reflect v3 expiry in EVERY read path (direct get / user-orders / REST),
            # not just the orderbook scans — an order whose expiry_height the chain has reached
            # reports 'expired', so a client never treats a stale order as fillable.
            'status': ('expired' if (order.status in (OrderStatus.OPEN, OrderStatus.PARTIAL)
                                     and self._is_expired(order))
                       else self._status_name(order.status)),
            'expiry_height': order.expiry_height if order.expiry_height > 0 else None,
            'fill_count': order.fill_count,
            'avg_fill_price': order.avg_fill_price,
        }
    
    @staticmethod
    def _format_ref(ref: bytes) -> Optional[str]:
        """Format a ref bytes to string."""
        if not ref or len(ref) < 36:
            return None
        txid = ref[:32]
        vout = struct.unpack('<I', ref[32:36])[0]
        return hash_to_hex_str(txid) + '_' + str(vout)
    
    @staticmethod
    def _status_name(status: int) -> str:
        """Get status name from ID."""
        names = {
            OrderStatus.OPEN: 'open',
            OrderStatus.PARTIAL: 'partial',
            OrderStatus.FILLED: 'filled',
            OrderStatus.CANCELLED: 'cancelled',
            OrderStatus.EXPIRED: 'expired',
        }
        return names.get(status, 'unknown')


# RPC registration note: swap.* electrum methods are registered in
# electrumx/server/glyph_api.py (GLYPH_METHODS), NOT here.  The orderbook
# query is `swap.get_orders(base_ref, quote_ref)` — with both refs supplied it
# returns the {bids, asks} orderbook via SwapIndex.get_orderbook(); with fewer
# it lists open orders.  (An older SWAP_METHODS dict here advertised method
# names like swap.get_orderbook that were never wired to any session — it was
# dead and misleading, so it was removed.)
