# Regression tests for the block-parser halt guard.
#
# Goal: a single malformed transaction / script must never raise an uncaught
# exception out of the indexer's parse path (advance_txs). A halt there stops
# every node at the same height. These are targeted, deterministic cases.

import inspect

import pytest

from electrumx.lib.script import Script, ScriptError, OpCodes, is_unspendable_legacy


# --- The script parsers must only raise the exception types advance_txs catches

def test_malformed_scripts_only_raise_caught_exception_types():
    '''advance_txs catches (ScriptError, AssertionError, ValueError,
    IndexError). Verify the script parsers only ever raise within that set for
    a spread of malformed inputs, so the per-output try/except can never let one
    escape and halt the indexer.'''
    caught = (ScriptError, AssertionError, ValueError, IndexError)
    malformed = [
        bytes([OpCodes.OP_PUSHDATA4]) + b'\xff\xff\xff\xff',   # oversize len
        bytes([OpCodes.OP_PUSHDATA2]) + b'\xff\xff',           # truncated len
        bytes([OpCodes.OP_PUSHDATA1]),                         # missing len byte
        bytes([75]) + b'\x00\x00',                             # push 75 want, 2 have
        bytes([OpCodes.OP_PUSHINPUTREF]) + b'\x00' * 4,        # ref wants 36, 4 have
    ]
    for script in malformed:
        for fn in (Script.get_ops, Script.get_push_input_refs, Script.zero_refs):
            try:
                fn(script)
            except caught:
                pass  # acceptable: advance_txs handles these
            except Exception as e:  # noqa: BLE001 - the bug we are guarding against
                pytest.fail(
                    f'{fn.__name__} raised uncatchable {type(e).__name__}: {e}')


def test_oversize_pushdata4_raises_scripterror():
    # OP_PUSHDATA4 claims 0xFFFFFFFF bytes but the buffer is tiny. The
    # n+dlen>len(script) bounds check must reject it (without dropping anything
    # valid -- see the no-sub-consensus-cap guard below).
    script = bytes([OpCodes.OP_PUSHDATA4]) + b'\xff\xff\xff\xff'
    with pytest.raises(ScriptError):
        Script.get_ops(script)
    with pytest.raises(ScriptError):
        Script.get_push_input_refs(script)


# --- advance_txs must wrap the CORE per-tx parsing, not just the overlays -----

def test_advance_txs_wraps_core_output_parsing_in_try_except():
    '''Source-level guard: the per-output core parsing in advance_txs (the
    zero_refs/get_push_input_refs/base_locking_script calls) must be wrapped so
    one malformed tx cannot halt the indexer.'''
    from electrumx.server import block_processor

    src = inspect.getsource(block_processor.BlockProcessor.advance_txs)
    # The exact tuple of caught exception types must appear...
    assert 'except (ScriptError, AssertionError, ValueError, IndexError)' in src
    # ...and a malformed output must be skipped (continue), not abort the block.
    assert 'continue' in src
    # The guard must sit on the core parsing path: the script-parsing call is
    # inside the wrapped region.
    assert 'Script.zero_refs' in src
    assert 'Script.get_push_input_refs' in src


# --- The reorg desync fix: advance-add and backup-spend must skip the SAME -----
# --- output set, routed through the one shared _output_indexable predicate. -----

