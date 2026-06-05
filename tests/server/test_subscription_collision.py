"""
Unit tests for three RXinDexer correctness/leak fixes:

M3 — balance-subscription scripthash collision / cross-leak
    (electrumx/server/glyph_subscriptions.py)

    Balance subscriptions are keyed by hashX(11)+ref, where hashX is the
    11-byte truncation of a 32-byte client scripthash.  Two distinct 32-byte
    scripthashes that share an 11-byte prefix collide on that key.  Before the
    fix, a single ``balance_scripthash[hashX]`` slot stored the LAST writer's
    full scripthash, so a crafted subscriber could (a) receive a victim's
    glyph.balance notifications and (b) overwrite the echoed scripthash.

    The fix stores each subscription's OWN full 32-byte scripthash and, when a
    notification carries a full scripthash, gates delivery on full-scripthash
    equality (hashX is only the cheap first-level bucket).  The 11-byte hashX
    notify path the indexer uses still reaches the right subscriber, echoing
    that subscriber's own scripthash.

M5 — mempool swap order keying drops orders on RBF
    (electrumx/server/mempool_glyph.py)

    A tx with two RSWP outputs must index BOTH orders, and remove_tx must
    remove both from swap_orders / swap_by_pair / swap_by_maker (no orphan).
"""

import asyncio
import types

from electrumx.lib.hash import HASHX_LEN
from electrumx.server.glyph_subscriptions import GlyphSubscriptionManager
from electrumx.server.mempool_glyph import MempoolGlyphIndex, MempoolSwapOrder


REF = b'\x11' * 36
REF2 = b'\x22' * 36


# ---------------------------------------------------------------------------
# M3 helpers: two 32-byte scripthashes that collide on the 11-byte hashX.
#
# GlyphSubscriptionManager._to_hashX reverses a 32-byte scripthash then takes
# the first HASHX_LEN bytes — i.e. the LAST HASHX_LEN bytes of the original.
# Two scripthashes collide iff their last HASHX_LEN bytes are equal.
# ---------------------------------------------------------------------------

def _colliding_pair():
    shared_tail = b'\x99' * HASHX_LEN
    sh_a = b'\xaa' * (32 - HASHX_LEN) + shared_tail
    sh_b = b'\xbb' * (32 - HASHX_LEN) + shared_tail
    assert sh_a != sh_b
    assert GlyphSubscriptionManager._to_hashX(sh_a) == GlyphSubscriptionManager._to_hashX(sh_b)
    return sh_a, sh_b


def _collect_manager():
    mgr = GlyphSubscriptionManager(types.SimpleNamespace())
    received = []

    async def cb(session_id, notification):
        received.append((session_id, notification))

    mgr.set_notify_callback(cb)
    return mgr, received


# --- M3 tests --------------------------------------------------------------

def test_colliding_scripthashes_share_hashX_bucket_but_keep_own_scripthash():
    """Sanity: the two scripthashes collide on the 11-byte key, yet each
    subscription stores its OWN full scripthash (no single shared slot)."""
    sh_a, sh_b = _colliding_pair()
    mgr = GlyphSubscriptionManager(types.SimpleNamespace())

    mgr.subscribe_balance(1, sh_a, REF)
    mgr.subscribe_balance(2, sh_b, REF)

    key = GlyphSubscriptionManager._to_hashX(sh_a) + REF
    # Both land in the same hashX(11)+ref bucket (the fast match is preserved).
    assert set(mgr.balance_subs[key].keys()) == {1, 2}
    # ...but each keeps its own full scripthash — no last-writer-wins overwrite.
    assert mgr.balance_subs[key][1] == sh_a
    assert mgr.balance_subs[key][2] == sh_b


def test_full_scripthash_notify_does_not_cross_leak():
    """A notification carrying the FULL scripthash of victim B must NOT be
    delivered to the colliding attacker A, and must echo B's own scripthash."""
    sh_a, sh_b = _colliding_pair()
    mgr, received = _collect_manager()

    mgr.subscribe_balance(100, sh_a, REF)  # attacker
    mgr.subscribe_balance(200, sh_b, REF)  # victim

    async def run():
        # Balance change for victim B, dispatched with B's full scripthash.
        await mgr.notify_balance_change(sh_b, REF, 7, 7)

    asyncio.run(run())

    # Only the victim (session 200) is notified; attacker (100) is gated out.
    assert [sid for sid, _ in received] == [200]
    _, note = received[0]
    # The echoed scripthash is the victim's own — never the attacker's.
    assert note['params']['scripthash'] == sh_b.hex()
    assert note['params']['scripthash'] != sh_a.hex()
    assert note['params']['balance'] == 7


