"""
Glyph/Swap WebSocket Subscriptions for RXinDexer

Provides real-time push notifications for:
- Token balance changes
- Token state updates  
- Swap orderbook changes
- Trade fill notifications
- User order updates

Extends ElectrumX's existing subscription infrastructure.
"""

import asyncio
from typing import Dict, Set, Optional, Any, Callable
from collections import defaultdict

from electrumx.lib import util
from electrumx.lib.hash import hash_to_hex_str


class GlyphSubscriptionManager:
    """
    Manages Glyph/Swap subscriptions for WebSocket clients.
    
    Subscription types:
    - glyph.balance: Token balance changes for address+token
    - glyph.token: Token state changes (supply, metadata)
    - glyph.transfers: Token transfer events
    - swap.orderbook: Orderbook updates for trading pair
    - swap.fills: Trade fills for trading pair
    - swap.user_orders: User's order updates
    - wave.name: WAVE name ownership changes
    - dmint.token: dMint mining stats updates
    """
    
    def __init__(self, env):
        self.logger = util.class_logger(__name__, self.__class__.__name__)
        self.env = env
        self.enabled = getattr(env, 'glyph_subscriptions', True)
        
        # Subscription maps: key -> set of session_ids
        # Glyph subscriptions
        self.balance_subs: Dict[bytes, Set[int]] = defaultdict(set)  # scripthash+ref -> sessions
        self.token_subs: Dict[bytes, Set[int]] = defaultdict(set)    # token_ref -> sessions
        self.transfer_subs: Dict[bytes, Set[int]] = defaultdict(set) # token_ref -> sessions
        
        # Swap subscriptions
        self.orderbook_subs: Dict[bytes, Set[int]] = defaultdict(set)  # pair_key -> sessions
        self.fills_subs: Dict[bytes, Set[int]] = defaultdict(set)      # pair_key -> sessions
        self.user_order_subs: Dict[bytes, Set[int]] = defaultdict(set) # scripthash -> sessions
        
        # WAVE subscriptions
        self.wave_name_subs: Dict[str, Set[int]] = defaultdict(set)  # name -> sessions
        
        # dMint subscriptions
        self.dmint_subs: Dict[bytes, Set[int]] = defaultdict(set)  # token_ref -> sessions
        
        # Session -> subscriptions (for cleanup on disconnect)
        self.session_subs: Dict[int, Set[tuple]] = defaultdict(set)
        
        # Notification callback
        self.notify_callback: Optional[Callable] = None
        
        if self.enabled:
            self.logger.info('Glyph/Swap subscriptions enabled')
    
    def set_notify_callback(self, callback: Callable):
        """Set the callback for sending notifications to sessions."""
        self.notify_callback = callback
    
    # ========================================================================
    # Subscribe/Unsubscribe Methods
    # ========================================================================
    
    def subscribe_balance(self, session_id: int, scripthash: bytes, token_ref: bytes) -> bool:
        """Subscribe to token balance changes for an address."""
        key = scripthash + token_ref
        self.balance_subs[key].add(session_id)
        self.session_subs[session_id].add(('balance', key))
        return True
    
    def unsubscribe_balance(self, session_id: int, scripthash: bytes, token_ref: bytes) -> bool:
        """Unsubscribe from token balance changes."""
        key = scripthash + token_ref
        if session_id in self.balance_subs.get(key, set()):
            self.balance_subs[key].discard(session_id)
            self.session_subs[session_id].discard(('balance', key))
            if not self.balance_subs[key]:
                del self.balance_subs[key]
            return True
        return False
    
    def subscribe_token(self, session_id: int, token_ref: bytes) -> bool:
        """Subscribe to token state changes."""
        self.token_subs[token_ref].add(session_id)
        self.session_subs[session_id].add(('token', token_ref))
        return True
    
    def unsubscribe_token(self, session_id: int, token_ref: bytes) -> bool:
        """Unsubscribe from token state changes."""
        if session_id in self.token_subs.get(token_ref, set()):
            self.token_subs[token_ref].discard(session_id)
            self.session_subs[session_id].discard(('token', token_ref))
            if not self.token_subs[token_ref]:
                del self.token_subs[token_ref]
            return True
        return False
    
    def subscribe_transfers(self, session_id: int, token_ref: bytes) -> bool:
        """Subscribe to token transfer events."""
        self.transfer_subs[token_ref].add(session_id)
        self.session_subs[session_id].add(('transfers', token_ref))
        return True
    
    def subscribe_orderbook(self, session_id: int, base_ref: bytes, quote_ref: bytes) -> bool:
        """Subscribe to orderbook updates for a trading pair."""
        key = base_ref + quote_ref
        self.orderbook_subs[key].add(session_id)
        self.session_subs[session_id].add(('orderbook', key))
        return True
    
    def unsubscribe_orderbook(self, session_id: int, base_ref: bytes, quote_ref: bytes) -> bool:
        """Unsubscribe from orderbook updates."""
        key = base_ref + quote_ref
        if session_id in self.orderbook_subs.get(key, set()):
            self.orderbook_subs[key].discard(session_id)
            self.session_subs[session_id].discard(('orderbook', key))
            if not self.orderbook_subs[key]:
                del self.orderbook_subs[key]
            return True
        return False
    
    def subscribe_fills(self, session_id: int, base_ref: bytes, quote_ref: bytes) -> bool:
        """Subscribe to trade fill notifications."""
        key = base_ref + quote_ref
        self.fills_subs[key].add(session_id)
        self.session_subs[session_id].add(('fills', key))
        return True
    
    def subscribe_user_orders(self, session_id: int, scripthash: bytes) -> bool:
        """Subscribe to user's order updates."""
        self.user_order_subs[scripthash].add(session_id)
        self.session_subs[session_id].add(('user_orders', scripthash))
        return True
    
    def subscribe_wave_name(self, session_id: int, name: str) -> bool:
        """Subscribe to WAVE name ownership changes."""
        name_lower = name.lower()
        self.wave_name_subs[name_lower].add(session_id)
        self.session_subs[session_id].add(('wave_name', name_lower))
        return True
    
    def subscribe_dmint(self, session_id: int, token_ref: bytes) -> bool:
        """Subscribe to dMint token mining stats."""
        self.dmint_subs[token_ref].add(session_id)
        self.session_subs[session_id].add(('dmint', token_ref))
        return True
    
    def unsubscribe_session(self, session_id: int):
        """Remove all subscriptions for a disconnected session."""
        subs = self.session_subs.pop(session_id, set())
        
        for sub_type, key in subs:
            if sub_type == 'balance':
                self.balance_subs[key].discard(session_id)
                if not self.balance_subs[key]:
                    del self.balance_subs[key]
            elif sub_type == 'token':
                self.token_subs[key].discard(session_id)
                if not self.token_subs[key]:
                    del self.token_subs[key]
            elif sub_type == 'transfers':
                self.transfer_subs[key].discard(session_id)
                if not self.transfer_subs[key]:
                    del self.transfer_subs[key]
            elif sub_type == 'orderbook':
                self.orderbook_subs[key].discard(session_id)
                if not self.orderbook_subs[key]:
                    del self.orderbook_subs[key]
            elif sub_type == 'fills':
                self.fills_subs[key].discard(session_id)
                if not self.fills_subs[key]:
                    del self.fills_subs[key]
            elif sub_type == 'user_orders':
                self.user_order_subs[key].discard(session_id)
                if not self.user_order_subs[key]:
                    del self.user_order_subs[key]
            elif sub_type == 'wave_name':
                self.wave_name_subs[key].discard(session_id)
                if not self.wave_name_subs[key]:
                    del self.wave_name_subs[key]
            elif sub_type == 'dmint':
                self.dmint_subs[key].discard(session_id)
                if not self.dmint_subs[key]:
                    del self.dmint_subs[key]
    
    # ========================================================================
    # Notification Methods (called by indexers)
    # ========================================================================
    
    async def notify_balance_change(self, scripthash: bytes, token_ref: bytes, 
                                     new_balance: int, delta: int):
        """Notify subscribers of a balance change."""
        if not self.notify_callback:
            return
        
        key = scripthash + token_ref
        sessions = self.balance_subs.get(key, set())
        
        if sessions:
            notification = {
                'method': 'glyph.balance',
                'params': {
                    'scripthash': scripthash.hex(),
                    'ref': self._format_ref(token_ref),
                    'balance': new_balance,
                    'delta': delta,
                }
            }
            await self._send_to_sessions(sessions, notification)
    
    async def notify_token_change(self, token_ref: bytes, token_data: Dict[str, Any]):
        """Notify subscribers of a token state change."""
        if not self.notify_callback:
            return
        
        sessions = self.token_subs.get(token_ref, set())
        
        if sessions:
            notification = {
                'method': 'glyph.token',
                'params': {
                    'ref': self._format_ref(token_ref),
                    'data': token_data,
                }
            }
            await self._send_to_sessions(sessions, notification)
    
    async def notify_transfer(self, token_ref: bytes, tx_hash: bytes, 
                               from_scripthash: bytes, to_scripthash: bytes,
                               amount: int, height: int):
        """Notify subscribers of a token transfer."""
        if not self.notify_callback:
            return
        
        sessions = self.transfer_subs.get(token_ref, set())
        
        if sessions:
            notification = {
                'method': 'glyph.transfer',
                'params': {
                    'ref': self._format_ref(token_ref),
                    'txid': hash_to_hex_str(tx_hash),
                    'from': from_scripthash.hex() if from_scripthash else None,
                    'to': to_scripthash.hex() if to_scripthash else None,
                    'amount': amount,
                    'height': height,
                }
            }
            await self._send_to_sessions(sessions, notification)
    
    async def notify_orderbook_change(self, base_ref: bytes, quote_ref: bytes,
                                       change_type: str, order_data: Dict[str, Any]):
        """Notify subscribers of an orderbook change."""
        if not self.notify_callback:
            return
        
        key = base_ref + quote_ref
        sessions = self.orderbook_subs.get(key, set())
        
        if sessions:
            notification = {
                'method': 'swap.orderbook',
                'params': {
                    'base_ref': self._format_ref(base_ref),
                    'quote_ref': self._format_ref(quote_ref),
                    'change': change_type,  # 'add', 'update', 'remove'
                    'order': order_data,
                }
            }
            await self._send_to_sessions(sessions, notification)
    
    async def notify_fill(self, base_ref: bytes, quote_ref: bytes,
                          fill_data: Dict[str, Any]):
        """Notify subscribers of a trade fill."""
        if not self.notify_callback:
            return
        
        key = base_ref + quote_ref
        sessions = self.fills_subs.get(key, set())
        
        if sessions:
            notification = {
                'method': 'swap.fill',
                'params': {
                    'base_ref': self._format_ref(base_ref),
                    'quote_ref': self._format_ref(quote_ref),
                    'fill': fill_data,
                }
            }
            await self._send_to_sessions(sessions, notification)
    
    async def notify_user_order(self, scripthash: bytes, order_data: Dict[str, Any],
                                 change_type: str):
        """Notify subscribers of a user's order update."""
        if not self.notify_callback:
            return
        
        sessions = self.user_order_subs.get(scripthash, set())
        
        if sessions:
            notification = {
                'method': 'swap.user_order',
                'params': {
                    'scripthash': scripthash.hex(),
                    'change': change_type,  # 'new', 'filled', 'partial', 'cancelled'
                    'order': order_data,
                }
            }
            await self._send_to_sessions(sessions, notification)
    
    async def notify_wave_name_change(self, name: str, new_owner: bytes,
                                       tx_hash: bytes, height: int):
        """Notify subscribers of a WAVE name ownership change."""
        if not self.notify_callback:
            return
        
        name_lower = name.lower()
        sessions = self.wave_name_subs.get(name_lower, set())
        
        if sessions:
            notification = {
                'method': 'wave.name',
                'params': {
                    'name': name,
                    'owner': new_owner.hex() if new_owner else None,
                    'txid': hash_to_hex_str(tx_hash),
                    'height': height,
                }
            }
            await self._send_to_sessions(sessions, notification)
    
    async def notify_dmint_update(self, token_ref: bytes, mining_data: Dict[str, Any]):
        """Notify subscribers of dMint mining stats update."""
        if not self.notify_callback:
            return
        
        sessions = self.dmint_subs.get(token_ref, set())
        
        if sessions:
            notification = {
                'method': 'dmint.update',
                'params': {
                    'ref': self._format_ref(token_ref),
                    'data': mining_data,
                }
            }
            await self._send_to_sessions(sessions, notification)
    
    async def _send_to_sessions(self, session_ids: Set[int], notification: Dict):
        """Send notification to multiple sessions."""
        if self.notify_callback:
            for session_id in session_ids:
                try:
                    await self.notify_callback(session_id, notification)
                except Exception as e:
                    self.logger.debug(f'Failed to notify session {session_id}: {e}')
    
    @staticmethod
    def _format_ref(ref: bytes) -> Optional[str]:
        """Format a ref bytes to string."""
        if not ref or len(ref) < 36:
            return None
        import struct
        txid = ref[:32]
        vout = struct.unpack('<I', ref[32:36])[0]
        return hash_to_hex_str(txid) + '_' + str(vout)
    
    def stats(self) -> Dict[str, int]:
        """Get subscription statistics."""
        return {
            'balance_subscriptions': sum(len(s) for s in self.balance_subs.values()),
            'token_subscriptions': sum(len(s) for s in self.token_subs.values()),
            'transfer_subscriptions': sum(len(s) for s in self.transfer_subs.values()),
            'orderbook_subscriptions': sum(len(s) for s in self.orderbook_subs.values()),
            'fills_subscriptions': sum(len(s) for s in self.fills_subs.values()),
            'user_order_subscriptions': sum(len(s) for s in self.user_order_subs.values()),
            'wave_name_subscriptions': sum(len(s) for s in self.wave_name_subs.values()),
            'dmint_subscriptions': sum(len(s) for s in self.dmint_subs.values()),
            'total_sessions': len(self.session_subs),
        }


# API method names for registration
GLYPH_SUBSCRIPTION_METHODS = {
    'glyph.subscribe.balance': 'glyph_subscribe_balance',
    'glyph.unsubscribe.balance': 'glyph_unsubscribe_balance',
    'glyph.subscribe.token': 'glyph_subscribe_token',
    'glyph.unsubscribe.token': 'glyph_unsubscribe_token',
    'glyph.subscribe.transfers': 'glyph_subscribe_transfers',
    'swap.subscribe.orderbook': 'swap_subscribe_orderbook',
    'swap.unsubscribe.orderbook': 'swap_unsubscribe_orderbook',
    'swap.subscribe.fills': 'swap_subscribe_fills',
    'swap.subscribe.user_orders': 'swap_subscribe_user_orders',
    'wave.subscribe.name': 'wave_subscribe_name',
    'dmint.subscribe.token': 'dmint_subscribe_token',
}
