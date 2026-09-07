"""
Tests for the singleton ref location chain.

A singleton's life is a chain of outpoints: mint -> each spend that re-creates the ref -> the
current UTXO, or a melt. `blockchain.ref.get` returns only the two endpoints; these rows are the
hops between them.

Before this, GlyphEventType.TRANSFER existed but was NEVER emitted — only DEPLOY, MINT and BURN
were written — which is also why get_token_trades returned nothing. The hops are recorded into the
existing GH keyspace rather than a parallel one, because its key shape (ref + height + tx_idx) is
already exactly right and already iterates height-ascending.

Run: PYTHONPATH=. python3 -m pytest tests/test_ref_location_history.py
"""
import os
import struct
import sys
from collections import defaultdict
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from electrumx.lib.hash import HASHX_LEN  # noqa: E402
from electrumx.lib.script import OpCodes  # noqa: E402
from electrumx.server.glyph_index import (  # noqa: E402
    NO_VOUT, GlyphDBKeys, GlyphEventType, GlyphIndex, pack_history_key, pack_ref,
)

TXID = bytes([0xAB]) * 32
REF = pack_ref(TXID, 0)
HOLDER = bytes([0x7A]) * HASHX_LEN


class _StubUtxoDB:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def iterator(self, prefix=b'', seek=None, reverse=False):
        keys = sorted(k for k in self.store if k.startswith(prefix))
        if seek is not None:
            keys = [k for k in keys if k >= seek]
        for key in keys:
            yield key, self.store[key]


class _Coin:
    P2PKH_VERBYTE = bytes.fromhex('00')
    P2SH_VERBYTES = [bytes.fromhex('05')]


def _index():
    idx = object.__new__(GlyphIndex)
    idx.enabled = True
    idx.db = SimpleNamespace(utxo_db=_StubUtxoDB(), db_height=1000)
    idx.env = SimpleNamespace(coin=_Coin)
    idx.history_cache = []
    # What is_plumbing_singleton reads. Empty means "nothing is plumbing", which is what the
    # hop tests want; the filter has its own tests below.
    idx._plumbing_cache = {}
    idx.contract_to_token_cache = {}
    idx.token_cache = {}
    idx.get_token = lambda ref: None            # not an FT unless a test says so
    idx._decode_cursor = lambda c: None
    idx._encode_cursor = lambda k: 'CUR'
    return idx


def _commit(idx):
    """Move recorded hops into the stub DB, as flush() would.

    Mirrors flush's collision handling. This helper originally did a plain last-write-wins
    assignment, which is precisely why the DEPLOY-clobbers-MINT-hop bug was invisible to these
    tests while being visible on mainnet the moment anyone queried a real singleton.
    """
    merged = {}
    for _height, key, value in idx.history_cache:
        prev = merged.get(key)
        merged[key] = GlyphIndex.merge_history_values(prev, value) if prev else value
    idx.db.utxo_db.store.update(merged)
    idx.history_cache.clear()


# --------------------------------------------------------------------------- value encoding

def test_transfer_round_trips_vout_and_holder():
    idx = _index()
    idx.record_ref_hop(REF, GlyphEventType.TRANSFER, TXID, 500, 3, 7, HOLDER)
    _height, _key, value = idx.history_cache[0]
    got = GlyphIndex.decode_history_value(value)
    assert got['event'] == GlyphEventType.TRANSFER
    assert got['txid'] == TXID
    assert got['vout'] == 7
    assert got['holder_hashX'] == HOLDER


def test_melt_has_no_vout():
    """A melt ends the chain: the ref was consumed, so there is no output to point at."""
    idx = _index()
    idx.record_ref_hop(REF, GlyphEventType.MELT, TXID, 900, 0, None, b'')
    _h, _k, value = idx.history_cache[0]
    got = GlyphIndex.decode_history_value(value)
    assert got['event'] == GlyphEventType.MELT
    assert got['vout'] is None, 'melt must not claim an output index'
    assert got['holder_hashX'] is None


def test_no_vout_sentinel_is_distinct_from_vout_zero():
    """vout 0 is a real, common output index and must not collide with the melt sentinel."""
    idx = _index()
    idx.record_ref_hop(REF, GlyphEventType.TRANSFER, TXID, 1, 0, 0, HOLDER)
    got = GlyphIndex.decode_history_value(idx.history_cache[0][2])
    assert got['vout'] == 0
    assert NO_VOUT != 0


# --------------------------------------------------------------------------- backward compatibility

def test_legacy_deploy_and_burn_rows_still_decode():
    """33-byte rows written before the location log existed must not mis-parse."""
    legacy = struct.pack('<B', GlyphEventType.DEPLOY) + TXID
    got = GlyphIndex.decode_history_value(legacy)
    assert got['event'] == GlyphEventType.DEPLOY
    assert got['txid'] == TXID
    assert got['vout'] is None and got['holder_hashX'] is None


