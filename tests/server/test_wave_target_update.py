"""
WAVE mutable-target-update hardening tests.

Covers the three fixes to electrumx/server/wave_index.py:

  FIX A - the new target value is validated (base58 P2PKH/P2SH, bounded length,
          str-typed) before it is stored and served to wallets.
  FIX B - the singleton->name map is recorded only for the CLAIM output (vout 0)
          and never silently overwrites an existing mapping (first-writer wins).
  FIX C - per-(ref, height) eager undo so several target updates landing in one
          flush each unwind correctly on reorg (no collapsed/lost intermediate
          state).

These use a real in-memory key-value DB so the full flush -> backup reorg cycle
runs end to end, and the real Radiant coin so base58 address validation is
genuinely exercised (a Mock coin would make every address "valid").
"""

import struct

import pytest


# --------------------------------------------------------------------------
# Minimal but faithful in-memory utxo_db: supports get / iterator(prefix|seek)
# and a write_batch with put / delete. Enough to run flush() and backup().
# --------------------------------------------------------------------------
class _Batch:
    def __init__(self, store):
        self._store = store

    def put(self, key, value):
        self._store[key] = value

    def delete(self, key):
        self._store.pop(key, None)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeUtxoDB:
    def __init__(self):
        self.store = {}

    def get(self, key, default=None):
        return self.store.get(key, default)

    def iterator(self, prefix=None, seek=None):
        items = sorted(self.store.items())
        for key, value in items:
            if seek is not None and key < seek:
                continue
            if prefix is not None and not key.startswith(prefix):
                continue
            yield key, value

    def write_batch(self):
        return _Batch(self.store)


class _FakeDB:
    def __init__(self):
        self.utxo_db = _FakeUtxoDB()
        self.db_height = 100


class _FakeOutput:
    def __init__(self, pk_script):
        self.pk_script = pk_script


class _FakeTx:
    def __init__(self, outputs):
        self.outputs = outputs
        self.inputs = []


def _make_env():
    from unittest.mock import Mock
    from electrumx.lib.coins import Radiant
    env = Mock()
    env.wave_index = True
    env.wave_genesis_ref = 'a' * 64 + '_0'
    env.wave_hot_names = 1000
    env.reorg_limit = 10
    env.coin = Radiant          # real coin -> real base58 validation
    env.glyph_index = None
    return env


def _p2pkh_tx():
    return _FakeTx([_FakeOutput(bytes.fromhex('76a914' + '11' * 20 + '88ac'))])


# Valid Radiant/Bitcoin-style mainnet P2PKH addresses (verbyte 0x00).
OLD_TARGET = '1BLZiLHCV17EqLWA9S42aFZScCnF1zbnPE'
TARGET_A = '1489r9fYzC9VgueuT16CPWiRRx4HKacYbB'
TARGET_B = '1JArS6jzE3AJ9sZ3aFij1BmTcpFGgN86hA'


@pytest.fixture
def wave_index():
    from electrumx.server.wave_index import WaveIndex
    return WaveIndex(_FakeDB(), _make_env())


def _register(wave_index, name='12345', target=OLD_TARGET, singleton_ref=None,
              tx_hash=None, height=410000, output_refs_by_vout=None):
    if singleton_ref is None:
        singleton_ref = bytes.fromhex('22' * 32) + struct.pack('<I', 0)
    if tx_hash is None:
        tx_hash = bytes.fromhex('e5' * 32)
    if output_refs_by_vout is None:
        output_refs_by_vout = {0: [(singleton_ref, 1)]}
    envelope = {
        'protocols': [2, 5, 11],  # NFT + MUT + WAVE
        'metadata': {'attrs': {
            'name': name, 'domain': 'rxd',
            'target': target, 'target_type': 'address',
        }},
    }
    wave_index.process_tx(
        tx_hash, _p2pkh_tx(), height, 0, envelope,
        output_refs_by_vout=output_refs_by_vout,
        spent_singleton_refs=set(),
    )
    return singleton_ref


def _update(wave_index, target, singleton_ref, tx_hash, height):
    envelope = {
        'protocols': [],  # mod payload: no protocol list
        'metadata': {'attrs': {
            'target': target, 'target_type': 'address',
        }},
    }
    wave_index.process_tx(
        tx_hash, _p2pkh_tx(), height, 0, envelope,
        output_refs_by_vout=None,
        spent_singleton_refs={singleton_ref},
    )


