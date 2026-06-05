#!/usr/bin/env python3
"""Scan the chain for version-3 transactions that carry an output with >=2
DISTINCT push-input-refs.

Why: the v3 txid is computed from a hash of each output's push-refs (see
electrumx/lib/tx.py calculate_pushrefs_count_and_hash).  A bug ordered those
refs big-endian; Radiant Core orders them little-endian (uint288 / std::set).
The fix only changes the computed txid for outputs that carry >=2 DISTINCT refs
in a v3 tx.  If any such output already exists in indexed history, the indexer
stored a wrong txid for that tx and a REINDEX is required after deploying the
fix.  This tool answers "do any exist, and how many?" by asking a Radiant node.

It reuses the indexer's own Script.get_push_input_refs so detection is faithful.

Usage:
    python3 contrib/scan_multiref_v3_outputs.py \
        --daemon http://user:pass@host:7332/ \
        --start 0 --end tip [--max-examples 20] [--progress 5000]

If --daemon is omitted, DAEMON_URL from the environment is used.
Scanning is read-only.  Ctrl-C prints the partial result.
"""

import argparse
import json
import os
import sys
import time
import urllib.request

# Import the indexer's faithful ref extractor.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from electrumx.lib.script import Script, ScriptError  # noqa: E402


def output_distinct_ref_count(spk_bytes):
    """Number of DISTINCT push-input-refs in a single output script.

    Returns 0 for a truncated/unparseable script (such scripts cannot appear in
    a confirmed v3 tx — the node rejects them at construction — so 0 is correct
    for on-chain data; this just keeps the scan robust).
    """
    try:
        all_refs, _normal, _singleton = Script.get_push_input_refs(spk_bytes)
    except (ScriptError, Exception):
        return 0
    return len(set(all_refs))


def affected_outputs_in_tx(tx):
    """Yield (vout_index, distinct_ref_count) for affected outputs of one tx.

    A tx is affected only if version == 3 AND an output has >=2 distinct refs.
    `tx` is a getblock(verbosity=2) tx dict.
    """
    if tx.get('version') != 3:
        return
    for vout in tx.get('vout', []):
        spk_hex = vout.get('scriptPubKey', {}).get('hex')
        if not spk_hex:
            continue
        n = output_distinct_ref_count(bytes.fromhex(spk_hex))
        if n >= 2:
            yield vout.get('n', -1), n


def scan_block(block):
    """Return list of (txid, vout, distinct_refs) affected entries in a block."""
    hits = []
    for tx in block.get('tx', []):
        for vout_idx, n in affected_outputs_in_tx(tx):
            hits.append((tx.get('txid'), vout_idx, n))
    return hits


class _RPC:
    def __init__(self, url):
        self.url = url
        self._id = 0

    def call(self, method, *params):
        self._id += 1
        payload = json.dumps({'jsonrpc': '2.0', 'id': self._id,
                              'method': method, 'params': list(params)}).encode()
        req = urllib.request.Request(self.url, data=payload,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read())
        if body.get('error'):
            raise RuntimeError(f'{method} RPC error: {body["error"]}')
        return body['result']


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--daemon', default=os.getenv('DAEMON_URL'),
                    help='Radiant node JSON-RPC URL (default: $DAEMON_URL)')
    ap.add_argument('--start', type=int, default=0, help='start height')
    ap.add_argument('--end', default='tip', help='end height (or "tip")')
    ap.add_argument('--max-examples', type=int, default=20)
    ap.add_argument('--progress', type=int, default=5000,
                    help='log progress every N blocks')
    ap.add_argument('--stop-after', type=int, default=0,
                    help='stop after this many affected outputs (0 = scan all)')
    args = ap.parse_args()

    if not args.daemon:
        ap.error('no daemon URL (pass --daemon or set DAEMON_URL)')

    rpc = _RPC(args.daemon)
    tip = rpc.call('getblockcount')
    end = tip if args.end == 'tip' else int(args.end)
    end = min(end, tip)

    total_affected = 0
    affected_txids = set()
    examples = []
    started = time.time()

    print(f'Scanning heights {args.start}..{end} (tip={tip}) for v3 outputs '
          f'with >=2 distinct push-refs...', flush=True)
    try:
        for h in range(args.start, end + 1):
            block_hash = rpc.call('getblockhash', h)
            block = rpc.call('getblock', block_hash, 2)
            for txid, vout, n in scan_block(block):
                total_affected += 1
                affected_txids.add(txid)
                if len(examples) < args.max_examples:
                    examples.append((h, txid, vout, n))
            if args.progress and h % args.progress == 0 and h > args.start:
                rate = (h - args.start) / max(1e-9, time.time() - started)
                print(f'  ...height {h}  affected_outputs={total_affected}  '
                      f'({rate:.0f} blk/s)', flush=True)
            if args.stop_after and total_affected >= args.stop_after:
                print(f'  stop-after {args.stop_after} reached at height {h}', flush=True)
                break
    except KeyboardInterrupt:
        print('\n[interrupted - partial result]', flush=True)

    print('\n==================== RESULT ====================')
    print(f'affected v3 outputs (>=2 distinct refs): {total_affected}')
    print(f'distinct affected txs:                   {len(affected_txids)}')
    if examples:
        print('examples (height, txid, vout, distinct_refs):')
        for h, txid, vout, n in examples:
            print(f'  {h}  {txid}:{vout}  refs={n}')
    print('-----------------------------------------------')
    if total_affected:
        print('VERDICT: REINDEX REQUIRED — these txs were indexed with the wrong '
              'v3 txid under the old big-endian ref ordering.')
        sys.exit(2)
    else:
        print('VERDICT: no affected outputs found in the scanned range — the '
              'sort fix is preventive only; no reindex needed for this range.')
        sys.exit(0)


if __name__ == '__main__':
    main()
