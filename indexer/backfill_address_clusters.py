#!/usr/bin/env python3
"""Backfill Address Clusters (common-input heuristic)

Builds an address -> cluster_id mapping using a common-input ownership heuristic:
- If multiple input addresses appear together in a single transaction, they are assumed to belong
  to the same wallet and will be merged into a single cluster.

Notes / caveats:
- This heuristic is imperfect (CoinJoin and some privacy patterns will create false merges).
- To reduce false positives, we skip transactions with too many distinct input addresses.

Progress tracking:
- Uses backfill_status table with backfill_type='address_clusters'.
"""

import argparse
import hashlib
import os
import sys
import time
from typing import List, Optional, Tuple


# Add repo root to path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from sqlalchemy import text

from database.session import get_indexer_session
from database.models import BackfillStatus


BACKFILL_TYPE = 'address_clusters'
HOLDERS_BACKFILL_TYPE = 'address_clusters_holders'


_B58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def _b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, 'big')
    out = ''
    while n > 0:
        n, r = divmod(n, 58)
        out = _B58_ALPHABET[r] + out
    pad = 0
    for b in raw:
        if b == 0:
            pad += 1
        else:
            break
    return ('1' * pad) + (out or '')


def _b58check(version: int, payload: bytes) -> str:
    data = bytes([version]) + payload
    chk = hashlib.sha256(hashlib.sha256(data).digest()).digest()[:4]
    return _b58encode(data + chk)


def _resolve_p2pkh_address_from_script_hex(script_hex: str) -> Optional[str]:
    if not isinstance(script_hex, str):
        return None
    s = script_hex.strip().lower()
    if not s.startswith('76a914'):
        return None
    if len(s) < (6 + 40 + 4):
        return None
    if s[46:50] != '88ac':
        return None
    try:
        h160 = bytes.fromhex(s[6:46])
    except Exception:
        return None
    # Observed addresses in DB are base58 starting with '1', so use version=0x00.
    return _b58check(0x00, h160)


def _parse_nonstandard_key(key: str) -> Optional[Tuple[str, int]]:
    if not isinstance(key, str) or not key.startswith('NONSTANDARD:'):
        return None
    rest = key[len('NONSTANDARD:'):]
    parts = rest.split(':')
    if len(parts) < 2:
        return None
    txid = parts[0]
    try:
        vout = int(parts[1])
    except Exception:
        return None
    if not txid:
        return None
    return txid, vout


def _get_or_create_status(db) -> BackfillStatus:
    status = db.query(BackfillStatus).filter(BackfillStatus.backfill_type == BACKFILL_TYPE).first()
    if not status:
        status = BackfillStatus(
            backfill_type=BACKFILL_TYPE,
            is_complete=False,
            last_processed_id=0,
            total_processed=0,
        )
        db.add(status)
        db.commit()
    return status


def _get_or_create_holders_status(db) -> BackfillStatus:
    status = db.query(BackfillStatus).filter(BackfillStatus.backfill_type == HOLDERS_BACKFILL_TYPE).first()
    if not status:
        status = BackfillStatus(
            backfill_type=HOLDERS_BACKFILL_TYPE,
            is_complete=False,
            last_processed_id=0,
            total_processed=0,
        )
        db.add(status)
        db.commit()
    return status


def _fetch_tx_ids(db, last_id: int, limit: int) -> List[int]:
    rows = db.execute(
        text(
            """
            SELECT id
            FROM transactions
            WHERE id > :last_id
            ORDER BY id
            LIMIT :limit
            """
        ),
        {'last_id': int(last_id or 0), 'limit': int(limit)},
    ).fetchall()
    return [int(r[0]) for r in rows]


def _fetch_input_addresses(db, tx_id: int, max_inputs: int) -> List[str]:
    rows = db.execute(
        text(
            """
            SELECT DISTINCT u.address, u.script_hex
            FROM transaction_inputs ti
            JOIN utxos u
              ON u.txid = ti.spent_txid
             AND u.vout = ti.spent_vout
            WHERE ti.transaction_id = :tx_id
              AND u.address IS NOT NULL
              AND length(btrim(u.address)) > 0
            LIMIT :max_inputs_plus
            """
        ),
        {'tx_id': int(tx_id), 'max_inputs_plus': int(max_inputs) + 1},
    ).fetchall()

    addrs: list[str] = []
    for r in rows:
        if not r or not r[0]:
            continue
        addr = str(r[0])
        script_hex = r[1]
        if addr.startswith('NONSTANDARD:') and isinstance(script_hex, str):
            resolved = _resolve_p2pkh_address_from_script_hex(script_hex)
            if resolved:
                addrs.append(resolved)
                continue
        addrs.append(addr)

    # If too many distinct input addresses, skip (privacy / batching heuristic)
    if len(addrs) > max_inputs:
        return []
    return addrs


