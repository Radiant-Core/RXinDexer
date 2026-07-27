"""
Regression tests for WAVE reverse_lookup name enrichment.

The bug: `wave.reverse_lookup` returned `ref`/`zone`/`owner` for every hit but
never a `name`. Enrichment lived in the RPC and REST layers and resolved the
name with `glyph_index.get_token_by_ref_str(hit['ref'])` — a lookup that can
*never* hit, because the two indexes key a WAVE name by different outpoints:

  * the wave index stores the *reveal* transaction's claim output
    (`claim_ref = reveal_txid + vout 0`), which is what reverse_lookup returns;
  * the Glyph token index stores the token's own commit/singleton ref.

The DB lookup therefore always missed, `get_token_by_ref_str` swallowed it and
returned None, and the name was silently dropped. Any caller filtering on the
presence of a name saw every owned WAVE name disappear.

The fix records a ref -> name back-edge (`WaveDBKeys.REF_NAME`) at registration
and names the hits inside `WaveIndex.reverse_lookup` itself, so both the RPC and
REST paths get it. These tests lock that in: a name-less hit is the failure.
"""

import struct

import pytest

from electrumx.lib.glyph import wave_full_name_from_token
from electrumx.server.wave_index import WaveDBKeys

from tests.server.test_wave_target_update import (  # reuse the in-memory harness
    _FakeDB,
    _make_env,
    _p2pkh_tx,
    OLD_TARGET,
)


REVEAL_TXID = bytes.fromhex('e5' * 32)
CLAIM_REF = REVEAL_TXID + struct.pack('<I', 0)
# Deliberately a *different* outpoint from CLAIM_REF — this divergence is the
# whole bug. A Glyph token for this name would be keyed here, not at CLAIM_REF.
TOKEN_REF = bytes.fromhex('22' * 32) + struct.pack('<I', 0)


@pytest.fixture
def wave_index():
    from electrumx.server.wave_index import WaveIndex
    return WaveIndex(_FakeDB(), _make_env())


def _register(wave_index, name='gatorcoin', domain='rxd', tx_hash=REVEAL_TXID,
              singleton_ref=TOKEN_REF, height=410000):
    envelope = {
        'protocols': [2, 5, 11],  # NFT + MUT + WAVE
        'metadata': {'attrs': {
            'name': name, 'domain': domain,
            'target': OLD_TARGET, 'target_type': 'address',
        }},
    }
    wave_index.process_tx(
        tx_hash, _p2pkh_tx(), height, 0, envelope,
        output_refs_by_vout={0: [(singleton_ref, 1)]},
        spent_singleton_refs=set(),
    )


def _flush(wave_index):
    with wave_index.db.utxo_db.write_batch() as batch:
        wave_index.flush(batch)


def _owner(wave_index, h160_hex='11' * 20):
    """Owner hashX for a P2PKH script — '11'*20 is the registrant in _p2pkh_tx."""
    script = bytes.fromhex('76a914' + h160_hex + '88ac')
    return wave_index.env.coin.hashX_from_script(script)