def test_legacy_mint_row_keeps_its_amount():
    """MINT rows carry an amount where the new layout carries vout+holder; the event type is what
    disambiguates them, not the length alone."""
    legacy = struct.pack('<B', GlyphEventType.MINT) + TXID + struct.pack('<Q', 4242)
    got = GlyphIndex.decode_history_value(legacy)
    assert got['amount'] == 4242
    assert got['vout'] is None, 'an amount must never be read as a vout'


def test_empty_value_does_not_raise():
    assert GlyphIndex.decode_history_value(b'')['event'] is None


# --------------------------------------------------------------------------- the chain

def test_chain_is_height_ascending_and_complete():
    idx = _index()
    # Recorded out of order; the key layout must still sort correctly.
    idx.record_ref_hop(REF, GlyphEventType.TRANSFER, TXID, 700, 1, 2, HOLDER)
    idx.record_ref_hop(REF, GlyphEventType.MINT, TXID, 500, 0, 0, HOLDER)
    idx.record_ref_hop(REF, GlyphEventType.MELT, TXID, 900, 5, None, b'')
    _commit(idx)

    out = idx.get_ref_location_history(REF)
    assert [r['height'] for r in out['rows']] == [500, 700, 900]
    assert [r['event'] for r in out['rows']] == ['mint', 'transfer', 'melt']
    assert out['rows'][0]['vout'] == 0
    assert out['rows'][2]['vout'] is None
    assert out['ref'] == f'{TXID[::-1].hex()}_0'


def test_other_refs_are_not_included():
    idx = _index()
    other = pack_ref(bytes([0xCD]) * 32, 0)
    idx.record_ref_hop(REF, GlyphEventType.MINT, TXID, 500, 0, 0, HOLDER)
    idx.record_ref_hop(other, GlyphEventType.MINT, TXID, 501, 0, 0, HOLDER)
    _commit(idx)
    assert len(idx.get_ref_location_history(REF)['rows']) == 1


def test_pagination_sets_a_cursor():
    idx = _index()
    for h in range(500, 510):
        idx.record_ref_hop(REF, GlyphEventType.TRANSFER, TXID, h, 0, 1, HOLDER)
    _commit(idx)
    out = idx.get_ref_location_history(REF, limit=3)
    assert len(out['rows']) == 3
    assert out['next_cursor'] == 'CUR'


def test_fungible_refs_return_empty_with_a_note():
    """An FT ref multiplies across outputs, so a location chain is not defined for it."""
    from electrumx.lib.glyph import GlyphProtocol

    idx = _index()
    idx.get_token = lambda ref: SimpleNamespace(protocols=[GlyphProtocol.GLYPH_FT])
    idx.record_ref_hop(REF, GlyphEventType.MINT, TXID, 500, 0, 0, HOLDER)
    _commit(idx)

    out = idx.get_ref_location_history(REF)
    assert out['rows'] == []
    assert 'singleton' in out['note']


def test_nft_with_ft_protocol_is_still_chained():
    """[1,2] is an NFT that also declares FT; only a pure FT is excluded."""
    from electrumx.lib.glyph import GlyphProtocol

    idx = _index()
    idx.get_token = lambda ref: SimpleNamespace(
        protocols=[GlyphProtocol.GLYPH_FT, GlyphProtocol.GLYPH_NFT])
    idx.record_ref_hop(REF, GlyphEventType.MINT, TXID, 500, 0, 0, HOLDER)
    _commit(idx)
    assert len(idx.get_ref_location_history(REF)['rows']) == 1


# --------------------------------------------------------------------------- holder resolution

def test_holder_address_resolves_from_the_owner_index():
    idx = _index()
    h160 = bytes(range(20))
    p2pkh = (bytes([OpCodes.OP_DUP, OpCodes.OP_HASH160, 20]) + h160
             + bytes([OpCodes.OP_EQUALVERIFY, OpCodes.OP_CHECKSIG]))
    idx.db.utxo_db.store[GlyphDBKeys.OWNER + HOLDER] = p2pkh
    idx.record_ref_hop(REF, GlyphEventType.TRANSFER, TXID, 500, 0, 1, HOLDER)
    _commit(idx)

    addr = idx.get_ref_location_history(REF)['rows'][0]['holder_address']
    assert isinstance(addr, str) and addr.startswith('1'), addr


def test_holder_address_is_none_when_unresolvable():
    """A hint, not a guarantee — no owner row, or a non-P2PKH/P2SH script, yields null."""
    idx = _index()
    idx.record_ref_hop(REF, GlyphEventType.TRANSFER, TXID, 500, 0, 1, HOLDER)
    idx.db.utxo_db.store[GlyphDBKeys.OWNER + HOLDER] = b'\x6a\x04junk'   # OP_RETURN
    _commit(idx)
    assert idx.get_ref_location_history(REF)['rows'][0]['holder_address'] is None


