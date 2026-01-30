# Copyright (c) 2016-2018, Neil Booth
#
# All rights reserved.
#
# See the file "LICENCE" for information about the copyright
# and warranty status of this software.

'''Mempool handling.'''

import itertools
import time
from abc import ABC, abstractmethod
from collections import defaultdict

import attr
from aiorpcx import TaskGroup, run_in_thread, sleep

from electrumx.lib.hash import hash_to_hex_str, hex_str_to_hash
from electrumx.lib.util import class_logger, chunks, pack_le_uint32, unpack_le_uint32_from
from electrumx.lib.script import Script
from electrumx.server.db import UTXO

# Import mempool Glyph index (optional)
try:
    from electrumx.server.mempool_glyph import MempoolGlyphIndex
    HAS_MEMPOOL_GLYPH = True
except ImportError:
    HAS_MEMPOOL_GLYPH = False
    MempoolGlyphIndex = None


@attr.s(slots=True)
class MemPoolTx(object):
    prevouts = attr.ib()
    # A pair is a (hashX, value) tuple
    in_pairs = attr.ib()
    out_pairs = attr.ib()
    fee = attr.ib()
    size = attr.ib()
    out_srefs = attr.ib()
    idx_to_script = attr.ib()   # Track full output script at given output index

@attr.s(slots=True)
class MemPoolTxSummary(object):
    hash = attr.ib()
    fee = attr.ib()
    has_unconfirmed_inputs = attr.ib()


class DBSyncError(Exception):
    pass


class MemPoolAPI(ABC):
    '''A concrete instance of this class is passed to the MemPool object
    and used by it to query DB and blockchain state.'''

    @abstractmethod
    async def height(self):
        '''Query bitcoind for its height.'''

    @abstractmethod
    def cached_height(self):
        '''Return the height of bitcoind the last time it was queried,
        for any reason, without actually querying it.
        '''

    @abstractmethod
    def db_height(self):
        '''Return the height flushed to the on-disk DB.'''

    @abstractmethod
    async def mempool_hashes(self):
        '''Query bitcoind for the hashes of all transactions in its
        mempool, returned as a list.'''

    @abstractmethod
    async def raw_transactions(self, hex_hashes):
        '''Query bitcoind for the serialized raw transactions with the given
        hashes.  Missing transactions are returned as None.

        hex_hashes is an iterable of hexadecimal hash strings.'''

    @abstractmethod
    async def lookup_utxos(self, prevouts):
        '''Return a list of (hashX, value) pairs each prevout if unspent,
        otherwise return None if spent or not found.

        prevouts - an iterable of (hash, index) pairs
        '''

    @abstractmethod
    async def on_mempool(self, touched, height):
        '''Called each time the mempool is synchronized.  touched is a set of
        hashXs touched since the previous call.  height is the
        daemon's height at the time the mempool was obtained.'''