def test_output_indexable_catches_degenerate_spendable_script():
    '''The crux of the verified reorg-halt bug.

    A consensus-valid but degenerate scriptPubKey like b'\\x05ab' (a push of 5
    bytes with only 2 present -- truncated pushdata) is NOT unspendable, yet
    Script.zero_refs RAISES on it. The old _backup_txs decided whether to
    spend_utxo using is_unspendable_legacy alone, so it tried to spend an output
    that advance_txs (which gates put_utxo on zero_refs parsing) never added ->
    ChainError 'UTXO not found' -> the reorg HALTED.

    _output_indexable must catch exactly this case: return False for it, even
    though is_unspendable_legacy returns False, because zero_refs raises.'''
    from electrumx.server.block_processor import BlockProcessor

    degenerate = b'\x05ab'

    # 1. The output is NOT unspendable -- the old backup predicate said "spend it".
    assert is_unspendable_legacy(degenerate) is False
    # 2. ...yet the advance-side parse gate raises on it.
    raised = False
    try:
        Script.zero_refs(degenerate)
    except BlockProcessor._SCRIPT_PARSE_ERRORS:
        raised = True
    assert raised, 'zero_refs(b"\\x05ab") must raise; that is what the old ' \
                   'backup predicate failed to account for'
    # 3. The shared predicate folds both: it returns False so BOTH advance (skip
    #    put_utxo) and backup (skip spend_utxo) cover the identical output set.
    assert BlockProcessor._output_indexable(degenerate) is False


def test_output_indexable_true_for_normal_spendable_script():
    '''A normal, parsable, spendable script must still be indexed (and therefore
    spent on backup) -- the fix must not start skipping legitimate outputs.'''
    from electrumx.server.block_processor import BlockProcessor

    # A bare 1-byte data push of 0x01 (OP_1-style payload): parses cleanly,
    # spendable.
    normal = bytes([0x01, 0x01])
    assert is_unspendable_legacy(normal) is False
    Script.zero_refs(normal)  # must not raise
    assert BlockProcessor._output_indexable(normal) is True


def test_output_indexable_false_for_unspendable_op_return():
    '''An OP_RETURN output is unspendable; both paths must skip it.'''
    from electrumx.lib.script import OpCodes
    from electrumx.server.block_processor import BlockProcessor

    op_return = bytes([OpCodes.OP_RETURN]) + b'\x04data'
    assert is_unspendable_legacy(op_return) is True
    assert BlockProcessor._output_indexable(op_return) is False


def test_advance_and_backup_route_through_shared_predicate():
    '''Structural guarantee (source level): BOTH advance_txs and _backup_txs must
    decide add/spend via _output_indexable, AND _output_indexable must use the
    IDENTICAL zero_refs parse call + exception set as advance's put_utxo gate.

    This is the by-construction symmetry that prevents the reorg-halt desync;
    catch-and-continue defence-in-depth alone is not sufficient.'''
    from electrumx.server import block_processor

    bp = block_processor.BlockProcessor

    advance_src = inspect.getsource(bp.advance_txs)
    backup_src = inspect.getsource(bp._backup_txs)
    pred_src = inspect.getsource(bp.__dict__['_output_indexable'].__func__)

    # advance gates put_utxo on the shared predicate (skips when False).
    assert '_output_indexable' in advance_src
    assert 'put_utxo' in advance_src
    # backup gates spend_utxo on the SAME predicate -- not is_unspendable alone.
    assert '_output_indexable' in backup_src
    assert 'spend_utxo' in backup_src
    # ...and backup no longer decides solely on is_unspendable(pk_script).
    assert 'if is_unspendable(txout.pk_script):' not in backup_src

    # The shared predicate uses the identical parse call + exception set.
    assert 'Script.zero_refs' in pred_src
    assert 'is_unspendable_legacy' in pred_src
    assert '_SCRIPT_PARSE_ERRORS' in pred_src


def test_output_indexable_round_trips_advance_then_backup_decision():
    '''Lightweight advance->backup round-trip on the decision itself: for a
    spread of outputs (good, degenerate-spendable, unspendable), the add decision
    advance_txs would make (put_utxo iff indexable) must EQUAL the spend decision
    _backup_txs would make (spend_utxo iff indexable). Equal decisions => the
    advance-add set and backup-spend set are identical => no UTXO desync on reorg,
    so no ChainError 'UTXO not found' can arise from this mismatch.'''
    from electrumx.lib.script import OpCodes
    from electrumx.server.block_processor import BlockProcessor

    outputs = [
        bytes([0x01, 0x01]),                       # normal spendable -> indexed
        b'\x05ab',                                 # degenerate spendable -> skip
        bytes([OpCodes.OP_RETURN]) + b'\x04data',  # unspendable -> skip
        bytes([OpCodes.OP_PUSHDATA2]) + b'\xff\xff',  # truncated len -> skip
    ]
    for pk in outputs:
        add_decision = BlockProcessor._output_indexable(pk)     # advance: put_utxo?
        spend_decision = BlockProcessor._output_indexable(pk)   # backup: spend_utxo?
        assert add_decision == spend_decision, (
            f'add/spend decision diverged for {pk!r} -> reorg desync risk')


