"""
v4 discovery indexes (BY_TYPE_RECENT / BY_PROTO / GLOBAL_RECENT).

Covers:
- Newest-first ordering + cursor pagination for get_tokens_by_type(order='recent')
- Legacy order='ref' still works
- get_recent_tokens (global, across types)
- get_tokens_by_protocol (facets that are not a primary token_type)
- Reorg: backup() removes the v4 rows written at a height (add/spend symmetry)
- In-place v3 -> v4 backfill migration + schema-version gating
"""

import contextlib
import struct

import pytest

try:
    import cbor2  # noqa: F401
    HAS_CBOR = True
except ImportError:
    HAS_CBOR = False

from electrumx.lib.glyph import GlyphProtocol, GlyphTokenType
from electrumx.server.glyph_index import (
    GlyphIndex,
    GlyphTokenInfo,
    GlyphDBKeys,
    CURRENT_SCHEMA_VERSION,
    pack_ref,
    pack_token_key,
)


# --------------------------------------------------------------------------- #
# Fakes (sorted+seek iteration and write_batch, like RocksDB)
# --------------------------------------------------------------------------- #

class _FakeBatch:
    def __init__(self, store):
        self._store = store

    def put(self, key, value):
        self._store[key] = value

    def delete(self, key):
        self._store.pop(key, None)


class _FakeUtxoDB:
    def __init__(self):
        self._store = {}

    def get(self, key):
        return self._store.get(key)

    def put(self, key, value):
        self._store[key] = value

    def iterator(self, prefix=b"", reverse=False, include_value=True, seek=None):
        items = [(k, v) for k, v in self._store.items() if k.startswith(prefix)]
        items.sort(key=lambda kv: kv[0], reverse=reverse)
        if seek:
            items = [(k, v) for k, v in items if k >= seek]
        if include_value:
            return iter(items)
        return iter([k for k, _v in items])

    @contextlib.contextmanager
    def write_batch(self):
        yield _FakeBatch(self._store)


class _FakeDB:
    def __init__(self):
        self.utxo_db = _FakeUtxoDB()
        self.db_height = 1000


class _FakeEnv:
    glyph_index = True
    reorg_limit = 0


def _make_index():
    db = _FakeDB()
    idx = GlyphIndex(db, _FakeEnv())
    return idx, db


def _token(name, ref_hex, height, token_type, protocols):
    t = GlyphTokenInfo()
    t.ref = pack_ref(bytes.fromhex(ref_hex), 0)
    t.name = name
    t.token_type = token_type
    t.protocols = list(protocols)
    t.deploy_height = height
    t.deploy_txid = bytes(32)
    t.metadata_hash = bytes(32)
    return t


def _deploy(idx, db, *tokens, height=None):
    """Flush tokens so all secondary index rows exist (as in production)."""
    for t in tokens:
        idx.token_cache[t.ref] = t
        idx.token_height[t.ref] = height if height is not None else (t.deploy_height or 0)
    idx.flush(_FakeBatch(db.utxo_db._store))


def _names(result):
    return [t["name"] for t in result["tokens"]]


pytestmark = pytest.mark.skipif(not HAS_CBOR, reason="cbor2 required")


# --------------------------------------------------------------------------- #
# Recency-ordered by-type
# --------------------------------------------------------------------------- #

class TestByTypeRecent:
    def _seed_nfts(self):
        idx, db = _make_index()
        toks = [
            _token(f"N{i}", f"{i:02x}" * 32, height=100 + i,
                   token_type=GlyphTokenType.NFT,
                   protocols=[GlyphProtocol.GLYPH_NFT])
            for i in range(5)
        ]
        _deploy(idx, db, *toks)
        return idx, db

    def test_recent_is_newest_first(self):
        idx, _ = self._seed_nfts()
        r = idx.get_tokens_by_type(GlyphTokenType.NFT, limit=10, order="recent")
        assert _names(r) == ["N4", "N3", "N2", "N1", "N0"]
        assert r["next_cursor"] is None

    def test_recent_cursor_pagination_no_overlap(self):
        idx, _ = self._seed_nfts()
        p1 = idx.get_tokens_by_type(GlyphTokenType.NFT, limit=2, order="recent")
        assert _names(p1) == ["N4", "N3"]
        assert p1["next_cursor"]
        p2 = idx.get_tokens_by_type(GlyphTokenType.NFT, limit=2, order="recent",
                                    cursor=p1["next_cursor"])
        assert _names(p2) == ["N2", "N1"]
        p3 = idx.get_tokens_by_type(GlyphTokenType.NFT, limit=2, order="recent",
                                    cursor=p2["next_cursor"])
        assert _names(p3) == ["N0"]
        assert p3["next_cursor"] is None

    def test_legacy_ref_order_still_works(self):
        idx, _ = self._seed_nfts()
        r = idx.get_tokens_by_type(GlyphTokenType.NFT, limit=10)  # default order='ref'
        # ref-ordered: stable by ref bytes, i.e. ascending ref_hex here
        assert _names(r) == ["N0", "N1", "N2", "N3", "N4"]

    def test_type_isolation(self):
        idx, db = self._seed_nfts()
        ft = _token("FT0", "f0" * 32, height=200, token_type=GlyphTokenType.FT,
                    protocols=[GlyphProtocol.GLYPH_FT])
        _deploy(idx, db, ft)
        nfts = idx.get_tokens_by_type(GlyphTokenType.NFT, order="recent")
        assert "FT0" not in _names(nfts)
        fts = idx.get_tokens_by_type(GlyphTokenType.FT, order="recent")
        assert _names(fts) == ["FT0"]


