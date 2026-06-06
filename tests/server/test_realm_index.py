"""
Realm (realm_v1) index tests for RXinDexer.

Covers electrumx/server/realm_index.py:
  - realm_v1 payload recognition + field extraction/validation
  - indexing a mint (process_tx) keyed by the realm slug id
  - realm.list / get_by_id / search query handlers
  - the "tradeable" property: the reported owner = the CURRENT NFT holder
    (resolved from the Glyph holder index), NOT the immutable payload owner
  - first-registration-wins (anti-squat) by id
  - flush -> reload (persistence) and reorg backup unwind

Mirrors tests/server/test_wave_target_update.py: a real in-memory KV DB so the
flush/backup cycle runs end to end, plus a fake Glyph index whose holder map can
be mutated to simulate a transfer.
"""

import struct

import pytest


# --------------------------------------------------------------------------
# Minimal in-memory utxo_db (get / iterator(prefix|seek) / write_batch).
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
        for key, value in sorted(self.store.items()):
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
    def __init__(self, pk_script=b''):
        self.pk_script = pk_script


class _FakeTx:
    def __init__(self, n_outputs=1):
        self.outputs = [_FakeOutput() for _ in range(n_outputs)]
        self.inputs = []


class _FakeGlyphIndex:
    """Holder map keyed by 36-byte ref -> address. Mutate to simulate a trade."""
    def __init__(self):
        self.holders = {}

    def set_holder(self, ref: bytes, address):
        self.holders[ref] = address

    def get_token_holders(self, ref, limit=100, cursor=None):
        addr = self.holders.get(ref)
        return {'holders': [{'address': addr, 'amount': 1}] if addr else []}


def _make_env():
    from unittest.mock import Mock
    env = Mock()
    env.realm_index = True
    env.reorg_limit = 10
    return env


# --------------------------------------------------------------------------
# realm_v1 Glyph payload (mirrors packages/sdk/src/realm.ts buildRealmPayload).
# --------------------------------------------------------------------------
def _realm_payload(id='founders-isle', name="Founder's Isle", kind='world',
                   spawn=(128, 18, 200), seed=7777, owner='1Minter',
                   creator='1Minter', royalty_bps=250, desc='first realm'):
    base = {
        'item_id': f'realm_{id}',
        'slot': 'misc',
        'rarity': 'legendary' if kind == 'world' else 'epic',
        'tags': ['realm', kind, 'destination'],
        'stats': {},
        'stackable': False,
        'creator': creator,
    }
    if royalty_bps:
        base['royalty_bps'] = royalty_bps
    realm = {'id': id, 'name': name, 'kind': kind, 'spawn': list(spawn),
             'seed': seed, 'owner': owner}
    if desc:
        realm['desc'] = desc
    return {
        'v': 2,
        'p': [2],  # GLYPH_NFT only
        'name': name,
        'app': {'namespace': 'rxd.game', 'schema': 'realm_v1',
                'data': {'base': base, 'realm': realm}},
    }


def _envelope(payload):
    return {'protocols': payload.get('p', []), 'metadata': payload}


def _singleton(seed_byte='22', vout=0):
    return bytes.fromhex(seed_byte * 32) + struct.pack('<I', vout)


@pytest.fixture
def index():
    from electrumx.server.realm_index import RealmIndex
    gi = _FakeGlyphIndex()
    idx = RealmIndex(_FakeDB(), _make_env(), glyph_index=gi)
    idx._test_glyph = gi  # convenience handle for tests
    return idx


def _mint(index, payload=None, singleton=None, tx_hash=None, height=420000):
    payload = payload or _realm_payload()
    singleton = singleton if singleton is not None else _singleton()
    tx_hash = tx_hash or bytes.fromhex('ab' * 32)
    index.process_tx(tx_hash, _FakeTx(1), height, 0, _envelope(payload),
                     output_refs_by_vout={0: [(singleton, 1)]},
                     spent_singleton_refs=set())
    return singleton