def test_script_to_address_handles_p2sh_and_rejects_junk():
    h160 = bytes(range(20))
    p2sh = bytes([OpCodes.OP_HASH160, 20]) + h160 + bytes([OpCodes.OP_EQUAL])
    got = GlyphIndex.script_to_address(p2sh, _Coin)
    assert isinstance(got, str) and got.startswith('3'), got
    assert GlyphIndex.script_to_address(b'', _Coin) is None
    assert GlyphIndex.script_to_address(b'\x6a\x04junk', _Coin) is None


# --------------------------------------------------------------------------- wiring

def test_block_processor_records_hops_where_it_detects_them():
    """Source guard: the hop must be recorded at the site that already distinguishes mint from
    transfer, where vout and holder are in scope — reconstructing either later needs a rescan."""
    src = open(os.path.join(os.path.dirname(__file__), '..', 'electrumx', 'server',
                            'block_processor.py'), encoding='utf-8').read()
    assert 'self.glyph_index.record_ref_hop(' in src
    assert 'GlyphEventType.MINT if ref in spent_outpoints' in src
    assert 'GlyphEventType.MELT' in src
    assert 'spent_singleton_refs - recreated_singletons' in src


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))


# --------------------------------------------------------------------------- backfill

from electrumx.server.ref_history_backfill import (  # noqa: E402
    RefHistoryBackfill, RefHistoryBackfillKeys,
)

OTHER_TXID = bytes([0xCD]) * 32


class _Out:
    def __init__(self, script):
        self.pk_script = script


class _In:
    def __init__(self, prev_hash=b'\x00' * 32, prev_idx=0, generation=False):
        self.prev_hash = prev_hash
        self.prev_idx = prev_idx
        self._gen = generation

    def is_generation(self):
        return self._gen


class _Tx:
    def __init__(self, inputs, outputs):
        self.inputs = inputs
        self.outputs = outputs


def _singleton_script(ref: bytes) -> bytes:
    """d8 <36-byte ref> + a p2pkh tail, the shape a singleton UTXO carries."""
    return (bytes([OpCodes.OP_PUSHINPUTREFSINGLETON]) + ref
            + bytes([OpCodes.OP_DUP, OpCodes.OP_HASH160, 20]) + bytes(20)
            + bytes([OpCodes.OP_EQUALVERIFY, OpCodes.OP_CHECKSIG]))


def _backfill():
    gi = _index()
    # Real ref extraction; only the holder hashX is stubbed (it needs a coin).
    bf = RefHistoryBackfill(gi.db, SimpleNamespace(coin=_Coin, ref_history_backfill=True), gi)
    bf._holder_hashX = lambda script: HOLDER
    return bf


def _block(txs):
    return SimpleNamespace(transactions=txs)


def test_backfill_derives_a_mint_when_the_tx_spends_the_ref_outpoint():
    """A ref minted from the outpoint the tx spends — same rule as the live path."""
    bf = _backfill()
    seed = _In(prev_hash=TXID, prev_idx=0)          # spends TXID:0, which IS the ref
    tx = _Tx([seed], [_Out(_singleton_script(REF))])
    rows, tracked = [], {}
    assert bf.scan_block(_block([(tx, OTHER_TXID)]), 500, tracked, rows) == 1
    decoded = GlyphIndex.decode_history_value(rows[0][1])
    assert decoded['event'] == GlyphEventType.MINT
    assert decoded['vout'] == 0


def test_backfill_derives_a_transfer_when_the_ref_moves():
    bf = _backfill()
    tx = _Tx([_In(prev_hash=bytes([0x99]) * 32, prev_idx=4)],
             [_Out(_singleton_script(REF))])
    rows, tracked = [], {}
    bf.scan_block(_block([(tx, OTHER_TXID)]), 600, tracked, rows)
    assert GlyphIndex.decode_history_value(rows[0][1])['event'] == GlyphEventType.TRANSFER


def test_backfill_tracks_outpoints_so_a_later_melt_is_detected():
    """The reason the scan keeps its own outpoint->refs map: the `ri` row is deleted on spend, so
    for a historic spend the DB cannot say what the input carried. Only sequential state can."""
    bf = _backfill()
    tracked, rows = {}, []

    # Block 500 creates the singleton at OTHER_TXID:0 ...
    mint_tx = _Tx([_In(prev_hash=TXID, prev_idx=0)], [_Out(_singleton_script(REF))])
    bf.scan_block(_block([(mint_tx, OTHER_TXID)]), 500, tracked, rows)
    assert (OTHER_TXID + struct.pack('<I', 0)) in tracked

    # ... block 900 spends it and re-creates nothing: a melt.
    rows.clear()
    melt_tx = _Tx([_In(prev_hash=OTHER_TXID, prev_idx=0)], [_Out(b'\x6a\x04data')])
    assert bf.scan_block(_block([(melt_tx, bytes([0x11]) * 32)]), 900, tracked, rows) == 1
    decoded = GlyphIndex.decode_history_value(rows[0][1])
    assert decoded['event'] == GlyphEventType.MELT
    assert decoded['vout'] is None
    # The outpoint is dropped once consumed, so the map tracks only the live set.
    assert (OTHER_TXID + struct.pack('<I', 0)) not in tracked


