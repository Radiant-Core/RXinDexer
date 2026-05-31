"""
Unit tests for the unconfirmed (mempool) Glyph ownership index keying.

These verify that the mempool index and the balance-subscription manager key
unconfirmed token movements by the holder's *base-address* hashX — the exact
key space the confirmed index uses — so that:

  * a token output credits the recipient's base address,
  * spending a token input debits the sender's base address,
  * a client querying with its standard 32-byte Electrum scripthash gets the
    right unconfirmed delta, and
  * glyph.subscribe.balance (keyed by the client scripthash) is matched by a
    notification keyed by the 11-byte base-address hashX produced during
    block/mempool processing.

The token-aware tx fields are faked at the MemPoolTx level (the shape the base
mempool hands to ``process_mempool_tx``), and the confirmed index / coin are
faked with the same hashX formula the real ones use
(``sha256(script).digest()[:HASHX_LEN]``).
"""

import asyncio
import hashlib
import struct
import types

from electrumx.lib.hash import HASHX_LEN
from electrumx.lib.script import Script
from electrumx.server.mempool_glyph import MempoolGlyphIndex
from electrumx.server.glyph_subscriptions import GlyphSubscriptionManager


# --- helpers ---------------------------------------------------------------

def _hashX(script: bytes) -> bytes:
    """Same formula as Coin.hashX_from_script."""
    return hashlib.sha256(script).digest()[:HASHX_LEN]


def _p2pkh(byte: int) -> bytes:
    """A distinct 25-byte P2PKH locking script."""
    return b'\x76\xa9\x14' + bytes([byte]) * 20 + b'\x88\xac'


def _token_script(ref: bytes, p2pkh: bytes) -> bytes:
    """OP_PUSHINPUTREFSINGLETON <ref> OP_DROP <P2PKH> — a Radiant NFT output."""
    return b'\xd8' + ref + b'\x75' + p2pkh


def _client_scripthash(p2pkh: bytes) -> bytes:
    """The 32-byte Electrum scripthash a wallet sends (display byte order)."""
    return hashlib.sha256(p2pkh).digest()[::-1]


def _outpoint(prev_hash: bytes, prev_idx: int) -> bytes:
    return prev_hash + struct.pack('<I', prev_idx)


class _FakeUtxoDB:
    def __init__(self, kv=None):
        self.kv = dict(kv or {})

    def get(self, key):
        return self.kv.get(key)


class _FakeGlyphIndex:
    """Stands in for the confirmed GlyphIndex: known-token set + DB + balance."""
    def __init__(self, known, kv, balances=None):
        self.known = set(known)
        self.db = types.SimpleNamespace(utxo_db=_FakeUtxoDB(kv))
        self._balances = balances or {}

    def _is_known_token(self, ref):
        return ref in self.known

    def get_balance(self, scripthash, ref):
        # mirror GlyphIndex: convert then look up by hashX
        hx = scripthash[::-1][:HASHX_LEN] if len(scripthash) == 32 else scripthash[:HASHX_LEN]
        return self._balances.get((hx, ref), 0)


class _MemTx:
    """Minimal MemPoolTx-shaped object."""
    def __init__(self, prevouts, in_pairs, out_pairs, idx_to_script):
        self.prevouts = tuple(prevouts)
        self.in_pairs = tuple(in_pairs)
        self.out_pairs = tuple(out_pairs)
        self.idx_to_script = list(idx_to_script)
        self.out_srefs = [[] for _ in idx_to_script]
        self.fee = 0
        self.size = 0


def _make_index(known, kv, balances=None):
    coin = types.SimpleNamespace(hashX_from_script=_hashX)
    env = types.SimpleNamespace(coin=coin)
    gi = _FakeGlyphIndex(known, kv, balances)
    return MempoolGlyphIndex(env, glyph_index=gi)


REF = bytes(range(36))          # a 36-byte token ref
REF2 = bytes(range(100, 136))   # a different ref (unknown token)


# --- tests -----------------------------------------------------------------

def test_to_hashX_roundtrip_matches_base_address():
    p2pkh = _p2pkh(0xaa)
    base_hashX = _hashX(p2pkh)
    client_sh = _client_scripthash(p2pkh)
    assert MempoolGlyphIndex._to_hashX(client_sh) == base_hashX
    # idempotent for an already-11-byte hashX (the notify path)
    assert MempoolGlyphIndex._to_hashX(base_hashX) == base_hashX
    # and the subscription manager agrees
    assert GlyphSubscriptionManager._to_hashX(client_sh) == base_hashX
    assert GlyphSubscriptionManager._to_hashX(base_hashX) == base_hashX


def test_credit_keys_recipient_base_address():
    recipient = _p2pkh(0x11)
    token_out = _token_script(REF, recipient)
    memtx = _MemTx(
        prevouts=[],
        in_pairs=[],
        out_pairs=[(_hashX(Script.zero_refs(token_out)), 1)],
        idx_to_script=[token_out],
    )
    idx = _make_index(known={REF}, kv={})
    assert idx.process_mempool_tx(b'\xaa' * 32, memtx) is True

    # Querying with the recipient's standard 32-byte scripthash sees +1.
    client_sh = _client_scripthash(recipient)
    assert idx.get_unconfirmed_glyph_balance(client_sh, REF) == 1
    # touched_balance recorded for dispatch, keyed by base hashX.
    assert (_hashX(recipient), REF) in idx.touched_balance


