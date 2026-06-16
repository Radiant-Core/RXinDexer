import asyncio
import heapq
import json
import os
import struct
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from electrumx.lib import util
from electrumx.lib.hash import Base58
from electrumx.lib.hash import HASHX_LEN
from electrumx.lib.util import pack_be_uint32, encode_undo, decode_undo

# M1 (DoS): the rich-list / get_stats endpoints scan the whole AB balance
# keyspace. To stop an attacker forcing a full scan per request by rotating the
# pagination ``offset``, we (a) hard-cap the reachable offset and (b) scan at
# most once per TTL into a bounded "top pool" that all (limit, offset) pages are
# sliced from. Both are env-overridable for large deployments.
TOP_ADDRESSES_MAX_OFFSET = int(os.getenv('ANALYTICS_TOP_MAX_OFFSET', '10000'))
TOP_ADDRESSES_MAX_LIMIT = 500  # mirrors the REST le=500 bound on `limit`
# Size of the cached pool: enough to satisfy the deepest allowed page.
TOP_ADDRESSES_POOL_SIZE = TOP_ADDRESSES_MAX_OFFSET + TOP_ADDRESSES_MAX_LIMIT
# How long a single full keyspace scan result is reused (seconds).
TOP_ADDRESSES_SCAN_TTL = int(os.getenv('ANALYTICS_TOP_SCAN_TTL', '120'))


# How many UTXO-set rows to process between asyncio yields during backfill.
# At ~1 µs per row this is ~10 ms per chunk — keeps the event loop responsive.
BACKFILL_CHUNK_SIZE = int(os.getenv('ANALYTICS_BACKFILL_CHUNK_SIZE', '10000'))


class AnalyticsDBKeys:
    BALANCE = b'AB'
    DISPLAY = b'AD'
    UTXO_META = b'AU'
    SUMMARY = b'AS'
    DAILY = b'AY'
    UNDO = b'AZU'
    # Checkpoint key: stores the last fully-processed UTXO iterator key so an
    # interrupted backfill can resume instead of restarting from scratch.
    BACKFILL_CURSOR = b'ABFC'
    # Presence of this key means a backfill is in progress (not yet committed).
    BACKFILL_IN_PROGRESS = b'ABFP'


BALANCE_BUCKETS = [
    (0, '<0.01'),
    (0.01, '0.01-1'),
    (1, '1-10'),
    (10, '10-100'),
    (100, '100-1k'),
    (1_000, '1k-10k'),
    (10_000, '10k-100k'),
    (100_000, '100k-1M'),
    (1_000_000, '1M-10M'),
    (10_000_000, '10M-100M'),
    (100_000_000, '100M-1B'),
    (1_000_000_000, '1B+'),
]

AGE_BUCKETS = [
    (0, '<1d'),
    (1, '1d-1w'),
    (7, '1w-1m'),
    (30, '1m-3m'),
    (90, '3m-6m'),
    (180, '6m-1y'),
    (365, '1y-2y'),
    (365 * 2, '2y-3y'),
    (365 * 3, '3y+'),
]