def test_backfill_emits_no_melt_when_the_ref_is_carried_forward():
    bf = _backfill()
    tracked, rows = {}, []
    mint_tx = _Tx([_In(prev_hash=TXID, prev_idx=0)], [_Out(_singleton_script(REF))])
    bf.scan_block(_block([(mint_tx, OTHER_TXID)]), 500, tracked, rows)

    rows.clear()
    move_tx = _Tx([_In(prev_hash=OTHER_TXID, prev_idx=0)], [_Out(_singleton_script(REF))])
    bf.scan_block(_block([(move_tx, bytes([0x22]) * 32)]), 600, tracked, rows)
    events = [GlyphIndex.decode_history_value(v)['event'] for _k, v in rows]
    assert GlyphEventType.MELT not in events
    assert events == [GlyphEventType.TRANSFER]


def test_backfill_rows_are_idempotent_with_the_live_path():
    """Same key AND same value, so overlap between backfill and live indexing is harmless —
    which is why the backfill needs no live-from watermark."""
    gi = _index()
    gi.record_ref_hop(REF, GlyphEventType.TRANSFER, OTHER_TXID, 600, 0, 0, HOLDER)
    live_key, live_value = gi.history_cache[0][1], gi.history_cache[0][2]

    bf = _backfill()
    rows, tracked = [], {}
    tx = _Tx([_In(prev_hash=bytes([0x99]) * 32, prev_idx=1)], [_Out(_singleton_script(REF))])
    bf.scan_block(_block([(tx, OTHER_TXID)]), 600, tracked, rows)
    assert rows[0][0] == live_key
    assert rows[0][1] == live_value


def test_backfill_survives_an_unparseable_output():
    bf = _backfill()
    rows, tracked = [], {}
    tx = _Tx([_In()], [_Out(bytes([OpCodes.OP_PUSHDATA4]) + b'\xff\xff\xff\xff'),
                       _Out(_singleton_script(REF))])
    # The bad output yields nothing; the good one still produces its hop.
    assert bf.scan_block(_block([(tx, OTHER_TXID)]), 700, tracked, rows) == 1


def test_backfill_ignores_coinbase_inputs():
    bf = _backfill()
    rows, tracked = [], {}
    tx = _Tx([_In(generation=True)], [_Out(_singleton_script(REF))])
    bf.scan_block(_block([(tx, OTHER_TXID)]), 100, tracked, rows)
    assert GlyphIndex.decode_history_value(rows[0][1])['event'] == GlyphEventType.TRANSFER


def test_backfill_checkpoint_keys_do_not_alias():
    """'GR' (HOLDER_BY_REF) and 'GD' (PAYLOAD_HASH) are both taken, so 'GL' was chosen."""
    prefixes = [v for k, v in vars(GlyphDBKeys).items() if isinstance(v, bytes)]
    mine = [RefHistoryBackfillKeys.CURSOR, RefHistoryBackfillKeys.TARGET,
            RefHistoryBackfillKeys.DONE]
    for a in mine:
        for b in prefixes + mine:
            if a != b:
                assert not b.startswith(a), f'{b!r} sits under {a!r}'
                assert not a.startswith(b), f'{a!r} sits under {b!r}'


def test_backfill_disabled_by_default():
    gi = _index()
    bf = RefHistoryBackfill(gi.db, SimpleNamespace(coin=_Coin), gi)
    assert bf.enabled is False
    assert bf.stats()['enabled'] is False


# ------------------------------------------------- the two bugs mainnet found (2026-09-06)
#
# Both were invisible here because these tests only ever exercised record_ref_hop in isolation:
# never against process_tx writing the same key in the same tx, and never against the hashX the
# block processor actually passes. On 205 every singleton returned a lone `deploy` row with
# vout: null and holder_address: null.

DEPLOY_ROW = struct.pack('<B', GlyphEventType.DEPLOY) + TXID
HOP_ROW = (struct.pack('<B', GlyphEventType.MINT) + TXID
           + struct.pack('<I', 2) + HOLDER)


