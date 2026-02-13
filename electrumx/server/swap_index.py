"""
Swap Index for RXinDexer

This module provides database storage and indexing for on-chain swap
advertisements (RSWP protocol). Tracks open orders, filled orders,
and swap history.

Designed to serve explorers, wallets, DEX interfaces, and market data APIs.
"""

import ast
import struct
import time
from typing import Optional, Dict, Any, List, Tuple
from collections import defaultdict

from electrumx.lib import util
from electrumx.lib.hash import hash_to_hex_str, hex_str_to_hash, sha256
from electrumx.lib.util import pack_be_uint32
from electrumx.lib.script import OpCodes

try:
    import cbor2
    HAS_CBOR = True
except ImportError:
    HAS_CBOR = False


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
                   glyph_envelope: Dict[str, Any] = None):
        """
        Process a transaction for swap orders.
        
        Detects RSWP protocol advertisements in OP_RETURN outputs.
        Format based on Radiant-Core swapindex.cpp implementation.
        """
        if not self.enabled:
            return
        
        timestamp = int(time.time())
        
        for vout_idx, txout in enumerate(tx.outputs):
            script = txout.pk_script
            
            # Check for OP_RETURN
            if not script or script[0] != OpCodes.OP_RETURN:
                continue
            
            # Parse RSWP advertisement
            order = self._parse_rswp_advertisement(script, tx_hash, vout_idx, height, timestamp)
            if order:
                self.order_cache[order.order_id] = order
                self.order_height[order.order_id] = height
                self.logger.debug(f'Indexed swap order: {hash_to_hex_str(order.order_id)}')

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
        entries = ast.literal_eval(raw.decode())
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
            
            if version == 2:
                return self._parse_rswp_v2(chunks, order)
            elif version == 1:
                return self._parse_rswp_v1(chunks, order)
            else:
                return None
                
        except Exception as e:
            self.logger.debug(f'RSWP parse error: {e}')
            return None
    
    def _parse_rswp_v2(self, chunks: List[bytes], order: SwapOrderInfo) -> Optional[SwapOrderInfo]:
        """
        Parse RSWP v2 format (extended).
        
        Format: <"RSWP"> <version=2> <flags> <offeredType> <termsType> <tokenID> [wantTokenID] <utxoHash> <utxoIndex> <priceTerms...> <signature>
        """
        FLAG_HAS_WANT = 0x01
        
        # Minimum chunks: RSWP(1) + ver(2) + flags(3) + offeredType(4) + termsType(5) + tokenID(6) + utxoHash(7) + utxoIndex(8) + terms(9) + sig(10)
        if len(chunks) < 10:
            return None
        
        idx = 3  # Start after version
        
        # Flags
        if len(chunks[idx]) != 1:
            return None
        flags = chunks[idx][0]
        idx += 1
        
        # Offered type
        if len(chunks[idx]) != 1:
            return None
        offered_type = chunks[idx][0]
        idx += 1
        
        # Terms type
        if len(chunks[idx]) != 1:
            return None
        terms_type = chunks[idx][0]
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
        
        # Signature is last push
        signature = remaining[-1]
        
        # Price term pushes are all pushes before the signature
        price_term_chunks = remaining[:-1]
        
        # Build order
        order.order_id = utxo_hash + struct.pack('<I', utxo_index)
        order.base_ref = token_id + struct.pack('<I', 0)  # Token ref
        if want_token_id:
            order.quote_ref = want_token_id + struct.pack('<I', 0)
        order.side = OrderSide.SELL if offered_type == 1 else OrderSide.BUY
        order.status = OrderStatus.OPEN
        
        # Parse price/amount from price_term_chunks based on termsType
        self._parse_price_terms(terms_type, price_term_chunks, order)
        
        return order
    
    def _parse_price_terms(self, terms_type: int, term_chunks: List[bytes],
                           order: SwapOrderInfo):
        """
        Parse price terms from RSWP advertisement.
        
        termsType 0 — Fixed price:
          term_chunks = [<price>, <amount>]
          price and amount are script-encoded integers.
          
        termsType 1 — Rate (ratio):
          term_chunks = [<numerator>, <denominator>, <amount>]
          Effective price = numerator / denominator (scaled to 10^8).
          
        termsType 2 — Fixed price + min fill:
          term_chunks = [<price>, <amount>, <min_fill>]
        """
        try:
            if terms_type == 0:
                # Fixed price: [price, amount]
                if len(term_chunks) >= 1:
                    order.price = self._parse_script_int(term_chunks[0]) or 0
                if len(term_chunks) >= 2:
                    order.amount = self._parse_script_int(term_chunks[1]) or 0
                    order.remaining_amount = order.amount
            elif terms_type == 1:
                # Rate: [numerator, denominator, amount]
                numerator = 0
                denominator = 1
                if len(term_chunks) >= 1:
                    numerator = self._parse_script_int(term_chunks[0]) or 0
                if len(term_chunks) >= 2:
                    denominator = self._parse_script_int(term_chunks[1]) or 1
                    if denominator == 0:
                        denominator = 1
                if len(term_chunks) >= 3:
                    order.amount = self._parse_script_int(term_chunks[2]) or 0
                    order.remaining_amount = order.amount
                # Convert rate to scaled price (10^8 precision)
                order.price = int((numerator * 10**8) / denominator)
            elif terms_type == 2:
                # Fixed price + min fill: [price, amount, min_fill]
                if len(term_chunks) >= 1:
                    order.price = self._parse_script_int(term_chunks[0]) or 0
                if len(term_chunks) >= 2:
                    order.amount = self._parse_script_int(term_chunks[1]) or 0
                    order.remaining_amount = order.amount
                if len(term_chunks) >= 3:
                    order.min_fill = self._parse_script_int(term_chunks[2]) or 0
            else:
                # Unknown terms type — store raw concatenated bytes as price
                # for forward compatibility
                raw = b''.join(term_chunks)
                if raw:
                    order.price = self._parse_script_int(raw) or 0
        except Exception:
            pass
    
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

            order_key = SwapDBKeys.ORDER + order_id
            self._record_undo(height, order_key)
            batch.put(order_key, order.to_bytes())

            # OPEN_BY_PAIR orderbook index
            if order.base_ref and order.quote_ref and order.status in (OrderStatus.OPEN, OrderStatus.PARTIAL):
                if order.side == OrderSide.BUY:
                    price_key = struct.pack('>Q', 0xFFFFFFFFFFFFFFFF - order.price)
                else:
                    price_key = struct.pack('>Q', order.price)

                pair_key = (
                    SwapDBKeys.OPEN_BY_PAIR
                    + order.base_ref
                    + order.quote_ref
                    + bytes([order.side])
                    + price_key
                    + order_id
                )
                self._record_undo(height, pair_key)
                batch.put(pair_key, b'')

            # OPEN_BY_MAKER user orders index
            if order.maker_scripthash:
                maker_key = SwapDBKeys.OPEN_BY_MAKER + order.maker_scripthash + order_id
                self._record_undo(height, maker_key)
                batch.put(maker_key, b'')

        # Flush history
        for height, key, value in self.history_cache:
            self._record_undo(height, key)
            batch.put(key, value)

        # Persist undo information last so it includes keys written above.
        for height, entries in sorted(self._undo_cache.items()):
            batch.put(self._undo_key(height), repr(entries).encode())
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
                if order and order.status in (OrderStatus.OPEN, OrderStatus.PARTIAL):
                    asks.append(self._order_to_dict(order))
        
        # Get bids (buys) - highest price first (inverted in key)
        if side is None or side == OrderSide.BUY:
            prefix = SwapDBKeys.OPEN_BY_PAIR + base_ref + quote_ref + bytes([OrderSide.BUY])
            for key, _ in self.db.utxo_db.iterator(prefix=prefix):
                if len(bids) >= limit:
                    break
                order_id = key[-36:]
                order = self.get_order(order_id)
                if order and order.status in (OrderStatus.OPEN, OrderStatus.PARTIAL):
                    bids.append(self._order_to_dict(order))
        
        return {'bids': bids, 'asks': asks}
    
    def get_open_orders(self, base_ref: bytes = None, limit: int = 100,
                        offset: int = 0) -> List[Dict]:
        """Get open orders, optionally filtered by base token."""
        results = []
        count = 0
        
        if base_ref:
            prefix = SwapDBKeys.OPEN_BY_PAIR + base_ref
        else:
            prefix = SwapDBKeys.OPEN_BY_PAIR
        
        for key, _ in self.db.utxo_db.iterator(prefix=prefix):
            if count < offset:
                count += 1
                continue
            if len(results) >= limit:
                break
            
            order_id = key[-36:]
            order = self.get_order(order_id)
            if order and order.status in (OrderStatus.OPEN, OrderStatus.PARTIAL):
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
                         offset: int = 0) -> List[Dict]:
        """Get trade history for a token."""
        results = []
        prefix = SwapDBKeys.HISTORY + base_ref
        count = 0
        
        for key, value in self.db.utxo_db.iterator(prefix=prefix, reverse=True):
            if count < offset:
                count += 1
                continue
            if len(results) >= limit:
                break
            
            # Parse history entry
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
            'status': self._status_name(order.status),
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


# API Method Registration
SWAP_METHODS = {
    'swap.get_order': 'swap_get_order',
    'swap.get_orderbook': 'swap_get_orderbook',
    'swap.get_open_orders': 'swap_get_open_orders',
    'swap.get_user_orders': 'swap_get_user_orders',
    'swap.get_history': 'swap_get_history',
    'swap.get_count': 'swap_get_count',
    'swap.get_pair_stats': 'swap_get_pair_stats',
}
