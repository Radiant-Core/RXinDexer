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

    def iterator(self, prefix=b'', reverse=False):
        '''Return an iterator that yields (key, value) pairs from the
        database sorted by key.

        If `prefix` is set, only keys starting with `prefix` will be
        included.  If `reverse` is True the items are returned in
        reverse order.
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
        self.iterator = self.db.iterator
        self.write_batch = partial(self.db.write_batch, transaction=True,
                                   sync=True)


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
            write_buffer_size=_int('ROCKSDB_WRITE_BUFFER_SIZE', 67108864),
            max_write_buffer_number=_int('ROCKSDB_MAX_WRITE_BUFFER_NUMBER', 3),
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

    def iterator(self, prefix=b'', reverse=False, seek=None):
        return RocksDBIterator(self.db, prefix, reverse, seek=seek)


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
    '''An iterator for RocksDB.'''

    def __init__(self, db, prefix, reverse, seek=None):
        self.prefix = prefix
        if reverse:
            self.iterator = reversed(db.iteritems())
            nxt_prefix = util.increment_byte_string(prefix)
            if nxt_prefix:
                self.iterator.seek(nxt_prefix)
                try:
                    next(self.iterator)
                except StopIteration:
                    self.iterator.seek(nxt_prefix)
            else:
                self.iterator.seek_to_last()
        else:
            self.iterator = db.iteritems()
            # R16: if a cursor seek key is provided, use it (must be >= prefix)
            start = seek if (seek and seek >= prefix) else prefix
            self.iterator.seek(start)

    def __iter__(self):
        return self

    def __next__(self):
        k, v = next(self.iterator)
        if not k.startswith(self.prefix):
            raise StopIteration
        return k, v