def test_deploy_row_no_longer_clobbers_the_mint_hop():
    """A reveal writes both rows for one ref at one (height, tx_idx). The key embeds only
    ref/height/tx_idx, so the DEPLOY that process_tx appends second used to overwrite the hop."""
    idx = _index()
    idx.record_ref_hop(REF, GlyphEventType.MINT, TXID, 500, 3, 2, HOLDER)
    # What process_tx appends afterwards, at the identical key.
    idx.history_cache.append((500, pack_history_key(REF, 500, 3), DEPLOY_ROW))
    _commit(idx)

    row = idx.get_ref_location_history(REF)['rows'][0]
    assert row['event'] == 'deploy', 'the event /tokens/{ref}/history has always reported'
    assert row['vout'] == 2, 'and now the location the hop carried'
    assert row['holder_address'] is None or isinstance(row['holder_address'], str)
    assert GlyphIndex.decode_history_value(
        idx.db.utxo_db.store[pack_history_key(REF, 500, 3)])['holder_hashX'] == HOLDER


def test_merge_is_symmetric_for_the_backfill_direction():
    """The live path sees the hop first, then the DEPLOY. The backfill finds the DEPLOY already
    in the DB and brings the hop. Both orders must land on the same row."""
    assert (GlyphIndex.merge_history_values(HOP_ROW, DEPLOY_ROW)
            == GlyphIndex.merge_history_values(DEPLOY_ROW, HOP_ROW))
    merged = GlyphIndex.merge_history_values(DEPLOY_ROW, HOP_ROW)
    decoded = GlyphIndex.decode_history_value(merged)
    assert decoded['event'] == GlyphEventType.DEPLOY
    assert decoded['vout'] == 2 and decoded['holder_hashX'] == HOLDER


def test_merge_is_idempotent_across_repeated_rescans():
    """Found on a live DB. The first rescan merges DEPLOY + hop into a full-length row that
    still carries the DEPLOY event byte. A second rescan then meets a row that is no longer
    bare, and a length-based rule falls through to "later write wins" and destroys the deploy
    label. Testing the EVENT TYPE instead keeps repeated runs stable."""
    once = GlyphIndex.merge_history_values(DEPLOY_ROW, HOP_ROW)
    assert GlyphIndex.decode_history_value(once)['event'] == GlyphEventType.DEPLOY
    twice = GlyphIndex.merge_history_values(once, HOP_ROW)
    assert twice == once, 'a second rescan must not overwrite the merged row'
    thrice = GlyphIndex.merge_history_values(twice, HOP_ROW)
    assert thrice == once


def test_merge_takes_a_freshly_derived_tail_over_a_stale_one():
    """The rescan exists to correct vout/holder, so an equal-length incoming tail must win."""
    stale = (struct.pack('<B', GlyphEventType.DEPLOY) + TXID
             + struct.pack('<I', 9) + bytes([0x11]) * HASHX_LEN)
    merged = GlyphIndex.merge_history_values(stale, HOP_ROW)
    decoded = GlyphIndex.decode_history_value(merged)
    assert decoded['event'] == GlyphEventType.DEPLOY, 'label preserved'
    assert decoded['vout'] == 2 and decoded['holder_hashX'] == HOLDER, 'tail refreshed'


def test_merge_preserves_a_burn_label_too():
    """BURN is also process_tx-only, so a hop must not overwrite it either."""
    burn = struct.pack('<B', GlyphEventType.BURN) + TXID
    merged = GlyphIndex.merge_history_values(burn, HOP_ROW)
    assert GlyphIndex.decode_history_value(merged)['event'] == GlyphEventType.BURN
    assert GlyphIndex.decode_history_value(merged)['vout'] == 2


def test_merge_of_two_hop_rows_takes_the_later():
    a = struct.pack('<B', GlyphEventType.TRANSFER) + TXID + struct.pack('<I', 1) + HOLDER
    b = struct.pack('<B', GlyphEventType.MELT) + TXID + struct.pack('<I', 7) + HOLDER
    assert GlyphIndex.merge_history_values(a, b) == b


def test_merge_handles_an_absent_side():
    assert GlyphIndex.merge_history_values(b'', HOP_ROW) == HOP_ROW
    assert GlyphIndex.merge_history_values(HOP_ROW, b'') == HOP_ROW


def test_merge_leaves_rows_that_carry_their_own_tail_alone():
    """Only a bare 33-byte row is enriched. A dMint MINT's amount must never be read as a vout."""
    mint_with_amount = (struct.pack('<B', GlyphEventType.MINT) + TXID
                        + struct.pack('<Q', 12345))
    assert GlyphIndex.merge_history_values(mint_with_amount, HOP_ROW) == HOP_ROW
    assert GlyphIndex.merge_history_values(HOP_ROW, mint_with_amount) == mint_with_amount
    assert GlyphIndex.decode_history_value(mint_with_amount)['amount'] == 12345


def test_merge_of_two_bare_rows_keeps_the_later():
    burn = struct.pack('<B', GlyphEventType.BURN) + TXID
    assert GlyphIndex.merge_history_values(DEPLOY_ROW, burn) == burn


def test_flush_collapses_duplicate_history_keys():
    """Source guard: a plain put loop over history_cache silently discards the earlier of two
    writes to one key. flush must merge."""
    src = open(os.path.join(os.path.dirname(__file__), '..', 'electrumx', 'server',
                            'glyph_index.py'), encoding='utf-8').read()
    assert 'merged_history' in src and 'self.merge_history_values(' in src


