#!/usr/bin/env python3
"""Restore DEPLOY event labels that a ref-history rescan overwrote with MINT.

WHY THIS EXISTS
---------------
A reveal writes two GH rows at one key (GH + ref + height + tx_idx): the block processor's MINT
hop (event + txid + vout + holder) and process_tx's DEPLOY row (event + txid). Same ref, same
height, same transaction index, so they collide.

``GlyphIndex.merge_history_values`` reconciles them -- the DEPLOY event byte is kept and the hop's
location tail is grafted on -- but its first version decided which side was which by ROW LENGTH.
That works exactly once. After a first rescan the row is no longer bare: it is full length and
still carries DEPLOY. A second rescan then sees two full-length rows, falls through to "later
write wins", and replaces the deploy label with `mint`. Observed on mainnet 2026-09-07, where
0ca8cf8e..._11 came back as `mint` with a correct vout and holder.

merge_history_values now keys on the EVENT TYPE instead, so it is stable across runs. This script
repairs the rows the length-based version already damaged -- the merge cannot, because the bare
DEPLOY row it would have merged with no longer exists.

Nothing is lost beyond the label: the txid, vout and holder in those rows are correct. Only the
event byte reads MINT where it should read DEPLOY.

HOW A DAMAGED ROW IS IDENTIFIED
-------------------------------
Every token's GT record stores ``deploy_height`` and ``deploy_txid``, which pin the reveal exactly.
A row is repaired only when ALL of these hold:

  * its key is GH + <a ref that has a GT record> + <that token's deploy_height> + <any tx_idx>
  * its value's txid equals that token's deploy_txid
  * its event byte is MINT
  * its tail is a LOCATION tail (4 or 15 bytes), i.e. a row a hop writer produced

The tail test is what keeps a dMint mint's own row safe: that carries an 8-byte amount, never a
location tail, so it is never touched.

USAGE (read-only unless --apply is given). Needs the venv Python, which has rocksdb and cbor2:

    /opt/RXinDexer/venv/bin/python3 contrib/repair_deploy_labels.py /data/rxindexer/utxo/
    ... --apply     perform the rewrite
    ... --list      print every affected ref

Run it with the indexer STOPPED. Read-only mode is safe against a live DB (it sees the last
flushed state), but --apply opens the DB for writing and must not race the indexer.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GT = b'GT'          # GT + ref(36)                          -> token record (CBOR)
GH = b'GH'          # GH + ref(36) + height(4 BE) + tx_idx  -> event
DEPLOY, MINT = 0, 1
LOCATION_TAIL_LENS = (4, 15)        # vout, or vout + holder hashX
BASE_LEN = 33                       # event(1) + txid(32)


def display(ref: bytes) -> str:
    return f"{ref[:32][::-1].hex()}_{int.from_bytes(ref[32:36], 'little')}"


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 0
    db_path = argv[1]
    apply = '--apply' in argv
    show = '--list' in argv

    import rocksdb
    from electrumx.server.glyph_index import GlyphTokenInfo

    db = rocksdb.DB(db_path, rocksdb.Options(create_if_missing=False),
                    read_only=not apply)

    tokens = 0
    repairs = []            # (key, old_value, new_value, ref)
    skipped_no_deploy = 0

    it = db.iteritems()
    it.seek(GT)
    for key, value in it:
        if not key.startswith(GT) or len(key) != len(GT) + 36:
            break
        ref = key[len(GT):]
        tokens += 1
        try:
            token = GlyphTokenInfo.from_bytes(value)
        except Exception:
            continue
        height = getattr(token, 'deploy_height', None)
        txid = getattr(token, 'deploy_txid', None)
        if height is None or not txid:
            skipped_no_deploy += 1
            continue

        # Every GH row for this ref at its deploy height. tx_idx is not stored on the token, so
        # the txid in the value is what pins the right row.
        prefix = GH + ref + struct.pack('>I', height)
        hit = db.iteritems()
        hit.seek(prefix)
        for hkey, hvalue in hit:
            if not hkey.startswith(prefix):
                break
            if len(hvalue) < BASE_LEN or hvalue[1:BASE_LEN] != txid:
                continue
            if hvalue[0] != MINT:
                continue
            if len(hvalue) - BASE_LEN not in LOCATION_TAIL_LENS:
                continue        # a dMint mint's amount row, or an unknown layout: leave alone
            repairs.append((hkey, hvalue, bytes([DEPLOY]) + hvalue[1:], ref))

    print(f'token records (GT)        : {tokens:,}')
    if skipped_no_deploy:
        print(f'  without a deploy record : {skipped_no_deploy:,}')
    print(f'deploy rows reading MINT  : {len(repairs):,}')

    if show:
        for _k, _old, _new, ref in repairs[:60]:
            print(f'  {display(ref)}')
        if len(repairs) > 60:
            print(f'  ... and {len(repairs) - 60:,} more')

    if not repairs:
        print('Nothing to repair.')
        return 0
    if not apply:
        print()
        print('Read-only. Re-run with --apply (indexer stopped) to rewrite the event byte.')
        return 0

    batch = rocksdb.WriteBatch()
    for i, (hkey, _old, new, _ref) in enumerate(repairs, 1):
        batch.put(hkey, new)
        if i % 20000 == 0:
            db.write(batch)
            batch = rocksdb.WriteBatch()
            print(f'  repaired {i:,}/{len(repairs):,}')
    db.write(batch)
    print(f'Repaired {len(repairs):,} rows: event byte MINT -> DEPLOY, tails untouched.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
