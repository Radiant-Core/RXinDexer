"""
Tests for the per-session subscription hard cap (H3 DoS / memory-exhaustion fix).

These prove that a single live connection cannot exhaust process memory by
looping subscribe with incrementing/oversized keys:

- a session can subscribe up to the cap;
- the (cap+1)th subscription is rejected with a clean error (no crash);
- an over-long WAVE name key is rejected;
- unsubscribing frees a slot;
- disconnect cleanup still works (and frees the whole session);
- the cap is per-session (a second session has its own budget);
- idempotent re-subscribes never trip the cap.
"""

import os
import types

import pytest

from electrumx.server.glyph_subscriptions import (
    GlyphSubscriptionManager,
    SubscriptionLimitError,
    DEFAULT_MAX_SUBS_PER_CLIENT,
    MAX_WAVE_NAME_LEN,
)


REF = b'\x11' * 36
SH = b'\x22' * 32


def _mgr(max_subs=None):
    """Build a manager with an explicit per-session cap."""
    ns = types.SimpleNamespace()
    if max_subs is not None:
        ns.max_subs_per_client = max_subs
    return GlyphSubscriptionManager(ns)


def _ref(i: int) -> bytes:
    """A distinct 36-byte ref for index i (distinct subscription keys)."""
    return i.to_bytes(4, 'big') + b'\x00' * 32


def test_subscribe_up_to_cap_succeeds():
    cap = 5
    mgr = _mgr(cap)
    for i in range(cap):
        assert mgr.subscribe_token(1, _ref(i)) is True
    assert len(mgr.session_subs[1]) == cap


def test_cap_plus_one_is_rejected_without_crash():
    cap = 3
    mgr = _mgr(cap)
    for i in range(cap):
        assert mgr.subscribe_token(7, _ref(i)) is True

    # The (cap+1)th distinct subscription must raise the typed limit error,
    # NOT a generic exception that would kill the session.
    with pytest.raises(SubscriptionLimitError):
        mgr.subscribe_token(7, _ref(cap))

    # And it must NOT have mutated the global maps or the session set.
    assert len(mgr.session_subs[7]) == cap
    assert _ref(cap) not in mgr.token_subs


def test_cap_applies_across_subscription_types():
    cap = 3
    mgr = _mgr(cap)
    assert mgr.subscribe_token(9, _ref(0)) is True
    assert mgr.subscribe_balance(9, SH, REF) is True
    assert mgr.subscribe_dmint(9, _ref(1)) is True
    # Fourth subscription of any type is over the cap.
    with pytest.raises(SubscriptionLimitError):
        mgr.subscribe_orderbook(9, _ref(2), _ref(3))
    assert len(mgr.session_subs[9]) == cap


def test_overlong_wave_name_rejected():
    mgr = _mgr(10000)
    long_name = 'a' * (MAX_WAVE_NAME_LEN + 1)
    with pytest.raises(SubscriptionLimitError):
        mgr.subscribe_wave_name(1, long_name)
    # Nothing was stored for the abusive key.
    assert long_name.lower() not in mgr.wave_name_subs
    assert len(mgr.session_subs[1]) == 0

    # A name exactly at the limit is accepted.
    ok_name = 'b' * MAX_WAVE_NAME_LEN
    assert mgr.subscribe_wave_name(1, ok_name) is True
    assert ok_name in mgr.wave_name_subs


def test_unsubscribe_frees_a_slot():
    cap = 2
    mgr = _mgr(cap)
    assert mgr.subscribe_token(3, _ref(0)) is True
    assert mgr.subscribe_token(3, _ref(1)) is True
    # At cap now.
    with pytest.raises(SubscriptionLimitError):
        mgr.subscribe_token(3, _ref(2))

    # Free a slot and the next subscribe succeeds.
    assert mgr.unsubscribe_token(3, _ref(0)) is True
    assert len(mgr.session_subs[3]) == cap - 1
    assert mgr.subscribe_token(3, _ref(2)) is True
    assert len(mgr.session_subs[3]) == cap


def test_disconnect_cleanup_frees_session_and_does_not_crash():
    cap = 4
    mgr = _mgr(cap)
    for i in range(cap):
        assert mgr.subscribe_token(5, _ref(i)) is True
    assert len(mgr.session_subs[5]) == cap

    # Simulate connection_lost -> unsubscribe_session.
    mgr.unsubscribe_session(5)
    assert 5 not in mgr.session_subs
    # Global maps emptied for this session's keys.
    for i in range(cap):
        assert _ref(i) not in mgr.token_subs

    # A fresh subscription after cleanup gets a full budget again.
    assert mgr.subscribe_token(5, _ref(0)) is True
    assert len(mgr.session_subs[5]) == 1


def test_cap_is_per_session():
    cap = 2
    mgr = _mgr(cap)
    assert mgr.subscribe_token(100, _ref(0)) is True
    assert mgr.subscribe_token(100, _ref(1)) is True
    with pytest.raises(SubscriptionLimitError):
        mgr.subscribe_token(100, _ref(2))

    # A different session has its own independent budget.
    assert mgr.subscribe_token(200, _ref(0)) is True
    assert mgr.subscribe_token(200, _ref(1)) is True
    assert len(mgr.session_subs[200]) == cap


def test_idempotent_resubscribe_does_not_trip_cap():
    cap = 1
    mgr = _mgr(cap)
    assert mgr.subscribe_token(11, _ref(0)) is True
    # Re-subscribing to the SAME key must not be rejected and must not grow.
    assert mgr.subscribe_token(11, _ref(0)) is True
    assert len(mgr.session_subs[11]) == 1
    # But a new distinct key is over the cap.
    with pytest.raises(SubscriptionLimitError):
        mgr.subscribe_token(11, _ref(1))


def test_check_does_not_leak_empty_session_entry():
    """A rejected first subscribe (cap<=0 disabled aside) must not create an
    empty session entry as a side effect of the check."""
    cap = 1
    mgr = _mgr(cap)
    # Session 50 has no entry yet; check path uses .get, so a successful first
    # subscribe is what creates it.
    assert 50 not in mgr.session_subs
    assert mgr.subscribe_token(50, _ref(0)) is True
    assert 50 in mgr.session_subs


def test_cap_disabled_when_non_positive():
    mgr = _mgr(0)
    # With the cap disabled, many subscriptions are accepted.
    for i in range(50):
        assert mgr.subscribe_token(1, _ref(i)) is True
    assert len(mgr.session_subs[1]) == 50


def test_default_cap_resolution():
    # No attr, no env var -> module default.
    saved = os.environ.pop('MAX_SUBS_PER_CLIENT', None)
    try:
        mgr = GlyphSubscriptionManager(types.SimpleNamespace())
        assert mgr.max_subs_per_client == DEFAULT_MAX_SUBS_PER_CLIENT
    finally:
        if saved is not None:
            os.environ['MAX_SUBS_PER_CLIENT'] = saved


def test_env_var_cap_resolution():
    saved = os.environ.get('MAX_SUBS_PER_CLIENT')
    os.environ['MAX_SUBS_PER_CLIENT'] = '7'
    try:
        mgr = GlyphSubscriptionManager(types.SimpleNamespace())
        assert mgr.max_subs_per_client == 7
    finally:
        if saved is None:
            os.environ.pop('MAX_SUBS_PER_CLIENT', None)
        else:
            os.environ['MAX_SUBS_PER_CLIENT'] = saved