# ==========================================================================
# Field extraction / validation
# ==========================================================================
class TestExtractRealmFields:
    def test_extracts_a_valid_realm(self):
        from electrumx.server.realm_index import extract_realm_fields
        f = extract_realm_fields(_realm_payload())
        assert f is not None
        assert f['id'] == 'founders-isle'
        assert f['name'] == "Founder's Isle"
        assert f['kind'] == 'world'
        assert f['seed'] == 7777
        assert f['spawn'] == [128, 18, 200]
        assert f['owner'] == '1Minter'
        assert f['creator'] == '1Minter'      # from base
        assert f['royalty_bps'] == 250         # from base
        assert f['desc'] == 'first realm'

    def test_rejects_non_realm_schema(self):
        from electrumx.server.realm_index import extract_realm_fields
        p = _realm_payload()
        p['app']['schema'] = 'game_item_v1'
        assert extract_realm_fields(p) is None

    def test_rejects_wrong_namespace(self):
        from electrumx.server.realm_index import extract_realm_fields
        p = _realm_payload()
        p['app']['namespace'] = 'rxd.other'
        assert extract_realm_fields(p) is None

    @pytest.mark.parametrize('bad_id', ['Bad Id', 'x', '-leading', 'a' * 49, 123, ''])
    def test_rejects_bad_id(self, bad_id):
        from electrumx.server.realm_index import extract_realm_fields
        p = _realm_payload()
        p['app']['data']['realm']['id'] = bad_id
        assert extract_realm_fields(p) is None

    def test_rejects_bad_kind(self):
        from electrumx.server.realm_index import extract_realm_fields
        p = _realm_payload(kind='galaxy')
        assert extract_realm_fields(p) is None

    @pytest.mark.parametrize('bad_seed', [-1, 0x1_0000_0000, 'x', 1.5])
    def test_rejects_bad_seed(self, bad_seed):
        from electrumx.server.realm_index import extract_realm_fields
        p = _realm_payload()
        p['app']['data']['realm']['seed'] = bad_seed
        assert extract_realm_fields(p) is None

    @pytest.mark.parametrize('bad_spawn', [[1, 2], [1, 2, 'z'], 'xyz', [1, 2, 3, 4]])
    def test_rejects_bad_spawn(self, bad_spawn):
        from electrumx.server.realm_index import extract_realm_fields
        p = _realm_payload()
        p['app']['data']['realm']['spawn'] = bad_spawn
        assert extract_realm_fields(p) is None

    def test_optional_fields_absent(self):
        from electrumx.server.realm_index import extract_realm_fields
        p = _realm_payload(creator=None, royalty_bps=0, desc=None)
        # creator=None is dropped by the JS builder; emulate by removing it.
        p['app']['data']['base'].pop('creator', None)
        f = extract_realm_fields(p)
        assert f is not None
        assert 'creator' not in f
        assert 'royalty_bps' not in f
        assert 'desc' not in f


# ==========================================================================
# Indexing + queries
# ==========================================================================
class TestIndexAndQuery:
    def test_index_and_get_by_id(self, index):
        ref = _mint(index)
        index._test_glyph.set_holder(ref, '1Minter')
        rec = index.get_by_id('founders-isle')
        assert rec is not None
        assert rec['name'] == "Founder's Isle"
        assert rec['kind'] == 'world'
        assert rec['seed'] == 7777
        assert rec['spawn'] == [128, 18, 200]
        assert rec['creator'] == '1Minter'
        assert rec['royalty_bps'] == 250
        assert rec['owner'] == '1Minter'          # current holder
        assert rec['minted_owner'] == '1Minter'   # immutable payload owner
        assert rec['ref'] is not None

    def test_get_by_id_unknown(self, index):
        assert index.get_by_id('nope') is None

    def test_non_realm_tx_is_ignored(self, index):
        p = _realm_payload()
        p['app']['schema'] = 'game_item_v1'
        index.process_tx(bytes.fromhex('cd' * 32), _FakeTx(1), 420001, 0,
                         _envelope(p), output_refs_by_vout={0: [(_singleton(), 1)]},
                         spent_singleton_refs=set())
        assert index.stats()['total_realms'] == 0

    def test_list_filters_and_sorts(self, index):
        _mint(index, _realm_payload(id='alpha-world', name='Alpha', kind='world'),
              singleton=_singleton('11'), tx_hash=bytes.fromhex('a1' * 32), height=420001)
        _mint(index, _realm_payload(id='beta-arena', name='Beta', kind='arena'),
              singleton=_singleton('22'), tx_hash=bytes.fromhex('b2' * 32), height=420002)
        _mint(index, _realm_payload(id='gamma-exp', name='Gamma', kind='experience'),
              singleton=_singleton('33'), tx_hash=bytes.fromhex('c3' * 32), height=420003)

        assert [r['id'] for r in index.list(kind='arena')] == ['beta-arena']
        # default sort 'new' = newest height first
        assert [r['id'] for r in index.list()] == ['gamma-exp', 'beta-arena', 'alpha-world']
        # name sort
        assert [r['name'] for r in index.list(sort='name')] == ['Alpha', 'Beta', 'Gamma']
        # search over name/desc/id
        assert [r['id'] for r in index.search('beta')] == ['beta-arena']
        assert index.search('zzz') == []

    def test_list_filters_by_current_owner(self, index):
        r1 = _mint(index, _realm_payload(id='one'), singleton=_singleton('11'),
                   tx_hash=bytes.fromhex('a1' * 32))
        r2 = _mint(index, _realm_payload(id='two'), singleton=_singleton('22'),
                   tx_hash=bytes.fromhex('b2' * 32), height=420001)
        index._test_glyph.set_holder(r1, '1Alice')
        index._test_glyph.set_holder(r2, '1Bob')
        assert [r['id'] for r in index.list(owner='1Alice')] == ['one']
        assert [r['id'] for r in index.list(owner='1Bob')] == ['two']


