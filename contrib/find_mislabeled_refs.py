#!/usr/bin/env python3
"""SUPERSEDED — kept only for historical reference. Do not trust its output.

This detected the batch-reveal mislabel by finding GT rows standing on a GC (contract) outpoint.
That premise died with two fixes:

  1. The ref fix put co-revealed NFTs on their own outpoint, so those rows are now CORRECT.
  2. The sibling-singleton fix stopped GC recording a co-revealed NFT as a mining contract.

Run against a DB built with both fixes it reports the correct state as broken: on the 2026-09-05
resync it flagged 70 properly-placed NFTs (at :11, :2, :16, :21, :33 ...) as mislabelled, because
GC still held the pre-fix contract sets. After a reindex with both fixes it should report zero —
and if it does not, the thing to suspect is GC, not the token rows.

Original description follows.

Find token rows that sit on a dMint contract outpoint, and separate the two populations.

Before the ref fix, a reveal's envelopes were paired to minted singletons positionally, so on a
batch reveal (several envelope-carrying inputs in one tx) a payload could be written under a
mining contract's outpoint instead of the outpoint its own input spent. See
tests/test_glyph_batch_reveal_ref.py for the worked mainnet case.

A GT (token) row whose ref is also a GC key (contract_ref -> token_ref) is standing on a contract
outpoint. But that alone over-counts, because the indexer also creates a BARE row for every minted
singleton. The two are told apart by metadata_hash:

  PAYLOAD-CARRYING  metadata_hash set   -> a real reveal payload on the wrong outpoint. THE BUG.
  BARE              metadata_hash empty -> an auto-created row for a minted singleton. Not this bug.

Usage (read-only; safe against a live DB, sees the last flushed state). Needs the venv Python,
which has rocksdb and cbor2:

    /opt/RXinDexer/venv/bin/python3 contrib/find_mislabeled_refs.py /data/rxindexer/utxo/
    ... --list          print every affected ref
    ... --repair-list   print ONLY payload-carrying refs, one per line, for the repair script
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GT = b'GT'          # GT + ref(36)          -> token record (CBOR)
GC = b'GC'          # GC + contract_ref(36) -> token_ref(36)


def display(ref: bytes) -> str:
    return f"{ref[:32][::-1].hex()}_{int.from_bytes(ref[32:36], 'little')}"


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 0
    db_path = argv[1]
    show = '--list' in argv
    repair_list = '--repair-list' in argv

    import rocksdb
    from electrumx.server.glyph_index import GlyphTokenInfo

    db = rocksdb.DB(db_path, rocksdb.Options(create_if_missing=False), read_only=True)

    # Every known contract outpoint -> the deploy it belongs to.
    contracts = {}
    it = db.iteritems()
    it.seek(GC)
    for key, value in it:
        if not key.startswith(GC) or len(key) != len(GC) + 36:
            break
        contracts[key[len(GC):]] = value

    payload, bare, undecodable = [], [], []
    it = db.iteritems()
    it.seek(GT)
    tokens = 0
    for key, value in it:
        if not key.startswith(GT) or len(key) != len(GT) + 36:
            break
        ref = key[len(GT):]
        tokens += 1
        if ref not in contracts:
            continue
        try:
            token = GlyphTokenInfo.from_bytes(value)
        except Exception:
            undecodable.append((ref, contracts[ref]))
            continue
        (payload if token.metadata_hash else bare).append((ref, contracts[ref]))

    if repair_list:
        for ref, _deploy in payload:
            print(display(ref))
        return 0

    print(f'contract outpoints (GC rows) : {len(contracts):,}')
    print(f'token rows (GT)              : {tokens:,}')
    print()
    print(f'token rows on a contract outpoint : {len(payload) + len(bare) + len(undecodable):,}')
    print(f'  PAYLOAD-CARRYING (the bug)      : {len(payload):,}   <-- repair these')
    print(f'  BARE (auto-created, not the bug): {len(bare):,}')
    if undecodable:
        print(f'  UNDECODABLE                     : {len(undecodable):,}')
    print()

    if payload:
        by_deploy = defaultdict(list)
        for ref, deploy in payload:
            by_deploy[deploy].append(ref)
        print(f'payload rows span {len(by_deploy):,} deploy transaction(s):')
        for deploy, refs in sorted(by_deploy.items(), key=lambda kv: -len(kv[1])):
            print(f'  {display(deploy)}  ->  {len(refs)} mislabelled: '
                  f'{", ".join(display(r).split("_")[1] for r in refs)}')
    else:
        print('No payload-carrying rows on contract outpoints — nothing to repair.')

    if show and bare:
        print()
        print('bare rows (informational; a separate matter from this bug):')
        for ref, deploy in bare[:40]:
            print(f'  {display(ref)}   on contract of {display(deploy)}')
        if len(bare) > 40:
            print(f'  … and {len(bare) - 40:,} more')

    print()
    print('NOTE: lower bound. This only sees mislabels that landed on an outpoint GC happens to')
    print('record. A payload written to a singleton that is not in GC is not detected here.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