# ------------------------------------------------------------------ holder is the BASE hashX

def test_block_processor_passes_the_base_hashX_to_the_hop():
    """The GO owner index is keyed by the base address hashX (the locking script with the ref
    preamble stripped). Passing the output's own ref-wrapped `hashX` made every lookup miss, so
    holder_address was structurally null on every row — not intermittently, always."""
    src = open(os.path.join(os.path.dirname(__file__), '..', 'electrumx', 'server',
                            'block_processor.py'), encoding='utf-8').read()
    call = src[src.index('self.glyph_index.record_ref_hop('):][:400]
    assert 'idx, base_hashX,' in call, 'the hop must carry the base hashX'
    assert 'idx, hashX,' not in call, 'not the output ref-wrapped hashX'
    # And it must be computed before the loop that records hops, or it is a stale value.
    assert src.index('base_hashX = script_hashX(base_script)') < \
        src.index('self.glyph_index.record_ref_hop(')


def test_backfill_derives_the_same_base_hashX_as_the_live_path():
    """_holder_hashX used zero_refs, faithfully reproducing the live path's mistake — its
    docstring even said so. A backfill run would have written unresolvable holders."""
    from electrumx.lib.hash import sha256
    from electrumx.lib.script import Script

    class _HashingCoin(_Coin):
        @staticmethod
        def hashX_from_script(script):
            return sha256(script)[:HASHX_LEN]

    gi = _index()
    bf = RefHistoryBackfill(gi.db, SimpleNamespace(coin=_HashingCoin,
                                                  ref_history_backfill=True), gi)
    script = _singleton_script(REF)
    got = bf._holder_hashX(script)
    assert got == _HashingCoin.hashX_from_script(Script.base_locking_script(script))
    assert got != _HashingCoin.hashX_from_script(Script.zero_refs(script)), \
        'the ref-wrapped hashX is what the GO index can never match'


def test_backfill_enriches_the_deploy_row_rather_than_replacing_it():
    """Source guard: a plain put would trade the `deploy` event for a `mint` that
    /tokens/{ref}/history never showed, and there is no way back without a reindex."""
    src = open(os.path.join(os.path.dirname(__file__), '..', 'electrumx', 'server',
                            'ref_history_backfill.py'), encoding='utf-8').read()
    assert 'GlyphIndex.merge_history_values(' in src


# ------------------------------------------------------------------ backfill observability
#
# A multi-hour rescan that logs only "Starting" and "complete" at INFO leaves an operator with
# no way to tell progress from a hang short of reading RocksDB by hand.

def test_status_endpoint_reports_progress_from_the_checkpoint_keys():
    from fastapi.testclient import TestClient
    from unittest.mock import Mock
    from electrumx.server.rest_api import app, set_indexer
    from electrumx.lib.util import pack_be_uint32

    store = {RefHistoryBackfillKeys.TARGET: pack_be_uint32(462_097),
             RefHistoryBackfillKeys.CURSOR: pack_be_uint32(110_600)}
    db = Mock()
    db.utxo_db = SimpleNamespace(get=store.get)
    db.db_height = 462_098
    idx = Mock()
    idx.enabled = True
    set_indexer(idx, db, Mock())
    try:
        body = TestClient(app).get('/ref-history/status').json()
        assert body['complete'] is False and body['started'] is True
        assert body['next_height'] == 110_600 and body['target_height'] == 462_097
        assert body['percent'] == 23.93
    finally:
        set_indexer(None, None, None)


def test_status_endpoint_reports_completion():
    from fastapi.testclient import TestClient
    from unittest.mock import Mock
    from electrumx.server.rest_api import app, set_indexer
    from electrumx.lib.util import pack_be_uint32

    store = {RefHistoryBackfillKeys.TARGET: pack_be_uint32(462_097),
             RefHistoryBackfillKeys.DONE: b'1'}
    db = Mock()
    db.utxo_db = SimpleNamespace(get=store.get)
    db.db_height = 462_098
    idx = Mock()
    idx.enabled = True
    set_indexer(idx, db, Mock())
    try:
        body = TestClient(app).get('/ref-history/status').json()
        assert body['complete'] is True and body['percent'] == 100.0
        assert body['next_height'] is None
    finally:
        set_indexer(None, None, None)


def test_status_endpoint_before_the_rescan_has_started():
    from fastapi.testclient import TestClient
    from unittest.mock import Mock
    from electrumx.server.rest_api import app, set_indexer

    db = Mock()
    db.utxo_db = SimpleNamespace(get=lambda k: None)
    db.db_height = 462_098
    idx = Mock()
    idx.enabled = True
    set_indexer(idx, db, Mock())
    try:
        body = TestClient(app).get('/ref-history/status').json()
        assert body['started'] is False and body['complete'] is False
        assert body['target_height'] is None and body['percent'] is None
    finally:
        set_indexer(None, None, None)


