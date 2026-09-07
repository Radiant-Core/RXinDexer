"""
Backfill for singleton ref location chains (the GH hop rows).

The live path records hops in ``advance_txs``, where the mint/transfer distinction, the vout and
the holder hashX are all already in scope. This reconstructs the same rows for blocks indexed
before that code existed.

Why a daemon rescan rather than a DB pass, unlike the GCM/GMT/GA/GMH migrations: a hop is a
property of a TRANSACTION, not of stored token metadata. Worse, the ``ri`` row that records which
refs an outpoint carried is DELETED when that outpoint is spent (see block_processor's ref-spend
path), so for any historic spend the DB no longer knows what the input held — which is exactly
what melt detection needs.

The fix is to exploit the one advantage a backfill has: it walks blocks in order from genesis, so
it can maintain the outpoint -> singleton-refs map itself, adding on output and dropping on spend.
That is the same state ``ri`` holds for the live path, reconstructed as the scan proceeds, and it
makes mint, transfer AND melt all recoverable.

Idempotent by construction: a hop's key is ``GH + ref + height + tx_idx``, so re-deriving one
rewrites the identical row. Overlap with blocks the live path already covered is therefore
harmless, which is why this needs no live-from watermark — it simply scans 0..tip.
"""

import asyncio
import os
import struct
from typing import Any, Dict, Optional, Set, Tuple

from electrumx.lib import util
from electrumx.lib.script import Script
from electrumx.lib.util import pack_be_uint32, unpack_be_uint32
from electrumx.server.glyph_index import (
    GlyphDBKeys, GlyphEventType, GlyphIndex, NO_VOUT, pack_history_key,
)

BACKFILL_CHUNK_BLOCKS = int(os.getenv('REF_HISTORY_BACKFILL_CHUNK_BLOCKS', '200'))
# How often the rescan reports progress at INFO. Frequent enough to show a rate, rare enough not
# to bury the log over ~460k blocks.
PROGRESS_EVERY_BLOCKS = 20_000
# Soft ceiling on the live singleton-outpoint map. Singletons are NFTs and contracts, so the live
# set is far smaller than the UTXO set, but a warning beats an unexplained RSS climb.
WARN_TRACKED_OUTPOINTS = 2_000_000


class RefHistoryBackfillKeys:
    """Checkpoint keys. 'GL' is unused and is not a prefix of any existing G-prefix — 'GR'
    (HOLDER_BY_REF) and 'GD' (PAYLOAD_HASH) both are, so neither could be used here."""
    CURSOR = b'GLc'     # -> be_u32(next height to scan)
    TARGET = b'GLt'     # -> be_u32(inclusive final height)
    DONE = b'GLf'       # -> b'1'


