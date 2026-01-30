"""
Subscription Rate Limiter for RXinDexer

Implements per-client rate limiting for subscriptions to prevent:
- Subscription spam attacks
- Memory exhaustion from excessive subscriptions
- Notification flooding

Configuration via environment variables:
- MAX_SUBS_PER_CLIENT: Maximum subscriptions per client session
- SUB_RATE_LIMIT: Maximum new subscriptions per second per client
- SUB_BURST_LIMIT: Maximum burst of subscriptions allowed
"""

import time
from typing import Dict, Set, Optional, Tuple
from collections import defaultdict
from dataclasses import dataclass, field

from electrumx.lib import util


@dataclass
class ClientRateLimitState:
    """Rate limiting state for a single client."""
    # Subscription tracking
    subscriptions: Set[str] = field(default_factory=set)
    subscription_count: int = 0
    
    # Token bucket for rate limiting
    tokens: float = 0.0
    last_update: float = field(default_factory=time.time)
    
    # Request tracking
    request_count: int = 0
    request_window_start: float = field(default_factory=time.time)
    
    # Violation tracking
    violations: int = 0
    last_violation: float = 0.0
    blocked_until: float = 0.0


class SubscriptionRateLimiter:
    """
    Per-client subscription rate limiter.
    
    Uses a token bucket algorithm for smooth rate limiting with
    configurable burst allowance.
    """
    
    def __init__(self, env=None):
        self.logger = util.class_logger(__name__, self.__class__.__name__)
        
        # Configuration from environment
        self.max_subs_per_client = getattr(env, 'max_subs_per_client', 10000) if env else 10000
        self.sub_rate_limit = getattr(env, 'sub_rate_limit', 100) if env else 100  # subs/sec
        self.sub_burst_limit = getattr(env, 'sub_burst_limit', 500) if env else 500
        self.violation_threshold = getattr(env, 'rate_violation_threshold', 10) if env else 10
        self.block_duration = getattr(env, 'rate_block_duration', 60) if env else 60  # seconds
        
        # Per-client state
        self.clients: Dict[str, ClientRateLimitState] = {}
        
        # Global tracking
        self.total_subscriptions = 0
        self.total_violations = 0
        
        self.logger.info(
            f'Subscription rate limiter initialized: '
            f'max_subs={self.max_subs_per_client}, '
            f'rate={self.sub_rate_limit}/s, '
            f'burst={self.sub_burst_limit}'
        )
    
    def get_client_state(self, client_id: str) -> ClientRateLimitState:
        """Get or create rate limit state for a client."""
        if client_id not in self.clients:
            self.clients[client_id] = ClientRateLimitState(
                tokens=float(self.sub_burst_limit)
            )
        return self.clients[client_id]
    
    def can_subscribe(self, client_id: str, subscription_key: str) -> Tuple[bool, Optional[str]]:
        """
        Check if a client can create a new subscription.
        
        Returns (allowed, error_message).
        """
        state = self.get_client_state(client_id)
        now = time.time()
        
        # Check if client is blocked
        if state.blocked_until > now:
            remaining = int(state.blocked_until - now)
            return False, f'Client blocked for {remaining}s due to rate limit violations'
        
        # Check subscription limit
        if state.subscription_count >= self.max_subs_per_client:
            self._record_violation(client_id, state, 'max_subscriptions')
            return False, f'Maximum subscriptions ({self.max_subs_per_client}) exceeded'
        
        # Check if already subscribed
        if subscription_key in state.subscriptions:
            return True, None  # Already subscribed, allow (no new subscription)
        
        # Token bucket rate limiting
        self._refill_tokens(state, now)
        
        if state.tokens < 1.0:
            self._record_violation(client_id, state, 'rate_limit')
            return False, 'Subscription rate limit exceeded, please slow down'
        
        return True, None
    
    def record_subscription(self, client_id: str, subscription_key: str):
        """Record a successful subscription."""
        state = self.get_client_state(client_id)
        
        if subscription_key not in state.subscriptions:
            state.subscriptions.add(subscription_key)
            state.subscription_count += 1
            state.tokens -= 1.0
            self.total_subscriptions += 1
    
    def record_unsubscription(self, client_id: str, subscription_key: str):
        """Record an unsubscription."""
        state = self.get_client_state(client_id)
        
        if subscription_key in state.subscriptions:
            state.subscriptions.remove(subscription_key)
            state.subscription_count -= 1
            self.total_subscriptions -= 1
    
    def remove_client(self, client_id: str):
        """Remove all state for a disconnected client."""
        if client_id in self.clients:
            state = self.clients.pop(client_id)
            self.total_subscriptions -= state.subscription_count
    
    def _refill_tokens(self, state: ClientRateLimitState, now: float):
        """Refill tokens based on elapsed time."""
        elapsed = now - state.last_update
        state.last_update = now
        
        # Add tokens based on rate and elapsed time
        new_tokens = elapsed * self.sub_rate_limit
        state.tokens = min(state.tokens + new_tokens, float(self.sub_burst_limit))
    
    def _record_violation(self, client_id: str, state: ClientRateLimitState, 
                          violation_type: str):
        """Record a rate limit violation."""
        now = time.time()
        state.violations += 1
        state.last_violation = now
        self.total_violations += 1
        
        self.logger.warning(
            f'Rate limit violation for {client_id}: {violation_type} '
            f'(violation #{state.violations})'
        )
        
        # Block client if too many violations
        if state.violations >= self.violation_threshold:
            state.blocked_until = now + self.block_duration
            self.logger.warning(
                f'Client {client_id} blocked for {self.block_duration}s '
                f'due to {state.violations} violations'
            )
    
    def get_client_stats(self, client_id: str) -> Dict[str, any]:
        """Get rate limiting stats for a client."""
        if client_id not in self.clients:
            return {'subscriptions': 0, 'violations': 0, 'blocked': False}
        
        state = self.clients[client_id]
        now = time.time()
        
        return {
            'subscriptions': state.subscription_count,
            'max_subscriptions': self.max_subs_per_client,
            'violations': state.violations,
            'blocked': state.blocked_until > now,
            'blocked_until': state.blocked_until if state.blocked_until > now else None,
            'tokens_available': int(state.tokens),
            'rate_limit': self.sub_rate_limit,
        }
    
    def get_global_stats(self) -> Dict[str, any]:
        """Get global rate limiting statistics."""
        now = time.time()
        blocked_count = sum(
            1 for state in self.clients.values() 
            if state.blocked_until > now
        )
        
        return {
            'total_clients': len(self.clients),
            'total_subscriptions': self.total_subscriptions,
            'total_violations': self.total_violations,
            'blocked_clients': blocked_count,
            'config': {
                'max_subs_per_client': self.max_subs_per_client,
                'sub_rate_limit': self.sub_rate_limit,
                'sub_burst_limit': self.sub_burst_limit,
                'violation_threshold': self.violation_threshold,
                'block_duration': self.block_duration,
            }
        }
    
    def reset_client(self, client_id: str):
        """Reset rate limiting state for a client (admin function)."""
        if client_id in self.clients:
            state = self.clients[client_id]
            state.violations = 0
            state.blocked_until = 0
            state.tokens = float(self.sub_burst_limit)
            self.logger.info(f'Reset rate limit state for client {client_id}')


