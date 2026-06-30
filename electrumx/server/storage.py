# Copyright (c) 2016-2017, the ElectrumX authors
#
# All rights reserved.
#
# See the file "LICENCE" for information about the copyright
# and warranty status of this software.

'''Backend database abstraction.'''

import os
from functools import partial

from electrumx.lib import util


def db_class(name):
    '''Returns a DB engine class.'''
    for db_class_ in util.subclasses(Storage):
        if db_class_.__name__.lower() == name.lower():
            db_class_.import_module()
            return db_class_
    raise RuntimeError('unrecognised DB engine "{}"'.format(name))


class Storage(object):
    '''Abstract base class of the DB backend abstraction.'''

    def __init__(self, name, for_sync):
        self.is_new = not os.path.exists(name)
        self.for_sync = for_sync or self.is_new
        self.open(name, create=self.is_new)

    @classmethod
    def import_module(cls):
        '''Import the DB engine module.'''
        raise NotImplementedError

    def open(self, name, create):
        '''Open an existing database or create a new one.'''
        raise NotImplementedError

    def close(self):
        '''Close an existing database.'''
        raise NotImplementedError

    def get(self, key):
        raise NotImplementedError

    def put(self, key, value):
        raise NotImplementedError

    def write_batch(self):
        '''Return a context manager that provides `put` and `delete`.

        Changes should only be committed when the context manager
        closes without an exception.
        '''
        raise NotImplementedError

    def iterator(self, prefix=b'', reverse=False, seek=None,
                 include_value=True):
        '''Return an iterator that yields (key, value) pairs from the
        database sorted by key — bare keys if `include_value` is false.

        If `prefix` is set, only keys starting with `prefix` will be
        included.  If `reverse` is True the items are returned in
        reverse order.

        `seek` is the cursor-pagination resume key (R16,
        docs/pagination-cursors.md): the next key to *serve*.  Ascending
        scans resume at the first key >= `seek`, descending scans at the
        last key <= `seek` — the cursor key itself is included in both
        directions.
        '''
        raise NotImplementedError

# pylint:disable=W0223


class LevelDB(Storage):
    '''LevelDB database engine.'''

    @classmethod
    def import_module(cls):
        import plyvel    # pylint:disable=E0401
        cls.module = plyvel

    def open(self, name, create):
        mof = 512 if self.for_sync else 128
        # Use snappy compression (the default)
        self.db = self.module.DB(name, create_if_missing=create,
                                 max_open_files=mof)
        self.close = self.db.close
        self.get = self.db.get
        self.put = self.db.put
        self.write_batch = partial(self.db.write_batch, transaction=True,
                                   sync=True)

    def iterator(self, prefix=b'', reverse=False, seek=None,
                 include_value=True):
        '''Prefix iterator with RocksDB-compatible cursor semantics.

        The cursor-pagination layer (R16) passes ``seek`` — the raw key to
        resume from — which plyvel's native iterator does not understand (and
        plyvel forbids combining ``prefix=`` with ``start``/``stop``), so the
        prefix is expressed as explicit [start, stop) bounds with the seek key
        clamped into them.  Without this every seek-using caller (dmint
        sync_from_index, cursor pagination) raises TypeError on the LevelDB
        engine.

        ``seek`` is the next key to *serve*, not the last key served
        (docs/pagination-cursors.md): ascending scans resume at the first
        key >= ``seek``; descending scans resume at the last key <=
        ``seek``.  The cursor key itself is therefore included in both
        directions, matching RocksDB's Seek/SeekForPrev positioning.
        '''
        kwargs = {'reverse': reverse, 'include_value': include_value}
        start = prefix
        stop = util.increment_byte_string(prefix) if prefix else None
        if seek and seek >= prefix:
            if reverse:
                if stop is None or seek < stop:
                    # Top of the range becomes the cursor key, inclusive —
                    # it has not been served yet.  A cursor at/above the
                    # prefix bound keeps the (exclusive) prefix bound.
                    stop = seek
                    kwargs['include_stop'] = True
            else:
                start = seek
        if start:
            kwargs['start'] = start
        if stop:
            kwargs['stop'] = stop
        return self.db.iterator(**kwargs)


# pylint:disable=E1101