# ==========================================================================
# Test 1 — on-chain-gating negative: an unmapped spent singleton can't touch
# any victim zone.
# ==========================================================================
class TestUnmappedSingletonCannotUpdate:
    def test_unmapped_singleton_leaves_victim_unchanged(self, wave_index):
        _register(wave_index)
        unmapped = bytes.fromhex('99' * 32) + struct.pack('<I', 7)
        _update(wave_index, TARGET_A, unmapped,
                bytes.fromhex('cc' * 32), 435100)
        assert wave_index.resolve('12345')['target'] == OLD_TARGET


# ==========================================================================
# Test 2 — legitimate update positive.
# ==========================================================================
class TestLegitimateUpdate:
    def test_mapped_singleton_updates_target(self, wave_index):
        singleton = _register(wave_index)
        assert wave_index.resolve('12345')['target'] == OLD_TARGET
        _update(wave_index, TARGET_A, singleton,
                bytes.fromhex('f7' * 32), 435095)
        result = wave_index.resolve('12345')
        assert result['target'] == TARGET_A
        assert result['available'] is False
        # Not a duplicate registration.
        assert wave_index._has_duplicates('12345') is False


# ==========================================================================
# Test 3 — target validation (FIX A).
# ==========================================================================
class TestTargetValidation:
    @pytest.mark.parametrize('bad_target', [
        'a' * 10000,                         # oversized
        {'addr': 'x'},                       # non-string (dict)
        12345,                               # non-string (int)
        'not_a_base58_address!!!',           # bad base58
        'mxosQ4CvQR8ipfWdRktyB3u16tauEdamGc',  # testnet verbyte (wrong net)
        '',                                  # empty
    ])
    def test_invalid_target_rejected(self, wave_index, bad_target):
        singleton = _register(wave_index)
        assert wave_index.resolve('12345')['target'] == OLD_TARGET
        _update(wave_index, bad_target, singleton,
                bytes.fromhex('f7' * 32), 435095)
        # Prior target retained.
        assert wave_index.resolve('12345')['target'] == OLD_TARGET

    def test_valid_p2pkh_accepted(self, wave_index):
        singleton = _register(wave_index)
        _update(wave_index, TARGET_A, singleton,
                bytes.fromhex('f7' * 32), 435095)
        assert wave_index.resolve('12345')['target'] == TARGET_A

    def test_validate_target_address_helper(self):
        from electrumx.server.wave_index import validate_target_address
        from electrumx.lib.coins import Radiant
        assert validate_target_address(Radiant, OLD_TARGET) is True
        assert validate_target_address(Radiant, TARGET_A) is True
        assert validate_target_address(Radiant, 'a' * 10000) is False
        assert validate_target_address(Radiant, {'x': 1}) is False
        assert validate_target_address(Radiant, 42) is False
        assert validate_target_address(Radiant, '') is False
        assert validate_target_address(Radiant, 'bad!!!') is False

    def test_invalid_genesis_target_nulled(self, wave_index):
        # A registration whose genesis target is not a valid address must NOT
        # serve that garbage as the resolved target.
        _register(wave_index, name='garbagename', target='not-an-address!!!')
        result = wave_index.resolve('garbagename')
        assert result is not None
        assert result['target'] is None

    def test_valid_genesis_target_kept(self, wave_index):
        _register(wave_index, name='goodname', target=OLD_TARGET)
        assert wave_index.resolve('goodname')['target'] == OLD_TARGET