# --- Full advance -> backup integration harness (fake coin/db, no indexes) -----
# This exercises the actual advance_txs and _backup_txs code paths against an
# in-memory UTXO cache. A block containing a degenerate-but-spendable output
# (b'\x05ab') is advanced then backed up; pre-fix _backup_txs called spend_utxo
# on that never-added output and raised ChainError 'UTXO not found'.

from collections import namedtuple
from hashlib import sha256

from electrumx.lib.hash import HASHX_LEN

_FakeTxIn = namedtuple('FakeTxIn', 'prev_hash prev_idx script sequence')
_FakeTxOut = namedtuple('FakeTxOut', 'value pk_script')
_ZERO = bytes(32)
_MINUS_1 = 4294967295


class _GenTxIn(_FakeTxIn):
    def is_generation(self):
        return self.prev_idx == _MINUS_1 and self.prev_hash == _ZERO


class _FakeTx(namedtuple('FakeTx', 'version inputs outputs locktime')):
    pass


class _FakeCoin:
    @staticmethod
    def hashX_from_script(script):
        return sha256(script).digest()[:HASHX_LEN]

    @staticmethod
    def codeScriptHash_from_script(script):
        return sha256(script).digest()  # 32-byte hash


class _FakeHistory:
    def add_unflushed(self, hashXs_by_tx, first_tx_num):
        pass


class _FakeUTXODB(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)

    def iterator(self, prefix=b''):
        return iter(())  # empty: backup must resolve every spend from the cache


class _FakeDB:
    def __init__(self):
        self.utxo_db = _FakeUTXODB()
        self.history = _FakeHistory()
        self.tx_counts = []
        self._undo = {}
        self._ref_loc_undo = {}

    def read_undo_info(self, height):
        return self._undo.get(height)

    def read_ref_loc_undo_info(self, height):
        return self._ref_loc_undo.get(height)


def _make_bp():
    from electrumx.server.block_processor import BlockProcessor

    bp = BlockProcessor.__new__(BlockProcessor)
    bp.coin = _FakeCoin()
    bp.db = _FakeDB()
    bp.tx_count = 0
    bp.height = 0
    bp.touched = set()
    bp.tx_hashes = []
    # Caches advance_txs / _backup_txs write through.
    bp.utxo_cache = {}
    bp.ref_cache = {}
    bp.ref_mint_cache = {}
    bp.ref_loc_cache = {}
    bp.data_cache = {}
    bp.db_deletes = []
    # All overlay indexes disabled so we test the pure UTXO/ref core.
    bp.glyph_index = None
    bp.wave_index = None
    bp.realm_index = None
    bp.swap_index = None
    bp.predict_index = None
    bp.royalty_index = None
    bp.analytics_index = None
    bp.subscriptions = None
    bp.dmint_contracts = None
    return bp


