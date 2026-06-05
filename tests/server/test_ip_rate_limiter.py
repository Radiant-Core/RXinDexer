"""Tests for the IP-persistent, proxy-aware rate limiter (H3 follow-up).

These prove the per-IP AGGREGATE layer that the per-session subscription cap
(GlyphSubscriptionManager) cannot provide:

- per-IP state PERSISTS across reconnects (cost carried over, not reset);
- the aggregate subscription cap is enforced across two sessions from one IP;
- the cost hard-limit blocks an IP and the block survives a reconnect;
- proxy-aware IP resolution: with TRUST_PROXY on, X-Forwarded-For is honoured
  from the trusted hop and the private/loopback proxy peer is NOT exempted;
  with it off, the socket peer is used;
- per-IP state is evicted after the idle TTL;
- behaviour is unchanged when the feature is disabled.

Sessions are lightweight fakes (an object exposing remote_address() and,
optionally, request headers) — no live server is required.
"""

import types

import pytest

from aiorpcx import NetAddress

from electrumx.server.rate_limiter import (
    IPRateLimiter,
    IPState,
    init_rate_limiters,
    get_ip_rate_limiter,
    DEFAULT_MAX_SUBS_PER_IP,
    DEFAULT_IP_COST_HARD_LIMIT,
    DEFAULT_RATE_BLOCK_DURATION,
    DEFAULT_IP_STATE_TTL,
)


# --------------------------------------------------------------------------- #
# Lightweight fakes
# --------------------------------------------------------------------------- #

class _FakeHeaders(dict):
    """Case-insensitive .get, like websockets.Headers (good enough for tests)."""

    def get(self, key, default=None):
        for k, v in self.items():
            if str(k).lower() == str(key).lower():
                return v
        return default


class FakeSession:
    """Minimal stand-in for an aiorpcx session for IP resolution."""

    def __init__(self, peer_host='1.2.3.4', port=50001, headers=None,
                 session_id=0):
        self._peer = NetAddress(peer_host, port) if peer_host else None
        self.session_id = session_id
        self.client_ip = None
        if headers is not None:
            self.request_headers = _FakeHeaders(headers)

    def remote_address(self):
        return self._peer


def _env(**overrides):
    ns = types.SimpleNamespace()
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


# --------------------------------------------------------------------------- #
# Config / defaults
# --------------------------------------------------------------------------- #

def test_defaults_when_no_env(monkeypatch):
    for var in ('RATE_LIMIT_ENABLED', 'TRUST_PROXY', 'TRUST_PROXY_HOPS',
                'MAX_SUBS_PER_IP', 'IP_COST_HARD_LIMIT', 'RATE_BLOCK_DURATION',
                'IP_STATE_TTL', 'IP_COST_DECAY_PER_SEC'):
        monkeypatch.delenv(var, raising=False)
    rl = IPRateLimiter(None)
    assert rl.enabled is True
    assert rl.trust_proxy is False
    assert rl.trust_proxy_hops == 1
    assert rl.max_subs_per_ip == DEFAULT_MAX_SUBS_PER_IP
    assert rl.ip_cost_hard_limit == DEFAULT_IP_COST_HARD_LIMIT
    assert rl.block_duration == DEFAULT_RATE_BLOCK_DURATION
    assert rl.ip_state_ttl == DEFAULT_IP_STATE_TTL


def test_env_attr_overrides():
    rl = IPRateLimiter(_env(
        trust_proxy=True, trust_proxy_hops=2, max_subs_per_ip=42,
        ip_cost_hard_limit=999.0, rate_block_duration=7, ip_state_ttl=11,
    ))
    assert rl.trust_proxy is True
    assert rl.trust_proxy_hops == 2
    assert rl.max_subs_per_ip == 42
    assert rl.ip_cost_hard_limit == 999.0
    assert rl.block_duration == 7
    assert rl.ip_state_ttl == 11