def test_status_path_is_public_like_its_siblings():
    """GET paths absent from the allowlist require X-API-Key, so a status endpoint added without
    listing it answers 401 on any node with REST_API_KEY set -- which is how this was found."""
    src = open(os.path.join(os.path.dirname(__file__), '..', 'electrumx', 'server',
                            'rest_api.py'), encoding='utf-8').read()
    allowlist = src[src.index('public_paths = ('):]
    allowlist = allowlist[:allowlist.index(')')]
    for path in ("'/ref-history'", "'/hashmark'", "'/declarations'"):
        assert path in allowlist, f'{path} must be reachable without an API key'


def test_progress_is_reported_at_info_on_an_interval():
    """Source guard: DEBUG-only progress is invisible to an operator running at INFO."""
    from electrumx.server.ref_history_backfill import PROGRESS_EVERY_BLOCKS
    assert PROGRESS_EVERY_BLOCKS > 0
    src = open(os.path.join(os.path.dirname(__file__), '..', 'electrumx', 'server',
                            'ref_history_backfill.py'), encoding='utf-8').read()
    assert "self.logger.info('Ref-history backfill: height %d/%d" in src


# ----------------------------------------------------- the plumbing filter (2026-09-07)
#
# Measured on mainnet mid-rescan: 18,957,168 TRANSFER hops, exactly half the GH keyspace, and
# every top hop-holder was a dMint contract re-created once per mint (218,751 rows for one
# contract). The useful location data was ~5k rows. A mining contract's movement log is not an
# ownership history, so TRANSFER hops are dropped for plumbing refs while MINT and MELT stay.

CONTRACT_REF = pack_ref(bytes([0xC0]) * 32, 1)


def _plumbing_index(gc_rows=(), pending=(), tokens=None):
    idx = _index()
    for ref in gc_rows:
        idx.db.utxo_db.store[GlyphDBKeys.CONTRACT_TO_TOKEN + ref] = bytes([0xEE]) * 36
    for ref in pending:
        idx.contract_to_token_cache[ref] = bytes([0xEE]) * 36
    lookup = dict(tokens or {})
    idx.get_token = lambda ref: lookup.get(ref)
    return idx


def test_a_dmint_contract_ref_is_plumbing_via_the_gc_index():
    idx = _plumbing_index(gc_rows=[CONTRACT_REF])
    assert idx.is_plumbing_singleton(CONTRACT_REF) is True
    assert idx.is_plumbing_singleton(REF) is False, 'an ordinary ref is not'


def test_a_contract_revealed_in_this_block_is_plumbing_before_its_gc_row_flushes():
    """A contract can be spent again in the block that revealed it, so the pending cache has to
    be consulted or the first mints of every dMint token slip through."""
    idx = _plumbing_index(pending=[CONTRACT_REF])
    assert idx.is_plumbing_singleton(CONTRACT_REF) is True


def test_a_companion_singleton_is_plumbing_even_without_a_gc_row():
    """GC held only 885 rows against 5,435 hop-holding refs, so the companion shape has to be
    caught too. Delegates to the same predicate the recency feeds use."""
    idx = _index()
    idx._is_companion_singleton = lambda token: True
    idx.get_token = lambda ref: SimpleNamespace(name=None, metadata_hash=b'')
    assert idx.is_plumbing_singleton(CONTRACT_REF) is True


def test_an_unknown_ref_fails_open_and_is_not_cached():
    """A singleton seen before its reveal is indexed must not be permanently marked
    non-plumbing -- record the hop, and re-decide once the record exists."""
    idx = _plumbing_index()
    assert idx.is_plumbing_singleton(CONTRACT_REF) is False
    assert CONTRACT_REF not in idx._plumbing_cache, 'an unknown verdict must not stick'
    # Once the GC row lands, the answer changes.
    idx.db.utxo_db.store[GlyphDBKeys.CONTRACT_TO_TOKEN + CONTRACT_REF] = bytes([0xEE]) * 36
    assert idx.is_plumbing_singleton(CONTRACT_REF) is True


def test_a_verdict_is_cached():
    idx = _plumbing_index(gc_rows=[CONTRACT_REF])
    assert idx.is_plumbing_singleton(CONTRACT_REF) is True
    idx.db.utxo_db.store.clear()          # a cached True survives the row going away
    assert idx.is_plumbing_singleton(CONTRACT_REF) is True
    assert idx._plumbing_cache[CONTRACT_REF] is True