class MemPool(object):
    '''Representation of the daemon's mempool.

        coin - a coin class from coins.py
        api - an object implementing MemPoolAPI

    Updated regularly in caught-up state.  Goal is to enable efficient
    response to the calls in the external interface.  To that end we
    maintain the following maps:

       tx:     tx_hash -> MemPoolTx
       hashXs: hashX   -> set of all hashes of txs touching the hashX
    '''

    def __init__(self, coin, api, refresh_secs=5.0, log_status_secs=60.0,
                 env=None, glyph_index=None, swap_index=None):
        assert isinstance(api, MemPoolAPI)
        self.coin = coin
        self.api = api
        self.logger = class_logger(__name__, self.__class__.__name__)
        self.txs = {}
        self.hashXs = defaultdict(set)              # None can be a key
        self.outpointToRefs = defaultdict(set)      # None can be a key
        self.codeScriptHashes = defaultdict(set)    # None can be a key
        self.srefs = defaultdict(list) # Ordered list to keep track of first and last srefs transactions
        self.refresh_secs = refresh_secs
        self.log_status_secs = log_status_secs
        
        # Initialize Glyph mempool index if available and enabled
        self.glyph_mempool = None
        if HAS_MEMPOOL_GLYPH and env:
            mempool_glyph_enabled = getattr(env, 'mempool_glyph_index', True)
            mempool_swap_enabled = getattr(env, 'mempool_swap_index', True)
            if mempool_glyph_enabled or mempool_swap_enabled:
                self.glyph_mempool = MempoolGlyphIndex(
                    env, glyph_index=glyph_index, swap_index=swap_index
                )
                self.logger.info('Mempool Glyph/Swap indexing initialized')

    async def _logging(self, synchronized_event):
        '''Print regular logs of mempool stats.'''
        self.logger.info('beginning processing of daemon mempool.  '
                         'This can take some time...')
        start = time.monotonic()
        await synchronized_event.wait()
        elapsed = time.monotonic() - start
        self.logger.info(f'synced in {elapsed:.2f}s')
        while True:
            mempool_size = sum(tx.size for tx in self.txs.values()) / 1_000_000
            glyph_stats = ''
            if self.glyph_mempool:
                stats = self.glyph_mempool.stats()
                glyph_stats = f' | glyph: {stats["glyph_txs"]} txs, swap: {stats["swap_orders"]} orders'
            self.logger.info(f'{len(self.txs):,d} txs {mempool_size:.2f} MB '
                             f'touching {len(self.hashXs):,d} addresses{glyph_stats}')
            await sleep(self.log_status_secs)
            await synchronized_event.wait()

    def _accept_transactions(self, tx_map, utxo_map, touched):
        '''Accept transactions in tx_map to the mempool if all their inputs
        can be found in the existing mempool or a utxo_map from the
        DB.

        Returns an (unprocessed tx_map, unspent utxo_map) pair.
        '''
        hashXs = self.hashXs
        outpointToRefs = self.outpointToRefs
        codeScriptHashes = self.codeScriptHashes # not used yet
        srefs = self.srefs
        txs = self.txs
        to_le_uint32 = pack_le_uint32

        deferred = {}
        unspent = set(utxo_map)
        # Try to find all prevouts so we can accept the TX
        for tx_hash, tx in tx_map.items():
            in_pairs = []
            try:
                for prevout in tx.prevouts:
                    utxo = utxo_map.get(prevout)
                    if not utxo:
                        prev_hash, prev_index = prevout
                        # Raises KeyError if prev_hash is not in txs
                        utxo = txs[prev_hash].out_pairs[prev_index]
                    in_pairs.append(utxo)
            except KeyError:
                deferred[tx_hash] = tx
                continue

            # Spend the prevouts
            unspent.difference_update(tx.prevouts)

            # Save the in_pairs, compute the fee and accept the TX
            tx.in_pairs = tuple(in_pairs)
            # Avoid negative fees if dealing with generation-like transactions
            # because some in_parts would be missing
            tx.fee = max(0, (sum(v for _, v in tx.in_pairs) -
                             sum(v for _, v in tx.out_pairs)))
            txs[tx_hash] = tx

            for hashX, _value in itertools.chain(tx.in_pairs, tx.out_pairs):
                touched.add(hashX)
                hashXs[hashX].add(tx_hash)

            for ref_hashes in tx.out_srefs:
                if ref_hashes:
                    for ref_hash in ref_hashes:
                        touched.add(ref_hash)
                        hashXs[ref_hash].add(tx_hash)
                        if tx_hash not in srefs[ref_hash]:
                            srefs[ref_hash].append(tx_hash)

            # Check every output script for refs to build up the outpointToRefs map for quickly enumering which refs
            # are associated with each outpoint for the purposes of returning refs for unconfirmed utxos in mempool
            out_idx = 0
            for pk_script in tx.idx_to_script:
                all_refs, normal_refs, singleton_refs = Script.get_push_input_refs(pk_script)
                all_refs_dedup = Script.dedup_refs(all_refs)
                normal_refs_dedup = Script.dedup_refs(normal_refs)
                singleton_refs_dedup = Script.dedup_refs(singleton_refs)
                # Save all the refs if any for the utxo
                refs_value = b''
                for ref_id in all_refs_dedup.keys():
                    enc_ref_type = 0
                    # check if it's a singleton ref by first ensuring it's not in the normal refs map
                    if not normal_refs_dedup.get(ref_id):
                        assert singleton_refs_dedup.get(ref_id)
                        enc_ref_type = 1
                    refs_value += ref_id + (enc_ref_type).to_bytes(1, "little")
                # cache the refs for the outpoint
                if len(refs_value):
                    outpointToRefs[tx_hash + to_le_uint32(out_idx)] = refs_value
                out_idx += 1 

        return deferred, {prevout: utxo_map[prevout] for prevout in unspent}

    async def _refresh_hashes(self, synchronized_event):
        '''Refresh our view of the daemon's mempool.'''
        # Touched accumulates between calls to on_mempool and each
        # call transfers ownership
        touched = set()
        while True:
            height = self.api.cached_height()
            hex_hashes = await self.api.mempool_hashes()
            if height != await self.api.height():
                continue
            hashes = set(hex_str_to_hash(hh) for hh in hex_hashes)
            try:
                await self._process_mempool(hashes, touched, height)
            except DBSyncError:
                # The UTXO DB is not at the same height as the
                # mempool; wait and try again
                self.logger.debug('waiting for DB to sync')
            else:
                synchronized_event.set()
                synchronized_event.clear()
                await self.api.on_mempool(touched, height)
                touched = set()
            await sleep(self.refresh_secs)

    async def _process_mempool(self, all_hashes, touched, mempool_height):
        # Re-sync with the new set of hashes
        txs = self.txs
        hashXs = self.hashXs
        codeScriptHashes = self.codeScriptHashes # not used yet
        outpointToRefs = self.outpointToRefs
        to_le_uint32 = pack_le_uint32
        srefs = self.srefs

        if mempool_height != self.api.db_height():
            raise DBSyncError

        # First handle txs that have disappeared
        for tx_hash in set(txs).difference(all_hashes):
            tx = txs.pop(tx_hash)
            tx_hashXs = set(hashX for hashX, value in tx.in_pairs)
            tx_hashXs.update(hashX for hashX, value in tx.out_pairs)
            tx_hashXs.update(ref_hash for ref_hashes in tx.out_srefs for ref_hash in ref_hashes)
            for hashX in tx_hashXs:
                hashXs[hashX].remove(tx_hash)
                if not hashXs[hashX]:
                    del hashXs[hashX]
                if tx_hash in srefs[hashX]:
                    srefs[hashX].remove(tx_hash)
                if not srefs[hashX]:
                    del srefs[hashX]
            touched.update(tx_hashXs)

            # Handle the outpoints that have disappeared from the mempool to remove the entries in outpointToRefs
            # This maintains the outpointToRefs to always contain the unconfirmed mempool outpoints which contain refs
            out_idx = 0
            for _pk_script in enumerate(tx.idx_to_script):
                outpointToRefs.pop(tx_hash + to_le_uint32(out_idx), None)
                out_idx += 1
                  
        # Process new transactions
        new_hashes = list(all_hashes.difference(txs))
        if new_hashes:
            group = TaskGroup()
            for hashes in chunks(new_hashes, 200):
                coro = self._fetch_and_accept(hashes, all_hashes, touched)
                await group.spawn(coro)

            tx_map = {}
            utxo_map = {}
            async for task in group:
                deferred, unspent = task.result()
                tx_map.update(deferred)
                utxo_map.update(unspent)

            prior_count = 0
            # FIXME: this is not particularly efficient
            while tx_map and len(tx_map) != prior_count:
                prior_count = len(tx_map)
                tx_map, utxo_map = self._accept_transactions(tx_map, utxo_map,
                                                             touched)
            if tx_map:
                self.logger.error(f'{len(tx_map)} txs dropped')

        return touched

    async def _fetch_and_accept(self, hashes, all_hashes, touched):
        '''Fetch a list of mempool transactions.'''
        hex_hashes_iter = (hash_to_hex_str(hash) for hash in hashes)
        raw_txs = await self.api.raw_transactions(hex_hashes_iter)

        def deserialize_txs():    # This function is pure
            to_hashX = self.coin.hashX_from_script
            deserializer = self.coin.DESERIALIZER

            txs = {}
            for tx_hash, raw_tx in zip(hashes, raw_txs):
                # The daemon may have evicted the tx from its
                # mempool or it may have gotten in a block
                if not raw_tx:
                    continue
                tx, tx_size = deserializer(raw_tx).read_tx_and_vsize()
                # Convert the inputs and outputs into (hashX, value) pairs
                # Drop generation-like inputs from MemPoolTx.prevouts
                txin_pairs = tuple((txin.prev_hash, txin.prev_idx)
                                   for txin in tx.inputs
                                   if not txin.is_generation())
                txout_pairs = tuple((to_hashX(Script.zero_refs(txout.pk_script)), txout.value)
                                    for txout in tx.outputs)

                out_srefs = []
                out_idx_to_scripts = []
                for txout in tx.outputs:
                    out_idx_to_scripts.append(txout.pk_script)
                    normal_refs, singleton_refs = Script.get_push_input_refs(txout.pk_script)[1:]

                    normal_mints = []
                    for ref in normal_refs[0:3]:
                        for txin in tx.inputs:
                            if txin.prev_hash == ref[:32] and pack_le_uint32(txin.prev_idx) == ref[32:]:
                                normal_mints.append(ref)

                    # Track all refs
                    track_refs = normal_mints + singleton_refs

                    if len(track_refs) > 0:
                        ref_hashes = [to_hashX(ref) for ref in track_refs]
                        out_srefs.append(ref_hashes)
                    else:
                        out_srefs.append([])

                txs[tx_hash] = MemPoolTx(txin_pairs, None, txout_pairs,
                                         0, tx_size, out_srefs, out_idx_to_scripts)

            return txs

        # Thread this potentially slow operation so as not to block
        tx_map = await run_in_thread(deserialize_txs)

        # Determine all prevouts not in the mempool, and fetch the
        # UTXO information from the database.  Failed prevout lookups
        # return None - concurrent database updates happen - which is
        # relied upon by _accept_transactions. Ignore prevouts that are
        # generation-like.
        prevouts = tuple(prevout for tx in tx_map.values()
                         for prevout in tx.prevouts
                         if prevout[0] not in all_hashes)
        utxos = await self.api.lookup_utxos(prevouts)
        utxo_map = {prevout: utxo for prevout, utxo in zip(prevouts, utxos)}

        return self._accept_transactions(tx_map, utxo_map, touched)

    #
    # External interface
    #

    def get_refs_by_outpoint(self, outpoint):
        # Format outpoint to a string utility function
        def outpoint_to_str(outpoint):
            num, = unpack_le_uint32_from(outpoint[32:])
            return f'{hash_to_hex_str(outpoint[:32])}i{num}'
        
        # Get from cache if present
        value = self.outpointToRefs.get(outpoint, None)
        if not value:
            return []
    
        refs = []
        for x in range(0, len(value), 37):
            ref_id = outpoint_to_str(value[x : x + 36])
            type_byte = value[x + 36: x + 37]
            ref_type = 'normal'
            if type_byte == (0).to_bytes(1, "little"):
                ref_type = 'normal'
            elif type_byte == (1).to_bytes(1, "little"):
                ref_type = 'single'
            else: 
                raise IndexError(f'fatal unexpected ref type byte')
            refs.append({
                'ref': ref_id,
                'type': ref_type
            })
        return refs
    
    async def keep_synchronized(self, synchronized_event):
        '''Keep the mempool synchronized with the daemon.'''
        async with TaskGroup() as group:
            await group.spawn(self._refresh_hashes(synchronized_event))
            await group.spawn(self._logging(synchronized_event))

            async for task in group:
                if not task.cancelled():
                    task.result()

    async def balance_delta(self, hashX):
        '''Return the unconfirmed amount in the mempool for hashX.

        Can be positive or negative.
        '''
        value = 0
        if hashX in self.hashXs:
            for hash_ in self.hashXs[hashX]:
                tx = self.txs[hash_]
                value -= sum(v for h168, v in tx.in_pairs if h168 == hashX)
                value += sum(v for h168, v in tx.out_pairs if h168 == hashX)
        return value

    async def potential_spends(self, hashX):
        '''Return a set of (prev_hash, prev_idx) pairs from mempool
        transactions that touch hashX.

        None, some or all of these may be spends of the hashX, but all
        actual spends of it (in the DB or mempool) will be included.
        '''
        result = set()
        for tx_hash in self.hashXs.get(hashX, ()):
            tx = self.txs[tx_hash]
            result.update(tx.prevouts)
        return result

    async def codescripthash_potential_spends(self, codeScriptHash):
        """
        Return a set of (prev_hash, prev_idx) pairs from mempool
        transactions that touch codeScriptHash.

        None, some or all of these may be spends of the codeScriptHash, but all
        actual spends of it (in the DB or mempool) will be included.
        
        This scans mempool transactions to find inputs that might be spending
        UTXOs with the given codeScriptHash.
        """
        result = set()
        script_codeScriptHash = self.coin.codeScriptHash_from_script
        
        # For each transaction in mempool, check if any of its inputs
        # might be spending a UTXO with the matching codeScriptHash
        for tx_hash, tx in self.txs.items():
            # Check if any output of this tx has the codeScriptHash
            # (meaning this tx creates UTXOs with this codeScriptHash)
            has_matching_output = False
            for pk_script in tx.idx_to_script:
                if pk_script is None:
                    continue
                try:
                    output_csh = script_codeScriptHash(pk_script)
                    if output_csh == codeScriptHash:
                        has_matching_output = True
                        break
                except Exception:
                    continue
            
            # If this tx touches the codeScriptHash (via outputs),
            # then its prevouts are potential spends
            if has_matching_output:
                result.update(tx.prevouts)
            
            # Also check: does any input of this tx spend a mempool UTXO
            # that has the matching codeScriptHash?
            for prev_hash, prev_idx in tx.prevouts:
                prev_tx = self.txs.get(prev_hash)
                if prev_tx and prev_idx < len(prev_tx.idx_to_script):
                    prev_script = prev_tx.idx_to_script[prev_idx]
                    if prev_script is not None:
                        try:
                            prev_csh = script_codeScriptHash(prev_script)
                            if prev_csh == codeScriptHash:
                                result.add((prev_hash, prev_idx))
                        except Exception:
                            continue
        
        return result
    
    async def transaction_summaries(self, hashX):
        '''Return a list of MemPoolTxSummary objects for the hashX.'''
        result = []
        for tx_hash in self.hashXs.get(hashX, ()):
            tx = self.txs[tx_hash]
            has_ui = any(hash in self.txs for hash, idx in tx.prevouts)
            result.append(MemPoolTxSummary(tx_hash, tx.fee, has_ui))
        return result

    async def unordered_UTXOs(self, hashX):
        '''Return an unordered list of UTXO named tuples from mempool
        transactions that pay to hashX.

        This does not consider if any other mempool transactions spend
        the outputs.
        '''
        utxos = []
        for tx_hash in self.hashXs.get(hashX, ()):
            tx = self.txs.get(tx_hash)
            for pos, (hX, value) in enumerate(tx.out_pairs):
                if hX == hashX:
                    utxos.append(UTXO(-1, pos, tx_hash, 0, value))
        return utxos
    
    async def codescripthash_unordered_UTXOs(self, codeScriptHash):
        """
        Return an unordered list of UTXO named tuples from mempool
        transactions that have outputs matching the given codeScriptHash.
        
        codeScriptHash is the SHA256 hash of the code portion of a script
        (everything after the state separator).
        
        This does not consider if any other mempool transactions spend
        the outputs.
        """
        utxos = []
        script_codeScriptHash = self.coin.codeScriptHash_from_script
        
        for tx_hash, tx in self.txs.items():
            # Check each output's script
            for pos, pk_script in enumerate(tx.idx_to_script):
                if pk_script is None:
                    continue
                try:
                    output_codeScriptHash = script_codeScriptHash(pk_script)
                    if output_codeScriptHash == codeScriptHash:
                        # Get value from out_pairs at this position
                        if pos < len(tx.out_pairs):
                            _, value = tx.out_pairs[pos]
                            utxos.append(UTXO(-1, pos, tx_hash, 0, value))
                except Exception:
                    # Skip outputs that can't be processed
                    continue
        
        return utxos

    async def first_last_summaries(self, hashX):
        '''Return first and last UTXO tuples from mempool transactions that contain a singleton ref hash.'''
        tx_hashes = self.srefs.get(hashX, [])
        if not tx_hashes:
            return []

        result = []
        # Get first and last, remove duplicate if there is only 1
        for tx_hash in list(dict.fromkeys([tx_hashes[0], tx_hashes[-1]])):
            tx = self.txs[tx_hash]
            has_ui = any(hash in self.txs for hash, idx in tx.prevouts)
            result.append(MemPoolTxSummary(tx_hash, tx.fee, has_ui))

        return result