class RocksDB(Storage):
    '''RocksDB database engine.'''

    def __init__(self, *args):
        self.db = None
        super().__init__(*args)

    @classmethod
    def import_module(cls):
        import rocksdb    # pylint:disable=E0401
        cls.module = rocksdb

    def open(self, name, create):
        env_name = os.environ.get('ELECTRUMX_ENV', 'dev').strip().lower()
        is_sync = self.for_sync

        # R23: read tuning env vars with per-env defaults
        def _int(var, default):
            try:
                return int(os.environ.get(var, default))
            except (ValueError, TypeError):
                return int(default)

        def _bool(var, default):
            v = os.environ.get(var, '').strip().lower()
            if not v:
                return default
            return v not in ('0', 'false', 'no')

        # max_open_files: more during sync, fewer while serving
        default_mof = 512 if is_sync else 256
        mof = _int('ROCKSDB_MAX_OPEN_FILES', default_mof)

        use_fsync = _bool('ROCKSDB_USE_FSYNC', env_name == 'prod')

        options = self.module.Options(
            create_if_missing=create,
            use_fsync=use_fsync,
            max_open_files=mof,
            target_file_size_base=_int('ROCKSDB_TARGET_FILE_SIZE_BASE', 33554432),
            write_buffer_size=_int('ROCKSDB_WRITE_BUFFER_SIZE', 134217728),
            max_write_buffer_number=_int('ROCKSDB_MAX_WRITE_BUFFER_NUMBER', 4),
            min_write_buffer_number_to_merge=_int('ROCKSDB_MIN_WRITE_BUFFER_NUMBER_TO_MERGE', 1),
            max_background_compactions=_int('ROCKSDB_MAX_BACKGROUND_COMPACTIONS', 4),
            max_background_flushes=_int('ROCKSDB_MAX_BACKGROUND_FLUSHES', 2),
        )

        # Block-based table options: bloom filter + block cache
        bloom_bits = _int('ROCKSDB_BLOOM_BITS_PER_KEY', 10)
        block_size = _int('ROCKSDB_BLOCK_SIZE', 4096)
        cache_mb = _int('ROCKSDB_BLOCK_CACHE_MB', 128)
        table_opts = self.module.BlockBasedTableFactory(
            filter_policy=self.module.BloomFilterPolicy(bloom_bits),
            block_cache=self.module.LRUCache(cache_mb * 1024 * 1024),
            block_size=block_size,
        )
        options.table_factory = table_opts

        # Compression (R23)
        compression_name = os.environ.get('ROCKSDB_COMPRESSION', 'lz4').strip().lower()
        _compression_map = {
            'none': self.module.CompressionType.no_compression,
            'snappy': self.module.CompressionType.snappy_compression,
            'lz4': self.module.CompressionType.lz4_compression,
            'zstd': self.module.CompressionType.zstd_compression,
            'zlib': self.module.CompressionType.zlib_compression,
        }
        options.compression = _compression_map.get(
            compression_name, self.module.CompressionType.lz4_compression
        )

        self.db = self.module.DB(name, options)
        self.get = self.db.get
        self.put = self.db.put

    def close(self):
        # R24: del self.db first so python-rocksdb destructor fires and closes
        # the underlying RocksDB handle before gc.collect() sweeps references.
        import gc
        db = self.db
        self.db = None
        self.get = None
        self.put = None
        del db
        gc.collect()

    def write_batch(self):
        return RocksDBWriteBatch(self.db)

    def iterator(self, prefix=b'', reverse=False, seek=None,
                 include_value=True):
        return RocksDBIterator(self.db, prefix, reverse, seek=seek,
                               include_value=include_value)


class RocksDBWriteBatch(object):
    '''A write batch for RocksDB.'''

    def __init__(self, db):
        self.batch = RocksDB.module.WriteBatch()
        self.db = db

    def __enter__(self):
        return self.batch

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not exc_val:
            self.db.write(self.batch)


class RocksDBIterator(object):
    '''An iterator for RocksDB.

    Yields (key, value) pairs — or bare keys when ``include_value`` is
    False, matching plyvel — for keys starting with ``prefix``, ascending
    or, with ``reverse``, descending.

    ``seek`` is the cursor-pagination resume key (R16,
    docs/pagination-cursors.md): the next key to *serve*.  Ascending scans
    resume at the first key >= ``seek``; descending scans at the last key
    <= ``seek``, so the cursor key itself is included in both directions.

    Positioning note: ``seek()`` on python-rocksdb's ReversedIterator is
    the plain RocksDB ``Seek`` (first key >= target) — only the step
    direction of subsequent iteration is reversed — so a descending scan
    must park via ``seek_for_prev`` (or emulate it) rather than ``seek``,
    which would start the page *above* the cursor and re-serve it.
    '''

    def __init__(self, db, prefix, reverse, seek=None, include_value=True):
        self.prefix = prefix
        source = db.iteritems() if include_value else db.iterkeys()
        if reverse:
            self.iterator = reversed(source)
            nxt_prefix = util.increment_byte_string(prefix)
            if seek and seek >= prefix and \
                    (nxt_prefix is None or seek < nxt_prefix):
                self._park_at_or_below(seek)
            elif nxt_prefix:
                self._park_below(nxt_prefix)
            else:
                # prefix is empty or all-0xff: nothing can sort above it.
                self.iterator.seek_to_last()
        else:
            self.iterator = source
            # R16: if a cursor seek key is provided, use it (must be >= prefix)
            start = seek if (seek and seek >= prefix) else prefix
            self.iterator.seek(start)

    @staticmethod
    def _key_of(entry):
        '''Key of an iteritems tuple or an iterkeys bare key.'''
        return entry[0] if isinstance(entry, tuple) else entry

    def _park_at_or_below(self, target):
        '''Position the reversed iterator on the largest key <= target.'''
        it = self.iterator
        if hasattr(it, 'seek_for_prev'):    # python-rocksdb >= 0.7
            it.seek_for_prev(target)
            return
        # Older python-rocksdb: Seek() parks on the first key >= target,
        # or invalidates when there is none.
        it.seek(target)
        try:
            key = self._key_of(next(it))    # yields current, steps down
        except StopIteration:
            # No key >= target, so the DB's last key (if any) is < target.
            it.seek_to_last()
            return
        if key == target:
            # The peek consumed an exact hit; re-park on it.
            it.seek(target)

    def _park_below(self, bound):
        '''Position the reversed iterator on the largest key < bound.'''
        it = self.iterator
        if not hasattr(it, 'seek_for_prev'):
            # Older python-rocksdb: park on the first key >= bound and
            # step down once.  When no key >= bound exists, the DB's last
            # key is already < bound.
            it.seek(bound)
            try:
                next(it)
            except StopIteration:
                it.seek_to_last()
            return
        it.seek_for_prev(bound)
        try:
            key = self._key_of(next(it))
        except StopIteration:
            return    # no key <= bound at all: leave exhausted
        if key != bound:
            # The peek consumed an in-range key; re-park on it.
            it.seek_for_prev(key)

    def __iter__(self):
        return self

    def __next__(self):
        entry = next(self.iterator)
        if not self._key_of(entry).startswith(self.prefix):
            raise StopIteration
        return entry
