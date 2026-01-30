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
from electrumx.lib.hash import hash_to_hex_str, hex_str_to_hash
from electrumx.lib.glyph import (
    contains_glyph_magic, parse_glyph_envelope, get_token_type,
    GlyphProtocol, is_dmint_reveal, is_wave_claim
)


class MempoolGlyphTx:
    """Represents an unconfirmed Glyph transaction."""
    __slots__ = ('tx_hash', 'token_ref', 'token_type', 'event_type',
                 'from_scripthash', 'to_scripthash', 'amount', 'fee', 'size')
    
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
        
        if self.glyph_enabled:
            self.logger.info('Mempool Glyph indexing enabled')
        if self.swap_enabled:
            self.logger.info('Mempool Swap indexing enabled')
    
    def process_mempool_tx(self, tx_hash: bytes, tx, raw_tx: bytes) -> bool:
        """
        Process a mempool transaction for Glyph/Swap content.
        
        Returns True if the transaction contains Glyph/Swap data.
        """
        found_glyph = False
        found_swap = False
        
        if self.glyph_enabled:
            found_glyph = self._process_glyph_tx(tx_hash, tx, raw_tx)
        
        if self.swap_enabled:
            found_swap = self._process_swap_tx(tx_hash, tx, raw_tx)
        
        return found_glyph or found_swap
    
    def _process_glyph_tx(self, tx_hash: bytes, tx, raw_tx: bytes) -> bool:
        """Process transaction for Glyph token transfers."""
        found = False
        
        for idx, txout in enumerate(tx.outputs):
            script = txout.pk_script
            
            # Check for Glyph magic
            if not contains_glyph_magic(script):
                continue
            
            # Parse envelope
            envelope = parse_glyph_envelope(script)
            if not envelope:
                continue
            
            # Skip dMint reveals (wait for confirmation)
            if is_dmint_reveal(envelope):
                self.logger.debug(f'Skipping dMint reveal in mempool: {hash_to_hex_str(tx_hash)}')
                continue
            
            # Skip WAVE claims (wait for confirmation)
            if is_wave_claim(envelope):
                self.logger.debug(f'Skipping WAVE claim in mempool: {hash_to_hex_str(tx_hash)}')
                continue
            
            # This is a regular Glyph transfer - index it
            glyph_tx = MempoolGlyphTx()
            glyph_tx.tx_hash = tx_hash
            glyph_tx.token_type = get_token_type(envelope.get('protocols', []))
            glyph_tx.event_type = 'transfer'
            
            # Extract ref from envelope or inputs
            ref = envelope.get('ref')
            if ref:
                glyph_tx.token_ref = ref
            
            # Get recipient scripthash from output
            from electrumx.lib.script import Script
            to_hashX = Script.hashX_from_script(Script.zero_refs(script))
            glyph_tx.to_scripthash = to_hashX
            
            # Try to get sender from inputs (first input with matching ref)
            for txin in tx.inputs:
                if not txin.is_generation():
                    # Would need UTXO lookup to get sender scripthash
                    # For now, leave from_scripthash as None
                    pass
            
            # Get amount if FT
            glyph_tx.amount = envelope.get('amount', 1)
            
            # Store in indexes
            self.glyph_txs[tx_hash] = glyph_tx
            
            if glyph_tx.token_ref:
                self.glyph_by_ref[glyph_tx.token_ref].add(tx_hash)
                self.touched_refs.add(glyph_tx.token_ref)
            
            if glyph_tx.to_scripthash:
                self.glyph_by_scripthash[glyph_tx.to_scripthash].add(tx_hash)
                self.touched_scripthashes.add(glyph_tx.to_scripthash)
            
            found = True
        
        return found
    
    def _process_swap_tx(self, tx_hash: bytes, tx, raw_tx: bytes) -> bool:
        """
        Process transaction for Swap orders (RSWP protocol).
        
        Detects RSWP advertisements in OP_RETURN outputs.
        Note: Unlike confirmed orders, mempool orders are tentative.
        """
        found = False
        
        for idx, txout in enumerate(tx.outputs):
            script = txout.pk_script
            
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
            
            if glyph_tx.token_ref and tx_hash in self.glyph_by_ref.get(glyph_tx.token_ref, set()):
                self.glyph_by_ref[glyph_tx.token_ref].discard(tx_hash)
                if not self.glyph_by_ref[glyph_tx.token_ref]:
                    del self.glyph_by_ref[glyph_tx.token_ref]
                self.touched_refs.add(glyph_tx.token_ref)
            
            if glyph_tx.to_scripthash and tx_hash in self.glyph_by_scripthash.get(glyph_tx.to_scripthash, set()):
                self.glyph_by_scripthash[glyph_tx.to_scripthash].discard(tx_hash)
                if not self.glyph_by_scripthash[glyph_tx.to_scripthash]:
                    del self.glyph_by_scripthash[glyph_tx.to_scripthash]
                self.touched_scripthashes.add(glyph_tx.to_scripthash)
            
            if glyph_tx.from_scripthash:
                self.glyph_by_scripthash[glyph_tx.from_scripthash].discard(tx_hash)
                if not self.glyph_by_scripthash[glyph_tx.from_scripthash]:
                    del self.glyph_by_scripthash[glyph_tx.from_scripthash]
                self.touched_scripthashes.add(glyph_tx.from_scripthash)
        
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
    
    # ========================================================================
    # Query Methods (API)
    # ========================================================================
    
    def get_unconfirmed_glyph_balance(self, scripthash: bytes, token_ref: bytes) -> int:
        """
        Get the unconfirmed balance delta for a token.
        
        Returns positive for incoming, negative for outgoing.
        """
        delta = 0
        
        for tx_hash in self.glyph_by_scripthash.get(scripthash, set()):
            glyph_tx = self.glyph_txs.get(tx_hash)
            if glyph_tx and glyph_tx.token_ref == token_ref:
                if glyph_tx.to_scripthash == scripthash:
                    delta += glyph_tx.amount
                if glyph_tx.from_scripthash == scripthash:
                    delta -= glyph_tx.amount
        
        return delta
    
    def get_unconfirmed_glyph_txs(self, scripthash: bytes) -> List[Dict[str, Any]]:
        """Get all unconfirmed Glyph transactions for a scripthash."""
        results = []
        
        for tx_hash in self.glyph_by_scripthash.get(scripthash, set()):
            glyph_tx = self.glyph_txs.get(tx_hash)
            if glyph_tx:
                results.append({
                    'txid': hash_to_hex_str(glyph_tx.tx_hash),
                    'ref': self._format_ref(glyph_tx.token_ref) if glyph_tx.token_ref else None,
                    'type': glyph_tx.event_type,
                    'amount': glyph_tx.amount,
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