def _ensure_addresses(db, addresses: List[str]):
    if not addresses:
        return

    # Bulk insert (best-effort)
    values = ",".join([f"(:a{i})" for i in range(len(addresses))])
    params = {f"a{i}": addr for i, addr in enumerate(addresses)}

    db.execute(
        text(
            f"""
            INSERT INTO address_clusters (address)
            VALUES {values}
            ON CONFLICT (address) DO NOTHING
            """
        ),
        params,
    )


def _merge_clusters_for_addresses(db, addresses: List[str]) -> int:
    if len(addresses) < 2:
        return 0

    # Ensure rows exist
    _ensure_addresses(db, addresses)

    cluster_rows = db.execute(
        text(
            """
            SELECT DISTINCT cluster_id
            FROM address_clusters
            WHERE address = ANY(:addrs)
            """
        ),
        {'addrs': addresses},
    ).fetchall()

    cluster_ids = sorted({int(r[0]) for r in cluster_rows if r and r[0] is not None})
    if len(cluster_ids) <= 1:
        return 0

    canonical = cluster_ids[0]
    changed = db.execute(
        text(
            """
            UPDATE address_clusters
            SET cluster_id = :canonical,
                updated_at = NOW()
            WHERE cluster_id = ANY(:ids)
              AND cluster_id <> :canonical
            """
        ),
        {'canonical': canonical, 'ids': cluster_ids},
    ).rowcount

    return int(changed or 0)


def _alias_nonstandard_holders(db, pairs: list[tuple[str, str]]) -> int:
    if not pairs:
        return 0

    canon_set = {c for _, c in pairs if c}
    alias_set = {a for a, _ in pairs if a}

    _ensure_addresses(db, sorted(canon_set))
    _ensure_addresses(db, sorted(alias_set))

    canon_rows = db.execute(
        text(
            """
            SELECT address, cluster_id
            FROM address_clusters
            WHERE address = ANY(:addrs)
            """
        ),
        {'addrs': list(canon_set)},
    ).fetchall()

    canon_map: dict[str, int] = {str(r[0]): int(r[1]) for r in canon_rows if r and r[0] is not None and r[1] is not None}
    if not canon_map:
        return 0

    values = []
    params: dict[str, object] = {}
    i = 0
    for alias, canon in pairs:
        cid = canon_map.get(canon)
        if cid is None:
            continue
        params[f"a{i}"] = alias
        params[f"c{i}"] = int(cid)
        values.append(f"(:a{i}, :c{i})")
        i += 1

    if not values:
        return 0

    updated = db.execute(
        text(
            f"""
            UPDATE address_clusters ac
            SET cluster_id = v.cluster_id,
                updated_at = NOW()
            FROM (VALUES {', '.join(values)}) AS v(address, cluster_id)
            WHERE ac.address = v.address
              AND ac.cluster_id <> v.cluster_id
            """
        ),
        params,
    ).rowcount

    return int(updated or 0)


def _fetch_nonstandard_holder_rows(db, last_id: int, limit: int):
    rows = db.execute(
        text(
            """
            SELECT id, address
            FROM token_holders
            WHERE id > :last_id
              AND address LIKE 'NONSTANDARD:%'
              AND balance > 0
            ORDER BY id
            LIMIT :limit
            """
        ),
        {'last_id': int(last_id or 0), 'limit': int(limit)},
    ).fetchall()
    return [(int(r[0]), str(r[1])) for r in rows if r and r[0] is not None and r[1] is not None]


def _resolve_holder_to_canonical_address(db, holder_key: str) -> Optional[str]:
    parsed = _parse_nonstandard_key(holder_key)
    if not parsed:
        return None
    txid, vout = parsed
    row = db.execute(
        text(
            """
            SELECT address, script_hex
            FROM utxos
            WHERE txid = :txid AND vout = :vout
            LIMIT 1
            """
        ),
        {'txid': txid, 'vout': int(vout)},
    ).fetchone()
    if not row:
        return None
    addr = row[0]
    script_hex = row[1]
    if isinstance(addr, str) and addr and not addr.startswith('NONSTANDARD:'):
        return addr
    if isinstance(script_hex, str):
        return _resolve_p2pkh_address_from_script_hex(script_hex)
    return None


def run_holders_backfill(db, time_budget_seconds: Optional[float], start_time: float, batch_size: int = 2000) -> Tuple[int, int]:
    status = _get_or_create_holders_status(db)
    processed = 0
    updated = 0

    while True:
        if time_budget_seconds is not None and (time.monotonic() - start_time) >= time_budget_seconds:
            status.total_processed = int(processed)
            db.commit()
            break

        rows = _fetch_nonstandard_holder_rows(db, int(status.last_processed_id or 0), batch_size)
        if not rows:
            status.is_complete = True
            db.commit()
            break

        pairs: list[tuple[str, str]] = []
        for hid, holder_key in rows:
            status.last_processed_id = int(hid)
            canon = _resolve_holder_to_canonical_address(db, holder_key)
            if canon:
                pairs.append((holder_key, canon))
            processed += 1

            if time_budget_seconds is not None and (time.monotonic() - start_time) >= time_budget_seconds:
                break

        if pairs:
            updated += _alias_nonstandard_holders(db, pairs)

        status.total_processed = int(processed)
        db.commit()

    return processed, updated


