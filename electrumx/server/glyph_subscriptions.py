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
import os
from typing import Dict, Set, Optional, Any, Callable
from collections import defaultdict

from electrumx.lib import util
from electrumx.lib.hash import hash_to_hex_str, HASHX_LEN


# Default hard cap on the number of glyph/swap/wave/dmint subscriptions a single
# session may hold at once.  This bounds the memory a single live connection can
# pin in the process-global subscription maps (H3 DoS / memory-exhaustion fix).
DEFAULT_MAX_SUBS_PER_CLIENT = 10000

# Upper bound on the length of a WAVE name used as a subscription key.  The key
# is the raw (lowercased) name string supplied by the client, so without a bound
# a client could pin arbitrarily large keys.  WAVE names are short identifiers;
# 256 is generous while still rejecting abusive payloads.
MAX_WAVE_NAME_LEN = 256


class SubscriptionLimitError(Exception):
    """Raised when a subscribe request would exceed a per-session limit.

    Carries a human-readable message that subscribe handlers surface to the
    client as a clean RPC error rather than tearing down the session.
    """


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

        # Hard per-session subscription cap (H3 DoS / memory-exhaustion fix).
        # Resolution order: explicit env attribute, then the MAX_SUBS_PER_CLIENT
        # environment variable, then the module default.  A value <= 0 disables
        # the cap (not recommended; kept as an explicit operator escape hatch).
        self.max_subs_per_client = self._resolve_max_subs(env)
        self.max_wave_name_len = MAX_WAVE_NAME_LEN
        
        # Subscription maps: key -> set of session_ids
        # Glyph subscriptions
        # Keyed by base-address hashX(11) + ref(36) — the same key space the
        # block/mempool indexers notify on, so client 32-byte scripthashes are
        # converted before subscribing (see _to_hashX).
        #
        # M3 fix (scripthash collision / cross-leak): the notify path matches in
        # hashX(11) space (the indexer only has the 11-byte base-address hashX),
        # but two distinct 32-byte client scripthashes can share an 11-byte
        # prefix and collide on this key.  To avoid leaking one subscriber's
        # full scripthash to another (and to stop a crafted subscriber from
        # overwriting a victim's echoed scripthash), each balance subscription
        # stores its OWN full 32-byte scripthash.  The map value is therefore
        # session_id -> full 32-byte scripthash, NOT a bare set of session_ids.
        # hashX(11)+ref remains the cheap first-level bucket for the fast notify
        # match; on delivery each subscriber is echoed only its own scripthash.
        self.balance_subs: Dict[bytes, Dict[int, bytes]] = defaultdict(dict)  # hashX+ref -> {session: full_scripthash}
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
            if self.max_subs_per_client > 0:
                self.logger.info(
                    f'Glyph/Swap subscriptions enabled '
                    f'(max {self.max_subs_per_client} subs/session)'
                )
            else:
                self.logger.warning(
                    'Glyph/Swap subscriptions enabled with NO per-session cap '
                    '(MAX_SUBS_PER_CLIENT<=0) — memory-exhaustion DoS possible'
                )

    @staticmethod
    def _resolve_max_subs(env) -> int:
        """Resolve the per-session subscription cap from env/environment.

        Resolution order: explicit ``env.max_subs_per_client`` attribute, then
        the ``MAX_SUBS_PER_CLIENT`` environment variable, then the module
        default.  Any value that is not cleanly int-coercible (e.g. a test
        ``Mock`` env attribute) falls through to the next source rather than
        raising, so the cap never breaks unrelated callers.
        """
        def _coerce(value):
            # Guard against bool (True/False are ints) leaking a 0/1 cap.
            if isinstance(value, bool) or value is None:
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        resolved = _coerce(getattr(env, 'max_subs_per_client', None))
        if resolved is None:
            resolved = _coerce(os.environ.get('MAX_SUBS_PER_CLIENT'))
        if resolved is None:
            resolved = DEFAULT_MAX_SUBS_PER_CLIENT
        return resolved

    # SCOPED FOLLOW-UP (H3, deferred — needs design): this cap is per *session*.
    # A client that opens many connections, or reconnects after disconnect
    # cleanup, gets a fresh cap each time, so the aggregate subscription count
    # across one IP is still unbounded.  Closing that bypass requires an
    # IP-persistent, proxy-aware (X-Forwarded-For / PROXY-protocol) throttle that
    # survives reconnects and is shared across sessions.  That is an
    # architectural change tracked separately and intentionally NOT done here.
    def _check_can_subscribe(self, session_id: int, sub_entry: tuple) -> None:
        """Enforce the per-session subscription cap (O(1)).

        Raises ``SubscriptionLimitError`` if accepting ``sub_entry`` would push
        the session above ``max_subs_per_client``.  Re-subscribing to something
        the session already holds is always allowed (it does not grow the map),
        so idempotent re-subscribes never trip the cap.
        """
        if self.max_subs_per_client <= 0:
            return  # cap disabled
        existing = self.session_subs.get(session_id)
        # Already subscribed -> no growth, always permitted.
        if existing is not None and sub_entry in existing:
            return
        current = len(existing) if existing is not None else 0
        if current >= self.max_subs_per_client:
            raise SubscriptionLimitError(
                f'subscription limit reached '
                f'({self.max_subs_per_client} per session)'
            )

    def set_notify_callback(self, callback: Callable):
        """Set the callback for sending notifications to sessions."""
        self.notify_callback = callback
    
    # ========================================================================
    # Subscribe/Unsubscribe Methods
    # ========================================================================
    
    @staticmethod
    def _to_hashX(scripthash: bytes) -> bytes:
        """Convert a 32-byte Electrum scripthash to the 11-byte base-address
        hashX used as the balance key.

        Mirrors ``GlyphIndex._scripthash_to_hashX`` and is idempotent for
        values already of hashX length (the notify path passes the 11-byte
        base-address hashX directly), so subscribe and notify share one key.
        """
        if scripthash is None:
            return scripthash
        if len(scripthash) == 32:
            return scripthash[::-1][:HASHX_LEN]
        return scripthash[:HASHX_LEN]

    def subscribe_balance(self, session_id: int, scripthash: bytes, token_ref: bytes) -> bool:
        """Subscribe to token balance changes for an address.

        ``scripthash`` is the standard 32-byte Electrum scripthash; it is
        converted to the holder's base-address hashX so the key matches the
        notification key produced during block/mempool processing.
        """
        hashX = self._to_hashX(scripthash)
        key = hashX + token_ref
        self._check_can_subscribe(session_id, ('balance', key))
        # Store this subscriber's OWN full 32-byte scripthash so notifications
        # echo it (never a colliding subscriber's) and a crafted subscriber
        # cannot overwrite a victim's stored scripthash (M3 fix).
        self.balance_subs[key][session_id] = scripthash
        self.session_subs[session_id].add(('balance', key))
        return True

    def unsubscribe_balance(self, session_id: int, scripthash: bytes, token_ref: bytes) -> bool:
        """Unsubscribe from token balance changes."""
        key = self._to_hashX(scripthash) + token_ref
        # balance_subs maps key -> {session_id: full_scripthash} (M3); use dict
        # membership/pop rather than set discard.
        if session_id in self.balance_subs.get(key, {}):
            self.balance_subs[key].pop(session_id, None)
            self.session_subs[session_id].discard(('balance', key))
            if not self.balance_subs[key]:
                del self.balance_subs[key]
            return True
        return False
    
    def subscribe_token(self, session_id: int, token_ref: bytes) -> bool:
        """Subscribe to token state changes."""
        self._check_can_subscribe(session_id, ('token', token_ref))
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
        self._check_can_subscribe(session_id, ('transfers', token_ref))
        self.transfer_subs[token_ref].add(session_id)
        self.session_subs[session_id].add(('transfers', token_ref))
        return True
    
    def subscribe_orderbook(self, session_id: int, base_ref: bytes, quote_ref: bytes) -> bool:
        """Subscribe to orderbook updates for a trading pair."""
        key = base_ref + quote_ref
        self._check_can_subscribe(session_id, ('orderbook', key))
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
        self._check_can_subscribe(session_id, ('fills', key))
        self.fills_subs[key].add(session_id)
        self.session_subs[session_id].add(('fills', key))
        return True

    def subscribe_user_orders(self, session_id: int, scripthash: bytes) -> bool:
        """Subscribe to user's order updates."""
        self._check_can_subscribe(session_id, ('user_orders', scripthash))
        self.user_order_subs[scripthash].add(session_id)
        self.session_subs[session_id].add(('user_orders', scripthash))
        return True

    def subscribe_wave_name(self, session_id: int, name: str) -> bool:
        """Subscribe to WAVE name ownership changes."""
        name_lower = name.lower()
        # The subscription KEY is the raw (lowercased) name string, so an
        # unbounded name lets a client pin arbitrarily large keys.  Reject
        # absurdly long names up front (H3 fix).
        if len(name_lower) > self.max_wave_name_len:
            raise SubscriptionLimitError(
                f'WAVE name too long '
                f'(max {self.max_wave_name_len} characters)'
            )
        self._check_can_subscribe(session_id, ('wave_name', name_lower))
        self.wave_name_subs[name_lower].add(session_id)
        self.session_subs[session_id].add(('wave_name', name_lower))
        return True

    def subscribe_dmint(self, session_id: int, token_ref: bytes) -> bool:
        """Subscribe to dMint token mining stats."""
        self._check_can_subscribe(session_id, ('dmint', token_ref))
        self.dmint_subs[token_ref].add(session_id)
        self.session_subs[session_id].add(('dmint', token_ref))
        return True
    
    def unsubscribe_session(self, session_id: int):
        """Remove all subscriptions for a disconnected session."""
        subs = self.session_subs.pop(session_id, set())
        
        for sub_type, key in subs:
            if sub_type == 'balance':
                # balance_subs value is {session_id: full_scripthash} (M3 fix).
                self.balance_subs[key].pop(session_id, None)
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
        """Notify subscribers of a balance change.

        ``scripthash`` may be the 32-byte Electrum scripthash or the 11-byte
        base-address hashX produced by block/mempool processing; both normalise
        to the same hashX key so the subscription matches.  The payload echoes
        the subscriber's original 32-byte scripthash when it is known.
        """
        if not self.notify_callback:
            return

        hashX = self._to_hashX(scripthash)
        key = hashX + token_ref
        # hashX(11)+ref is the cheap first-level bucket (preserves the fast
        # notify match the indexer relies on — it only has the 11-byte hashX).
        subs = self.balance_subs.get(key)
        if not subs:
            return

        # If the caller passed a full 32-byte scripthash, gate delivery on full
        # scripthash equality so a colliding subscriber (same 11-byte prefix,
        # different full scripthash) is NOT notified (M3 fix).  The block/mempool
        # dispatchers pass the 11-byte base-address hashX, which legitimately
        # applies to every subscriber in this bucket; in that case each
        # subscriber is still echoed only its OWN stored full scripthash.
        full_filter = scripthash if scripthash is not None and len(scripthash) == 32 else None
        ref_str = self._format_ref(token_ref)
        for session_id, sub_scripthash in list(subs.items()):
            if full_filter is not None and sub_scripthash != full_filter:
                continue
            notification = {
                'method': 'glyph.balance',
                'params': {
                    # Echo this subscriber's OWN scripthash, never a colliding
                    # subscriber's (M3 fix).
                    'scripthash': sub_scripthash.hex(),
                    'ref': ref_str,
                    'balance': new_balance,
                    'delta': delta,
                }
            }
            await self._send_to_sessions({session_id}, notification)
    
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