class AnalyticsIndex:
    def __init__(self, db, env):
        self.logger = util.class_logger(__name__, self.__class__.__name__)
        self.db = db
        self.env = env
        self.coin = env.coin
        self.enabled = getattr(env, 'analytics_index', True)
        self.summary_cache: Dict[bytes, bytes] = {}
        self.summary_height: Dict[bytes, int] = {}
        self.balance_cache: Dict[bytes, int] = {}
        self.balance_height: Dict[bytes, int] = {}
        self.balance_deletes: Set[bytes] = set()
        self.display_cache: Dict[bytes, bytes] = {}
        self.display_height: Dict[bytes, int] = {}
        self.utxo_meta_cache: Dict[bytes, bytes] = {}
        self.utxo_meta_height: Dict[bytes, int] = {}
        self.utxo_meta_deletes: Set[bytes] = set()
        self.daily_cache: Dict[bytes, bytes] = {}
        self.daily_height: Dict[bytes, int] = {}
        self._undo_cache: Dict[int, List[Tuple[bytes, Optional[bytes]]]] = defaultdict(list)
        self._undo_seen: Dict[int, Set[bytes]] = defaultdict(set)
        # M1: cached result of the (expensive) full AB-keyspace scan that backs
        # the rich list. Holds the descending-sorted top pool plus the total
        # address count, refreshed at most once per TOP_ADDRESSES_SCAN_TTL.
        self._top_pool: Optional[List[Tuple[int, bytes]]] = None
        self._top_total: int = 0
        self._top_scan_ts: float = 0.0
        current_height = getattr(db, 'db_height', -1)
        reorg_limit = getattr(env, 'reorg_limit', 0)
        min_keep = max(0, current_height - reorg_limit + 1) if reorg_limit else 0
        self._last_undo_pruned = min_keep - 1
        if self.enabled:
            self.logger.info('Chain analytics indexing enabled')

    def _undo_key(self, height: int) -> bytes:
        return AnalyticsDBKeys.UNDO + pack_be_uint32(height)

    def _record_undo(self, height: int, key: bytes):
        if not self.enabled:
            return
        if key in self._undo_seen[height]:
            return
        self._undo_seen[height].add(key)
        prev_value = self.db.utxo_db.get(key)
        self._undo_cache[height].append((key, prev_value))

    def _prune_old_undo_keys(self, batch):
        reorg_limit = getattr(self.env, 'reorg_limit', 0)
        if not reorg_limit:
            return
        min_keep = max(0, self.db.db_height - reorg_limit + 1)
        prune_to = min_keep - 1
        if prune_to <= self._last_undo_pruned:
            return
        for height in range(self._last_undo_pruned + 1, prune_to + 1):
            batch.delete(self._undo_key(height))
        self._last_undo_pruned = prune_to

    def backup(self, batch, height: int):
        if not self.enabled:
            return
        raw = self.db.utxo_db.get(self._undo_key(height))
        if not raw:
            return
        entries = decode_undo(raw)  # R22
        for key, prev in entries:
            if prev is None:
                batch.delete(key)
            else:
                batch.put(key, prev)
        batch.delete(self._undo_key(height))

    def memory_estimate(self) -> int:
        '''Approximate bytes held by unflushed in-memory caches.

        Used by block_processor.check_cache_size() to trigger a flush before
        these caches grow large enough to OOM the process.
        '''
        if not self.enabled:
            return 0
        undo_entries = sum(len(v) for v in self._undo_cache.values())
        return (
            len(self.summary_cache) * 400
            + len(self.summary_height) * 140
            + len(self.balance_cache) * 140
            + len(self.balance_height) * 140
            + len(self.balance_deletes) * 100
            + len(self.display_cache) * 400
            + len(self.display_height) * 140
            + len(self.utxo_meta_cache) * 400
            + len(self.utxo_meta_height) * 140
            + len(self.utxo_meta_deletes) * 100
            + len(self.daily_cache) * 400
            + len(self.daily_height) * 140
            + undo_entries * 120
        )

    def flush(self, batch):
        if not self.enabled:
            return
        self._prune_old_undo_keys(batch)
        for key, value in self.summary_cache.items():
            height = self.summary_height.get(key)
            if height is not None:
                self._record_undo(height, key)
            batch.put(key, value)
        for key, value in self.balance_cache.items():
            height = self.balance_height.get(key)
            if height is not None:
                self._record_undo(height, key)
            batch.put(key, struct.pack('<Q', value))
        for key in self.balance_deletes:
            height = self.balance_height.get(key)
            if height is not None:
                self._record_undo(height, key)
            batch.delete(key)
        for key, value in self.display_cache.items():
            height = self.display_height.get(key)
            if height is not None:
                self._record_undo(height, key)
            batch.put(key, value)
        for key, value in self.utxo_meta_cache.items():
            height = self.utxo_meta_height.get(key)
            if height is not None:
                self._record_undo(height, key)
            batch.put(key, value)
        for key in self.utxo_meta_deletes:
            height = self.utxo_meta_height.get(key)
            if height is not None:
                self._record_undo(height, key)
            batch.delete(key)
        for key, value in self.daily_cache.items():
            height = self.daily_height.get(key)
            if height is not None:
                self._record_undo(height, key)
            batch.put(key, value)
        for height, entries in sorted(self._undo_cache.items()):
            batch.put(self._undo_key(height), encode_undo(entries))  # R22
        self.summary_cache.clear()
        self.summary_height.clear()
        self.balance_cache.clear()
        self.balance_height.clear()
        self.balance_deletes.clear()
        self.display_cache.clear()
        self.display_height.clear()
        self.utxo_meta_cache.clear()
        self.utxo_meta_height.clear()
        self.utxo_meta_deletes.clear()
        self.daily_cache.clear()
        self.daily_height.clear()
        self._undo_cache.clear()
        self._undo_seen.clear()

    def _balance_key(self, hashX: bytes) -> bytes:
        return AnalyticsDBKeys.BALANCE + hashX

    def _display_key(self, hashX: bytes) -> bytes:
        return AnalyticsDBKeys.DISPLAY + hashX

    def _utxo_meta_key(self, tx_hash: bytes, tx_pos: int) -> bytes:
        return AnalyticsDBKeys.UTXO_META + tx_hash + struct.pack('<I', tx_pos)

    def _daily_key(self, day: int) -> bytes:
        return AnalyticsDBKeys.DAILY + pack_be_uint32(day)

    def _get_balance(self, hashX: bytes) -> int:
        key = self._balance_key(hashX)
        if key in self.balance_cache:
            return self.balance_cache[key]
        raw = self.db.utxo_db.get(key)
        return struct.unpack('<Q', raw)[0] if raw else 0

    def _put_balance(self, height: int, hashX: bytes, amount: int):
        key = self._balance_key(hashX)
        self.balance_height[key] = height
        if amount <= 0:
            self.balance_cache.pop(key, None)
            self.balance_deletes.add(key)
        else:
            self.balance_deletes.discard(key)
            self.balance_cache[key] = amount

    def _get_summary(self, key: bytes, default: Any) -> Any:
        raw = self.summary_cache.get(key)
        if raw is None:
            raw = self.db.utxo_db.get(key)
        if raw is None:
            return default
        return json.loads(raw.decode())

    def _set_summary(self, height: int, suffix: bytes, value: Any):
        key = AnalyticsDBKeys.SUMMARY + suffix
        self.summary_cache[key] = json.dumps(value).encode()
        self.summary_height[key] = height

    def _get_daily(self, day: int) -> Dict[str, int]:
        key = self._daily_key(day)
        raw = self.daily_cache.get(key)
        if raw is None:
            raw = self.db.utxo_db.get(key)
        if raw is None:
            return {'coins_moved': 0, 'active_addresses': 0, 'new_addresses': 0}
        return json.loads(raw.decode())

    def _set_daily(self, height: int, day: int, value: Dict[str, int]):
        key = self._daily_key(day)
        self.daily_cache[key] = json.dumps(value).encode()
        self.daily_height[key] = height

    def _bucket_name(self, value_sats: int) -> str:
        coins = value_sats / self.coin.VALUE_PER_COIN
        for threshold, label in reversed(BALANCE_BUCKETS):
            if coins >= threshold:
                return label
        return BALANCE_BUCKETS[0][1]

    def _age_bucket_name(self, age_days: int) -> str:
        for threshold, label in reversed(AGE_BUCKETS):
            if age_days >= threshold:
                return label
        return AGE_BUCKETS[0][1]

    def _estimate_block_day(self, height: int) -> int:
        return max(0, height // 144)

    def _display_text(self, display: Union[str, bytes], hashX: bytes) -> str:
        if isinstance(display, str):
            return display
        if not isinstance(display, (bytes, bytearray)):
            return hashX.hex()
        script = bytes(display)
        try:
            if (
                len(script) == 25
                and script[0] == 0x76
                and script[1] == 0xa9
                and script[2] == 0x14
                and script[-2] == 0x88
                and script[-1] == 0xac
            ):
                return Base58.encode_check(self.coin.P2PKH_VERBYTE + script[3:23])
            if (
                len(script) == 23
                and script[0] == 0xa9
                and script[1] == 0x14
                and script[-1] == 0x87
            ):
                return Base58.encode_check(self.coin.P2SH_VERBYTES[0] + script[2:22])
        except Exception:
            pass
        return script.hex() or hashX.hex()

    def _get_utxo_meta(self, tx_hash: bytes, tx_pos: int) -> Optional[Tuple[int, int, bytes]]:
        key = self._utxo_meta_key(tx_hash, tx_pos)
        raw = self.utxo_meta_cache.get(key)
        if raw is None and key not in self.utxo_meta_deletes:
            raw = self.db.utxo_db.get(key)
        if not raw:
            return None
        birth_height, value = struct.unpack('<IQ', raw[:12])
        hashX = raw[12:12 + HASHX_LEN]
        return birth_height, value, hashX

    def _put_utxo_meta(self, height: int, tx_hash: bytes, tx_pos: int, birth_height: int, value: int, hashX: bytes):
        key = self._utxo_meta_key(tx_hash, tx_pos)
        self.utxo_meta_cache[key] = struct.pack('<IQ', birth_height, value) + hashX
        self.utxo_meta_height[key] = height
        self.utxo_meta_deletes.discard(key)

    def _delete_utxo_meta(self, height: int, tx_hash: bytes, tx_pos: int):
        key = self._utxo_meta_key(tx_hash, tx_pos)
        self.utxo_meta_cache.pop(key, None)
        self.utxo_meta_deletes.add(key)
        self.utxo_meta_height[key] = height

    def process_block(self, height: int, spends: List[Tuple[bytes, int, bytes, int]], adds: List[Tuple[bytes, int, bytes, int, Union[str, bytes]]]):
        if not self.enabled:
            return
        balance_distribution = self._get_summary(AnalyticsDBKeys.SUMMARY + b'balance_distribution', {label: 0 for _, label in BALANCE_BUCKETS})
        balance_amounts = self._get_summary(AnalyticsDBKeys.SUMMARY + b'balance_distribution_amounts', {label: 0 for _, label in BALANCE_BUCKETS})
        age_distribution = self._get_summary(AnalyticsDBKeys.SUMMARY + b'age_distribution', {label: 0 for _, label in AGE_BUCKETS})
        day = self._estimate_block_day(height)
        daily = self._get_daily(day)
        active_addresses: Set[bytes] = set()
        new_addresses = 0
        coins_moved = 0

        for prev_hash, prev_idx, spent_hashX, spent_value in spends:
            amount_before = self._get_balance(spent_hashX)
            if amount_before > 0:
                bucket_before = self._bucket_name(amount_before)
                balance_distribution[bucket_before] = max(0, balance_distribution.get(bucket_before, 0) - 1)
                balance_amounts[bucket_before] = max(0, balance_amounts.get(bucket_before, 0) - amount_before)
            amount_after = max(0, amount_before - spent_value)
            self._put_balance(height, spent_hashX, amount_after)
            if amount_after > 0:
                bucket_after = self._bucket_name(amount_after)
                balance_distribution[bucket_after] = balance_distribution.get(bucket_after, 0) + 1
                balance_amounts[bucket_after] = balance_amounts.get(bucket_after, 0) + amount_after

            meta = self._get_utxo_meta(prev_hash, prev_idx)
            if meta:
                birth_height, meta_value, _ = meta
                age_days = max(0, self._estimate_block_day(height) - self._estimate_block_day(birth_height))
                age_bucket = self._age_bucket_name(age_days)
                age_distribution[age_bucket] = max(0, age_distribution.get(age_bucket, 0) - meta_value)
            self._delete_utxo_meta(height, prev_hash, prev_idx)
            coins_moved += spent_value

        for tx_hash, tx_pos, hashX, value, display in adds:
            amount_before = self._get_balance(hashX)
            if amount_before > 0:
                bucket_before = self._bucket_name(amount_before)
                balance_distribution[bucket_before] = max(0, balance_distribution.get(bucket_before, 0) - 1)
                balance_amounts[bucket_before] = max(0, balance_amounts.get(bucket_before, 0) - amount_before)
            amount_after = amount_before + value
            self._put_balance(height, hashX, amount_after)
            bucket_after = self._bucket_name(amount_after)
            balance_distribution[bucket_after] = balance_distribution.get(bucket_after, 0) + 1
            balance_amounts[bucket_after] = balance_amounts.get(bucket_after, 0) + amount_after
            if amount_before == 0:
                new_addresses += 1
            active_addresses.add(hashX)
            display_key = self._display_key(hashX)
            if display and self.db.utxo_db.get(display_key) is None and display_key not in self.display_cache:
                self.display_cache[display_key] = self._display_text(display, hashX).encode()
                self.display_height[display_key] = height
            self._put_utxo_meta(height, tx_hash, tx_pos, height, value, hashX)
            age_distribution[self._age_bucket_name(0)] = age_distribution.get(self._age_bucket_name(0), 0) + value

        daily['coins_moved'] += coins_moved
        daily['active_addresses'] += len(active_addresses)
        daily['new_addresses'] += new_addresses
        self._set_daily(height, day, daily)
        self._set_summary(height, b'balance_distribution', balance_distribution)
        self._set_summary(height, b'balance_distribution_amounts', balance_amounts)
        self._set_summary(height, b'age_distribution', age_distribution)
        self._set_summary(height, b'last_processed_height', height)

    def _needs_backfill(self) -> bool:
        '''Return True if a fresh (or resumed) backfill is required.

        A backfill that was previously interrupted is detected by the presence
        of the BACKFILL_IN_PROGRESS sentinel in the DB; in that case we always
        resume rather than treating it as a completed run.
        '''
        # An interrupted previous run is always resumed.
        if self.db.utxo_db.get(AnalyticsDBKeys.BACKFILL_IN_PROGRESS):
            return True
        current = self._get_summary(AnalyticsDBKeys.SUMMARY + b'last_processed_height', None)
        if current is None:
            return True
        age_dist = self._get_summary(AnalyticsDBKeys.SUMMARY + b'age_distribution', {})
        has_only_new_utxos = age_dist.get('<1d', 0) > 0 and all(
            age_dist.get(label, 0) == 0
            for threshold, label in AGE_BUCKETS
            if label != '<1d'
        )
        if has_only_new_utxos:
            self.logger.warning(
                'Age distribution shows only <1d UTXOs (%d) - '
                're-running backfill to fix birth heights',
                age_dist.get('<1d', 0)
            )
            return True
        return False

    async def backfill(self, height: int):
        '''Asynchronous, checkpoint/resume UTXO backfill.

        Safe to run as a background task concurrent with block processing —
        writes go directly to write_batch (bypassing shared in-memory caches)
        so there is no race with block_processor's periodic flush().  Yields
        to the asyncio event loop every BACKFILL_CHUNK_SIZE rows so serving is
        never blocked for more than a few milliseconds.  Progress is
        checkpointed to disk after each chunk so an interrupted run resumes
        from where it left off rather than restarting from the beginning.
        Exceptions are caught and logged rather than propagated, so a backfill
        failure cannot kill the block-processing task group.
        '''
        if not self.enabled:
            return
        try:
            await self._backfill_impl(height)
        except Exception:
            self.logger.exception('Analytics backfill failed; will retry on next startup')

    async def _backfill_impl(self, height: int):
        if not self._needs_backfill():
            return

        # Mark in-progress *before* we start so that if we crash mid-scan the
        # next startup knows to resume rather than incorrectly believing the
        # previous (partial) results are complete.
        with self.db.utxo_db.write_batch() as _b:
            _b.put(AnalyticsDBKeys.BACKFILL_IN_PROGRESS, b'1')

        # Reload cursor: if we were previously interrupted, continue from the
        # last committed position.
        cursor_raw = self.db.utxo_db.get(AnalyticsDBKeys.BACKFILL_CURSOR)
        resume_cursor: Optional[bytes] = cursor_raw if cursor_raw else None

        if resume_cursor:
            self.logger.info(
                'Resuming analytics backfill from cursor %s', resume_cursor.hex()
            )
        else:
            self.logger.info('Starting analytics backfill (height=%d)', height)

        age_distribution = {label: 0 for _, label in AGE_BUCKETS}
        # balance_by_hashX is kept for the full scan so that all UTXOs for an
        # address are summed before a single final AB write, avoiding races with
        # block_processor's flush() which also writes AB keys concurrently.
        balance_by_hashX: Dict[bytes, int] = defaultdict(int)
        # Per-chunk AU/AD accumulators — written directly to DB each chunk.
        chunk_utxo_meta: Dict[bytes, bytes] = {}
        chunk_display: Dict[bytes, bytes] = {}

        prefix = b'u'
        chunk_count = 0
        total_utxos = 0
        last_key: Optional[bytes] = None

        for db_key, db_value in self.db.utxo_db.iterator(prefix=prefix):
            # Skip rows already processed in a previous (interrupted) run.
            if resume_cursor is not None:
                if db_key <= resume_cursor:
                    continue
                # Past the resume point — stop skipping.
                resume_cursor = None

            hashX = db_key[1:1 + HASHX_LEN]
            tx_pos = struct.unpack('<I', db_key[-9:-5])[0]
            tx_num = struct.unpack('<Q', db_key[-5:] + bytes(3))[0]
            value = struct.unpack('<Q', db_value)[0]
            tx_hash, birth_height = self.db.fs_tx_hash(tx_num)
            if tx_hash is None:
                continue

            # Write AU (UTXO meta) directly — bypass shared utxo_meta_cache.
            meta_key = self._utxo_meta_key(tx_hash, tx_pos)
            chunk_utxo_meta[meta_key] = struct.pack('<IQ', birth_height, value) + hashX

            # Accumulate per-address balance across the full scan.
            balance_by_hashX[hashX] += value

            # Write AD (display) if not already present — bypass display_cache.
            display_key = self._display_key(hashX)
            if display_key not in chunk_display and self.db.utxo_db.get(display_key) is None:
                chunk_display[display_key] = hashX.hex().encode()

            age_days = max(0, self._estimate_block_day(height) - self._estimate_block_day(birth_height))
            age_distribution[self._age_bucket_name(age_days)] += value
            last_key = db_key
            total_utxos += 1
            chunk_count += 1

            if chunk_count >= BACKFILL_CHUNK_SIZE:
                # Flush AU/AD for this chunk directly to disk and checkpoint
                # the cursor so a crash only loses at most one chunk of work.
                with self.db.utxo_db.write_batch() as batch:
                    for k, v in chunk_utxo_meta.items():
                        batch.put(k, v)
                    for k, v in chunk_display.items():
                        batch.put(k, v)
                    batch.put(AnalyticsDBKeys.BACKFILL_CURSOR, last_key)
                chunk_utxo_meta.clear()
                chunk_display.clear()
                chunk_count = 0
                self.logger.debug(
                    'Backfill checkpoint: %d UTXOs processed so far', total_utxos
                )
                await asyncio.sleep(0)

        # Final AU/AD flush for the trailing partial chunk.
        with self.db.utxo_db.write_batch() as batch:
            for k, v in chunk_utxo_meta.items():
                batch.put(k, v)
            for k, v in chunk_display.items():
                batch.put(k, v)

        # Write all AB (balance) keys in a single batch — this is safe because
        # we accumulated the complete per-address total across the whole scan.
        balance_distribution = {label: 0 for _, label in BALANCE_BUCKETS}
        balance_amounts = {label: 0 for _, label in BALANCE_BUCKETS}
        with self.db.utxo_db.write_batch() as batch:
            for hashX, amount in balance_by_hashX.items():
                if amount <= 0:
                    continue
                batch.put(self._balance_key(hashX), struct.pack('<Q', amount))
                bucket = self._bucket_name(amount)
                balance_distribution[bucket] += 1
                balance_amounts[bucket] += amount

        # Write final summaries + clear checkpoint sentinel atomically.
        with self.db.utxo_db.write_batch() as batch:
            for suffix, value in (
                (b'balance_distribution', balance_distribution),
                (b'balance_distribution_amounts', balance_amounts),
                (b'age_distribution', age_distribution),
                (b'last_processed_height', height),
            ):
                batch.put(AnalyticsDBKeys.SUMMARY + suffix, json.dumps(value).encode())
            batch.delete(AnalyticsDBKeys.BACKFILL_CURSOR)
            batch.delete(AnalyticsDBKeys.BACKFILL_IN_PROGRESS)
        self.logger.info(
            'Chain analytics backfill complete (%d UTXOs processed)', total_utxos
        )

    def _recompute_balance_distribution(self) -> Dict[str, int]:
        """Recompute balance distribution from actual per-address balances."""
        distribution = {label: 0 for _, label in BALANCE_BUCKETS}
        prefix = AnalyticsDBKeys.BALANCE
        count = 0
        for key, raw in self.db.utxo_db.iterator(prefix=prefix):
            amount = struct.unpack('<Q', raw)[0]
            if amount <= 0:
                continue
            bucket = self._bucket_name(amount)
            distribution[bucket] = distribution.get(bucket, 0) + 1
            count += 1
        self.logger.info(
            'Balance distribution recomputed from %d addresses: %s',
            count, distribution
        )
        return distribution

    def get_balance_distribution(self) -> Dict[str, Any]:
        counts = self._get_summary(
            AnalyticsDBKeys.SUMMARY + b'balance_distribution',
            {label: 0 for _, label in BALANCE_BUCKETS},
        )
        amounts = self._get_summary(
            AnalyticsDBKeys.SUMMARY + b'balance_distribution_amounts',
            {label: 0 for _, label in BALANCE_BUCKETS},
        )
        # Detect legacy '1M+' bucket and auto-migrate counts
        if counts.get('1M+', 0) > 0:
            self.logger.info(
                'Legacy 1M+ bucket detected (%d entries) — recomputing',
                counts['1M+'],
            )
            counts = self._recompute_balance_distribution()
            height = self._get_summary(
                AnalyticsDBKeys.SUMMARY + b'last_processed_height', 0,
            )
            self._set_summary(height, b'balance_distribution', counts)
            # Persist immediately so subsequent calls are fast
            key = AnalyticsDBKeys.SUMMARY + b'balance_distribution'
            self.db.utxo_db.put(key, repr(counts).encode())
            self.summary_cache.pop(key, None)
        # Return combined structure: {bucket: {count, amount}}
        return {
            label: {'count': counts.get(label, 0), 'amount': amounts.get(label, 0)}
            for _, label in BALANCE_BUCKETS
        }

    def get_supply_aging(self) -> Dict[str, Any]:
        return self._get_summary(AnalyticsDBKeys.SUMMARY + b'age_distribution', {label: 0 for _, label in AGE_BUCKETS})

    def _refresh_top_pool(self) -> None:
        """Scan the AB balance keyspace once into a bounded, cached top pool.

        M1 (DoS): a global rich list is inherently a scan of the balance
        keyspace. Rather than re-scan per (limit, offset) — which let an attacker
        force a full scan every request by rotating ``offset`` — we scan at most
        once per ``TOP_ADDRESSES_SCAN_TTL`` into the top ``TOP_ADDRESSES_POOL_SIZE``
        addresses (a bounded min-heap, so memory is O(pool), not O(all
        addresses)). Every page is then sliced from this cached pool in memory.
        We also cache the total address count here so ``get_stats`` reuses the
        same scan instead of running its own ``sum(1 for _ in ...)`` scan.
        """
        now = time.monotonic()
        if (
            self._top_pool is not None
            and now - self._top_scan_ts < TOP_ADDRESSES_SCAN_TTL
        ):
            return

        prefix = AnalyticsDBKeys.BALANCE
        need = TOP_ADDRESSES_POOL_SIZE
        heap: List[Tuple[int, bytes]] = []  # min-heap of (amount, hashX)
        total = 0
        for key, raw in self.db.utxo_db.iterator(prefix=prefix):
            amount = struct.unpack('<Q', raw)[0]
            if amount <= 0:
                continue
            total += 1
            entry = (amount, key[2:])
            if len(heap) < need:
                heapq.heappush(heap, entry)
            elif amount > heap[0][0]:
                heapq.heapreplace(heap, entry)

        self._top_pool = sorted(heap, key=lambda t: t[0], reverse=True)
        self._top_total = total
        self._top_scan_ts = now

    def get_top_addresses(self, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        """Rich list (top RXD balances).

        Pages are sliced from a cached top pool produced by a single scan that
        runs at most once per TTL (see ``_refresh_top_pool``), so rotating
        ``offset`` across requests can never force repeated full keyspace scans.
        ``offset`` is hard-capped at ``TOP_ADDRESSES_MAX_OFFSET`` and ``limit``
        at ``TOP_ADDRESSES_MAX_LIMIT`` so a request can never reach beyond the
        cached pool.
        """
        # Clamp inputs so a page can never escape the cached pool (defence in
        # depth — the REST layer rejects oversized offsets before we get here).
        limit = max(0, min(int(limit), TOP_ADDRESSES_MAX_LIMIT))
        offset = max(0, min(int(offset), TOP_ADDRESSES_MAX_OFFSET))

        self._refresh_top_pool()
        pool = self._top_pool or []
        page = pool[offset:offset + limit]

        rows = []
        for amount, hashX in page:
            display = self.db.utxo_db.get(self._display_key(hashX))
            rows.append({
                'hashX': hashX.hex(),
                'address': display.decode() if display else hashX.hex(),
                'balance': amount,
            })
        return {
            'total': self._top_total,
            'limit': limit,
            'offset': offset,
            'rows': rows,
        }

    def get_movement(self, days: int = 30) -> Dict[str, Any]:
        last_height = self._get_summary(AnalyticsDBKeys.SUMMARY + b'last_processed_height', 0)
        current_day = self._estimate_block_day(last_height)
        start = max(0, current_day - days + 1)
        pfx = AnalyticsDBKeys.DAILY
        plen = len(pfx)
        # M1: bound the AY keyspace scan to the requested [start, current_day]
        # window instead of reading every day ever recorded. Daily keys are
        # ``pfx + pack_be_uint32(day)``; big-endian packing makes them sort
        # ascending by day, so we can stop iterating as soon as we pass
        # ``current_day``. ``days`` is itself capped by the REST layer
        # (le=3650), so the retained window is bounded regardless of how many
        # historical days exist on disk. Cross-backend safe: only uses
        # ``prefix`` (both LevelDB and RocksDB iterators yield ascending order).
        dd = {}
        for k, v in self.db.utxo_db.iterator(prefix=pfx):
            d = struct.unpack('>I', k[plen:plen + 4])[0]
            if d > current_day:
                break  # keys are day-ascending; nothing past the window remains
            if d >= start:
                dd[d] = json.loads(v.decode())
        empty = {'coins_moved': 0, 'active_addresses': 0, 'new_addresses': 0}
        items = [{'day': d, **dd.get(d, empty)} for d in range(start, current_day + 1)]
        return {'days': days, 'series': items}

    def get_stats(self) -> Dict[str, Any]:
        # M1: reuse the cached rich-list scan instead of running a second full
        # AB-keyspace scan (sum(1 for _ in iterator(...))) on every cache miss.
        self._refresh_top_pool()
        return {
            'enabled': self.enabled,
            'last_processed_height': self._get_summary(AnalyticsDBKeys.SUMMARY + b'last_processed_height', 0),
            'rich_list_entries': self._top_total,
        }
