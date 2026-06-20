# Focused regression tests for the reorg undo-key fix.
#
#   1. ref-loc/WAVE undo info must be read back under the same key it was
#      written (b'RU' + height), not under the plain UTXO undo key (b'U' + ...).
#      Pre-fix read_ref_loc_undo_info() read with undo_key(height) so the b'RU'
#      undo info written by flush_ref_loc_undo_infos() was never found on reorg.
#   2. block_processor diff_pos must return len(hashes1) (the parameter), not a
#      nonexistent outer 'hashes' name, when two hash lists fully agree.

from electrumx.server.db import DB
from electrumx.lib.util import pack_be_uint32


class FakeKVStore(dict):
    '''Minimal stand-in for a leveldb/rocksdb handle: get() returns None on miss.'''

    def get(self, key, default=None):
        return dict.get(self, key, default)

    def iterator(self, prefix=b''):
        '''Yield (key, value) in lexicographic byte order for keys matching the
        prefix -- the ordering real RocksDB/LevelDB iterators guarantee, which
        clear_excess_undo_info relies on to break at the first in-window height.'''
        for key in sorted(self):
            if key.startswith(prefix):
                yield key, self[key]

    def write_batch(self):
        store = self

        class _Batch:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def delete(self_inner, key):
                store.pop(key, None)

        return _Batch()


def _bare_db():
    '''A DB instance with only the bits these unit tests touch.

    DB.__init__ chdirs and opens real storage, so build an uninitialised
    instance and wire in an in-memory utxo_db. The key-builder and
    read/flush undo methods are pure functions of (height, utxo_db).
    '''
    db = DB.__new__(DB)
    db.utxo_db = FakeKVStore()
    return db


def test_ref_loc_undo_key_uses_RU_prefix():
    db = _bare_db()
    # Writer key and reader key must agree, and use the b'RU' prefix.
    assert db.ref_loc_undo_key(123) == b'RU' + pack_be_uint32(123)
    # And must NOT collide with the plain UTXO undo key.
    assert db.ref_loc_undo_key(123) != db.undo_key(123)


def test_ref_loc_undo_roundtrip():
    '''Write ref-loc undo info via the writer, read it back via the patched
    reader. Pre-fix the reader looked under b'U' + height and got None.'''
    db = _bare_db()
    height = 4567

    # Two synthetic (ref(36) + loc(32)) = 68-byte undo entries for this height.
    ref_a = b'\xaa' * 36
    loc_a = b'\x11' * 32
    ref_b = b'\xbb' * 36
    loc_b = b'\x22' * 32
    undo_info = [ref_a + loc_a, ref_b + loc_b]

    def batch_put(key, value):
        db.utxo_db[key] = value

    db.flush_ref_loc_undo_infos(batch_put, [(undo_info, height)])

    # The data must live under the b'RU' key...
    assert db.utxo_db.get(db.ref_loc_undo_key(height)) is not None
    # ...and the plain UTXO undo key must be empty for this height.
    assert db.utxo_db.get(db.undo_key(height)) is None

    # The patched reader must find it.
    read_back = db.read_ref_loc_undo_info(height)
    assert read_back == b''.join(undo_info)

    # And it must round-trip in 68-byte chunks (ref + loc) intact.
    assert read_back[0:36] == ref_a
    assert read_back[36:68] == loc_a
    assert read_back[68:104] == ref_b
    assert read_back[104:136] == loc_b


def test_ref_loc_undo_missing_height_returns_none():
    db = _bare_db()
    assert db.read_ref_loc_undo_info(999999) is None


# --- clear_excess_undo_info must GC the b'RU' ref-loc undo keys too ------------
# Pre-fix it only swept b'U' keys; b'RU' sorts under b'R' so ref-loc undo grew
# unbounded forever. The GC must apply the SAME keep-window to both prefixes
# without touching undo still needed for an in-window reorg.

class _Env:
    def __init__(self, reorg_limit):
        self.reorg_limit = reorg_limit


def test_clear_excess_undo_info_gcs_RU_keys(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the block-file glob runs in cwd
    db = _bare_db()
    db.env = _Env(reorg_limit=10)
    db.db_height = 1000
    import logging
    db.logger = logging.getLogger('test')

    # keep-window: min_undo_height = 1000 - 10 + 1 = 991. Heights < 991 are stale.
    stale = [900, 950, 990]
    keep = [991, 995, 1000]
    for h in stale + keep:
        db.utxo_db[db.undo_key(h)] = b'u-undo'
        db.utxo_db[db.ref_loc_undo_key(h)] = b'ru-undo'

    db.clear_excess_undo_info()

    # Stale entries gone for BOTH prefixes...
    for h in stale:
        assert db.utxo_db.get(db.undo_key(h)) is None
        assert db.utxo_db.get(db.ref_loc_undo_key(h)) is None, (
            f'stale b"RU" undo at height {h} was NOT garbage-collected')
    # ...and in-window entries preserved for BOTH prefixes (needed for reorg).
    for h in keep:
        assert db.utxo_db.get(db.undo_key(h)) == b'u-undo'
        assert db.utxo_db.get(db.ref_loc_undo_key(h)) == b'ru-undo'


def test_clear_excess_undo_info_source_sweeps_RU_prefix():
    '''Source-level guard so the b'RU' sweep cannot silently regress out.'''
    import inspect
    src = inspect.getsource(DB.clear_excess_undo_info)
    assert "prefix=b'RU'" in src or 'prefix=b"RU"' in src


def test_db_version_bumped_forces_resync():
    '''The DB version was bumped so a patched node force-resyncs on first boot
    (old indexes may carry latent reorg corruption from the wrong undo key).
    Older versions must be dropped from DB_VERSIONS.'''
    assert max(DB.DB_VERSIONS) >= 9
    # Pre-9 versions must NOT be accepted, so an existing index triggers a
    # rebuild instead of being trusted.
    assert 8 not in DB.DB_VERSIONS
    assert 6 not in DB.DB_VERSIONS


# --- diff_pos must reference hashes1, not a stray 'hashes' ---------------------

def _diff_pos(hashes1, hashes2):
    '''Copy of the inner diff_pos from BlockProcessor._calc_reorg_range so we
    can unit-test the fix without spinning up the whole processor. Must stay in
    sync with block_processor.py; the regression is the final return value.'''
    for n, (hash1, hash2) in enumerate(zip(hashes1, hashes2)):
        if hash1 != hash2:
            return n
    return len(hashes1)


def test_diff_pos_all_match_returns_length():
    hashes = [b'a', b'b', b'c']
    # Pre-fix this raised NameError('hashes') because the local was hashes1.
    assert _diff_pos(hashes, list(hashes)) == 3


def test_diff_pos_first_difference():
    assert _diff_pos([b'a', b'b', b'c'], [b'a', b'X', b'c']) == 1


def test_block_processor_diff_pos_no_stray_name():
    '''Guard against the original bug regressing in the real source: the inner
    function must not reference a bare 'hashes' free variable.'''
    import inspect
    from electrumx.server import block_processor

    src = inspect.getsource(block_processor.BlockProcessor._calc_reorg_range)
    assert 'return len(hashes1)' in src
    assert 'return len(hashes)\n' not in src