# ==========================================================================
# The "tradeable" property: owner follows the NFT holder.
# ==========================================================================
class TestOwnerFollowsHolder:
    def test_owner_reflects_current_holder_not_payload(self, index):
        ref = _mint(index, _realm_payload(owner='1Minter', creator='1Minter'))
        index._test_glyph.set_holder(ref, '1Minter')
        assert index.get_by_id('founders-isle')['owner'] == '1Minter'

        # Transfer the NFT: the holder index now points at a new address.
        index._test_glyph.set_holder(ref, '1Buyer')
        rec = index.get_by_id('founders-isle')
        assert rec['owner'] == '1Buyer'          # edit rights moved with the NFT
        assert rec['minted_owner'] == '1Minter'  # immutable creator/owner unchanged
        assert rec['creator'] == '1Minter'

    def test_owner_falls_back_to_payload_when_unresolved(self, index):
        # No holder recorded (e.g. glyph index can't resolve) → fall back to the
        # payload owner rather than reporting None.
        _mint(index, _realm_payload(owner='1Minter'))
        assert index.get_by_id('founders-isle')['owner'] == '1Minter'


# ==========================================================================
# Anti-squat: first registration of a slug wins.
# ==========================================================================
class TestFirstRegistrationWins:
    def test_duplicate_id_ignored(self, index):
        _mint(index, _realm_payload(id='dup', name='Original', owner='1Alice'),
              singleton=_singleton('11'), tx_hash=bytes.fromhex('a1' * 32))
        _mint(index, _realm_payload(id='dup', name='Hijack', owner='1Mallory'),
              singleton=_singleton('99'), tx_hash=bytes.fromhex('99' * 32), height=420005)
        rec = index.get_by_id('dup')
        assert rec['name'] == 'Original'
        assert rec['minted_owner'] == '1Alice'

    def test_duplicate_ignored_across_flush(self, index):
        _mint(index, _realm_payload(id='dup', name='Original', owner='1Alice'),
              singleton=_singleton('11'), tx_hash=bytes.fromhex('a1' * 32))
        with index.db.utxo_db.write_batch() as batch:
            index.flush(batch)
        assert index.realm_cache == {}
        _mint(index, _realm_payload(id='dup', name='Hijack', owner='1Mallory'),
              singleton=_singleton('99'), tx_hash=bytes.fromhex('99' * 32), height=420005)
        assert index.get_by_id('dup')['name'] == 'Original'


# ==========================================================================
# Persistence (flush -> reload) and reorg backup unwind.
# ==========================================================================
class TestPersistenceAndReorg:
    def test_survives_flush(self, index):
        ref = _mint(index)
        with index.db.utxo_db.write_batch() as batch:
            index.flush(batch)
        assert index.realm_cache == {}  # caches cleared
        index._test_glyph.set_holder(ref, '1Holder')
        rec = index.get_by_id('founders-isle')  # served from DB only
        assert rec is not None
        assert rec['name'] == "Founder's Isle"
        assert rec['owner'] == '1Holder'

    def test_backup_unwinds_a_mint(self, index):
        _mint(index, height=420010)
        with index.db.utxo_db.write_batch() as batch:
            index.flush(batch)
        assert index.get_by_id('founders-isle') is not None
        with index.db.utxo_db.write_batch() as batch:
            index.backup(batch, 420010)
        assert index.get_by_id('founders-isle') is None  # reorg removed it
        assert index.stats()['total_realms'] == 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
