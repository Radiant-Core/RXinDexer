"""WAVE name-lifecycle tests: expiry stamping, renewal, grace, lapse,
supersession, burn-release, and reorg-undo safety.

Reuses the in-memory harness from test_wave_target_update. Time is fully
controlled: registrations pass an explicit ``block_time`` and queries pass
``now``, and the env pins ``wave_expiry_floor_ts = 0`` so the transition
floor doesn't mask lapse behaviour (a dedicated test covers the floor).
"""
import struct

import pytest

from tests.server.test_wave_target_update import (
    _FakeDB, _FakeOutput, _FakeTx, _make_env, _p2pkh_tx,
    OLD_TARGET, TARGET_A,
)

from electrumx.server.wave_index import (
    WaveIndex, WaveDBKeys, name_to_hash,
    WAVE_REGISTRATION_TERM, WAVE_GRACE_PERIOD, wave_name_price,
    WAVE_TREASURY_ADDRESS_DEFAULT,
)

# A fixed, readable timeline (arbitrary epoch anchor).
T0 = 1_600_000_000                      # registration block time
EXPIRY = T0 + WAVE_REGISTRATION_TERM    # expected first expiry
GRACE_END = EXPIRY + WAVE_GRACE_PERIOD

NAME = '12345'
REG_HEIGHT = 410000


def _lifecycle_env():
    env = _make_env()
    env.wave_expiry_enforce = True
    env.wave_expiry_floor_ts = 0        # tests control time directly
    env.wave_treasury_address = WAVE_TREASURY_ADDRESS_DEFAULT
    return env


@pytest.fixture
def idx():
    return WaveIndex(_FakeDB(), _lifecycle_env())


def _register(idx, name=NAME, target=OLD_TARGET, singleton_ref=None,
              tx_hash=None, height=REG_HEIGHT, block_time=T0):
    if singleton_ref is None:
        singleton_ref = bytes.fromhex('22' * 32) + struct.pack('<I', 0)
    if tx_hash is None:
        tx_hash = bytes.fromhex('e5' * 32)
    envelope = {
        'protocols': [2, 5, 11],
        'metadata': {'attrs': {
            'name': name, 'domain': 'rxd',
            'target': target, 'target_type': 'address',
        }},
    }
    idx.process_tx(
        tx_hash, _p2pkh_tx(), height, 0, envelope,
        output_refs_by_vout={0: [(singleton_ref, 1)]},
        spent_singleton_refs=set(),
        block_time=block_time,
    )
    return singleton_ref


def _treasury_tx(idx, amount, recreate_singleton=None):
    """A tx paying ``amount`` to the treasury; optionally re-creates the
    spent singleton at vout 0 (a renewal keeps the name alive)."""
    treasury_out = _FakeOutput(idx.treasury_script)
    treasury_out.value = amount
    return _FakeTx([_p2pkh_tx().outputs[0], treasury_out])


def _renew(idx, singleton_ref, amount, height, block_time,
           tx_hash=None):
    tx = _treasury_tx(idx, amount)
    idx.process_tx(
        tx_hash or bytes.fromhex('ab' * 32), tx, height, 0, None,
        # singleton re-created at vout 0 (owner keeps the name)
        output_refs_by_vout={0: [(singleton_ref, 1)]},
        spent_singleton_refs={singleton_ref},
        block_time=block_time,
    )


def _burn(idx, singleton_ref, height, block_time=0, tx_hash=None):
    """Spend the singleton with NO re-created output — destroys it."""
    idx.process_tx(
        tx_hash or bytes.fromhex('cd' * 32), _p2pkh_tx(), height, 0, None,
        output_refs_by_vout={},
        spent_singleton_refs={singleton_ref},
        block_time=block_time,
    )


# ==========================================================================
# Registration stamps a block-time-derived expiry
# ==========================================================================
class TestExpiryStamp:
    def test_registration_sets_expiry_and_status(self, idx):
        _register(idx)
        result = idx.resolve(NAME, now=T0 + 1000)
        assert result['expires'] == EXPIRY
        assert result['status'] == 'active'

    def test_no_block_time_means_no_expiry(self, idx):
        _register(idx, block_time=0)
        result = idx.resolve(NAME, now=T0 + 10 * WAVE_REGISTRATION_TERM)
        # Legacy semantics: no expiry row -> cannot lapse, no lifecycle fields
        assert result is not None
        assert 'expires' not in result

    def test_check_available_reports_lifecycle(self, idx):
        _register(idx)
        result = idx.check_available(NAME, now=T0 + 1000)
        assert result['available'] is False
        assert result['expires'] == EXPIRY