def test_init_installs_global():
    rl = init_rate_limiters(_env(max_subs_per_ip=5))
    assert get_ip_rate_limiter() is rl
    assert rl.max_subs_per_ip == 5


# --------------------------------------------------------------------------- #
# Proxy-aware IP resolution
# --------------------------------------------------------------------------- #

def test_socket_peer_when_proxy_off():
    rl = IPRateLimiter(_env(trust_proxy=False))
    sess = FakeSession(peer_host='8.8.8.8',
                       headers={'X-Forwarded-For': '203.0.113.7'})
    # Proxy off => the X-Forwarded-For header is IGNORED, socket peer used.
    assert rl.client_ip(sess) == '8.8.8.8'


def test_xff_honoured_from_trusted_hop_when_proxy_on():
    rl = IPRateLimiter(_env(trust_proxy=True, trust_proxy_hops=1))
    sess = FakeSession(
        peer_host='127.0.0.1',
        headers={'X-Forwarded-For': '203.0.113.7, 10.0.0.1'},
    )
    # hops=1 => take the right-most (the hop the trusted proxy appended).
    assert rl.client_ip(sess) == '10.0.0.1'


def test_xff_multi_hop_selection():
    rl = IPRateLimiter(_env(trust_proxy=True, trust_proxy_hops=2))
    sess = FakeSession(
        peer_host='127.0.0.1',
        headers={'X-Forwarded-For': '203.0.113.7, 198.51.100.9, 10.0.0.1'},
    )
    # hops=2 => take the 2nd-from-right (skip one untrusted appended hop).
    assert rl.client_ip(sess) == '198.51.100.9'


def test_x_real_ip_fallback_when_proxy_on():
    rl = IPRateLimiter(_env(trust_proxy=True))
    sess = FakeSession(peer_host='127.0.0.1',
                       headers={'X-Real-IP': '203.0.113.42'})
    assert rl.client_ip(sess) == '203.0.113.42'


def test_private_peer_not_exempt_when_proxy_trusted():
    # With a trusted proxy the socket peer is loopback; the limiter must NOT
    # exempt it (that would let everything through the proxy go untracked).
    rl = IPRateLimiter(_env(trust_proxy=True))
    loopback_sess = FakeSession(peer_host='127.0.0.1')
    assert rl.is_exempt_peer(loopback_sess) is False
    # And with no XFF, the resolved IP falls back to the (loopback) peer so it
    # is still tracked rather than silently exempted.
    assert rl.client_ip(loopback_sess) == '127.0.0.1'


def test_private_peer_exempt_when_proxy_off():
    rl = IPRateLimiter(_env(trust_proxy=False))
    loopback_sess = FakeSession(peer_host='127.0.0.1')
    assert rl.is_exempt_peer(loopback_sess) is True
    public_sess = FakeSession(peer_host='1.2.3.4')
    assert rl.is_exempt_peer(public_sess) is False


# --------------------------------------------------------------------------- #
# Cost persistence across reconnect
# --------------------------------------------------------------------------- #

def test_cost_persists_across_reconnect():
    # No decay so we can assert exact carry-over.
    rl = IPRateLimiter(_env(ip_cost_decay_per_sec=0.0,
                            ip_cost_hard_limit=1_000_000))
    ip = '203.0.113.10'
    now = 1000.0

    # --- Session A connects, runs up some cost, disconnects.
    seed_a = rl.register_session(ip, session_id=1, now=now)
    assert seed_a == 0.0   # first ever connection: nothing persisted yet
    rl.release_session(ip, session_id=1, session_cost=500.0, now=now + 1)

    # --- Session B (a RECONNECT from the same IP) must inherit the cost.
    seed_b = rl.register_session(ip, session_id=2, now=now + 2)
    assert seed_b == 500.0, 'reconnect did NOT carry over persisted cost'

    # The IP state itself was never dropped on the disconnect.
    st = rl.get_state(ip)
    assert st is not None
    assert st.cost == 500.0
    assert 2 in st.sessions and 1 not in st.sessions


