"""Tests for SessionGroup.retain — the per-group retained-cost cap.

Regression guard for the 2026-07-10 outage: a reconnect storm drove per-/24
`retained_cost` to millions (7x the per-session hard limit). Because that cost
feeds every new session's `extra_cost()` (weight 1.0 for the IP group), any new
session in the /24 was seeded past `cost_hard_limit` and killed on its first
request with "excessive resource usage" (-101), and it decayed at only
`hard_limit/5000` per second → the /24 stayed locked out for HOURS.

Capping retained cost at `cost_hard_limit` bounds this: cost above the hard
limit adds no extra throttling (a group there already fully throttles) and only
prolongs recovery, so the cap is a pure win.
"""

from electrumx.server.session import SessionGroup


def _group(retained=0.0):
    # name, weight, sessions, retained_cost
    return SessionGroup('72.129.131', 1.0, set(), retained)


HARD = 1_000_000.0


def test_increment_never_exceeds_hard_limit():
    g = _group()
    # A reconnect storm: many disconnecting sessions each dump ~soft_limit cost.
    for _ in range(1000):
        g.retain(100_000.0, HARD)
    assert g.retained_cost == HARD  # clamped, not 100,000,000


def test_single_large_increment_is_clamped():
    g = _group(retained=900_000.0)
    g.retain(5_000_000.0, HARD)
    assert g.retained_cost == HARD


def test_decay_reduces_cost():
    g = _group(retained=HARD)
    g.retain(-60_000.0, HARD)  # one _recalc_concurrency refund tick
    assert g.retained_cost == HARD - 60_000.0


def test_decay_clamps_preexisting_oversaturation_in_one_tick():
    # A group left at 7M by a pre-cap build (or an earlier storm) must be pulled
    # back to the cap on the very next decay tick, not bled off over ~10 hours.
    g = _group(retained=7_026_041.0)
    g.retain(-60_000.0, HARD)
    assert g.retained_cost == HARD


def test_never_goes_negative():
    g = _group(retained=10_000.0)
    g.retain(-60_000.0, HARD)
    assert g.retained_cost == 0.0


def test_hard_limit_zero_disables_cap():
    # aiorpcx uses cost_hard_limit <= 0 to mean "do not limit"; the cap must
    # honour that sentinel and not clamp.
    g = _group()
    g.retain(5_000_000.0, 0)
    assert g.retained_cost == 5_000_000.0


def test_cost_reflects_capped_retained():
    g = _group()
    g.retain(9_000_000.0, HARD)
    # cost() = retained_cost + live session cost (no live sessions here)
    assert g.cost() == HARD