# ==========================================================================
# Grace and lapse
# ==========================================================================
class TestGraceAndLapse:
    def test_grace_window_still_resolves(self, idx):
        _register(idx)
        result = idx.resolve(NAME, now=EXPIRY + 1)
        assert result is not None
        assert result['status'] == 'grace'
        assert result['grace_until'] == GRACE_END

    def test_lapsed_name_stops_resolving(self, idx):
        _register(idx)
        assert idx.resolve(NAME, now=GRACE_END + 1) is None

    def test_lapsed_name_reports_available(self, idx):
        _register(idx)
        result = idx.check_available(NAME, now=GRACE_END + 1)
        assert result['available'] is True
        assert result['expired'] is True
        assert result['previous_ref'] is not None

    def test_hot_cache_cannot_resurrect_lapsed_name(self, idx):
        _register(idx)
        # Populate the hot cache while active…
        assert idx.resolve(NAME, now=T0 + 1000) is not None
        assert idx.resolve(NAME, now=GRACE_END + 1) is None

    def test_enforce_off_disables_lapse(self):
        env = _lifecycle_env()
        env.wave_expiry_enforce = False
        idx = WaveIndex(_FakeDB(), env)
        _register(idx)
        assert idx.resolve(NAME, now=GRACE_END + 1) is not None


# ==========================================================================
# Renewal
# ==========================================================================
class TestRenewal:
    def test_renewal_extends_from_current_expiry(self, idx):
        singleton = _register(idx)
        price = wave_name_price(NAME)
        # Renew EARLY (half-way through the term): the new term stacks on
        # the current expiry, never on the renewal time.
        renew_time = T0 + WAVE_REGISTRATION_TERM // 2
        _renew(idx, singleton, price, REG_HEIGHT + 100, renew_time)
        result = idx.resolve(NAME, now=renew_time)
        assert result['expires'] == EXPIRY + WAVE_REGISTRATION_TERM

    def test_renewal_in_grace_extends_from_block_time(self, idx):
        singleton = _register(idx)
        price = wave_name_price(NAME)
        renew_time = EXPIRY + WAVE_GRACE_PERIOD // 2  # already past expiry
        _renew(idx, singleton, price, REG_HEIGHT + 100, renew_time)
        result = idx.resolve(NAME, now=renew_time)
        assert result['expires'] == renew_time + WAVE_REGISTRATION_TERM

    def test_underpaid_renewal_ignored(self, idx):
        singleton = _register(idx)
        _renew(idx, singleton, wave_name_price(NAME) - 1,
               REG_HEIGHT + 100, T0 + 1000)
        assert idx.resolve(NAME, now=T0 + 2000)['expires'] == EXPIRY

    def test_unrelated_singleton_cannot_renew(self, idx):
        _register(idx)
        stranger = bytes.fromhex('99' * 32) + struct.pack('<I', 7)
        _renew(idx, stranger, wave_name_price(NAME) * 2,
               REG_HEIGHT + 100, T0 + 1000)
        assert idx.resolve(NAME, now=T0 + 2000)['expires'] == EXPIRY

    def test_one_payment_cannot_renew_two_names(self, idx):
        s1 = _register(idx, name='name-a',
                       singleton_ref=bytes.fromhex('31' * 32) + struct.pack('<I', 0),
                       tx_hash=bytes.fromhex('41' * 32))
        s2 = _register(idx, name='name-b',
                       singleton_ref=bytes.fromhex('32' * 32) + struct.pack('<I', 0),
                       tx_hash=bytes.fromhex('42' * 32))
        price = wave_name_price('name-a')
        # One tx spends BOTH singletons but pays for only one renewal.
        tx = _treasury_tx(idx, price)
        idx.process_tx(
            bytes.fromhex('ac' * 32), tx, REG_HEIGHT + 100, 0, None,
            output_refs_by_vout={0: [(s1, 1)], 1: [(s2, 1)]},
            spent_singleton_refs={s1, s2},
            block_time=T0 + 1000,
        )
        renewed = [idx.resolve(n, now=T0 + 2000)['expires'] != EXPIRY
                   for n in ('name-a', 'name-b')]
        assert renewed.count(True) == 1  # budget covers exactly one


