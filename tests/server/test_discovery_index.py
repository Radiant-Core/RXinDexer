"""
v4 discovery indexes (BY_TYPE_RECENT / BY_PROTO / GLOBAL_RECENT).

Covers:
- Newest-first ordering + cursor pagination for get_tokens_by_type(order='recent')
- Legacy order='ref' still works
- get_recent_tokens (global, across types)
- get_tokens_by_protocol (facets that are not a primary token_type)
- Reorg: backup() removes the v4 rows written at a height (add/spend symmetry)
- In-place v3 -> v4 backfill migration + schema-version gating
- Curation of the recency feeds: UNKNOWN (type 0) and companion singletons
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
# GLOBAL_RECENT excludes UNKNOWN (type 0) so it matches the per-type feeds.
#
# Kept as a write-path invariant, NOT because of the launch-day report that
# originally motivated it (423e020): that report was retracted — the reporting
# client had a parameter-parsing bug and was querying token_type=0, i.e. the
# UNKNOWN bucket itself, so the type-0 rows and apparent staleness it saw were
# that bucket's honest contents. The real all-types feed measured clean before
# and after. What survives is the code fact these tests pin: _discovery_rows
# did unconditionally index type-0 records into GLOBAL_RECENT, so the feed
# could surface half-hydrated rows (no name, no protocols) even though it
# happened not to at the time.
# --------------------------------------------------------------------------- #

class TestGlobalRecentExcludesUnknown:
    def _seed(self):
        idx, db = _make_index()
        real = _token("REAL", "e1" * 32, 200, GlyphTokenType.NFT,
                      [GlyphProtocol.GLYPH_NFT])
        # Partial WAVE-name owner row: WAVE protocol without NFT -> UNKNOWN type.
        wave = _token("altapi.rxd", "e2" * 32, 210, GlyphTokenType.UNKNOWN,
                      [GlyphProtocol.GLYPH_WAVE])
        # Empty/malformed reveal: no protocols, no name -> UNKNOWN type.
        junk = _token(None, "e3" * 32, 220, GlyphTokenType.UNKNOWN, [])
        newer = _token("NEWER", "e4" * 32, 230, GlyphTokenType.NFT,
                       [GlyphProtocol.GLYPH_NFT])
        _deploy(idx, db, real, wave, junk, newer)
        return idx, db

    def test_write_path_omits_unknown_from_global(self):
        idx, db = self._seed()
        # Only the two typed tokens get a GLOBAL_RECENT (GQ) row.
        gq = [k for k, _ in db.utxo_db.iterator(prefix=GlyphDBKeys.GLOBAL_RECENT)]
        assert len(gq) == 2
        # ...but every token still has a BY_TYPE_RECENT (GZ) row (type-0 bucket
        # for the UNKNOWNs) — nothing is dropped from the index entirely.
        gz = [k for k, _ in db.utxo_db.iterator(prefix=GlyphDBKeys.BY_TYPE_RECENT)]
        assert len(gz) == 4

    def test_global_feed_is_typed_only_newest_first(self):
        idx, _ = self._seed()
        r = idx.get_recent_tokens(limit=10)
        assert _names(r) == ["NEWER", "REAL"]  # 230, 200 — UNKNOWNs excluded
        assert r["next_cursor"] is None

    def test_unknown_bucket_still_queryable_by_type(self):
        idx, _ = self._seed()
        u = idx.get_tokens_by_type(GlyphTokenType.UNKNOWN, order="recent")
        assert _names(u) == [None, "altapi.rxd"]  # 220, 210

    def test_legacy_global_rows_filtered_on_read(self):
        """A DB backfilled by an earlier v4 build still holds UNKNOWN GQ rows;
        the read predicate hides them without a re-migration, and paginated
        cursors stay newest-first with no duplicates across skipped rows."""
        idx, db = self._seed()
        # Simulate the pre-fix backfill: force the UNKNOWN rows back into GQ.
        from electrumx.server.glyph_index import pack_global_recent_key
        for ref_hex, h in (("e2" * 32, 210), ("e3" * 32, 220)):
            key = pack_global_recent_key(h, pack_ref(bytes.fromhex(ref_hex), 0))
            db.utxo_db._store[key] = struct.pack("<B", 0)
        assert len([k for k, _ in
                    db.utxo_db.iterator(prefix=GlyphDBKeys.GLOBAL_RECENT)]) == 4

        # Walk one row at a time; leading/interleaved junk must not stall or dup.
        seen, cur = [], None
        for _ in range(10):
            p = idx.get_recent_tokens(limit=1, cursor=cur)
            seen += _names(p)
            cur = p["next_cursor"]
            if cur is None:
                break
        assert seen == ["NEWER", "REAL"]


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


# --------------------------------------------------------------------------- #
# Companion singletons: a mint's extra singleton outputs (WAVE zone/mutable
# contract at vout 1, dMint mining contracts at vout 1..N) are Phase-1
# registered as bare NFT/[2] records with no name. They are the parent's
# plumbing, not separately discoverable assets, so they are kept out of BOTH
# recency feeds. Verified on mainnet: every WAVE registration contributed one,
# ~47% of the all-types feed.
# --------------------------------------------------------------------------- #

class _FakeOutput:
    def __init__(self, script):
        self.pk_script = script


class _FakeTx:
    """Minimal tx whose outputs carry OP_PUSHINPUTREFSINGLETON (0xd8) pushes."""
    def __init__(self, *refs):
        self.outputs = [_FakeOutput(b"\xd8" + r) for r in refs]


def _token_at(name, txid_hex, vout, height, token_type, protocols,
              revealed=True):
    """Like _token but at an explicit vout, and optionally 'bare' (no metadata),
    which is how Phase 1 leaves a singleton the reveal never enriched."""
    t = GlyphTokenInfo()
    t.ref = pack_ref(bytes.fromhex(txid_hex), vout)
    t.name = name
    t.token_type = token_type
    t.protocols = list(protocols)
    t.deploy_height = height
    t.deploy_txid = bytes.fromhex(txid_hex)
    t.metadata_hash = bytes(32) if revealed else b""
    return t


WAVE_TXID = "76a32cb6" * 8


def _wave_registration(height=447893):
    """The real on-chain shape: name singleton at vout 0, zone contract at
    vout 1 (mainnet example 76a32cb6_0 = '440000.rxd' / 76a32cb6_1 = nameless)."""
    name = _token_at("440000.rxd", WAVE_TXID, 0, height, GlyphTokenType.WAVE,
                     [GlyphProtocol.GLYPH_NFT, GlyphProtocol.GLYPH_MUT,
                      GlyphProtocol.GLYPH_WAVE])
    zone = _token_at(None, WAVE_TXID, 1, height, GlyphTokenType.NFT,
                     [GlyphProtocol.GLYPH_NFT], revealed=False)
    return name, zone


class TestCompanionSingletonMarking:
    def test_reveal_marks_sibling_singleton(self):
        idx, _db = _make_index()
        name, zone = _wave_registration()
        for t in (name, zone):
            idx.token_cache[t.ref] = t
            idx.token_height[t.ref] = t.deploy_height

        marked = idx._mark_companion_singletons(
            name.ref, _FakeTx(name.ref, zone.ref), name.deploy_height)

        assert marked == 1
        assert zone.is_companion is True
        # Back-link is the canonical txid_vout display form, directly
        # comparable to the 'ref' field of the parent's API row.
        assert zone.parent_ref == idx._token_to_dict(name)["ref"]
        assert name.is_companion is False  # never demotes itself

    def test_named_sibling_is_not_demoted(self):
        """A sibling that was revealed in its own right is a real asset."""
        idx, _db = _make_index()
        name, _zone = _wave_registration()
        sibling = _token_at("REAL", WAVE_TXID, 1, 447893, GlyphTokenType.NFT,
                            [GlyphProtocol.GLYPH_NFT])
        for t in (name, sibling):
            idx.token_cache[t.ref] = t
            idx.token_height[t.ref] = t.deploy_height

        assert idx._mark_companion_singletons(
            name.ref, _FakeTx(name.ref, sibling.ref), 447893) == 0
        assert sibling.is_companion is False

    def test_marking_is_idempotent(self):
        idx, _db = _make_index()
        name, zone = _wave_registration()
        for t in (name, zone):
            idx.token_cache[t.ref] = t
            idx.token_height[t.ref] = t.deploy_height
        tx = _FakeTx(name.ref, zone.ref)

        assert idx._mark_companion_singletons(name.ref, tx, 447893) == 1
        assert idx._mark_companion_singletons(name.ref, tx, 447893) == 0


class TestCompanionExcludedFromRecencyFeeds:
    def _seed(self, mark=True):
        idx, db = _make_index()
        name, zone = _wave_registration()
        other = _token_at("PLAIN", "aa" * 32, 0, 447800, GlyphTokenType.NFT,
                          [GlyphProtocol.GLYPH_NFT])
        if mark:
            zone.is_companion = True
            zone.parent_ref = idx._token_to_dict(name)["ref"]
        _deploy(idx, db, name, zone, other)
        return idx, db, name, zone

    def test_write_path_omits_both_recency_rows(self):
        _idx, db, _name, _zone = self._seed()
        store = db.utxo_db._store
        gq = [k for k in store if k.startswith(GlyphDBKeys.GLOBAL_RECENT)]
        gz2 = [k for k in store if k.startswith(
            GlyphDBKeys.BY_TYPE_RECENT + struct.pack("<B", GlyphTokenType.NFT))]
        # GLOBAL_RECENT: the WAVE name + the standalone NFT, not the zone.
        assert len(gq) == 2
        # BY_TYPE_RECENT type 2: only the standalone NFT.
        assert len(gz2) == 1
        # ...but the protocol facet row survives, so nothing vanishes entirely.
        gp = [k for k in store if k.startswith(
            GlyphDBKeys.BY_PROTO + struct.pack("<B", GlyphProtocol.GLYPH_NFT))]
        assert len(gp) == 3

    def test_global_feed_has_no_nameless_companion(self):
        idx, _db, _name, _zone = self._seed()
        assert _names(idx.get_recent_tokens(limit=10)) == ["440000.rxd", "PLAIN"]

    def test_by_type_recent_excludes_companion(self):
        idx, _db, _name, _zone = self._seed()
        r = idx.get_tokens_by_type(GlyphTokenType.NFT, order="recent")
        assert _names(r) == ["PLAIN"]

    def test_legacy_ref_order_still_enumerates_companion(self):
        """order='ref' stays a complete enumeration — the row is curated out of
        the *feeds*, not hidden from the index."""
        idx, _db, _name, zone = self._seed()
        r = idx.get_tokens_by_type(GlyphTokenType.NFT, order="ref")
        assert len(r["tokens"]) == 2
        assert any(t["is_companion"] for t in r["tokens"])
        assert zone.ref.hex() in [t["ref_hex"] for t in r["tokens"]]

    def test_companion_still_reachable_by_ref(self):
        idx, _db, name, zone = self._seed()
        got = idx.get_token(zone.ref)
        assert got is not None and got.is_companion
        assert got.parent_ref == idx._token_to_dict(name)["ref"]


class TestCompanionLegacyRecordsFilteredOnRead:
    """A DB indexed before the flag existed holds unmarked companion records
    with live GQ/GZ rows. The read predicate re-derives the property so those
    backends serve clean feeds with no reindex."""

    def _seed_legacy(self):
        # mark=False => flushed exactly as a pre-fix build would have.
        idx, db, name, zone = TestCompanionExcludedFromRecencyFeeds()._seed(
            mark=False)
        return idx, db, name, zone

    def test_legacy_rows_are_present_in_the_index(self):
        _idx, db, _name, _zone = self._seed_legacy()
        gq = [k for k in db.utxo_db._store
              if k.startswith(GlyphDBKeys.GLOBAL_RECENT)]
        assert len(gq) == 3  # the unmarked companion IS indexed

    def test_read_predicate_hides_unmarked_companion(self):
        idx, _db, _name, zone = self._seed_legacy()
        assert zone.is_companion is False  # nothing rewrote the record
        assert _names(idx.get_recent_tokens(limit=10)) == ["440000.rxd", "PLAIN"]
        assert _names(idx.get_tokens_by_type(
            GlyphTokenType.NFT, order="recent")) == ["PLAIN"]

    def test_cursor_walk_over_filtered_rows_has_no_dupes(self):
        idx, _db, _name, _zone = self._seed_legacy()
        seen, cur = [], None
        for _ in range(10):
            p = idx.get_recent_tokens(limit=1, cursor=cur)
            seen += _names(p)
            cur = p["next_cursor"]
            if cur is None:
                break
        assert seen == ["440000.rxd", "PLAIN"]


class TestCompanionDerivationDoesNotOverReach:
    """Guards on the legacy derivation: it must only catch plumbing."""

    def test_standalone_bare_nft_at_vout_zero_is_kept(self):
        idx, db = _make_index()
        bare = _token_at(None, "bb" * 32, 0, 447900, GlyphTokenType.NFT,
                         [GlyphProtocol.GLYPH_NFT], revealed=False)
        _deploy(idx, db, bare)
        assert idx._is_companion_singleton(bare) is False
        assert len(idx.get_recent_tokens()["tokens"]) == 1

    def test_bare_nft_with_no_vout_zero_sibling_is_kept(self):
        idx, db = _make_index()
        orphan = _token_at(None, "cc" * 32, 1, 447900, GlyphTokenType.NFT,
                           [GlyphProtocol.GLYPH_NFT], revealed=False)
        _deploy(idx, db, orphan)
        assert idx._is_companion_singleton(orphan) is False

    def test_bare_nft_whose_vout_zero_sibling_is_unnamed_is_kept(self):
        idx, db = _make_index()
        a = _token_at(None, "dd" * 32, 0, 447900, GlyphTokenType.NFT,
                      [GlyphProtocol.GLYPH_NFT], revealed=False)
        b = _token_at(None, "dd" * 32, 1, 447900, GlyphTokenType.NFT,
                      [GlyphProtocol.GLYPH_NFT], revealed=False)
        _deploy(idx, db, a, b)
        assert idx._is_companion_singleton(b) is False

    def test_revealed_sibling_is_kept(self):
        idx, db = _make_index()
        name, _zone = _wave_registration()
        revealed = _token_at(None, WAVE_TXID, 1, 447893, GlyphTokenType.NFT,
                             [GlyphProtocol.GLYPH_NFT], revealed=True)
        _deploy(idx, db, name, revealed)
        assert idx._is_companion_singleton(revealed) is False

    def test_non_nft_sibling_is_kept(self):
        """An FT minted at vout 1 alongside a named token is a real asset."""
        idx, db = _make_index()
        name, _zone = _wave_registration()
        ft = _token_at(None, WAVE_TXID, 1, 447893, GlyphTokenType.FT,
                       [GlyphProtocol.GLYPH_FT], revealed=False)
        _deploy(idx, db, name, ft)
        assert idx._is_companion_singleton(ft) is False


class TestCompanionWriteDeleteSymmetry:
    """_discovery_rows must stay a pure function of (ref, token) so write,
    re-write dedup, delete and the v3->v4 migration derive the same key set."""

    def test_migration_reproduces_the_same_exclusions(self):
        idx, db = _make_index()
        name, zone = _wave_registration()
        zone.is_companion = True
        # Seed GT rows only, as a v3 DB would have them.
        store = db.utxo_db._store
        for t in (name, zone):
            store[pack_token_key(t.ref)] = t.to_bytes()
        assert not any(k.startswith(GlyphDBKeys.GLOBAL_RECENT) for k in store)

        idx._migrate_3_to_4()

        gq = [k for k in store if k.startswith(GlyphDBKeys.GLOBAL_RECENT)]
        assert len(gq) == 1  # the companion is not backfilled into the feed
        assert _names(idx.get_recent_tokens()) == ["440000.rxd"]

    def test_delete_removes_exactly_what_write_created(self):
        idx, db = _make_index()
        name, zone = _wave_registration()
        zone.is_companion = True
        _deploy(idx, db, name, zone)
        store = db.utxo_db._store
        before = set(store)

        with db.utxo_db.write_batch() as batch:
            for t in (name, zone):
                idx._delete_discovery_rows(batch, t.ref, t, t.deploy_height)

        # Every discovery row is gone; nothing orphaned, nothing left behind.
        for pfx in (GlyphDBKeys.GLOBAL_RECENT, GlyphDBKeys.BY_TYPE_RECENT,
                    GlyphDBKeys.BY_PROTO):
            assert not any(k.startswith(pfx) for k in store)
        assert before - set(store)  # it actually deleted something

    def test_is_companion_survives_serialisation_round_trip(self):
        _name, zone = _wave_registration()
        zone.is_companion = True
        zone.parent_ref = "abc_0"
        back = GlyphTokenInfo.from_bytes(zone.to_bytes())
        assert back.is_companion is True
        assert back.parent_ref == "abc_0"

    def test_absent_flag_defaults_false(self):
        _name, zone = _wave_registration()
        back = GlyphTokenInfo.from_bytes(zone.to_bytes())
        assert back.is_companion is False