# ==========================================================================
# Test 4 — scoped recording + first-writer guard (FIX B).
# ==========================================================================
class TestScopedSingletonRecording:
    def test_only_vout0_singleton_recorded(self, wave_index):
        from electrumx.server.wave_index import name_to_hash
        claim_singleton = bytes.fromhex('22' * 32) + struct.pack('<I', 0)
        branch_singleton = bytes.fromhex('33' * 32) + struct.pack('<I', 5)
        _register(
            wave_index, name='scoped', target=OLD_TARGET,
            singleton_ref=claim_singleton,
            output_refs_by_vout={
                0: [(claim_singleton, 1)],
                5: [(branch_singleton, 1)],  # unrelated branch-output singleton
            },
        )
        nh = name_to_hash('scoped')
        assert wave_index.singleton_cache.get(claim_singleton) == nh
        assert branch_singleton not in wave_index.singleton_cache

    def test_branch_singleton_spend_does_not_update_name(self, wave_index):
        claim_singleton = bytes.fromhex('22' * 32) + struct.pack('<I', 0)
        branch_singleton = bytes.fromhex('33' * 32) + struct.pack('<I', 5)
        _register(
            wave_index, name='scoped', target=OLD_TARGET,
            singleton_ref=claim_singleton,
            output_refs_by_vout={
                0: [(claim_singleton, 1)],
                5: [(branch_singleton, 1)],
            },
        )
        # Spending the unrelated branch singleton must NOT repoint the name.
        _update(wave_index, TARGET_A, branch_singleton,
                bytes.fromhex('f7' * 32), 435095)
        assert wave_index.resolve('scoped')['target'] == OLD_TARGET

    def test_first_writer_guard_cache(self, wave_index):
        from electrumx.server.wave_index import name_to_hash
        shared = bytes.fromhex('44' * 32) + struct.pack('<I', 0)
        _register(wave_index, name='firstname', target=OLD_TARGET,
                  singleton_ref=shared, tx_hash=bytes.fromhex('a1' * 32))
        # A second registration that claims the SAME singleton must NOT remap it.
        _register(wave_index, name='secondname', target=TARGET_A,
                  singleton_ref=shared, tx_hash=bytes.fromhex('b2' * 32),
                  height=410001)
        assert wave_index.singleton_cache[shared] == name_to_hash('firstname')

    def test_first_writer_guard_db(self, wave_index):
        from electrumx.server.wave_index import name_to_hash, WaveDBKeys
        shared = bytes.fromhex('44' * 32) + struct.pack('<I', 0)
        _register(wave_index, name='firstname', target=OLD_TARGET,
                  singleton_ref=shared, tx_hash=bytes.fromhex('a1' * 32))
        # Flush so the mapping lives only on disk, then clear the cache.
        with wave_index.db.utxo_db.write_batch() as batch:
            wave_index.flush(batch)
        assert wave_index.singleton_cache == {}
        assert wave_index.db.utxo_db.get(WaveDBKeys.SINGLETON + shared) == \
            name_to_hash('firstname')
        # New registration claiming the same (now on-disk) singleton: rejected.
        _register(wave_index, name='secondname', target=TARGET_A,
                  singleton_ref=shared, tx_hash=bytes.fromhex('b2' * 32),
                  height=410002)
        assert shared not in wave_index.singleton_cache


# ==========================================================================
# Test 5 — reorg with multiple updates in one flush (FIX C).
# ==========================================================================
class TestReorgMultiUpdate:
    def test_two_updates_one_flush_unwind(self, wave_index):
        singleton = _register(wave_index, height=410000)  # H0 -> OLD_TARGET
        # Two distinct target updates land in the SAME flush batch.
        _update(wave_index, TARGET_A, singleton,
                bytes.fromhex('a1' * 32), 410001)          # H1 -> A
        _update(wave_index, TARGET_B, singleton,
                bytes.fromhex('b2' * 32), 410002)          # H2 -> B
        with wave_index.db.utxo_db.write_batch() as batch:
            wave_index.flush(batch)

        # Cache cleared; resolves come straight from DB now.
        assert wave_index.resolve('12345')['target'] == TARGET_B

        # Backup H2 -> should return to A (NOT skip A straight to original).
        with wave_index.db.utxo_db.write_batch() as batch:
            wave_index.backup(batch, 410002)
        wave_index.hot_names.clear()
        assert wave_index.resolve('12345')['target'] == TARGET_A

        # Backup H1 -> should return to the original target (NOT leave B/A).
        with wave_index.db.utxo_db.write_batch() as batch:
            wave_index.backup(batch, 410001)
        wave_index.hot_names.clear()
        assert wave_index.resolve('12345')['target'] == OLD_TARGET

    def test_register_and_updates_same_flush_unwind(self, wave_index):
        # Harder variant: registration AND both updates share one flush.
        singleton = bytes.fromhex('22' * 32) + struct.pack('<I', 0)
        _register(wave_index, singleton_ref=singleton, height=410000)
        _update(wave_index, TARGET_A, singleton,
                bytes.fromhex('a1' * 32), 410001)
        _update(wave_index, TARGET_B, singleton,
                bytes.fromhex('b2' * 32), 410002)
        with wave_index.db.utxo_db.write_batch() as batch:
            wave_index.flush(batch)

        assert wave_index.resolve('12345')['target'] == TARGET_B

        with wave_index.db.utxo_db.write_batch() as batch:
            wave_index.backup(batch, 410002)
        wave_index.hot_names.clear()
        assert wave_index.resolve('12345')['target'] == TARGET_A

        with wave_index.db.utxo_db.write_batch() as batch:
            wave_index.backup(batch, 410001)
        wave_index.hot_names.clear()
        assert wave_index.resolve('12345')['target'] == OLD_TARGET