def test_mint_and_melt_survive_the_filter_in_the_live_path():
    """Source guard: the endpoints must still be recorded for a plumbing ref, or a contract's
    chain loses both ends and becomes untraceable."""
    src = open(os.path.join(os.path.dirname(__file__), '..', 'electrumx', 'server',
                            'block_processor.py'), encoding='utf-8').read()
    assert 'event != GlyphEventType.TRANSFER' in src, 'only TRANSFER may be filtered'
    assert 'is_plumbing_singleton(ref)' in src
    # The melt path records unconditionally; it must not have gained a filter.
    melt = src[src.index('GlyphEventType.MELT'):][:400]
    assert 'is_plumbing_singleton' not in melt


def test_backfill_skips_a_plumbing_transfer_but_keeps_its_mint():
    bf = _backfill()
    bf.glyph_index.db.utxo_db.store[GlyphDBKeys.CONTRACT_TO_TOKEN + REF] = bytes([0xEE]) * 36

    # A mint: the tx spends the ref's own outpoint. Recorded even for plumbing.
    mint_tx = _Tx([_In(prev_hash=TXID, prev_idx=0)], [_Out(_singleton_script(REF))])
    rows, tracked = [], {}
    assert bf.scan_block(_block([(mint_tx, OTHER_TXID)]), 500, tracked, rows) == 1
    assert GlyphIndex.decode_history_value(rows[0][1])['event'] == GlyphEventType.MINT

    # A move: dropped.
    rows.clear()
    move_tx = _Tx([_In(prev_hash=OTHER_TXID, prev_idx=0)], [_Out(_singleton_script(REF))])
    assert bf.scan_block(_block([(move_tx, bytes([0x22]) * 32)]), 600, tracked, rows) == 0
    assert rows == []


def test_backfill_keeps_a_plumbing_melt():
    """The chain must still terminate: a melt is one row per contract, not one per mint."""
    bf = _backfill()
    bf.glyph_index.db.utxo_db.store[GlyphDBKeys.CONTRACT_TO_TOKEN + REF] = bytes([0xEE]) * 36
    tracked = {OTHER_TXID + struct.pack('<I', 0): {REF}}
    rows = []
    melt_tx = _Tx([_In(prev_hash=OTHER_TXID, prev_idx=0)], [_Out(b'\x6a\x04data')])
    assert bf.scan_block(_block([(melt_tx, bytes([0x33]) * 32)]), 900, tracked, rows) == 1
    assert GlyphIndex.decode_history_value(rows[0][1])['event'] == GlyphEventType.MELT


def test_backfill_still_records_an_ordinary_transfer():
    bf = _backfill()
    tx = _Tx([_In(prev_hash=bytes([0x99]) * 32, prev_idx=4)], [_Out(_singleton_script(REF))])
    rows, tracked = [], {}
    assert bf.scan_block(_block([(tx, OTHER_TXID)]), 600, tracked, rows) == 1
    assert GlyphIndex.decode_history_value(rows[0][1])['event'] == GlyphEventType.TRANSFER


# --------------------------------------------------- `filtered`: is this chain complete?
#
# Asked for by a consumer (CoinFlow) rendering a "Token Journey". Without it a short chain for a
# plumbing ref is indistinguishable from a ref that genuinely moved twice, so a UI would present
# a deliberately incomplete list as a full derivation.

def test_a_normal_chain_is_not_filtered():
    idx = _index()
    idx.record_ref_hop(REF, GlyphEventType.TRANSFER, TXID, 500, 1, 0, HOLDER)
    _commit(idx)
    out = idx.get_ref_location_history(REF)
    assert out['filtered'] is False
    assert 'note' not in out
    assert len(out['rows']) == 1


def test_a_plumbing_chain_is_flagged_and_explained():
    idx = _index()
    idx.db.utxo_db.store[GlyphDBKeys.CONTRACT_TO_TOKEN + REF] = bytes([0xEE]) * 36
    idx.record_ref_hop(REF, GlyphEventType.MINT, TXID, 500, 1, 0, HOLDER)
    _commit(idx)
    out = idx.get_ref_location_history(REF)
    assert out['filtered'] is True
    assert 'note' in out and 'transfer hops are not indexed' in out['note']
    assert len(out['rows']) == 1, 'the endpoints it does have are still returned'


def test_a_fungible_ref_is_not_applicable_rather_than_filtered():
    """`filtered` must not be used to mean "no chain exists" -- an FT has no location at all,
    which is a different answer from "we dropped some of it"."""
    from electrumx.lib.glyph import GlyphProtocol
    idx = _index()
    idx.get_token = lambda ref: SimpleNamespace(
        protocols=[GlyphProtocol.GLYPH_FT], name='FT', metadata_hash=b'')
    out = idx.get_ref_location_history(REF)
    assert out['filtered'] is False
    assert 'fungible' in out['note']
    assert out['rows'] == []


def test_filtered_is_always_present_so_a_consumer_can_rely_on_it():
    idx = _index()
    for ref in (REF, pack_ref(bytes([0xEE]) * 32, 3)):
        assert 'filtered' in idx.get_ref_location_history(ref)
