"""Regression tests for the blockchain.ref.get [mint, location] cache.

_ref_get_cache used to be keyed by the RAW 36-byte ref, but _notify_sessions
invalidates caches by intersecting their keys with the block processor's
touched set — which contains 11-byte hashXs (script_hashX(ref)). A raw-ref
key never matched a hashX, so the intersection was always empty and ref.get
served a stale [mint, location] after a singleton moved, until LRU eviction,
a reconnect, or a reorg clear. The cache is now keyed by the ref's hashX,
exactly like _history_cache and _status_cache. These tests pin that contract
without standing up a full session/db.
"""
from unittest import mock

import pylru
import pytest

from electrumx.lib.coins import Coin
from electrumx.server.session import SessionManager

REF = b"\x42" * 36  # a raw singleton ref
REF_HASHX = Coin.hashX_from_script(REF)  # what block_processor puts in touched

MINT_TX = b"\xaa" * 32
LOC_A = b"\xbb" * 32
LOC_B = b"\xcc" * 32


def _mk_mgr(locations):
    sm = SessionManager.__new__(SessionManager)
    sm._ref_get_cache = pylru.lrucache(16)
    sm._ref_get_lookups = 0
    sm._ref_get_hits = 0
    sm._history_cache = pylru.lrucache(16)
    sm._status_cache = pylru.lrucache(16)
    sm.env = mock.Mock()
    sm.env.coin = Coin  # real hashX_from_script: sha256(ref)[:HASHX_LEN]
    sm.db = mock.Mock()
    sm.db.get_ref_mint = mock.Mock(return_value=MINT_TX)
    sm.db.get_ref_location = mock.Mock(side_effect=locations)
    return sm


def _arm_notify(sm):
    sm.notified_height = 100
    sm._refresh_hsub_results = mock.AsyncMock()
    sm.sessions = []


@pytest.mark.asyncio
async def test_ref_get_db_caches_by_hashX():
    sm = _mk_mgr([LOC_A, LOC_A])

    r1, _ = await sm.ref_get_db(REF)
    r2, _ = await sm.ref_get_db(REF)

    assert r1 == r2 == [MINT_TX, LOC_A]
    assert sm.db.get_ref_location.call_count == 1  # second call was a hit
    assert sm._ref_get_hits == 1
    # The key must be the hashX (what touched contains), not the raw ref.
    assert REF_HASHX in sm._ref_get_cache
    assert REF not in sm._ref_get_cache


@pytest.mark.asyncio
async def test_notify_invalidates_ref_get_cache():
    """The live bug: a singleton moves, the block processor adds
    script_hashX(ref) to touched, and ref.get must stop serving the old
    location instead of the stale creation tx."""
    sm = _mk_mgr([LOC_A, LOC_B])
    _arm_notify(sm)

    r1, _ = await sm.ref_get_db(REF)
    assert r1 == [MINT_TX, LOC_A]

    # New block touches the ref — the touched set exactly as block_processor
    # builds it (hashXs, not raw refs).
    await sm._notify_sessions(101, {REF_HASHX})

    assert REF_HASHX not in sm._ref_get_cache
    r2, _ = await sm.ref_get_db(REF)
    assert r2 == [MINT_TX, LOC_B]  # fresh location, not the stale LOC_A
    assert sm.db.get_ref_location.call_count == 2


@pytest.mark.asyncio
async def test_notify_leaves_untouched_refs_cached():
    sm = _mk_mgr([LOC_A])
    _arm_notify(sm)

    await sm.ref_get_db(REF)
    await sm._notify_sessions(101, {b"\x07" * 11})  # some other hashX

    assert REF_HASHX in sm._ref_get_cache
    await sm.ref_get_db(REF)
    assert sm.db.get_ref_location.call_count == 1  # still served from cache