def test_cost_decays_over_time():
    rl = IPRateLimiter(_env(ip_cost_decay_per_sec=10.0,
                            ip_cost_hard_limit=1_000_000))
    ip = '203.0.113.11'
    rl.add_cost(ip, 1000.0, now=0.0)
    # 50 seconds later, 10/sec decay => 500 removed.
    allowed, _ = rl.check_cost(ip, now=50.0)
    assert allowed is True
    st = rl.get_state(ip)
    assert abs(st.cost - 500.0) < 1e-6


# --------------------------------------------------------------------------- #
# Cost hard limit + block persistence
# --------------------------------------------------------------------------- #

def test_cost_hard_limit_blocks_and_block_persists_across_reconnect():
    rl = IPRateLimiter(_env(ip_cost_hard_limit=1000.0,
                            ip_cost_decay_per_sec=0.0,
                            rate_block_duration=300))
    ip = '203.0.113.20'
    now = 5000.0

    # Session A drives cost to/over the limit and disconnects.
    rl.register_session(ip, session_id=1, now=now)
    rl.add_cost(ip, 1200.0, now=now)
    allowed, reason = rl.check_cost(ip, now=now)
    assert allowed is False and 'cost limit' in reason
    rl.release_session(ip, session_id=1, session_cost=0.0, now=now)

    st = rl.get_state(ip)
    assert st.violations == 1
    assert st.blocked_until == now + 300

    # Session B reconnects 10s later: still blocked (block persisted).
    rl.register_session(ip, session_id=2, now=now + 10)
    allowed, reason = rl.check_cost(ip, now=now + 10)
    assert allowed is False and 'blocked' in reason

    # After the block window the IP is allowed again (cost was reset on block,
    # decay 0 so it stays low).
    allowed, _ = rl.check_cost(ip, now=now + 301)
    assert allowed is True


def test_cost_limit_disabled_when_non_positive():
    rl = IPRateLimiter(_env(ip_cost_hard_limit=0))
    ip = '203.0.113.21'
    rl.add_cost(ip, 10_000_000.0, now=0.0)
    allowed, reason = rl.check_cost(ip, now=0.0)
    assert allowed is True and reason == ''


# --------------------------------------------------------------------------- #
# Aggregate subscription cap across sessions from one IP
# --------------------------------------------------------------------------- #

def test_aggregate_sub_cap_across_two_sessions_one_ip():
    rl = IPRateLimiter(_env(max_subs_per_ip=10))
    ip = '203.0.113.30'
    rl.register_session(ip, session_id=1, now=0.0)
    rl.register_session(ip, session_id=2, now=0.0)

    # Session 1 takes 6 subs, session 2 takes 4 -> aggregate 10 (at cap).
    rl.note_subscribed(ip, count=6)
    rl.note_subscribed(ip, count=4)

    # The 11th (from EITHER session) is rejected by the aggregate cap.
    allowed, reason = rl.check_can_subscribe(ip, count=1)
    assert allowed is False
    assert 'per IP' in reason

    # Different IP has its own independent budget.
    other = '203.0.113.31'
    allowed, _ = rl.check_can_subscribe(other, count=1)
    assert allowed is True


def test_aggregate_sub_cap_with_explicit_current_override():
    # The live wiring passes the authoritative aggregate count from the
    # GlyphSubscriptionManager; prove the cap honours that override.
    rl = IPRateLimiter(_env(max_subs_per_ip=5))
    ip = '203.0.113.32'
    rl.register_session(ip, session_id=1, now=0.0)
    allowed, _ = rl.check_can_subscribe(ip, count=1, current=4)
    assert allowed is True
    allowed, reason = rl.check_can_subscribe(ip, count=1, current=5)
    assert allowed is False and 'per IP' in reason


def test_sub_cap_disabled_when_non_positive():
    rl = IPRateLimiter(_env(max_subs_per_ip=0))
    ip = '203.0.113.33'
    rl.note_subscribed(ip, count=100000)
    allowed, _ = rl.check_can_subscribe(ip, count=1)
    assert allowed is True