class TestReverseLookupNames:
    def test_hits_carry_name_and_full_name(self, wave_index):
        _register(wave_index)
        _flush(wave_index)

        hits = wave_index.reverse_lookup(_owner(wave_index))

        assert len(hits) == 1
        assert hits[0]['name'] == 'gatorcoin'
        assert hits[0]['full_name'] == 'gatorcoin.rxd'

    def test_no_hit_is_ever_nameless(self, wave_index):
        """The exact downstream failure: callers filter on `name` and lose
        every owned name when enrichment silently drops it."""
        for i, label in enumerate(['alpha', 'bravo', 'charlie']):
            _register(
                wave_index, name=label,
                tx_hash=bytes.fromhex(f'{i:02x}' * 32),
                singleton_ref=bytes.fromhex(f'{0xa0 + i:02x}' * 32) + struct.pack('<I', 0),
            )
        _flush(wave_index)

        hits = wave_index.reverse_lookup(_owner(wave_index))

        assert len(hits) == 3
        nameless = [h for h in hits if not h.get('name')]
        assert nameless == [], f'{len(nameless)}/{len(hits)} hits have no name'
        assert sorted(h['name'] for h in hits) == ['alpha', 'bravo', 'charlie']

    def test_name_resolves_from_the_unflushed_cache(self, wave_index):
        """`_get_ref_name` must read the pending cache before the DB, so a name
        is never bare merely because its block has not been flushed yet.

        (reverse_lookup itself scans the on-disk reverse-owner index and so
        surfaces nothing at all pre-flush — see the assertion below. The cache
        read matters for any caller reaching the helper mid-block.)
        """
        _register(wave_index)

        assert wave_index.reverse_lookup(_owner(wave_index)) == []
        assert wave_index._get_ref_name(CLAIM_REF) == 'gatorcoin.rxd'

    def test_name_survives_a_transfer_to_a_new_owner(self, wave_index):
        """The ref -> name edge is keyed by the claim ref, which does not move,
        so a name must stay resolvable after changing hands."""
        from tests.server.test_wave_target_update import (
            _move, _plain_singleton_script, B_H160,
        )
        _register(wave_index)
        _flush(wave_index)

        _move(wave_index, TOKEN_REF, _plain_singleton_script(B_H160),
              bytes.fromhex('f7' * 32), 435095)
        _flush(wave_index)

        hits = wave_index.reverse_lookup(_owner(wave_index, B_H160))

        assert [h.get('full_name') for h in hits] == ['gatorcoin.rxd']

    def test_ref_and_token_ref_genuinely_differ(self, wave_index):
        """Guard the premise. If a future change ever made the wave ref equal
        the Glyph token ref, the old glyph_index lookup would start working and
        this whole class of bug would look fixed for the wrong reason."""
        _register(wave_index)
        _flush(wave_index)

        hits = wave_index.reverse_lookup(_owner(wave_index))

        assert hits[0]['ref'] == wave_index._format_ref(CLAIM_REF)
        assert hits[0]['ref'] != wave_index._format_ref(TOKEN_REF)

    def test_subdomain_keeps_its_parent_in_full_name(self, wave_index):
        _register(wave_index, name='shop', domain='rxd')
        _flush(wave_index)
        hits = wave_index.reverse_lookup(_owner(wave_index))
        assert hits[0]['full_name'] == 'shop.rxd'
        assert hits[0]['name'] == 'shop'

    def test_rejected_registration_leaves_no_name_row(self, wave_index):
        """A registration naming an unresolvable parent is dropped, so it must
        not leave a REF_NAME row claiming that outpoint."""
        _register(wave_index, name='shop', domain='no-such-parent')

        assert wave_index.refname_cache == {}
        assert wave_index._get_ref_name(CLAIM_REF) is None

    def test_missing_ref_name_row_omits_name_without_failing(self, wave_index):
        """A DB predating REF_NAME (or one whose backfill has not run) must
        degrade to a name-less hit, not raise."""
        _register(wave_index)
        _flush(wave_index)
        del wave_index.db.utxo_db.store[WaveDBKeys.REF_NAME + CLAIM_REF]

        hits = wave_index.reverse_lookup(_owner(wave_index))

        assert len(hits) == 1
        assert 'name' not in hits[0]
        assert 'full_name' not in hits[0]


class TestRefNamePrefixIsolation:
    def test_ref_name_rows_do_not_pollute_the_reverse_owner_scan(self, wave_index):
        """REF_NAME must not share a head with REVERSE_OWNER. `WR + hashX + ref`
        is prefix-scanned, so a REF_NAME prefix of `WRN` would be swept up by
        any owner whose hashX begins with 0x4E ('N') and decoded as a bogus ref.
        """
        assert not WaveDBKeys.REF_NAME.startswith(WaveDBKeys.REVERSE_OWNER)
        assert not WaveDBKeys.REVERSE_OWNER.startswith(WaveDBKeys.REF_NAME)

        # Owner hashX chosen to start with b'N' — the collision trigger.
        hostile_owner = b'N' + bytes(range(10))
        wave_index.db.utxo_db.store[
            WaveDBKeys.REF_NAME + CLAIM_REF] = b'gatorcoin.rxd'

        assert wave_index.reverse_lookup(hostile_owner) == []

    def test_ref_name_rows_do_not_pollute_the_name_scan(self, wave_index):
        """Likewise for `WN` (NAME), which list_names/stats prefix-scan."""
        assert not WaveDBKeys.REF_NAME.startswith(WaveDBKeys.NAME)
        assert not WaveDBKeys.NAME.startswith(WaveDBKeys.REF_NAME)

        _register(wave_index)
        _flush(wave_index)

        assert wave_index._count_db_prefix(WaveDBKeys.NAME) == 1

    def test_ref_name_head_is_unique_among_all_wave_prefixes(self):
        prefixes = [v for k, v in vars(WaveDBKeys).items()
                    if not k.startswith('_') and isinstance(v, bytes)]
        others = [p for p in prefixes if p != WaveDBKeys.REF_NAME]
        for other in others:
            assert not other.startswith(WaveDBKeys.REF_NAME)
            assert not WaveDBKeys.REF_NAME.startswith(other)


