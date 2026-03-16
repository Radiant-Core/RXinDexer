import ast
import struct
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from electrumx.lib import util
from electrumx.lib.hash import Base58
from electrumx.lib.hash import HASHX_LEN
from electrumx.lib.util import pack_be_uint32


class AnalyticsDBKeys:
    BALANCE = b'AB'
    DISPLAY = b'AD'
    UTXO_META = b'AU'
    SUMMARY = b'AS'
    DAILY = b'AY'
    UNDO = b'AZU'


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
        entries = ast.literal_eval(raw.decode())
        for key, prev in entries:
            if prev is None:
                batch.delete(key)
            else:
                batch.put(key, prev)
        batch.delete(self._undo_key(height))

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
            batch.put(self._undo_key(height), repr(entries).encode())
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
        return ast.literal_eval(raw.decode())

    def _set_summary(self, height: int, suffix: bytes, value: Any):
        key = AnalyticsDBKeys.SUMMARY + suffix
        self.summary_cache[key] = repr(value).encode()
        self.summary_height[key] = height

    def _get_daily(self, day: int) -> Dict[str, int]:
        key = self._daily_key(day)
        raw = self.daily_cache.get(key)
        if raw is None:
            raw = self.db.utxo_db.get(key)
        if raw is None:
            return {'coins_moved': 0, 'active_addresses': 0, 'new_addresses': 0}
        return ast.literal_eval(raw.decode())

    def _set_daily(self, height: int, day: int, value: Dict[str, int]):
        key = self._daily_key(day)
        self.daily_cache[key] = repr(value).encode()
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
            amount_after = max(0, amount_before - spent_value)
            self._put_balance(height, spent_hashX, amount_after)
            if amount_after > 0:
                bucket_after = self._bucket_name(amount_after)
                balance_distribution[bucket_after] = balance_distribution.get(bucket_after, 0) + 1

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
            amount_after = amount_before + value
            self._put_balance(height, hashX, amount_after)
            bucket_after = self._bucket_name(amount_after)
            balance_distribution[bucket_after] = balance_distribution.get(bucket_after, 0) + 1
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
        self._set_summary(height, b'age_distribution', age_distribution)
        self._set_summary(height, b'last_processed_height', height)

    def backfill(self, height: int):
        if not self.enabled:
            return
        current = self._get_summary(AnalyticsDBKeys.SUMMARY + b'last_processed_height', None)
        if current is not None:
            return
        balance_distribution = {label: 0 for _, label in BALANCE_BUCKETS}
        age_distribution = {label: 0 for _, label in AGE_BUCKETS}
        balance_by_hashX: Dict[bytes, int] = defaultdict(int)
        prefix = b'u'
        for db_key, db_value in self.db.utxo_db.iterator(prefix=prefix):
            hashX = db_key[1:1 + HASHX_LEN]
            tx_pos = struct.unpack('<I', db_key[-9:-5])[0]
            tx_num = struct.unpack('<Q', db_key[-5:] + bytes(3))[0]
            value = struct.unpack('<Q', db_value)[0]
            tx_hash, birth_height = self.db.fs_tx_hash(tx_num)
            if tx_hash is None:
                continue
            balance_by_hashX[hashX] += value
            self._put_utxo_meta(height, tx_hash, tx_pos, birth_height, value, hashX)
            display_key = self._display_key(hashX)
            if self.db.utxo_db.get(display_key) is None and display_key not in self.display_cache:
                self.display_cache[display_key] = hashX.hex().encode()
                self.display_height[display_key] = height
            age_days = max(0, self._estimate_block_day(height) - self._estimate_block_day(birth_height))
            age_distribution[self._age_bucket_name(age_days)] += value

        for hashX, amount in balance_by_hashX.items():
            self._put_balance(height, hashX, amount)
            balance_distribution[self._bucket_name(amount)] += 1

        self._set_summary(height, b'balance_distribution', balance_distribution)
        self._set_summary(height, b'age_distribution', age_distribution)
        self._set_summary(height, b'last_processed_height', height)
        self.logger.info('Chain analytics backfill prepared')

    def get_balance_distribution(self) -> Dict[str, Any]:
        return self._get_summary(AnalyticsDBKeys.SUMMARY + b'balance_distribution', {label: 0 for _, label in BALANCE_BUCKETS})

    def get_supply_aging(self) -> Dict[str, Any]:
        return self._get_summary(AnalyticsDBKeys.SUMMARY + b'age_distribution', {label: 0 for _, label in AGE_BUCKETS})

    def get_top_addresses(self, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        rows = []
        prefix = AnalyticsDBKeys.BALANCE
        for key, raw in self.db.utxo_db.iterator(prefix=prefix):
            amount = struct.unpack('<Q', raw)[0]
            if amount <= 0:
                continue
            hashX = key[2:]
            display = self.db.utxo_db.get(self._display_key(hashX))
            rows.append({
                'hashX': hashX.hex(),
                'address': display.decode() if display else hashX.hex(),
                'balance': amount,
            })
        rows.sort(key=lambda item: item['balance'], reverse=True)
        return {
            'total': len(rows),
            'limit': limit,
            'offset': offset,
            'rows': rows[offset:offset + limit],
        }

    def get_movement(self, days: int = 30) -> Dict[str, Any]:
        last_height = self._get_summary(AnalyticsDBKeys.SUMMARY + b'last_processed_height', 0)
        current_day = self._estimate_block_day(last_height)
        items = []
        start = max(0, current_day - days + 1)
        for day in range(start, current_day + 1):
            daily = self._get_daily(day)
            items.append({'day': day, **daily})
        return {'days': days, 'series': items}

    def get_stats(self) -> Dict[str, Any]:
        top = {'total': sum(1 for _ in self.db.utxo_db.iterator(prefix=AnalyticsDBKeys.BALANCE))}
        return {
            'enabled': self.enabled,
            'last_processed_height': self._get_summary(AnalyticsDBKeys.SUMMARY + b'last_processed_height', 0),
            'rich_list_entries': top['total'],
        }