def test_unsubscribe_frees_aggregate_slots():
    rl = IPRateLimiter(_env(max_subs_per_ip=3))
    ip = '203.0.113.34'
    rl.register_session(ip, session_id=1, now=0.0)
    rl.note_subscribed(ip, count=3)
    allowed, _ = rl.check_can_subscribe(ip, count=1)
    assert allowed is False
    rl.note_unsubscribed(ip, count=1)
    allowed, _ = rl.check_can_subscribe(ip, count=1)
    assert allowed is True


# --------------------------------------------------------------------------- #
# TTL eviction
# --------------------------------------------------------------------------- #

def test_idle_ip_state_evicted_after_ttl():
    rl = IPRateLimiter(_env(ip_state_ttl=100, ip_cost_decay_per_sec=0.0))
    ip = '203.0.113.40'
    rl.register_session(ip, session_id=1, now=0.0)
    rl.release_session(ip, session_id=1, session_cost=10.0, now=0.0)
    assert rl.get_state(ip) is not None

    # Before TTL elapses, state survives (reconnect window).
    assert rl.evict_stale(now=50.0) == 0
    assert rl.get_state(ip) is not None

    # After TTL of inactivity, the idle state is reaped.
    removed = rl.evict_stale(now=200.0)
    assert removed == 1
    assert rl.get_state(ip) is None


def test_live_sessions_never_evicted():
    rl = IPRateLimiter(_env(ip_state_ttl=10, ip_cost_decay_per_sec=0.0))
    ip = '203.0.113.41'
    rl.register_session(ip, session_id=1, now=0.0)
    # Session still live (never released) -> never evicted even past the TTL.
    assert rl.evict_stale(now=1000.0) == 0
    assert rl.get_state(ip) is not None


# --------------------------------------------------------------------------- #
# Feature disabled -> graceful no-op
# --------------------------------------------------------------------------- #

def test_disabled_limiter_is_a_noop():
    rl = IPRateLimiter(_env(rate_limit_enabled=False, max_subs_per_ip=1,
                            ip_cost_hard_limit=1.0))
    ip = '203.0.113.50'
    # register returns 0 cost and creates no state.
    assert rl.register_session(ip, session_id=1) == 0.0
    assert rl.get_state(ip) is None
    rl.add_cost(ip, 10_000.0)
    rl.note_subscribed(ip, count=1000)
    # All checks allow.
    assert rl.check_cost(ip) == (True, '')
    assert rl.check_can_subscribe(ip, count=1)[0] is True
    # Still no state was created behind the scenes.
    assert rl.get_state(ip) is None


# --------------------------------------------------------------------------- #
# IPState unit behaviour
# --------------------------------------------------------------------------- #

def test_ipstate_decay_floor_at_zero():
    st = IPState('x', now=0.0)
    st.cost = 5.0
    st.decay(now=1000.0, decay_per_sec=1.0)  # would go negative
    assert st.cost == 0.0


# --------------------------------------------------------------------------- #
# Session-layer wiring (uses the real SessionManager methods, fake sessions)
# --------------------------------------------------------------------------- #

from electrumx.server.session import SessionManager


class _StubMgr:
    """Bind the SessionManager wiring methods to a minimal object.

    We invoke the real, unbound SessionManager methods so the test exercises
    the exact production code in add_session / remove_session / client_ip /
    ip_glyph_sub_count without constructing a full server.
    """

    def __init__(self, limiter, bp=None):
        self.ip_rate_limiter = limiter
        self.sessions = {}
        self.bp = bp
        import logging
        self.logger = logging.getLogger('stub')

    client_ip = SessionManager.client_ip
    ip_glyph_sub_count = SessionManager.ip_glyph_sub_count
    _ip_addr_group_name = SessionManager._ip_addr_group_name
    _register_session_ip = SessionManager._register_session_ip