# ==========================================================================
# Test 6 — owner tracking on singleton moves (transfer / sale / mod).
# ==========================================================================
def _p2pkh(h160_hex):
    return bytes.fromhex('76a914' + h160_hex + '88ac')


def _plain_singleton_script(h160_hex):
    # OP_PUSHINPUTREFSINGLETON <ref(36)> OP_DROP <P2PKH>
    return bytes.fromhex('d8' + '22' * 36 + '75') + _p2pkh(h160_hex)


def _auth_singleton_script(h160_hex):
    # ( OP_REQUIREINPUTREF <ref> <sigHash> OP_2DROP ) OP_STATESEPARATOR
    # OP_PUSHINPUTREFSINGLETON <ref> OP_DROP <P2PKH>  — the form a target/state
    # update produces. base_locking_script must still recover the trailing P2PKH.
    return (bytes.fromhex('d1' + '22' * 36 + '20' + '33' * 32 + '6d' + 'bd' +
                          'd8' + '22' * 36 + '75') + _p2pkh(h160_hex))


def _move(wave_index, singleton_ref, script, tx_hash, height, vout=0):
    outs = [_FakeOutput(b'')] * vout + [_FakeOutput(script)]
    wave_index.process_tx(
        tx_hash, _FakeTx(outs), height, 0,
        None,  # plain move: no glyph envelope
        output_refs_by_vout={vout: [(singleton_ref, 1)]},
        spent_singleton_refs={singleton_ref},
    )


REG_H160 = '11' * 20  # owner at registration (from _p2pkh_tx)
B_H160 = 'bb' * 20
CLAIM_REF = bytes.fromhex('e5' * 32) + struct.pack('<I', 0)


class TestOwnerTracking:
    def _hashX(self, wave_index, h160_hex):
        return wave_index.env.coin.hashX_from_script(_p2pkh(h160_hex))

    def test_owner_set_at_registration(self, wave_index):
        _register(wave_index)
        assert wave_index._get_owner(CLAIM_REF) == self._hashX(wave_index, REG_H160)

    def test_owner_updates_on_transfer(self, wave_index):
        singleton = _register(wave_index)
        assert wave_index._get_owner(CLAIM_REF) == self._hashX(wave_index, REG_H160)
        _move(wave_index, singleton, _plain_singleton_script(B_H160),
              bytes.fromhex('f7' * 32), 435095)
        assert wave_index._get_owner(CLAIM_REF) == self._hashX(wave_index, B_H160)

    def test_owner_hashX_stable_across_auth_form(self, wave_index):
        # A target/state update wraps the same address in an auth covenant; the
        # owner hashX must be the base address — identical to the plain form.
        singleton = _register(wave_index)
        _move(wave_index, singleton, _auth_singleton_script(B_H160),
              bytes.fromhex('f7' * 32), 435095)
        assert wave_index._get_owner(CLAIM_REF) == self._hashX(wave_index, B_H160)

    def test_unmapped_singleton_does_not_change_owner(self, wave_index):
        _register(wave_index)
        unmapped = bytes.fromhex('99' * 32) + struct.pack('<I', 7)
        _move(wave_index, unmapped, _plain_singleton_script(B_H160),
              bytes.fromhex('cc' * 32), 435100)
        assert wave_index._get_owner(CLAIM_REF) == self._hashX(wave_index, REG_H160)

    def test_reverse_lookup_follows_owner_and_filters_stale(self, wave_index):
        singleton = _register(wave_index)
        # Flush registration so the OLD owner's reverse entry lands on disk.
        with wave_index.db.utxo_db.write_batch() as batch:
            wave_index.flush(batch)
        old_owner = self._hashX(wave_index, REG_H160)
        new_owner = self._hashX(wave_index, B_H160)
        assert [e['ref'] for e in wave_index.reverse_lookup(old_owner)] == \
            [wave_index._format_ref(CLAIM_REF)]

        # Move to B in a SEPARATE flush — the old reverse entry now lingers.
        _move(wave_index, singleton, _plain_singleton_script(B_H160),
              bytes.fromhex('f7' * 32), 435095)
        with wave_index.db.utxo_db.write_batch() as batch:
            wave_index.flush(batch)

        # New owner finds it; stale old-owner entry is filtered out.
        assert [e['ref'] for e in wave_index.reverse_lookup(new_owner)] == \
            [wave_index._format_ref(CLAIM_REF)]
        assert wave_index.reverse_lookup(old_owner) == []


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