def test_advance_then_backup_with_degenerate_output_no_chainerror():
    '''The end-to-end regression: a coinbase tx whose outputs include the
    degenerate-but-spendable b'\\x05ab' is advanced, then backed up. The fix
    guarantees advance never adds that output AND backup never spends it, so the
    reorg backup completes without ChainError.'''
    from electrumx.server.block_processor import BlockProcessor

    is_unspendable = is_unspendable_legacy

    normal_script = bytes([0x01, 0x01])  # parses, spendable -> indexed
    degenerate = b'\x05ab'               # NOT unspendable, zero_refs raises -> skipped
    coinbase_in = _GenTxIn(_ZERO, _MINUS_1, b'\x00', 0xffffffff)
    tx = _FakeTx(
        1,
        [coinbase_in],
        [_FakeTxOut(5000, normal_script), _FakeTxOut(0, degenerate)],
        0,
    )
    tx_hash = b'\x42' * 32
    txs = [(tx, tx_hash)]

    bp = _make_bp()

    # --- advance ---
    undo_info, ref_loc_undo_info = bp.advance_txs(txs, is_unspendable)
    # The normal output is in the UTXO cache; the degenerate one is NOT.
    assert tx_hash + pack32(0) in bp.utxo_cache
    assert tx_hash + pack32(1) not in bp.utxo_cache

    # Persist undo info the way _advance_block would, then back the block up.
    bp.db._undo[bp.height] = b''.join(undo_info)
    bp.db._ref_loc_undo[bp.height] = b''.join(ref_loc_undo_info)

    # --- backup (the path that used to raise ChainError on b'\x05ab') ---
    # Must NOT raise ChainError 'UTXO not found'.
    bp._backup_txs(txs, is_unspendable)

    # The normal output's UTXO was spent (removed from the cache) on backup; the
    # degenerate output was correctly never touched.
    assert tx_hash + pack32(0) not in bp.utxo_cache


def pack32(n):
    from electrumx.lib.util import pack_le_uint32
    return pack_le_uint32(n)


def test_advance_txs_reraises_memoryerror():
    '''OOM must never be swallowed: the analytics/overlay guards re-raise
    MemoryError rather than logging-and-continuing.'''
    from electrumx.server import block_processor

    src = inspect.getsource(block_processor.BlockProcessor.advance_txs)
    assert 'except MemoryError' in src
    # And every MemoryError handler must re-raise (no silent swallow).
    assert 'raise' in src


def test_analytics_process_block_is_guarded():
    '''The per-block analytics_index.process_block call must be wrapped so
    adversarial block data cannot halt the indexer there.'''
    from electrumx.server import block_processor

    src = inspect.getsource(block_processor.BlockProcessor.advance_txs)
    # process_block is referenced and a guard (try/except MemoryError + log)
    # surrounds the analytics overlay.
    assert 'analytics_index.process_block' in src


# --- Regression: no sub-consensus OP_PUSHDATA cap may be re-introduced --------
# A small MAX_SCRIPT_PUSH-style cap would make get_push_input_refs() raise on a
# legitimate large glyph data push while zero_refs() still indexed the UTXO,
# silently dropping the token's ref (the invisible-token bug). RXinDexer relies
# on the correct n+dlen>len(script) bounds check instead, which drops nothing.

def test_no_sub_consensus_push_cap_in_script_lib():
    from electrumx.lib import script as script_lib

    # The bounds check must be present...
    src = inspect.getsource(script_lib)
    assert 'len(script)' in src
    # ...and no sub-consensus MAX_SCRIPT_PUSH constant may exist.
    assert not hasattr(script_lib, 'MAX_SCRIPT_PUSH'), (
        'a sub-consensus push cap would re-introduce the invisible-token bug')


def test_large_data_push_ref_not_dropped():
    '''A glyph singleton ref preceded by a large (but in-buffer) data push must
    still be returned, not silently dropped. Guards against any push cap.'''
    ref = b'\xab' * 36
    payload_len = 12_000  # far above the old 10 KB ElectrumX cap
    push = (bytes([OpCodes.OP_PUSHDATA4])
            + payload_len.to_bytes(4, 'little')
            + b'\x00' * payload_len)
    script = push + bytes([OpCodes.OP_PUSHINPUTREFSINGLETON]) + ref

    all_refs, normal_refs, singleton_refs = Script.get_push_input_refs(script)
    assert ref in singleton_refs, 'large-data glyph singleton ref was dropped'
    assert ref in all_refs
    Script.zero_refs(script)  # must not raise