class RequestRateLimiter:
    """
    Per-client request rate limiter using sliding window.
    
    Limits the number of requests per time window to prevent
    request flooding.
    """
    
    def __init__(self, env=None):
        self.logger = util.class_logger(__name__, self.__class__.__name__)
        
        # Configuration
        self.window_seconds = getattr(env, 'rate_window_seconds', 60) if env else 60
        self.max_requests_per_window = getattr(env, 'max_requests_per_window', 1000) if env else 1000
        self.cost_soft_limit = getattr(env, 'cost_soft_limit', 1000) if env else 1000
        self.cost_hard_limit = getattr(env, 'cost_hard_limit', 10000) if env else 10000
        
        # Per-client tracking
        self.clients: Dict[str, Dict[str, any]] = {}
    
    def check_request(self, client_id: str, cost: float = 1.0) -> Tuple[bool, Optional[str]]:
        """
        Check if a request should be allowed.
        
        Returns (allowed, error_message).
        """
        now = time.time()
        
        if client_id not in self.clients:
            self.clients[client_id] = {
                'requests': [],
                'total_cost': 0.0,
                'window_start': now,
            }
        
        state = self.clients[client_id]
        
        # Clean up old requests outside window
        cutoff = now - self.window_seconds
        state['requests'] = [t for t in state['requests'] if t > cutoff]
        
        # Reset cost if window has passed
        if state['window_start'] < cutoff:
            state['total_cost'] = 0.0
            state['window_start'] = now
        
        # Check request count limit
        if len(state['requests']) >= self.max_requests_per_window:
            return False, f'Request limit ({self.max_requests_per_window}/min) exceeded'
        
        # Check cost limit
        if state['total_cost'] + cost > self.cost_hard_limit:
            return False, 'Request cost limit exceeded'
        
        return True, None
    
    def record_request(self, client_id: str, cost: float = 1.0):
        """Record a request."""
        now = time.time()
        
        if client_id not in self.clients:
            self.clients[client_id] = {
                'requests': [],
                'total_cost': 0.0,
                'window_start': now,
            }
        
        state = self.clients[client_id]
        state['requests'].append(now)
        state['total_cost'] += cost
    
    def get_cost_remaining(self, client_id: str) -> float:
        """Get remaining cost budget for a client."""
        if client_id not in self.clients:
            return float(self.cost_hard_limit)
        
        state = self.clients[client_id]
        now = time.time()
        
        # Reset if window passed
        if state['window_start'] < now - self.window_seconds:
            return float(self.cost_hard_limit)
        
        return max(0.0, self.cost_hard_limit - state['total_cost'])
    
    def remove_client(self, client_id: str):
        """Remove state for a disconnected client."""
        self.clients.pop(client_id, None)


# Global instances
_subscription_limiter: Optional[SubscriptionRateLimiter] = None
_request_limiter: Optional[RequestRateLimiter] = None


def get_subscription_limiter() -> SubscriptionRateLimiter:
    """Get the global subscription rate limiter."""
    global _subscription_limiter
    if _subscription_limiter is None:
        _subscription_limiter = SubscriptionRateLimiter()
    return _subscription_limiter


def get_request_limiter() -> RequestRateLimiter:
    """Get the global request rate limiter."""
    global _request_limiter
    if _request_limiter is None:
        _request_limiter = RequestRateLimiter()
    return _request_limiter


def init_rate_limiters(env) -> Tuple[SubscriptionRateLimiter, RequestRateLimiter]:
    """Initialize global rate limiters with environment config."""
    global _subscription_limiter, _request_limiter
    _subscription_limiter = SubscriptionRateLimiter(env)
    _request_limiter = RequestRateLimiter(env)
    return _subscription_limiter, _request_limiter