def test_hashX_notify_reaches_all_bucket_subscribers_with_own_scripthash():
    """The indexer dispatches with the 11-byte base-address hashX (it has no
    full scripthash).  Every subscriber in the bucket is then notified, each
    echoing ONLY its own stored full scripthash."""
    sh_a, sh_b = _colliding_pair()
    mgr, received = _collect_manager()

    mgr.subscribe_balance(1, sh_a, REF)
    mgr.subscribe_balance(2, sh_b, REF)

    hashX = GlyphSubscriptionManager._to_hashX(sh_a)  # == _to_hashX(sh_b)

    async def run():
        await mgr.notify_balance_change(hashX, REF, 3, 1)

    asyncio.run(run())

    by_session = {sid: note['params']['scripthash'] for sid, note in received}
    assert by_session == {1: sh_a.hex(), 2: sh_b.hex()}


def test_resubscribe_cannot_overwrite_victim_scripthash():
    """A crafted subscriber re-subscribing on the colliding key must not change
    the victim's stored scripthash (no shared per-hashX slot anymore)."""
    sh_a, sh_b = _colliding_pair()
    mgr = GlyphSubscriptionManager(types.SimpleNamespace())

    mgr.subscribe_balance(200, sh_b, REF)   # victim subscribes first
    mgr.subscribe_balance(100, sh_a, REF)   # attacker subscribes second

    key = GlyphSubscriptionManager._to_hashX(sh_b) + REF
    # Victim's scripthash is untouched by the attacker's later subscribe.
    assert mgr.balance_subs[key][200] == sh_b
    assert mgr.balance_subs[key][100] == sh_a


def test_unsubscribe_and_cleanup_preserve_other_subscriber():
    """Unsubscribing the attacker (or disconnecting it) must leave the victim's
    subscription intact, and vice versa."""
    sh_a, sh_b = _colliding_pair()
    mgr = GlyphSubscriptionManager(types.SimpleNamespace())
    key = GlyphSubscriptionManager._to_hashX(sh_a) + REF

    mgr.subscribe_balance(1, sh_a, REF)
    mgr.subscribe_balance(2, sh_b, REF)

    # Explicit unsubscribe of session 1 keeps session 2.
    assert mgr.unsubscribe_balance(1, sh_a, REF) is True
    assert set(mgr.balance_subs[key].keys()) == {2}
    assert mgr.balance_subs[key][2] == sh_b

    # Disconnect cleanup of session 2 empties the bucket entirely.
    mgr.unsubscribe_session(2)
    assert key not in mgr.balance_subs
    assert 2 not in mgr.session_subs


def test_balance_subscription_still_respects_h3_cap():
    """The H3 per-session cap must still apply to balance subscriptions after
    the data-structure change."""
    ns = types.SimpleNamespace(max_subs_per_client=1)
    mgr = GlyphSubscriptionManager(ns)
    sh_a, sh_b = _colliding_pair()

    assert mgr.subscribe_balance(9, sh_a, REF) is True
    # A second distinct balance sub is over the cap.
    from electrumx.server.glyph_subscriptions import SubscriptionLimitError
    import pytest
    with pytest.raises(SubscriptionLimitError):
        mgr.subscribe_balance(9, sh_b, REF2)
    assert len(mgr.session_subs[9]) == 1


# --- M5 helpers ------------------------------------------------------------

class _FakeMemTx:
    """Minimal MemPoolTx-shaped object exposing idx_to_script."""
    def __init__(self, scripts):
        self.idx_to_script = list(scripts)
        self.out_pairs = ()
        self.in_pairs = ()
        self.prevouts = ()


def _make_swap_index():
    """A MempoolGlyphIndex with glyph indexing off so only swap parsing runs."""
    env = types.SimpleNamespace(
        coin=None,
        mempool_glyph_index=False,
        mempool_swap_index=True,
    )
    return MempoolGlyphIndex(env)


def _order(order_id, base_ref, quote_ref, maker, tx_hash):
    o = MempoolSwapOrder()
    o.tx_hash = tx_hash
    o.order_id = order_id
    o.base_ref = base_ref
    o.quote_ref = quote_ref
    o.maker_scripthash = maker
    o.side = 1
    return o


# --- M5 tests --------------------------------------------------------------

