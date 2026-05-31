"""
Mempool Glyph/Swap Index for RXinDexer

Extends the base mempool to track unconfirmed Glyph token transfers
and Swap orders. This provides real-time updates for wallets and DEX UIs.

Design decisions (per ARCHITECTURE.md):
- Index Glyph transfers in mempool: YES (wallets need unconfirmed balance)
- Index Swap orders in mempool: YES (DEX needs real-time orderbook)
- Index dMint reveals in mempool: NO (prevent gaming)
- Index WAVE claims in mempool: NO (prevent front-running)
"""

import struct
from typing import Dict, Set, List, Optional, Tuple, Any
from collections import defaultdict

from electrumx.lib import util
from electrumx.lib.hash import hash_to_hex_str, hex_str_to_hash, HASHX_LEN
from electrumx.lib.util import pack_le_uint32
from electrumx.lib.script import Script
from electrumx.lib.glyph import (
    contains_glyph_magic, parse_glyph_envelope, get_token_type,
    GlyphProtocol, is_dmint_reveal, is_wave_claim
)


class MempoolGlyphTx:
    """Represents an unconfirmed Glyph transaction."""
    __slots__ = ('tx_hash', 'token_ref', 'token_type', 'event_type',
                 'from_scripthash', 'to_scripthash', 'amount', 'fee', 'size',
                 'movements')

    def __init__(self):
        self.tx_hash = b''
        self.token_ref = b''
        self.token_type = 0
        self.event_type = 'transfer'  # transfer, mint, burn
        self.from_scripthash = None
        self.to_scripthash = None
        self.amount = 0
        self.fee = 0
        self.size = 0
        # Per-(base_hashX, ref) signed balance deltas this tx applies.
        # A transfer of one token yields a +value credit to the recipient's
        # base-address hashX and a -value debit from the sender's, both for
        # the same ref.  A tx can move several tokens, hence a list.
        self.movements: List[Tuple[bytes, bytes, int]] = []


class MempoolSwapOrder:
    """Represents an unconfirmed Swap order."""
    __slots__ = ('tx_hash', 'order_id', 'base_ref', 'quote_ref',
                 'side', 'price', 'amount', 'maker_scripthash', 'fee')
    
    def __init__(self):
        self.tx_hash = b''
        self.order_id = b''
        self.base_ref = b''
        self.quote_ref = b''
        self.side = 0  # 0=BUY, 1=SELL
        self.price = 0
        self.amount = 0
        self.maker_scripthash = b''
        self.fee = 0