def test_unknown_ref_not_indexed():
    out = _token_script(REF2, _p2pkh(0x22))
    memtx = _MemTx([], [], [(_hashX(out), 1)], [out])
    idx = _make_index(known={REF}, kv={})   # REF2 is not a known token
    assert idx.process_mempool_tx(b'\xbb' * 32, memtx) is False
    assert idx.get_unconfirmed_glyph_balance(_client_scripthash(_p2pkh(0x22)), REF2) == 0


def test_debit_keys_sender_base_address():
    sender = _p2pkh(0x33)
    sender_base = _hashX(sender)
    prev_hash = b'\xcd' * 32
    op = _outpoint(prev_hash, 0)
    kv = {
        b'ri' + op: REF + b'\x01',     # spent output carried the singleton ref
        b'rb' + op: sender_base,        # ...and its base-address hashX
    }
    # The spend re-sends to a *different* address (recipient).
    recipient = _p2pkh(0x44)
    token_out = _token_script(REF, recipient)
    memtx = _MemTx(
        prevouts=[(prev_hash, 0)],
        in_pairs=[(_hashX(Script.zero_refs(_token_script(REF, sender))), 1)],
        out_pairs=[(_hashX(Script.zero_refs(token_out)), 1)],
        idx_to_script=[token_out],
    )
    idx = _make_index(known={REF}, kv=kv)
    assert idx.process_mempool_tx(b'\xee' * 32, memtx) is True

    # Sender: -1 unconfirmed; recipient: +1 unconfirmed.
    assert idx.get_unconfirmed_glyph_balance(_client_scripthash(sender), REF) == -1
    assert idx.get_unconfirmed_glyph_balance(_client_scripthash(recipient), REF) == 1


def test_self_send_nets_zero():
    addr = _p2pkh(0x55)
    addr_base = _hashX(addr)
    prev_hash = b'\x01' * 32
    op = _outpoint(prev_hash, 2)
    kv = {b'ri' + op: REF + b'\x01', b'rb' + op: addr_base}
    token_out = _token_script(REF, addr)   # re-send to the same address
    memtx = _MemTx(
        prevouts=[(prev_hash, 2)],
        in_pairs=[(_hashX(Script.zero_refs(token_out)), 1)],
        out_pairs=[(_hashX(Script.zero_refs(token_out)), 1)],
        idx_to_script=[token_out],
    )
    idx = _make_index(known={REF}, kv=kv)
    idx.process_mempool_tx(b'\x07' * 32, memtx)
    assert idx.get_unconfirmed_glyph_balance(_client_scripthash(addr), REF) == 0


def test_remove_tx_reverts_movements():
    recipient = _p2pkh(0x66)
    token_out = _token_script(REF, recipient)
    memtx = _MemTx([], [], [(_hashX(token_out), 1)], [token_out])
    idx = _make_index(known={REF}, kv={})
    txh = b'\x09' * 32
    idx.process_mempool_tx(txh, memtx)
    client_sh = _client_scripthash(recipient)
    assert idx.get_unconfirmed_glyph_balance(client_sh, REF) == 1
    idx.remove_tx(txh)
    assert idx.get_unconfirmed_glyph_balance(client_sh, REF) == 0
    assert txh not in idx.glyph_txs


def test_subscription_key_reconciliation_fires():
    """subscribe with the 32-byte scripthash; notify with the 11-byte base
    hashX (as produced by processing) must reach that subscriber, echoing the
    original scripthash."""
    p2pkh = _p2pkh(0x77)
    base_hashX = _hashX(p2pkh)
    client_sh = _client_scripthash(p2pkh)

    env = types.SimpleNamespace()
    mgr = GlyphSubscriptionManager(env)

    received = []

    async def cb(session_id, notification):
        received.append((session_id, notification))

    mgr.set_notify_callback(cb)
    assert mgr.subscribe_balance(42, client_sh, REF) is True

    async def run():
        # Dispatch keyed by the 11-byte base hashX (what mempool/block produce).
        await mgr.notify_balance_change(base_hashX, REF, 5, 3)

    asyncio.get_event_loop().run_until_complete(run()) if False else asyncio.run(run())

    assert len(received) == 1, received
    session_id, note = received[0]
    assert session_id == 42
    assert note['method'] == 'glyph.balance'
    # payload echoes the wallet's original 32-byte scripthash, not the hashX
    assert note['params']['scripthash'] == client_sh.hex()
    assert note['params']['balance'] == 5
    assert note['params']['delta'] == 3


def test_subscription_no_match_for_other_address():
    mgr = GlyphSubscriptionManager(types.SimpleNamespace())
    received = []

    async def cb(session_id, notification):
        received.append((session_id, notification))

    mgr.set_notify_callback(cb)
    mgr.subscribe_balance(1, _client_scripthash(_p2pkh(0x01)), REF)

    async def run():
        # A different address's base hashX must not fire.
        await mgr.notify_balance_change(_hashX(_p2pkh(0x02)), REF, 1, 1)

    asyncio.run(run())
    assert received == []