# --------------------------------------------------------------------------- #
# Global recent + by-protocol
# --------------------------------------------------------------------------- #

class TestGlobalRecentAndProto:
    def _seed_mixed(self):
        idx, db = _make_index()
        ft = _token("FT", "a1" * 32, 100, GlyphTokenType.FT,
                    [GlyphProtocol.GLYPH_FT])
        nft = _token("NFT", "a2" * 32, 110, GlyphTokenType.NFT,
                     [GlyphProtocol.GLYPH_NFT])
        # A mutable container NFT carries several protocols at once.
        cont = _token("CONT", "a3" * 32, 120, GlyphTokenType.CONTAINER,
                      [GlyphProtocol.GLYPH_NFT, GlyphProtocol.GLYPH_CONTAINER,
                       GlyphProtocol.GLYPH_MUT])
        _deploy(idx, db, ft, nft, cont)
        return idx, db

    def test_global_recent_across_types(self):
        idx, _ = self._seed_mixed()
        r = idx.get_recent_tokens(limit=10)
        assert _names(r) == ["CONT", "NFT", "FT"]  # 120, 110, 100

    def test_proto_container_facet(self):
        idx, _ = self._seed_mixed()
        r = idx.get_tokens_by_protocol(GlyphProtocol.GLYPH_CONTAINER)
        assert _names(r) == ["CONT"]

    def test_proto_mutable_facet(self):
        idx, _ = self._seed_mixed()
        r = idx.get_tokens_by_protocol(GlyphProtocol.GLYPH_MUT)
        assert _names(r) == ["CONT"]

    def test_proto_nft_facet_lists_all_nft_carriers(self):
        idx, _ = self._seed_mixed()
        r = idx.get_tokens_by_protocol(GlyphProtocol.GLYPH_NFT)
        # both the plain NFT and the container (which also carries NFT), newest first
        assert _names(r) == ["CONT", "NFT"]


# --------------------------------------------------------------------------- #
# Re-write (mutable metadata UPDATE) must not duplicate a token
# --------------------------------------------------------------------------- #

class TestReWriteDedup:
    def test_height_change_moves_not_duplicates(self):
        idx, db = _make_index()
        ref_hex = "ab" * 32
        # Genesis at height 100.
        t1 = _token("T", ref_hex, 100, GlyphTokenType.NFT, [GlyphProtocol.GLYPH_NFT])
        _deploy(idx, db, t1, height=100)
        # Same ref re-revealed (mutable UPDATE) at height 150.
        t2 = _token("T", ref_hex, 150, GlyphTokenType.NFT, [GlyphProtocol.GLYPH_NFT])
        _deploy(idx, db, t2, height=150)

        g = idx.get_recent_tokens(limit=10)
        assert _names(g) == ["T"]  # exactly once, not twice
        # Only one GLOBAL_RECENT row exists for this ref (no orphan at inv(100)).
        rows = [k for k, _ in db.utxo_db.iterator(prefix=GlyphDBKeys.GLOBAL_RECENT)]
        assert len(rows) == 1
        by_type = idx.get_tokens_by_type(GlyphTokenType.NFT, order="recent")
        assert _names(by_type) == ["T"]

    def test_protocol_change_on_update_prunes_stale_facet(self):
        idx, db = _make_index()
        ref_hex = "cd" * 32
        t1 = _token("T", ref_hex, 100, GlyphTokenType.NFT, [GlyphProtocol.GLYPH_NFT])
        _deploy(idx, db, t1, height=100)
        # Update adds ENCRYPTED (and keeps NFT), same ref, new height.
        t2 = _token("T", ref_hex, 150, GlyphTokenType.NFT,
                    [GlyphProtocol.GLYPH_NFT, GlyphProtocol.GLYPH_ENCRYPTED])
        t2.is_encrypted = True
        _deploy(idx, db, t2, height=150)

        # Now appears in the encrypted facet, exactly once in NFT facet.
        assert _names(idx.get_tokens_by_protocol(GlyphProtocol.GLYPH_ENCRYPTED)) == ["T"]
        assert _names(idx.get_tokens_by_protocol(GlyphProtocol.GLYPH_NFT)) == ["T"]


# --------------------------------------------------------------------------- #
# Reorg: backup() unwinds the v4 rows
# --------------------------------------------------------------------------- #

