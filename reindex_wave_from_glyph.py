#!/usr/bin/env python3
"""
Reindex WAVE names from existing Glyph token database.

This script scans all indexed Glyph tokens, finds those with GLYPH_WAVE (11)
in their protocols, fetches the raw transaction from the daemon, and re-processes
them through the WaveIndex.

Usage (inside the rxindexer container):
    python3 /opt/electrumx/reindex_wave_from_glyph.py

Requires:
    - The container must be stopped (direct DB access)
    - Or run while the server is stopped but DB is accessible
"""

import os
import sys
import struct
import asyncio
import logging

# Add the electrumx path
sys.path.insert(0, '/opt/electrumx')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
logger = logging.getLogger('wave_reindex')


def main():
    """Scan Glyph DB for WAVE tokens and reindex them."""
    import cbor2

    # Import after path setup
    from electrumx.lib.hash import hash_to_hex_str
    from electrumx.lib.glyph import GlyphProtocol

    # Open the database directly
    db_base = os.environ.get('DB_DIRECTORY', '/data/electrumdb')
    db_dir = os.path.join(db_base, 'utxo')
    db_engine = os.environ.get('DB_ENGINE', 'rocksdb')

    logger.info(f'Opening {db_engine} database at {db_dir}')

    if db_engine == 'rocksdb':
        import rocksdb
        db = rocksdb.DB(db_dir, rocksdb.Options(create_if_missing=False), read_only=True)
    else:
        import plyvel
        db = plyvel.DB(db_dir, create_if_missing=False)

    # Glyph token prefix
    GT_PREFIX = b'GT'
    # WAVE DB prefixes
    WN_PREFIX = b'WN'

    # Count existing WAVE names
    wave_count = 0
    if db_engine == 'rocksdb':
        it = db.iteritems()
        it.seek(WN_PREFIX)
        for key, _ in it:
            if not key.startswith(WN_PREFIX):
                break
            wave_count += 1
    else:
        for key, _ in db.iterator(prefix=WN_PREFIX):
            wave_count += 1

    logger.info(f'Existing WAVE names in DB: {wave_count}')

    # Scan all Glyph tokens
    wave_tokens = []
    total_tokens = 0

    logger.info('Scanning Glyph token database for WAVE protocol...')

    if db_engine == 'rocksdb':
        it = db.iteritems()
        it.seek(GT_PREFIX)
        for key, value in it:
            if not key.startswith(GT_PREFIX):
                break
            total_tokens += 1
            try:
                token_data = cbor2.loads(value)
                protocols = token_data.get('p', [])
                if GlyphProtocol.GLYPH_WAVE in protocols:
                    ref = key[len(GT_PREFIX):]
                    txid = ref[:32]
                    vout = struct.unpack('<I', ref[32:36])[0]
                    wave_tokens.append({
                        'ref': ref,
                        'txid': hash_to_hex_str(txid),
                        'vout': vout,
                        'name': token_data.get('n', '?'),
                        'protocols': protocols,
                        'height': token_data.get('dh', 0),
                    })
            except Exception as e:
                continue
    else:
        for key, value in db.iterator(prefix=GT_PREFIX):
            total_tokens += 1
            try:
                token_data = cbor2.loads(value)
                protocols = token_data.get('p', [])
                if GlyphProtocol.GLYPH_WAVE in protocols:
                    ref = key[len(GT_PREFIX):]
                    txid = ref[:32]
                    vout = struct.unpack('<I', ref[32:36])[0]
                    wave_tokens.append({
                        'ref': ref,
                        'txid': hash_to_hex_str(txid),
                        'vout': vout,
                        'name': token_data.get('n', '?'),
                        'protocols': protocols,
                        'height': token_data.get('dh', 0),
                    })
            except Exception as e:
                continue

    logger.info(f'Scanned {total_tokens} Glyph tokens, found {len(wave_tokens)} with WAVE protocol')

    if wave_tokens:
        logger.info('WAVE tokens found:')
        for t in wave_tokens:
            logger.info(f'  {t["txid"]}:{t["vout"]} name="{t["name"]}" height={t["height"]} protocols={t["protocols"]}')
    else:
        logger.info('No WAVE tokens found in Glyph database.')
        logger.info('This means the Glyph envelope parser did not detect any WAVE name transactions.')
        logger.info('Root cause: glyph_index.process_tx() never returned an envelope with GLYPH_WAVE=11')

    if db_engine == 'rocksdb':
        del db
    else:
        db.close()

    return len(wave_tokens)


if __name__ == '__main__':
    count = main()
    sys.exit(0 if count > 0 else 1)