def run_backfill(batch_size: int, max_inputs: int, commit_every: int, start_from: Optional[int], include_token_holders: bool, holders_batch_size: int, only_token_holders: bool):
    total_merges = 0
    total_txs = 0

    with get_indexer_session() as db:
        statement_timeout_ms = int(os.getenv('CLUSTER_STATEMENT_TIMEOUT_MS', '0') or 0)
        if statement_timeout_ms > 0:
            try:
                db.execute(text(f"SET statement_timeout TO {statement_timeout_ms}"))
            except Exception:
                db.rollback()

        time_budget_seconds_env = os.getenv('CLUSTER_TIME_BUDGET_SECONDS', '').strip()
        time_budget_seconds = None
        if time_budget_seconds_env:
            try:
                time_budget_seconds = float(time_budget_seconds_env)
            except Exception:
                time_budget_seconds = None

        start_time = time.monotonic()

        if not only_token_holders:
            status = _get_or_create_status(db)

            if start_from is not None:
                status.last_processed_id = int(start_from)
                db.commit()

            while True:
                if time_budget_seconds is not None and (time.monotonic() - start_time) >= time_budget_seconds:
                    status.total_processed = int(total_txs)
                    db.commit()
                    break

                tx_ids = _fetch_tx_ids(db, int(status.last_processed_id or 0), batch_size)
                if not tx_ids:
                    status.is_complete = True
                    db.commit()
                    break

                for i, tx_id in enumerate(tx_ids, start=1):
                    status.last_processed_id = int(tx_id)

                    addrs = _fetch_input_addresses(db, tx_id, max_inputs=max_inputs)
                    if len(addrs) >= 2:
                        total_merges += _merge_clusters_for_addresses(db, addrs)

                    total_txs += 1

                    if time_budget_seconds is not None and (time.monotonic() - start_time) >= time_budget_seconds:
                        status.total_processed = int(total_txs)
                        db.commit()
                        return total_txs, total_merges

                    if (total_txs % commit_every) == 0:
                        status.total_processed = int(total_txs)
                        db.commit()

                status.total_processed = int(total_txs)
                db.commit()

        holders_processed = 0
        holders_updated = 0
        if include_token_holders:
            holders_processed, holders_updated = run_holders_backfill(
                db,
                time_budget_seconds=time_budget_seconds,
                start_time=start_time,
                batch_size=holders_batch_size,
            )

        if holders_updated:
            total_merges += int(holders_updated)

    return total_txs, total_merges


def main():
    parser = argparse.ArgumentParser(description='Backfill address_clusters using common-input heuristic')
    parser.add_argument('--batch-size', type=int, default=int(os.getenv('CLUSTER_BACKFILL_BATCH_SIZE', '2000')))
    parser.add_argument('--max-inputs', type=int, default=int(os.getenv('CLUSTER_MAX_INPUTS', '20')))
    parser.add_argument('--commit-every', type=int, default=int(os.getenv('CLUSTER_COMMIT_EVERY', '200')))
    parser.add_argument('--time-budget-seconds', type=float, default=None)
    parser.add_argument('--statement-timeout-ms', type=int, default=None)
    parser.add_argument('--include-token-holders', type=int, default=1)
    parser.add_argument('--only-token-holders', type=int, default=0)
    parser.add_argument('--holders-batch-size', type=int, default=int(os.getenv('CLUSTER_HOLDERS_BATCH_SIZE', '2000')))
    parser.add_argument('--start-from', type=int, default=None)
    args = parser.parse_args()

    if args.time_budget_seconds is not None:
        os.environ['CLUSTER_TIME_BUDGET_SECONDS'] = str(args.time_budget_seconds)
    if args.statement_timeout_ms is not None:
        os.environ['CLUSTER_STATEMENT_TIMEOUT_MS'] = str(args.statement_timeout_ms)

    txs, merges = run_backfill(
        batch_size=args.batch_size,
        max_inputs=args.max_inputs,
        commit_every=args.commit_every,
        start_from=args.start_from,
        include_token_holders=bool(int(args.include_token_holders)),
        holders_batch_size=int(args.holders_batch_size),
        only_token_holders=bool(int(args.only_token_holders)),
    )

    print(f"Processed transactions: {txs}")
    print(f"Cluster rows updated: {merges}")


if __name__ == '__main__':
    main()