# ==========================================================================
# Lapsed-name supersession
# ==========================================================================
class TestSupersession:
    def test_reregistration_of_lapsed_name_becomes_canonical(self, idx):
        _register(idx)
        new_singleton = bytes.fromhex('33' * 32) + struct.pack('<I', 0)
        new_tx = bytes.fromhex('f1' * 32)
        _register(idx, target=TARGET_A, singleton_ref=new_singleton,
                  tx_hash=new_tx, height=REG_HEIGHT + 500,
                  block_time=GRACE_END + 100)
        result = idx.resolve(NAME, now=GRACE_END + 200)
        assert result is not None
        assert result['target'] == TARGET_A
        assert result['expires'] == GRACE_END + 100 + WAVE_REGISTRATION_TERM
        # Not recorded as a duplicate — it IS the canonical now.
        assert idx._has_duplicates(NAME) is False

    def test_reregistration_of_active_name_stays_duplicate(self, idx):
        _register(idx)
        _register(idx, target=TARGET_A,
                  singleton_ref=bytes.fromhex('33' * 32) + struct.pack('<I', 0),
                  tx_hash=bytes.fromhex('f1' * 32), height=REG_HEIGHT + 500,
                  block_time=T0 + 1000)  # well within the term
        assert idx.resolve(NAME, now=T0 + 2000)['target'] == OLD_TARGET
        assert idx._has_duplicates(NAME) is True

    def test_old_singleton_loses_control_after_supersession(self, idx):
        old_singleton = _register(idx)
        new_singleton = bytes.fromhex('33' * 32) + struct.pack('<I', 0)
        _register(idx, target=TARGET_A, singleton_ref=new_singleton,
                  tx_hash=bytes.fromhex('f1' * 32), height=REG_HEIGHT + 500,
                  block_time=GRACE_END + 100)
        # Old owner tries a mod-update against the new canonical.
        envelope = {
            'protocols': [],
            'metadata': {'attrs': {
                'target': OLD_TARGET, 'target_type': 'address',
            }},
        }
        idx.process_tx(
            bytes.fromhex('f2' * 32), _p2pkh_tx(), REG_HEIGHT + 600, 0,
            envelope, output_refs_by_vout=None,
            spent_singleton_refs={old_singleton},
            block_time=GRACE_END + 200,
        )
        assert idx.resolve(NAME, now=GRACE_END + 300)['target'] == TARGET_A
        # …and cannot renew it either.
        _renew(idx, old_singleton, wave_name_price(NAME) * 2,
               REG_HEIGHT + 700, GRACE_END + 400)
        assert (idx.resolve(NAME, now=GRACE_END + 500)['expires']
                == GRACE_END + 100 + WAVE_REGISTRATION_TERM)