class MempoolGlyphIndex:
    """
    Mempool extension for Glyph and Swap indexing.
    
    Maintains in-memory indexes of unconfirmed:
    - Token transfers (for wallet balance display)
    - Swap orders (for DEX orderbook)
    
    Does NOT index:
    - dMint reveals (wait for confirmation to prevent gaming)
    - WAVE claims (wait for confirmation to prevent front-running)
    """
    
    def __init__(self, env, glyph_index=None, swap_index=None):
        self.logger = util.class_logger(__name__, self.__class__.__name__)
        self.env = env
        self.glyph_index = glyph_index
        self.swap_index = swap_index
        # Coin class supplies hashX_from_script, exactly as the block
        # processor uses it for the confirmed ownership index.
        self.coin = getattr(env, 'coin', None)

        # Configuration
        self.glyph_enabled = getattr(env, 'mempool_glyph_index', True)
        self.swap_enabled = getattr(env, 'mempool_swap_index', True)
        
        # Glyph mempool indexes
        self.glyph_txs: Dict[bytes, MempoolGlyphTx] = {}
        self.glyph_by_ref: Dict[bytes, Set[bytes]] = defaultdict(set)  # ref -> tx_hashes
        self.glyph_by_scripthash: Dict[bytes, Set[bytes]] = defaultdict(set)  # scripthash -> tx_hashes
        
        # Swap mempool indexes
        self.swap_orders: Dict[bytes, MempoolSwapOrder] = {}
        self.swap_by_pair: Dict[bytes, Set[bytes]] = defaultdict(set)  # pair_key -> order_ids
        self.swap_by_maker: Dict[bytes, Set[bytes]] = defaultdict(set)  # scripthash -> order_ids
        
        # Touched tracking for notifications
        self.touched_refs: Set[bytes] = set()
        self.touched_scripthashes: Set[bytes] = set()
        # (base_hashX, ref) pairs whose unconfirmed balance changed since the
        # last dispatch.  Drained by the balance-subscription dispatcher so
        # glyph.subscribe.balance fires for the holder's base address.
        self.touched_balance: Set[Tuple[bytes, bytes]] = set()

        if self.glyph_enabled:
            self.logger.info('Mempool Glyph indexing enabled')
        if self.swap_enabled:
            self.logger.info('Mempool Swap indexing enabled')
    
    def process_mempool_tx(self, tx_hash: bytes, memtx) -> bool:
        """
        Process a mempool transaction (a ``MemPoolTx``) for Glyph/Swap content.

        ``memtx`` carries the data the base mempool already resolved:
          * ``prevouts``      -> tuple of (prev_hash, prev_idx) for inputs
          * ``in_pairs``      -> tuple of (hashX, value) for inputs (resolved)
          * ``out_pairs``     -> tuple of (hashX, value) for outputs
          * ``idx_to_script`` -> full locking script per output index

        Returns True if the transaction touched any indexed Glyph/Swap state.
        """
        found_glyph = False
        found_swap = False

        if self.glyph_enabled:
            found_glyph = self._process_glyph_memtx(tx_hash, memtx)

        if self.swap_enabled:
            found_swap = self._process_swap_memtx(tx_hash, memtx)

        return found_glyph or found_swap

    # ------------------------------------------------------------------
    # hashX helpers (mirror GlyphIndex / the block processor exactly)
    # ------------------------------------------------------------------

    @staticmethod
    def _to_hashX(scripthash: bytes) -> bytes:
        """Convert a 32-byte Electrum scripthash to the internal 11-byte hashX.

        Mirrors ``GlyphIndex._scripthash_to_hashX``: reverse to natural sha256
        order then take the first HASHX_LEN bytes.  A value already of hashX
        length (internal callers) is returned unchanged, so the conversion is
        idempotent.
        """
        if scripthash is None:
            return scripthash
        if len(scripthash) == 32:
            return scripthash[::-1][:HASHX_LEN]
        return scripthash[:HASHX_LEN]

    def _base_hashX(self, script: bytes) -> Optional[bytes]:
        """Base-address hashX of a (possibly ref-wrapped) locking script.

        Strips the Radiant ref preamble then hashes the underlying address
        exactly as the confirmed ownership index does
        (``coin.hashX_from_script(Script.base_locking_script(script))``), so a
        wallet's standard scripthash matches.  Returns None when no coin is
        available or the script is provably unspendable.
        """
        if self.coin is None:
            return None
        try:
            return self.coin.hashX_from_script(Script.base_locking_script(script))
        except Exception:
            return None

    def _is_known_token(self, ref: bytes) -> bool:
        """Whether ``ref`` is an already-indexed Glyph token.

        Delegates to the confirmed index so the mempool only tracks balances
        for real tokens (not arbitrary Radiant refs).  Without a confirmed
        index we cannot tell, so nothing is indexed.
        """
        gi = self.glyph_index
        if gi is None:
            return False
        try:
            return gi._is_known_token(ref)
        except Exception:
            return False

    def _utxo_db(self):
        """The serving RocksDB handle (via the confirmed index), or None."""
        gi = self.glyph_index
        db = getattr(gi, 'db', None) if gi is not None else None
        return getattr(db, 'utxo_db', None) if db is not None else None

    def _process_glyph_memtx(self, tx_hash: bytes, memtx) -> bool:
        """Index unconfirmed Glyph balance movements for a transaction.

        Mirrors the confirmed ownership index: a token output credits the
        recipient's base-address hashX by the output value, and spending a
        token input debits the sender's base-address hashX by the spent value.
        Token outpoints are recognised by their ref opcodes (so ordinary
        ref-carrying transfers are detected, not only envelope reveals) and
        filtered to known tokens via the confirmed index.

        Outputs that ship a dMint-reveal or WAVE-claim Glyph envelope are
        skipped (indexing those in mempool enables gaming / front-running).
        """
        idx_to_script = getattr(memtx, 'idx_to_script', None) or []
        out_pairs = getattr(memtx, 'out_pairs', None) or ()
        prevouts = getattr(memtx, 'prevouts', None) or ()
        in_pairs = getattr(memtx, 'in_pairs', None) or ()

        movements: List[Tuple[bytes, bytes, int]] = []

        # --- Credits: outputs carrying a known token ref ------------------
        for idx, script in enumerate(idx_to_script):
            if not script:
                continue
            # Do not index dMint reveals / WAVE claims in mempool.
            if contains_glyph_magic(script):
                envelope = parse_glyph_envelope(script)
                if envelope and (is_dmint_reveal(envelope) or is_wave_claim(envelope)):
                    continue
            try:
                all_refs, _, _ = Script.get_push_input_refs(script)
            except Exception:
                continue
            refs = Script.dedup_refs(all_refs)
            if not refs:
                continue
            value = out_pairs[idx][1] if idx < len(out_pairs) else 0
            base_hashX = None
            for ref in refs.keys():
                if len(ref) == 36 and self._is_known_token(ref):
                    if base_hashX is None:
                        base_hashX = self._base_hashX(script)
                    if base_hashX:
                        movements.append((base_hashX, ref, value))

        # --- Debits: inputs spending a known token outpoint ---------------
        udb = self._utxo_db()
        if udb is not None:
            for i, prevout in enumerate(prevouts):
                prev_hash, prev_idx = prevout
                outpoint = prev_hash + pack_le_uint32(prev_idx)
                spent_refs = udb.get(b'ri' + outpoint)
                if not spent_refs:
                    continue
                value = in_pairs[i][1] if i < len(in_pairs) else 0
                # The block processor persists the spent output's base-address
                # hashX (b'rb' + outpoint); fall back to the output's own hashX
                # (a token output created before the b'rb' map existed).
                base_hashX = udb.get(b'rb' + outpoint)
                if not base_hashX:
                    base_hashX = in_pairs[i][0] if i < len(in_pairs) else None
                if not base_hashX:
                    continue
                seen = set()
                for j in range(0, len(spent_refs), 37):
                    ref = spent_refs[j:j + 36]
                    if len(ref) == 36 and ref not in seen and self._is_known_token(ref):
                        seen.add(ref)
                        movements.append((base_hashX, ref, -value))

        if not movements:
            return False

        self._record_movements(tx_hash, movements)
        return True

    def _record_movements(self, tx_hash: bytes,
                          movements: List[Tuple[bytes, bytes, int]]):
        """Store balance movements for a tx and update the lookup indexes."""
        glyph_tx = MempoolGlyphTx()
        glyph_tx.tx_hash = tx_hash
        glyph_tx.event_type = 'transfer'
        glyph_tx.movements = movements

        # Representative fields for the listing APIs: prefer the first credit.
        primary = next((m for m in movements if m[2] > 0), movements[0])
        glyph_tx.token_ref = primary[1]
        glyph_tx.amount = abs(primary[2])
        glyph_tx.to_scripthash = next((hx for hx, _r, d in movements if d > 0), None)
        glyph_tx.from_scripthash = next((hx for hx, _r, d in movements if d < 0), None)

        self.glyph_txs[tx_hash] = glyph_tx
        for hashX, ref, _delta in movements:
            self.glyph_by_scripthash[hashX].add(tx_hash)
            self.glyph_by_ref[ref].add(tx_hash)
            self.touched_scripthashes.add(hashX)
            self.touched_refs.add(ref)
            self.touched_balance.add((hashX, ref))

    def _process_swap_memtx(self, tx_hash: bytes, memtx) -> bool:
        """
        Process transaction for Swap orders (RSWP protocol).

        Detects RSWP advertisements in OP_RETURN outputs.
        Note: Unlike confirmed orders, mempool orders are tentative.
        """
        found = False

        for idx, script in enumerate(getattr(memtx, 'idx_to_script', None) or []):
            # Check for OP_RETURN (0x6a)
            if not script or script[0] != 0x6a:
                continue

            # Parse for RSWP marker
            order = self._parse_rswp_mempool(script, tx_hash, idx)
            if order:
                self.swap_orders[tx_hash] = order

                # Index by pair
                pair_key = order.base_ref + order.quote_ref
                self.swap_by_pair[pair_key].add(order.order_id)

                # Index by maker
                if order.maker_scripthash:
                    self.swap_by_maker[order.maker_scripthash].add(order.order_id)

                self.logger.debug(f'Indexed mempool swap order: {hash_to_hex_str(tx_hash)}')
                found = True

        return found

    def _parse_rswp_mempool(self, script: bytes, tx_hash: bytes, vout: int) -> Optional[MempoolSwapOrder]:
        """
        Parse RSWP advertisement from OP_RETURN script.
        
        Protocol: OP_RETURN <"RSWP"> <version> ... <tokenID> ... <utxoHash> <utxoIndex> <terms> <sig>
        """
        try:
            chunks = self._parse_script_chunks(script)
            if len(chunks) < 3:
                return None
            
            # Check for RSWP marker (second chunk after OP_RETURN)
            if len(chunks) < 2 or chunks[1] != b'RSWP':
                return None
            
            # Parse version
            if len(chunks) < 3 or len(chunks[2]) != 1:
                return None
            version = chunks[2][0]
            
            order = MempoolSwapOrder()
            order.tx_hash = tx_hash
            
            if version == 2:
                # v2: flags(3) offeredType(4) termsType(5) tokenID(6) [wantTokenID] utxoHash utxoIndex terms sig
                if len(chunks) < 10:
                    return None
                
                idx = 3
                flags = chunks[idx][0] if len(chunks[idx]) == 1 else 0
                idx += 1
                
                offered_type = chunks[idx][0] if len(chunks[idx]) == 1 else 0
                idx += 2  # Skip termsType
                
                # Token ID (32 bytes)
                if len(chunks[idx]) != 32:
                    return None
                token_id = chunks[idx]
                idx += 1
                
                # Want token ID (optional)
                want_token_id = None
                if flags & 0x01:
                    if idx < len(chunks) and len(chunks[idx]) == 32:
                        want_token_id = chunks[idx]
                        idx += 1
                
                # UTXO Hash (32 bytes)
                if idx >= len(chunks) or len(chunks[idx]) != 32:
                    return None
                utxo_hash = chunks[idx]
                idx += 1
                
                # UTXO Index
                if idx >= len(chunks):
                    return None
                utxo_index = self._parse_int(chunks[idx])
                
                order.order_id = utxo_hash + struct.pack('<I', utxo_index)
                order.base_ref = token_id + struct.pack('<I', 0)
                if want_token_id:
                    order.quote_ref = want_token_id + struct.pack('<I', 0)
                order.side = 1 if offered_type == 1 else 0  # SELL=1, BUY=0
                
            elif version == 1:
                # v1: type(3) tokenID(4) utxoHash(5) utxoIndex(6) terms(7) sig(8)
                if len(chunks) < 9:
                    return None
                
                idx = 4  # Skip type
                
                # Token ID
                if len(chunks[idx]) != 32:
                    return None
                token_id = chunks[idx]
                idx += 1
                
                # UTXO Hash
                if len(chunks[idx]) != 32:
                    return None
                utxo_hash = chunks[idx]
                idx += 1
                
                # UTXO Index
                utxo_index = self._parse_int(chunks[idx])
                
                order.order_id = utxo_hash + struct.pack('<I', utxo_index)
                order.base_ref = token_id + struct.pack('<I', 0)
                order.side = 1  # v1 defaults to SELL
            else:
                return None
            
            return order
            
        except Exception:
            return None
    
    def _parse_script_chunks(self, script: bytes) -> List[bytes]:
        """Parse Bitcoin script into data chunks."""
        chunks = []
        pos = 0
        
        while pos < len(script):
            opcode = script[pos]
            pos += 1
            
            if opcode == 0x6a or opcode == 0x00:  # OP_RETURN or OP_FALSE
                chunks.append(bytes([opcode]))
            elif 1 <= opcode <= 75:  # Direct push
                if pos + opcode <= len(script):
                    chunks.append(script[pos:pos + opcode])
                    pos += opcode
                else:
                    break
            elif opcode == 0x4c:  # OP_PUSHDATA1
                if pos < len(script):
                    length = script[pos]
                    pos += 1
                    if pos + length <= len(script):
                        chunks.append(script[pos:pos + length])
                        pos += length
            elif opcode == 0x4d:  # OP_PUSHDATA2
                if pos + 2 <= len(script):
                    length = struct.unpack('<H', script[pos:pos + 2])[0]
                    pos += 2
                    if pos + length <= len(script):
                        chunks.append(script[pos:pos + length])
                        pos += length
            elif 0x51 <= opcode <= 0x60:  # OP_1 through OP_16
                chunks.append(bytes([opcode - 0x50]))
            else:
                chunks.append(bytes([opcode]))
        
        return chunks
    
    def _parse_int(self, data: bytes) -> int:
        """Parse script integer."""
        if not data:
            return 0
        if len(data) == 1:
            return data[0]
        if len(data) == 2:
            return struct.unpack('<H', data)[0]
        if len(data) <= 4:
            return struct.unpack('<I', data.ljust(4, b'\x00'))[0]
        return 0
    
    def remove_tx(self, tx_hash: bytes):
        """Remove a transaction from the mempool index (confirmed or evicted)."""
        # Remove from Glyph indexes
        if tx_hash in self.glyph_txs:
            glyph_tx = self.glyph_txs.pop(tx_hash)

            for hashX, ref, _delta in glyph_tx.movements:
                refs_set = self.glyph_by_ref.get(ref)
                if refs_set is not None:
                    refs_set.discard(tx_hash)
                    if not refs_set:
                        del self.glyph_by_ref[ref]
                    self.touched_refs.add(ref)

                sh_set = self.glyph_by_scripthash.get(hashX)
                if sh_set is not None:
                    sh_set.discard(tx_hash)
                    if not sh_set:
                        del self.glyph_by_scripthash[hashX]
                    self.touched_scripthashes.add(hashX)

                # Reversing this movement changes the unconfirmed balance.
                self.touched_balance.add((hashX, ref))
        
        # Remove from Swap indexes
        if tx_hash in self.swap_orders:
            swap_order = self.swap_orders.pop(tx_hash)
            
            pair_key = swap_order.base_ref + swap_order.quote_ref
            if swap_order.order_id in self.swap_by_pair.get(pair_key, set()):
                self.swap_by_pair[pair_key].discard(swap_order.order_id)
                if not self.swap_by_pair[pair_key]:
                    del self.swap_by_pair[pair_key]
            
            if swap_order.order_id in self.swap_by_maker.get(swap_order.maker_scripthash, set()):
                self.swap_by_maker[swap_order.maker_scripthash].discard(swap_order.order_id)
                if not self.swap_by_maker[swap_order.maker_scripthash]:
                    del self.swap_by_maker[swap_order.maker_scripthash]
    
    def get_touched_and_clear(self) -> Tuple[Set[bytes], Set[bytes]]:
        """Get and clear the touched sets for notification dispatch."""
        refs = self.touched_refs
        scripthashes = self.touched_scripthashes
        self.touched_refs = set()
        self.touched_scripthashes = set()
        return refs, scripthashes

    def get_touched_balance_and_clear(self) -> Set[Tuple[bytes, bytes]]:
        """Get and clear the (base_hashX, ref) pairs whose unconfirmed balance
        changed, for the glyph.subscribe.balance dispatcher."""
        pairs = self.touched_balance
        self.touched_balance = set()
        return pairs

    # ========================================================================
    # Query Methods (API)
    # ========================================================================
    
    def get_unconfirmed_glyph_balance(self, scripthash: bytes, token_ref: bytes) -> int:
        """
        Get the unconfirmed balance delta for a token at an address.

        ``scripthash`` is the standard 32-byte Electrum scripthash a wallet
        passes; movements are keyed by the holder's 11-byte base-address hashX,
        so convert first (mirrors GlyphIndex).  Returns positive for net
        incoming, negative for net outgoing.
        """
        hashX = self._to_hashX(scripthash)
        delta = 0

        for tx_hash in self.glyph_by_scripthash.get(hashX, set()):
            glyph_tx = self.glyph_txs.get(tx_hash)
            if not glyph_tx:
                continue
            for mhashX, mref, mdelta in glyph_tx.movements:
                if mhashX == hashX and mref == token_ref:
                    delta += mdelta

        return delta

    def get_unconfirmed_glyph_txs(self, scripthash: bytes) -> List[Dict[str, Any]]:
        """Get all unconfirmed Glyph movements touching an address.

        ``scripthash`` is the 32-byte Electrum scripthash; convert to the
        holder's base-address hashX before lookup.  ``amount`` is signed
        (positive incoming, negative outgoing) per movement.
        """
        hashX = self._to_hashX(scripthash)
        results = []

        for tx_hash in self.glyph_by_scripthash.get(hashX, set()):
            glyph_tx = self.glyph_txs.get(tx_hash)
            if not glyph_tx:
                continue
            for mhashX, mref, mdelta in glyph_tx.movements:
                if mhashX != hashX:
                    continue
                results.append({
                    'txid': hash_to_hex_str(glyph_tx.tx_hash),
                    'ref': self._format_ref(mref) if mref else None,
                    'type': glyph_tx.event_type,
                    'amount': mdelta,
                    'confirmed': False,
                })

        return results
    
    def get_unconfirmed_token_txs(self, token_ref: bytes) -> List[Dict[str, Any]]:
        """Get all unconfirmed transactions for a specific token."""
        results = []
        
        for tx_hash in self.glyph_by_ref.get(token_ref, set()):
            glyph_tx = self.glyph_txs.get(tx_hash)
            if glyph_tx:
                results.append({
                    'txid': hash_to_hex_str(glyph_tx.tx_hash),
                    'type': glyph_tx.event_type,
                    'amount': glyph_tx.amount,
                    'to': glyph_tx.to_scripthash.hex() if glyph_tx.to_scripthash else None,
                    'confirmed': False,
                })
        
        return results
    
    def get_unconfirmed_swap_orders(self, base_ref: bytes = None, 
                                     quote_ref: bytes = None) -> List[Dict[str, Any]]:
        """Get unconfirmed swap orders, optionally filtered by pair."""
        results = []
        
        if base_ref and quote_ref:
            pair_key = base_ref + quote_ref
            order_ids = self.swap_by_pair.get(pair_key, set())
        else:
            order_ids = set(self.swap_orders.keys())
        
        for order_id in order_ids:
            order = self.swap_orders.get(order_id)
            if order:
                results.append({
                    'order_id': hash_to_hex_str(order.order_id) if order.order_id else None,
                    'txid': hash_to_hex_str(order.tx_hash),
                    'base_ref': self._format_ref(order.base_ref),
                    'quote_ref': self._format_ref(order.quote_ref),
                    'side': 'buy' if order.side == 0 else 'sell',
                    'price': order.price,
                    'amount': order.amount,
                    'confirmed': False,
                })
        
        return results
    
    def get_user_unconfirmed_orders(self, scripthash: bytes) -> List[Dict[str, Any]]:
        """Get unconfirmed orders for a specific user."""
        results = []
        
        for order_id in self.swap_by_maker.get(scripthash, set()):
            order = self.swap_orders.get(order_id)
            if order:
                results.append({
                    'order_id': hash_to_hex_str(order.order_id) if order.order_id else None,
                    'txid': hash_to_hex_str(order.tx_hash),
                    'base_ref': self._format_ref(order.base_ref),
                    'quote_ref': self._format_ref(order.quote_ref),
                    'side': 'buy' if order.side == 0 else 'sell',
                    'price': order.price,
                    'amount': order.amount,
                    'confirmed': False,
                })
        
        return results
    
    @staticmethod
    def _format_ref(ref: bytes) -> Optional[str]:
        """Format a ref bytes to string."""
        if not ref or len(ref) < 36:
            return None
        txid = ref[:32]
        vout = struct.unpack('<I', ref[32:36])[0]
        return hash_to_hex_str(txid) + '_' + str(vout)
    
    def stats(self) -> Dict[str, Any]:
        """Get mempool Glyph/Swap statistics."""
        return {
            'glyph_txs': len(self.glyph_txs),
            'glyph_refs_tracked': len(self.glyph_by_ref),
            'glyph_scripthashes_tracked': len(self.glyph_by_scripthash),
            'swap_orders': len(self.swap_orders),
            'swap_pairs_tracked': len(self.swap_by_pair),
            'swap_makers_tracked': len(self.swap_by_maker),
        }