class RefHistoryBackfill:
    """Reconstructs singleton hop rows for historic blocks."""

    def __init__(self, db, env, glyph_index):
        self.logger = util.class_logger(__name__, self.__class__.__name__)
        self.db = db
        self.env = env
        self.glyph_index = glyph_index
        self.enabled = bool(glyph_index) and getattr(env, 'ref_history_backfill', False)

    # ---- state ----
    def state(self) -> Tuple[bool, int, int]:
        done = bool(self.db.utxo_db.get(RefHistoryBackfillKeys.DONE))
        cur = self.db.utxo_db.get(RefHistoryBackfillKeys.CURSOR)
        tgt = self.db.utxo_db.get(RefHistoryBackfillKeys.TARGET)
        return (done,
                unpack_be_uint32(cur)[0] if cur else 0,
                unpack_be_uint32(tgt)[0] if tgt else -1)

    def stats(self) -> Dict[str, Any]:
        done, nxt, tgt = self.state()
        return {
            'enabled': self.enabled,
            'complete': done,
            'next_height': None if done else nxt,
            'target_height': tgt if tgt >= 0 else None,
        }

    # ---- scan ----
    def _singleton_refs(self, script: bytes) -> Set[bytes]:
        """Singleton refs carried by an output script, or empty on a script that will not parse.

        A malformed script must not abort the scan — the live path treats one as ref-less too.
        """
        try:
            return {rb for rb, rt in self.glyph_index._extract_refs_from_script(script)
                    if rt == 1}
        except Exception:
            return set()

    def _holder_hashX(self, script: bytes) -> bytes:
        """The BASE address hashX, matching what record_ref_hop stores.

        Must be the base locking script (ref preamble stripped), not ``zero_refs``: the GO owner
        index this resolves through is keyed by base hashX, so a ref-wrapped hashX never matches
        and holder_address comes back null for every row.
        """
        try:
            return self.env.coin.hashX_from_script(Script.base_locking_script(script))
        except Exception:
            return b''

    def scan_block(self, block, height: int, tracked: Dict[bytes, Set[bytes]],
                   rows: list) -> int:
        """Derive every hop in one block, updating the outpoint -> refs map.

        ``tracked`` is the reconstruction of the ``ri`` table: it must be updated for EVERY block,
        including ones that yield no hops, or a later melt will be missed.
        """
        found = 0
        for tx_idx, (tx, tx_hash) in enumerate(block.transactions):
            spent_outpoints = set()
            consumed_refs: Set[bytes] = set()
            for txin in tx.inputs:
                if txin.is_generation():
                    continue
                outpoint = txin.prev_hash + struct.pack('<I', txin.prev_idx)
                spent_outpoints.add(outpoint)
                # What the spent outpoint carried, from the map we have been building.
                consumed_refs |= tracked.pop(outpoint, set())

            recreated: Set[bytes] = set()
            for vout, txout in enumerate(tx.outputs):
                refs = self._singleton_refs(txout.pk_script)
                if not refs:
                    continue
                outpoint = tx_hash + struct.pack('<I', vout)
                tracked[outpoint] = refs
                holder = self._holder_hashX(txout.pk_script)
                for ref in refs:
                    recreated.add(ref)
                    # A ref whose own outpoint this tx spends is being minted from that seed;
                    # otherwise the ref is moving. Same rule as the live path.
                    event = (GlyphEventType.MINT if ref in spent_outpoints
                             else GlyphEventType.TRANSFER)
                    # And the same plumbing filter, or the rescan re-derives the very rows the
                    # live path now declines to write.
                    if (event == GlyphEventType.TRANSFER
                            and self.glyph_index.is_plumbing_singleton(ref)):
                        continue
                    rows.append((pack_history_key(ref, height, tx_idx),
                                 struct.pack('<B', event) + tx_hash
                                 + struct.pack('<I', vout) + holder))
                    found += 1

            for ref in consumed_refs - recreated:
                rows.append((pack_history_key(ref, height, tx_idx),
                             struct.pack('<B', GlyphEventType.MELT) + tx_hash
                             + struct.pack('<I', NO_VOUT)))
                found += 1
        return found

    async def backfill(self, height: int, daemon, caught_up_event=None):
        """Rescan historic blocks for singleton hops. Logged, never propagated."""
        if not self.enabled:
            return
        if caught_up_event is not None:
            await caught_up_event.wait()
        try:
            await self._impl(height, daemon)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception('Ref-history backfill failed; will resume on next startup')

    async def _impl(self, height: int, daemon):
        done, next_height, target = self.state()
        if done:
            return
        if target < 0:
            target = height
            with self.db.utxo_db.write_batch() as batch:
                batch.put(RefHistoryBackfillKeys.TARGET, pack_be_uint32(target))
                batch.put(RefHistoryBackfillKeys.CURSOR, pack_be_uint32(0))
            next_height = 0
            self.logger.info('Starting ref-history backfill: heights 0..%d', target)
        else:
            self.logger.info('Resuming ref-history backfill at %d (target %d)',
                             next_height, target)

        # The outpoint -> refs map must be rebuilt from genesis, so a resumed run restarts the
        # scan rather than continuing mid-chain: melts depend on state accumulated from block 0,
        # and picking up at an arbitrary height would silently miss them. Re-scanning is cheap
        # relative to getting it wrong, and the rows are idempotent.
        if next_height != 0:
            self.logger.info('Restarting from 0: melt detection needs the ref map built from '
                             'genesis, and re-derived rows are idempotent')
            next_height = 0

        tracked: Dict[bytes, Set[bytes]] = {}
        total = 0
        warned = False
        last_progress = -1      # -1 so the first chunk reports, confirming the rescan is alive

        while next_height <= target:
            count = min(BACKFILL_CHUNK_BLOCKS, target - next_height + 1)
            hex_hashes = await daemon.block_hex_hashes(next_height, count)
            raw_blocks = await daemon.raw_blocks(hex_hashes)

            rows: list = []
            for offset, raw_block in enumerate(raw_blocks):
                h = next_height + offset
                try:
                    block = self.env.coin.block(raw_block)
                except Exception:
                    self.logger.exception('ref-history backfill: undecodable block at %d', h)
                    continue
                total += self.scan_block(block, h, tracked, rows)

            # Enrich, never replace. A reveal's key already holds the DEPLOY row written at index
            # time; overwriting it with the hop would trade the `deploy` event that
            # /tokens/{ref}/history has always reported for a `mint` it never showed. Merging
            # keeps the event and adds the vout and holder this backfill exists to supply.
            with self.db.utxo_db.write_batch() as batch:
                for key, value in rows:
                    existing = self.db.utxo_db.get(key)
                    batch.put(key, GlyphIndex.merge_history_values(existing, value)
                              if existing else value)
                next_height += count
                batch.put(RefHistoryBackfillKeys.CURSOR, pack_be_uint32(next_height))

            if not warned and len(tracked) > WARN_TRACKED_OUTPOINTS:
                warned = True
                self.logger.warning(
                    'ref-history backfill is tracking %d live singleton outpoints; '
                    'memory will scale with this', len(tracked))

            # Progress at INFO on an interval. A DEBUG-only line means an operator watching a
            # multi-hour rescan at INFO sees nothing at all between "Starting" and "complete",
            # and has to read the checkpoint keys out of RocksDB to know it is alive.
            if next_height // PROGRESS_EVERY_BLOCKS != last_progress:
                last_progress = next_height // PROGRESS_EVERY_BLOCKS
                self.logger.info('Ref-history backfill: height %d/%d (%.1f%%), %d hops',
                                 next_height - 1, target,
                                 100.0 * next_height / max(target, 1), total)
            else:
                self.logger.debug('ref-history backfill: height %d/%d, %d hops',
                                  next_height - 1, target, total)
            await asyncio.sleep(0)

        with self.db.utxo_db.write_batch() as batch:
            batch.put(RefHistoryBackfillKeys.DONE, b'1')
            batch.delete(RefHistoryBackfillKeys.CURSOR)
        self.logger.info('Ref-history backfill complete: %d hops up to height %d',
                         total, target)