class TestReorgUndo:
    def test_backup_removes_v4_rows(self):
        idx, db = _make_index()
        store = db.utxo_db._store
        height = 500
        cont = _token("CONT", "cc" * 32, height, GlyphTokenType.CONTAINER,
                      [GlyphProtocol.GLYPH_NFT, GlyphProtocol.GLYPH_CONTAINER])
        _deploy(idx, db, cont, height=height)

        # v4 rows are present after flush.
        assert any(k.startswith(GlyphDBKeys.BY_TYPE_RECENT) for k in store)
        assert any(k.startswith(GlyphDBKeys.GLOBAL_RECENT) for k in store)
        assert any(k.startswith(GlyphDBKeys.BY_PROTO) for k in store)

        # Unwind the block.
        idx.backup(_FakeBatch(store), height)

        assert not any(k.startswith(GlyphDBKeys.BY_TYPE_RECENT) for k in store)
        assert not any(k.startswith(GlyphDBKeys.GLOBAL_RECENT) for k in store)
        assert not any(k.startswith(GlyphDBKeys.BY_PROTO) for k in store)
        # And the primary GT row is gone too (nothing left to hydrate).
        assert store.get(pack_token_key(cont.ref)) is None
        assert idx.get_recent_tokens()["tokens"] == []


# --------------------------------------------------------------------------- #
# In-place v3 -> v4 migration
# --------------------------------------------------------------------------- #

class TestMigration:
    def _seed_v3_gt_rows_only(self, idx, db, tokens):
        """Write only GT rows (as a pre-v4 DB would have), no GZ/GP/GQ."""
        store = db.utxo_db._store
        for t in tokens:
            store[pack_token_key(t.ref)] = t.to_bytes()

    def test_migrate_3_to_4_backfills(self):
        idx, db = _make_index()
        toks = [
            _token("FT", "b1" * 32, 100, GlyphTokenType.FT, [GlyphProtocol.GLYPH_FT]),
            _token("ENC", "b2" * 32, 130, GlyphTokenType.NFT,
                   [GlyphProtocol.GLYPH_NFT, GlyphProtocol.GLYPH_ENCRYPTED]),
            _token("NFT", "b3" * 32, 120, GlyphTokenType.NFT, [GlyphProtocol.GLYPH_NFT]),
        ]
        self._seed_v3_gt_rows_only(idx, db, toks)
        store = db.utxo_db._store
        assert not any(k.startswith(GlyphDBKeys.GLOBAL_RECENT) for k in store)

        n = idx._migrate_3_to_4()
        assert n == 3

        # Global newest-first across all seeded tokens.
        assert _names(idx.get_recent_tokens()) == ["ENC", "NFT", "FT"]
        # Facet + type lists now resolve via the backfilled rows.
        assert _names(idx.get_tokens_by_protocol(GlyphProtocol.GLYPH_ENCRYPTED)) == ["ENC"]
        assert _names(idx.get_tokens_by_type(GlyphTokenType.NFT, order="recent")) == ["ENC", "NFT"]
        # list_encrypted_tokens (BY_PROTO seek) works off the backfill too.
        assert _names(idx.list_encrypted_tokens()) == ["ENC"]

    def test_migrate_is_idempotent(self):
        idx, db = _make_index()
        toks = [_token("A", "c1" * 32, 100, GlyphTokenType.FT, [GlyphProtocol.GLYPH_FT])]
        self._seed_v3_gt_rows_only(idx, db, toks)
        assert idx._migrate_3_to_4() == 1
        # Re-running must not duplicate or error.
        assert idx._migrate_3_to_4() == 1
        assert _names(idx.get_recent_tokens()) == ["A"]

    def test_check_schema_version_migrates_v3(self):
        idx, db = _make_index()
        store = db.utxo_db._store
        toks = [_token("A", "d1" * 32, 100, GlyphTokenType.FT, [GlyphProtocol.GLYPH_FT])]
        self._seed_v3_gt_rows_only(idx, db, toks)
        store[GlyphDBKeys.SCHEMA_VERSION] = bytes([3])

        idx._check_schema_version()

        assert store[GlyphDBKeys.SCHEMA_VERSION] == bytes([CURRENT_SCHEMA_VERSION])
        assert _names(idx.get_recent_tokens()) == ["A"]

    def test_fresh_db_stamps_current_version(self):
        idx, db = _make_index()
        store = db.utxo_db._store
        assert GlyphDBKeys.SCHEMA_VERSION not in store
        idx._check_schema_version()
        assert store[GlyphDBKeys.SCHEMA_VERSION] == bytes([CURRENT_SCHEMA_VERSION])

    def test_pre_v3_without_migrator_hard_fails(self):
        idx, db = _make_index()
        db.utxo_db._store[GlyphDBKeys.SCHEMA_VERSION] = bytes([2])
        with pytest.raises(RuntimeError):
            idx._check_schema_version()

    def test_newer_version_hard_fails(self):
        idx, db = _make_index()
        db.utxo_db._store[GlyphDBKeys.SCHEMA_VERSION] = bytes([CURRENT_SCHEMA_VERSION + 1])
        with pytest.raises(RuntimeError):
            idx._check_schema_version()
