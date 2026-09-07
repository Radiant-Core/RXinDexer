#!/usr/bin/env python3
"""Delete TRANSFER location hops recorded for protocol plumbing.

WHY
---
``record_ref_hop`` originally recorded a hop for every singleton movement. A dMint mining
contract is spent and re-created by EVERY mint, so each mint produced a row. Measured on mainnet
2026-09-07, mid-rescan:

    hop TRANSFER    18,957,168   50.0%    <- this, and still growing
    mint-history    18,950,268   50.0%    (pre-existing dMint mint log, untouched by this script)
    hop MINT/DEPLOY/MELT  4,862    0.0%
    bare DEPLOY           8,421    0.0%

Every one of the top hop-holders was a dMint contract; a single one held 218,751 rows. That is
not an ownership history and no interface can render it, so the write paths now decline these
(see ``GlyphIndex.is_plumbing_singleton``, which reuses the ``is_companion`` classification the
recency feeds already apply to "WAVE zone contract, dMint mining contract").

The filter only prevents new rows. This removes the ones already written -- by the reindex's live
path across every height, and again by the ref-history rescans.

WHAT IS DELETED
---------------
A GH row is removed only when ALL of these hold:

  * its value carries a LOCATION tail (37 or 48 bytes) -- so a dMint mint's 41-byte amount row
    is never touched, nor a 33-byte DEPLOY/BURN row
  * its event byte is TRANSFER -- MINT and MELT are kept, so a contract's chain keeps both
    endpoints and stays traceable
  * its ref is plumbing per the production predicate, reused here rather than reimplemented

USAGE (read-only unless --apply). Needs the venv Python, which has rocksdb and cbor2:

    /opt/RXinDexer/venv/bin/python3 contrib/prune_plumbing_hops.py /data/rxindexer/utxo/
    ... --apply     perform the deletion

Run with the indexer STOPPED. Read-only mode is safe against a live DB; --apply is not.
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GH = b'GH'
GT = b'GT'
TRANSFER = 2
LOCATION_TAIL_LENS = (37, 48)       # event+txid+vout [+ holder]


class _Logger:
    def info(self, *a, **k):
        pass

    warning = exception = debug = info


def _build_predicate(db):
    """A minimal GlyphIndex wired to this DB, so the real is_plumbing_singleton is used rather
    than a second copy of the rule that could drift from it."""
    from electrumx.server.glyph_index import GlyphIndex, GlyphTokenInfo, pack_token_key

    cache = {}

    def get_token(ref):
        if ref in cache:
            return cache[ref]
        raw = db.get(pack_token_key(ref))
        token = None
        if raw:
            try:
                token = GlyphTokenInfo.from_bytes(raw)
            except Exception:
                token = None
        cache[ref] = token
        return token

    idx = object.__new__(GlyphIndex)
    idx.db = SimpleNamespace(utxo_db=SimpleNamespace(get=db.get))
    idx.logger = _Logger()
    idx._plumbing_cache = {}
    idx.contract_to_token_cache = {}
    idx.token_cache = {}
    idx.get_token = get_token
    return idx


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 0
    db_path = argv[1]
    apply = '--apply' in argv

    import rocksdb
    db = rocksdb.DB(db_path, rocksdb.Options(create_if_missing=False),
                    read_only=not apply)
    idx = _build_predicate(db)

    scanned = doomed = kept_endpoint = mint_history = bare = 0
    plumbing_refs = set()
    batch = rocksdb.WriteBatch() if apply else None

    it = db.iteritems()
    it.seek(GH)
    for key, value in it:
        if not key.startswith(GH):
            break
        scanned += 1
        if scanned % 5_000_000 == 0:
            print(f'  scanned {scanned:,}, {doomed:,} to delete', flush=True)
        if not value:
            continue
        if len(value) not in LOCATION_TAIL_LENS:
            if len(value) == 41:
                mint_history += 1
            else:
                bare += 1
            continue                      # not a location hop at all
        ref = key[len(GH):len(GH) + 36]
        if len(ref) != 36:
            continue
        if not idx.is_plumbing_singleton(ref):
            continue
        plumbing_refs.add(ref)
        if value[0] != TRANSFER:
            kept_endpoint += 1            # MINT / MELT: the chain's endpoints stay
            continue
        doomed += 1
        if apply:
            batch.delete(key)
            if doomed % 100_000 == 0:
                db.write(batch)
                batch = rocksdb.WriteBatch()

    if apply and batch is not None:
        db.write(batch)

    print()
    print(f'GH rows scanned              : {scanned:,}')
    print(f'  dMint mint-history rows    : {mint_history:,}  (untouched)')
    print(f'  bare deploy/burn rows      : {bare:,}  (untouched)')
    print(f'plumbing refs seen           : {len(plumbing_refs):,}')
    print(f'  their MINT/MELT endpoints  : {kept_endpoint:,}  (kept)')
    print(f'plumbing TRANSFER hops       : {doomed:,}  '
          f'({100.0 * doomed / scanned:.1f}% of GH)' if scanned else '')
    if not apply:
        print()
        print('Read-only. Re-run with --apply (indexer stopped) to delete them.')
    else:
        print()
        print(f'Deleted {doomed:,} rows. Run a RocksDB compaction to reclaim the space, or let '
              f'background compaction do it.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
