"""Unit tests for the confirmed-only address-status cache.

blockchain.scripthash.subscribe hashes a scripthash's entire confirmed history.
For scripthashes with very large histories the status was recomputed (full DB
history read + hash) on every call, pegging the server. address_status now caches
the confirmed-only status per hashX and invalidates it on the same touched set as
the history cache. These tests pin that behaviour without standing up a full
session/db.
"""
import asyncio
from unittest import mock

import pylru
import pytest
from aiorpcx import RPCError

from electrumx.server.session import SessionManager, ElectrumX

HX = b"\x01" * 11  # a hashX


def _mk_mgr():
    sm = SessionManager.__new__(SessionManager)
    sm._status_cache = pylru.lrucache(16)
    sm._status_lookups = 0
    sm._status_hits = 0
    sm._history_cache = pylru.lrucache(16)
    sm._ref_get_cache = pylru.lrucache(16)
    # Force address_status down the "history too large" → unlimited-fetch path,
    # which is the expensive one the cache is meant to avoid repeating.
    sm.limited_history = mock.AsyncMock(side_effect=RPCError(1, "history too large"))
    return sm


def _mk_session(sm, full_history, mempool_txs):
    s = ElectrumX.__new__(ElectrumX)
    s.session_mgr = sm
    s.mempool_statuses = {}
    s.bump_cost = lambda *a, **k: None
    s.db = mock.Mock()
    s.db.limited_history = mock.AsyncMock(return_value=full_history)
    s.mempool = mock.Mock()
    s.mempool.transaction_summaries = mock.AsyncMock(return_value=mempool_txs)
    return s


def _mp_tx(h):
    tx = mock.Mock()
    tx.hash = h
    tx.has_unconfirmed_inputs = False
    return tx


@pytest.mark.asyncio
async def test_status_cached_when_no_mempool():
    sm = _mk_mgr()
    s = _mk_session(sm, [(b"\xaa" * 32, 100), (b"\xbb" * 32, 101)], [])

    st1 = await s.address_status(HX)
    st2 = await s.address_status(HX)

    assert st1 is not None
    assert st1 == st2
    # Full history read only once — second call served from the status cache.
    assert s.db.limited_history.await_count == 1
    assert sm._status_hits == 1
    assert HX in sm._status_cache


@pytest.mark.asyncio
async def test_status_not_cached_while_mempool_present():
    sm = _mk_mgr()
    s = _mk_session(sm, [(b"\xaa" * 32, 100)], [_mp_tx(b"\xcc" * 32)])

    await s.address_status(HX)
    await s.address_status(HX)

    # With mempool entries the status changes with the mempool, so it is
    # recomputed every call and never cached.
    assert s.db.limited_history.await_count == 2
    assert HX in s.mempool_statuses
    assert HX not in sm._status_cache


@pytest.mark.asyncio
async def test_notify_invalidates_status_cache():
    sm = _mk_mgr()
    sm._status_cache[HX] = "deadbeef"
    sm._history_cache[HX] = "x"
    sm._ref_get_cache[HX] = "y"
    sm.notified_height = 100
    sm._refresh_hsub_results = mock.AsyncMock()
    sm.sessions = []

    await sm._notify_sessions(101, {HX})  # height changed + hashX touched

    assert HX not in sm._status_cache
    assert HX not in sm._history_cache


@pytest.mark.asyncio
async def test_status_recomputes_after_invalidation():
    sm = _mk_mgr()
    s = _mk_session(sm, None, [])
    hist_a = [(b"\xaa" * 32, 100)]
    hist_b = [(b"\xaa" * 32, 100), (b"\xbb" * 32, 102)]  # new tx in a later block
    s.db.limited_history = mock.AsyncMock(side_effect=[hist_a, hist_b])

    st_a = await s.address_status(HX)  # computes from hist_a, caches
    del sm._status_cache[HX]  # what _notify_sessions does on touch
    st_b = await s.address_status(HX)  # recomputes from hist_b

    assert st_a != st_b  # status reflects the new confirmed history
    assert s.db.limited_history.await_count == 2


# --- reorg cache clear (LOW fix) -------------------------------------------

class _OnceEvent:
    """A backed_up_event stand-in: wait() returns once (reorg signalled), then
    raises CancelledError to break the _handle_chain_reorgs while-loop so the
    coroutine completes deterministically after exactly one iteration."""
    def __init__(self):
        self._calls = 0

    async def wait(self):
        self._calls += 1
        if self._calls > 1:
            raise asyncio.CancelledError()
        return True


@pytest.mark.asyncio
async def test_reorg_clears_status_and_history_caches():
    """A signalled reorg must clear _status_cache and _history_cache (added in
    a8c788f) alongside the tx_hashes/merkle/ref_get caches — defense-in-depth
    against a stale confirmed-status window after a deep reorg."""
    sm = SessionManager.__new__(SessionManager)
    sm._reorg_count = 0
    sm._tx_hashes_cache = pylru.lrucache(16)
    sm._merkle_cache = pylru.lrucache(16)
    sm._ref_get_cache = pylru.lrucache(16)
    sm._status_cache = pylru.lrucache(16)
    sm._history_cache = pylru.lrucache(16)
    sm.logger = mock.Mock()
    sm.bp = mock.Mock()
    sm.bp.backed_up_event = _OnceEvent()

    # Seed every cache.
    sm._tx_hashes_cache[1] = "tx"
    sm._merkle_cache[1] = "mk"
    sm._ref_get_cache[1] = "ref"
    sm._status_cache[HX] = "status"
    sm._history_cache[HX] = "history"

    with pytest.raises(asyncio.CancelledError):
        await sm._handle_chain_reorgs()

    # Exactly one reorg was processed.
    assert sm._reorg_count == 1
    # All caches — including the two new ones — were cleared.
    assert len(sm._tx_hashes_cache) == 0
    assert len(sm._merkle_cache) == 0
    assert len(sm._ref_get_cache) == 0
    assert len(sm._status_cache) == 0
    assert len(sm._history_cache) == 0