# ==========================================================================
# Burn releases the name
# ==========================================================================
class TestBurnRelease:
    def test_burn_frees_name_immediately(self, idx):
        singleton = _register(idx)
        _burn(idx, singleton, REG_HEIGHT + 100)
        assert idx.resolve(NAME, now=T0 + 1000) is None
        assert idx.check_available(NAME, now=T0 + 1000)['available'] is True

    def test_burned_name_can_be_reregistered_canonically(self, idx):
        singleton = _register(idx)
        _burn(idx, singleton, REG_HEIGHT + 100)
        _register(idx, target=TARGET_A,
                  singleton_ref=bytes.fromhex('33' * 32) + struct.pack('<I', 0),
                  tx_hash=bytes.fromhex('f1' * 32), height=REG_HEIGHT + 200,
                  block_time=T0 + 5000)
        result = idx.resolve(NAME, now=T0 + 6000)
        assert result['target'] == TARGET_A
        assert idx._has_duplicates(NAME) is False

    def test_burn_survives_flush(self, idx):
        singleton = _register(idx)
        with idx.db.utxo_db.write_batch() as batch:
            idx.flush(batch)
        _burn(idx, singleton, REG_HEIGHT + 100)
        with idx.db.utxo_db.write_batch() as batch:
            idx.flush(batch)
        assert idx.resolve(NAME, now=T0 + 1000) is None
        assert idx.db.utxo_db.get(
            WaveDBKeys.NAME + name_to_hash(NAME)) is None

    def test_transfer_does_not_free_name(self, idx):
        singleton = _register(idx)
        # Plain transfer: singleton re-created at vout 0, no envelope.
        idx.process_tx(
            bytes.fromhex('cd' * 32), _p2pkh_tx(), REG_HEIGHT + 100, 0, None,
            output_refs_by_vout={0: [(singleton, 1)]},
            spent_singleton_refs={singleton},
            block_time=T0 + 1000,
        )
        assert idx.resolve(NAME, now=T0 + 2000) is not None

    def test_enforce_off_disables_burn_release(self):
        env = _lifecycle_env()
        env.wave_expiry_enforce = False
        idx = WaveIndex(_FakeDB(), env)
        singleton = _register(idx)
        _burn(idx, singleton, REG_HEIGHT + 100)
        assert idx.resolve(NAME, now=T0 + 1000) is not None


# ==========================================================================
# Transition floor
# ==========================================================================
class TestExpiryFloor:
    def test_floor_prevents_early_lapse(self):
        env = _lifecycle_env()
        env.wave_expiry_floor_ts = GRACE_END + 10_000_000
        idx = WaveIndex(_FakeDB(), env)
        _register(idx)
        # Past the name's own expiry + grace — but the floor holds it.
        result = idx.resolve(NAME, now=GRACE_END + 1)
        assert result is not None
        assert result['expires'] == GRACE_END + 10_000_000


# ==========================================================================
# Reorg-undo safety
# ==========================================================================
class TestLifecycleUndo:
    def _flush(self, idx):
        with idx.db.utxo_db.write_batch() as batch:
            idx.flush(batch)

    def test_renewal_undo_restores_prior_expiry(self, idx):
        singleton = _register(idx)
        self._flush(idx)
        renew_height = REG_HEIGHT + 100
        _renew(idx, singleton, wave_name_price(NAME), renew_height, T0 + 1000)
        self._flush(idx)
        assert idx.resolve(NAME, now=T0 + 2000)['expires'] > EXPIRY
        with idx.db.utxo_db.write_batch() as batch:
            idx.backup(batch, renew_height)
        assert idx.resolve(NAME, now=T0 + 2000)['expires'] == EXPIRY

    def test_supersession_undo_restores_old_canonical(self, idx):
        _register(idx)
        self._flush(idx)
        super_height = REG_HEIGHT + 500
        _register(idx, target=TARGET_A,
                  singleton_ref=bytes.fromhex('33' * 32) + struct.pack('<I', 0),
                  tx_hash=bytes.fromhex('f1' * 32), height=super_height,
                  block_time=GRACE_END + 100)
        self._flush(idx)
        assert idx.resolve(NAME, now=GRACE_END + 200)['target'] == TARGET_A
        with idx.db.utxo_db.write_batch() as batch:
            idx.backup(batch, super_height)
        idx.hot_names.clear()
        # Old canonical mapping (and its lapsed expiry) restored: the name is
        # back to its pre-reorg lapsed state.
        assert idx.resolve(NAME, now=GRACE_END + 200) is None
        assert (idx.db.utxo_db.get(WaveDBKeys.NAME + name_to_hash(NAME))
                is not None)

    def test_burn_undo_restores_name(self, idx):
        singleton = _register(idx)
        self._flush(idx)
        burn_height = REG_HEIGHT + 100
        _burn(idx, singleton, burn_height)
        self._flush(idx)
        assert idx.resolve(NAME, now=T0 + 1000) is None
        with idx.db.utxo_db.write_batch() as batch:
            idx.backup(batch, burn_height)
        idx.hot_names.clear()
        result = idx.resolve(NAME, now=T0 + 1000)
        assert result is not None
        assert result['target'] == OLD_TARGET
        assert result['expires'] == EXPIRY