class TestBackfillRefNames:
    """A mainnet DB synced before REF_NAME existed has names but no ref -> name
    rows; without an in-place backfill it would keep serving name-less hits
    until a full reindex."""

    def _seed_glyph_token(self, wave_index, name='gatorcoin.rxd'):
        from electrumx.server.glyph_index import GlyphDBKeys, GlyphTokenInfo
        token = GlyphTokenInfo()
        token.ref = TOKEN_REF
        token.protocols = [2, 5, 11]
        token.name = name
        token.deploy_txid = REVEAL_TXID
        token.deploy_height = 410000
        wave_index.db.utxo_db.store[
            GlyphDBKeys.TOKEN + TOKEN_REF] = token.to_bytes()

    def test_backfill_names_hits_on_an_existing_db(self, wave_index):
        _register(wave_index)
        _flush(wave_index)
        # Simulate the pre-fix on-disk state: names indexed, no REF_NAME rows.
        del wave_index.db.utxo_db.store[WaveDBKeys.REF_NAME + CLAIM_REF]
        self._seed_glyph_token(wave_index)

        assert wave_index.reverse_lookup(_owner(wave_index))[0].get('name') is None

        assert wave_index.backfill_ref_names(None) == 1
        _flush(wave_index)

        hits = wave_index.reverse_lookup(_owner(wave_index))
        assert hits[0]['name'] == 'gatorcoin'
        assert hits[0]['full_name'] == 'gatorcoin.rxd'

    def test_backfill_covers_both_ref_forms(self, wave_index):
        """The two population paths key the same name at different outpoints,
        so the backfill must write both to be correct on either lineage."""
        self._seed_glyph_token(wave_index)
        wave_index.backfill_ref_names(None)
        _flush(wave_index)

        store = wave_index.db.utxo_db.store
        assert store[WaveDBKeys.REF_NAME + CLAIM_REF] == b'gatorcoin.rxd'
        assert store[WaveDBKeys.REF_NAME + TOKEN_REF] == b'gatorcoin.rxd'

    def test_backfill_is_a_noop_once_populated(self, wave_index):
        _register(wave_index)
        _flush(wave_index)
        self._seed_glyph_token(wave_index)

        assert wave_index.backfill_ref_names(None) == 0

    def test_backfill_records_no_undo_for_historical_rows(self, wave_index):
        """Backfilled rows restate history; recording undo for them at ancient
        heights would be pure waste and could unwind a valid row on reorg."""
        self._seed_glyph_token(wave_index)
        wave_index.backfill_ref_names(None)
        _flush(wave_index)

        undo_rows = [k for k in wave_index.db.utxo_db.store
                     if k.startswith(WaveDBKeys.UNDO)]
        assert undo_rows == []


class TestWaveFullNameFromToken:
    """The RPC/REST fallback shares this helper; the original code read only
    `attrs.name`, which is optional user metadata."""

    def test_prefers_top_level_name(self):
        assert wave_full_name_from_token(
            {'name': 'gatorcoin.rxd', 'attrs': {'name': 'other'}}
        ) == 'gatorcoin.rxd'

    def test_falls_back_to_attrs(self):
        assert wave_full_name_from_token(
            {'name': None, 'attrs': {'name': 'gatorcoin', 'domain': 'rxd'}}
        ) == 'gatorcoin.rxd'

    def test_attrs_without_domain_defaults_to_rxd(self):
        assert wave_full_name_from_token(
            {'attrs': {'name': 'gatorcoin'}}) == 'gatorcoin.rxd'

    @pytest.mark.parametrize('token', [None, {}, {'attrs': {}},
                                       {'attrs': None}, {'name': ''},
                                       {'attrs': {'name': 123}},
                                       {'attrs': 'not-a-dict'}])
    def test_unresolvable_returns_none(self, token):
        assert wave_full_name_from_token(token) is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