def test_session_wiring_seeds_cost_on_reconnect():
    limiter = IPRateLimiter(_env(ip_cost_decay_per_sec=0.0,
                                 ip_cost_hard_limit=1_000_000))
    mgr = _StubMgr(limiter)
    ip = '203.0.113.60'

    # --- Session A: register (real wiring), accrue cost, release.
    sess_a = FakeSession(peer_host=ip, session_id=1)
    sess_a.cost = 5.0
    mgr._register_session_ip(sess_a)
    assert sess_a.client_ip == ip
    # The seed remembered is the (unchanged) base cost.
    assert sess_a._ip_seed_cost == 5.0

    # Session ran up to cost 800; release folds back extra = 800 - 5 = 795.
    sess_a.cost = 800.0
    extra = max(0.0, sess_a.cost - sess_a._ip_seed_cost)
    limiter.release_session(sess_a.client_ip, sess_a.session_id,
                            session_cost=extra, sub_count=0)

    # --- Session B reconnects: real wiring must seed its cost from the IP state.
    sess_b = FakeSession(peer_host=ip, session_id=2)
    sess_b.cost = 5.0
    mgr._register_session_ip(sess_b)
    # Persisted IP cost = 795 (the extra) > base 5 -> session cost seeded up.
    assert sess_b.cost == 795.0
    assert sess_b._ip_seed_cost == 795.0


def test_ip_addr_group_name_proxy_aware():
    # Proxy ON: socket peer is loopback, real client comes from XFF; group must
    # be the /24 of the forwarded client, NOT exempted as private.
    limiter = IPRateLimiter(_env(trust_proxy=True, trust_proxy_hops=1))
    mgr = _StubMgr(limiter)
    sess = FakeSession(peer_host='127.0.0.1', session_id=1,
                       headers={'X-Forwarded-For': '1.2.3.4, 10.0.0.2'})
    # hops=1 -> right-most appended hop 10.0.0.2 is private -> exempt (None).
    assert mgr._ip_addr_group_name(sess) is None

    sess2 = FakeSession(peer_host='127.0.0.1', session_id=2,
                        headers={'X-Forwarded-For': '1.2.3.4'})
    # Real client 1.2.3.4 is public -> grouped on its /24, NOT exempted even
    # though the socket peer (the proxy) is loopback.
    assert mgr._ip_addr_group_name(sess2) == '1.2.3'

    # Proxy OFF: behaviour unchanged -> private socket peer exempt.
    limiter_off = IPRateLimiter(_env(trust_proxy=False))
    mgr_off = _StubMgr(limiter_off)
    assert mgr_off._ip_addr_group_name(
        FakeSession(peer_host='127.0.0.1')) is None
    assert mgr_off._ip_addr_group_name(
        FakeSession(peer_host='1.2.3.4')) == '1.2.3'


def test_ip_glyph_sub_count_aggregates_live_sessions():
    # bp.subscriptions.session_subs holds the authoritative per-session subs.
    subs = types.SimpleNamespace(session_subs={
        1: {('token', b'a'), ('token', b'b')},   # 2 subs
        2: {('token', b'c')},                      # 1 sub
        3: {('token', b'd')},                      # different IP
    })
    bp = types.SimpleNamespace(subscriptions=subs)
    limiter = IPRateLimiter(_env())
    mgr = _StubMgr(limiter, bp=bp)

    ip = '203.0.113.70'
    s1 = FakeSession(peer_host=ip, session_id=1); s1.client_ip = ip
    s2 = FakeSession(peer_host=ip, session_id=2); s2.client_ip = ip
    s3 = FakeSession(peer_host='203.0.113.71', session_id=3)
    s3.client_ip = '203.0.113.71'
    mgr.sessions = {s1: [], s2: [], s3: []}

    # Aggregate across the IP's two live sessions = 2 + 1 = 3.
    assert mgr.ip_glyph_sub_count(ip) == 3
    # The other IP only has its own one sub.
    assert mgr.ip_glyph_sub_count('203.0.113.71') == 1