def test_tx_with_two_rswp_orders_indexes_both(monkeypatch):
    """A single mempool tx carrying two RSWP outputs indexes BOTH orders
    (keying by tx_hash previously overwrote the first)."""
    idx = _make_swap_index()
    tx_hash = b'\x77' * 32

    base_a = b'\xa1' * 32 + b'\x00\x00\x00\x00'
    base_b = b'\xb2' * 32 + b'\x00\x00\x00\x00'
    quote = b'\xc3' * 32 + b'\x00\x00\x00\x00'
    maker_a = b'\xd4' * 32
    maker_b = b'\xe5' * 32
    oid_a = b'\x01' * 36
    oid_b = b'\x02' * 36

    order_a = _order(oid_a, base_a, quote, maker_a, tx_hash)
    order_b = _order(oid_b, base_b, quote, maker_b, tx_hash)

    # Two OP_RETURN outputs; _parse_rswp_mempool returns a distinct order each.
    parsed = iter([order_a, order_b])
    monkeypatch.setattr(idx, '_parse_rswp_mempool',
                        lambda script, txh, vout: next(parsed))

    memtx = _FakeMemTx([b'\x6a\x01', b'\x6a\x02'])
    assert idx.process_mempool_tx(tx_hash, memtx) is True

    # Both orders are indexed by order_id.
    assert set(idx.swap_orders.keys()) == {oid_a, oid_b}
    # tx_hash -> {order_ids} maps the tx to both orders (for removal).
    assert idx.swap_by_tx[tx_hash] == {oid_a, oid_b}
    # by_pair holds both orders under their respective pair keys.
    assert idx.swap_by_pair[base_a + quote] == {oid_a}
    assert idx.swap_by_pair[base_b + quote] == {oid_b}
    # by_maker holds each maker's order.
    assert idx.swap_by_maker[maker_a] == {oid_a}
    assert idx.swap_by_maker[maker_b] == {oid_b}


def test_remove_tx_removes_all_orders_no_orphan(monkeypatch):
    """remove_tx for a tx that created two orders removes BOTH from
    swap_orders / swap_by_pair / swap_by_maker — no orphan left behind."""
    idx = _make_swap_index()
    tx_hash = b'\x88' * 32

    base_a = b'\xa1' * 32 + b'\x00\x00\x00\x00'
    base_b = b'\xb2' * 32 + b'\x00\x00\x00\x00'
    quote = b'\xc3' * 32 + b'\x00\x00\x00\x00'
    maker_a = b'\xd4' * 32
    maker_b = b'\xe5' * 32
    oid_a = b'\x01' * 36
    oid_b = b'\x02' * 36

    order_a = _order(oid_a, base_a, quote, maker_a, tx_hash)
    order_b = _order(oid_b, base_b, quote, maker_b, tx_hash)
    parsed = iter([order_a, order_b])
    monkeypatch.setattr(idx, '_parse_rswp_mempool',
                        lambda script, txh, vout: next(parsed))

    memtx = _FakeMemTx([b'\x6a\x01', b'\x6a\x02'])
    idx.process_mempool_tx(tx_hash, memtx)

    # Evict / RBF the tx.
    idx.remove_tx(tx_hash)

    # Nothing left behind anywhere.
    assert idx.swap_orders == {}
    assert tx_hash not in idx.swap_by_tx
    assert idx.swap_by_pair == {}
    assert idx.swap_by_maker == {}
    # And the public query surfaces no phantom orders.
    assert idx.get_unconfirmed_swap_orders() == []
    assert idx.get_user_unconfirmed_orders(maker_a) == []
    assert idx.get_user_unconfirmed_orders(maker_b) == []


def test_remove_one_tx_leaves_other_txs_orders(monkeypatch):
    """Removing one tx's order must not disturb a second tx's order that shares
    a pair/maker index bucket."""
    idx = _make_swap_index()
    base = b'\xa1' * 32 + b'\x00\x00\x00\x00'
    quote = b'\xc3' * 32 + b'\x00\x00\x00\x00'
    maker = b'\xd4' * 32

    tx1 = b'\x11' * 32
    tx2 = b'\x22' * 32
    oid1 = b'\x01' * 36
    oid2 = b'\x02' * 36

    o1 = _order(oid1, base, quote, maker, tx1)
    o2 = _order(oid2, base, quote, maker, tx2)

    monkeypatch.setattr(idx, '_parse_rswp_mempool',
                        lambda script, txh, vout: o1)
    idx.process_mempool_tx(tx1, _FakeMemTx([b'\x6a\x01']))
    monkeypatch.setattr(idx, '_parse_rswp_mempool',
                        lambda script, txh, vout: o2)
    idx.process_mempool_tx(tx2, _FakeMemTx([b'\x6a\x02']))

    # Both orders share the same pair and maker buckets.
    assert idx.swap_by_pair[base + quote] == {oid1, oid2}
    assert idx.swap_by_maker[maker] == {oid1, oid2}

    idx.remove_tx(tx1)

    # tx2's order survives intact in every index.
    assert set(idx.swap_orders.keys()) == {oid2}
    assert idx.swap_by_tx[tx2] == {oid2}
    assert idx.swap_by_pair[base + quote] == {oid2}
    assert idx.swap_by_maker[maker] == {oid2}
