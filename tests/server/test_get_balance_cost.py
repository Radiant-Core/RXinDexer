"""Regression test: get_balance session cost must track WORK, not balance value.

2026-07-11 incident: `bump_cost(1.0 + confirmed / 500000)` charged by the
balance in photons — a wallet holding ~5,000 RXD (5e11 photons) cost 1,000,000
per get_balance, instantly saturating COST_HARD_LIMIT. On disconnect that cost
folded into the client's /24 SessionGroup (weight 1.0), so every player in the
subnet got -101 "excessive resource usage" for ~80 minutes. Exactly the players
holding enough RXD to claim land plots (1,000 RXD) were the ones locked out.

Cost must be O(work): ~1.0 on a balance-cache hit, 1.0 + len(utxos)/50 on a
miss (the upstream ElectrumX formula).
"""

import asyncio
from types import SimpleNamespace

from electrumx.server.session import ElectrumX


class FakeDB:
    def __init__(self, cached=None, utxos=()):
        self._cached = cached
        self._utxos = list(utxos)

    def get_cached_balance(self, hashX):
        return self._cached

    def set_cached_balance(self, hashX, value):
        self._cached = value

    async def all_utxos(self, hashX):
        return self._utxos


class FakeMempool:
    def combined_mempool_state(self, hashX):
        return [], [], 0


def _get_balance(db):
    bumps = []
    session = SimpleNamespace(db=db, mempool=FakeMempool(), bump_cost=bumps.append)
    result = asyncio.run(ElectrumX.get_balance(session, b'\x00' * 11))
    return result, sum(bumps)


def test_whale_balance_cache_hit_costs_o1():
    # 50,000 RXD = 5e12 photons. Under the old formula this cost 10,000,000.
    result, cost = _get_balance(FakeDB(cached=5_000_000_000_000))
    assert result['confirmed'] == 5_000_000_000_000
    assert cost <= 2.0


def test_whale_balance_cache_miss_costs_by_utxo_count():
    utxos = [SimpleNamespace(value=1_000_000_000_000) for _ in range(100)]
    result, cost = _get_balance(FakeDB(cached=None, utxos=utxos))
    assert result['confirmed'] == 100_000_000_000_000
    assert cost == 1.0 + 100 / 50  # upstream formula: work, not value


def test_cost_is_independent_of_balance_magnitude():
    _, cost_small = _get_balance(FakeDB(cached=1))
    _, cost_huge = _get_balance(FakeDB(cached=10**15))
    assert cost_small == cost_huge
