"""
Tests for the Canon declaration index (scriptSig scanner).

The properties that matter:
  * only SCHEMA-VALID documents are indexed, and signature validity never gates indexing;
  * ``sig_valid=None`` (unchecked) is distinct from ``False`` (bad) — conflating them would drop
    valid declarations on a node without the optional coincurve dependency;
  * the earliest reveal of a doc_hash wins;
  * rows come back height-ascending.

Includes the real document the user supplied, so the schema check is exercised against production
bytes rather than only synthetic ones.

Run: PYTHONPATH=. python3 -m pytest tests/test_declaration_index.py
"""
import json
import os
import struct
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from electrumx.server.declaration_index import (  # noqa: E402
    CANON_MAGIC, DeclarationDBKeys, DeclarationIndex,
    MESSAGE_MAGIC, parse_declaration, read_pushes, verify_signmessage,
    signature_verification_enabled, _signer_hash,
)
from electrumx.lib.hash import sha256  # noqa: E402

# The document from canon-declaration-14XmXG3d.json, verbatim.
REAL_DOC = {
    "format": "canon-declaration",
    "version": 1,
    "network": "radiant-mainnet",
    "signer": "14XmXG3dSBWZUukGT3xzS9zxpiZ53vgx1i",
    "declares": [{
        "kind": "creator",
        "ref": "262cd460c405e62fcd1c06a41c7707d269faa98c79209efc43862ce72926a44b00000000",
        "label": "CraigD Profile",
    }],
    "issuedAt": "2026-09-02T14:43:29.677Z",
    "expiresAt": "2027-12-31T00:00:00.000Z",
    "signature": ("IAkHAxMngzoZnWfdK8hpknF+3+R6Zck/ogCnqOCxhq8CGgcMgcZaa0jWACDm7"
                  "RaerQNoHUCTwOqcX2vaNiUz1qc="),
}


def doc_bytes(doc=None) -> bytes:
    return json.dumps(doc if doc is not None else REAL_DOC).encode('utf-8')


def push_bytes(data: bytes) -> bytes:
    """Minimal-ish push encoding for building a scriptSig."""
    if len(data) <= 0x4b:
        return bytes([len(data)]) + data
    if len(data) <= 0xff:
        return bytes([0x4c, len(data)]) + data
    return bytes([0x4d]) + struct.pack('<H', len(data)) + data


def canon_push(doc=None) -> bytes:
    return push_bytes(CANON_MAGIC + doc_bytes(doc))


# --------------------------------------------------------------------------- parsing

def test_real_document_parses():
    out = parse_declaration(CANON_MAGIC + doc_bytes())
    assert out is not None
    assert out['signer'] == REAL_DOC['signer']
    assert out['network'] == 'radiant-mainnet'
    assert len(out['entries']) == 1
    e = out['entries'][0]
    assert e['kind'] == 'creator'
    assert e['ref'].hex() == REAL_DOC['declares'][0]['ref']
    assert e['action'] == 'declare', 'action defaults to declare when absent'
    assert e['label'] == 'CraigD Profile'
    assert out['issued_at'] > 0 and out['expires_at'] > out['issued_at']


def test_doc_hash_is_over_the_pushed_body():
    out = parse_declaration(CANON_MAGIC + doc_bytes())
    assert out['doc_hash'] == sha256(doc_bytes())


def test_revoke_action_accepted_and_bad_action_rejected():
    d = json.loads(json.dumps(REAL_DOC))
    d['declares'][0]['action'] = 'revoke'
    assert parse_declaration(CANON_MAGIC + doc_bytes(d))['entries'][0]['action'] == 'revoke'
    d['declares'][0]['action'] = 'delete'
    assert parse_declaration(CANON_MAGIC + doc_bytes(d)) is None


def test_schema_violations_are_not_indexed():
    def mutated(**changes):
        d = json.loads(json.dumps(REAL_DOC))
        d.update(changes)
        return parse_declaration(CANON_MAGIC + doc_bytes(d))

    assert mutated(format='something-else') is None
    assert mutated(version=3) is None          # beyond SUPPORTED_VERSIONS
    assert mutated(version='1') is None        # string, not int
    assert mutated(version=None) is None
    assert mutated(signer='') is None
    assert mutated(network='') is None
    assert mutated(signature='') is None
    assert mutated(declares=[]) is None
    assert mutated(declares='not-a-list') is None


def test_bad_ref_lengths_rejected():
    d = json.loads(json.dumps(REAL_DOC))
    d['declares'][0]['ref'] = 'ab' * 32       # 64 hex, not 72
    assert parse_declaration(CANON_MAGIC + doc_bytes(d)) is None
    d['declares'][0]['ref'] = 'zz' * 36
    assert parse_declaration(CANON_MAGIC + doc_bytes(d)) is None


def test_non_declarations_are_ignored():
    assert parse_declaration(b'') is None
    assert parse_declaration(b'short') is None
    assert parse_declaration(b'xxxx' + doc_bytes()) is None          # wrong magic
    assert parse_declaration(CANON_MAGIC + b'not json' * 8) is None


def test_oversized_document_rejected():
    d = json.loads(json.dumps(REAL_DOC))
    d['declares'][0]['label'] = 'x' * 200     # label cap is 256 bytes
    assert parse_declaration(CANON_MAGIC + doc_bytes(d)) is not None
    d['declares'][0]['label'] = 'x' * 300
    assert parse_declaration(CANON_MAGIC + doc_bytes(d)) is None


def test_read_pushes_finds_the_document_in_a_scriptsig():
    script = push_bytes(b'\x30' * 71) + push_bytes(b'\x02' * 33) + canon_push()
    pushes = read_pushes(script)
    assert any(p.startswith(CANON_MAGIC) for p in pushes)
    assert len(pushes) == 3


# --------------------------------------------------------------------------- signatures

def test_message_magic_matches_the_node():
    # Radiant-Core src/validation.cpp: strMessageMagic
    assert MESSAGE_MAGIC == b'Bitcoin Signed Message:\n'


def test_unchecked_is_none_not_false():
    """The distinction the whole trust model rests on: without an EC backend the answer is
    'unknown', and must never be reported as 'invalid'."""
    if signature_verification_enabled():
        import pytest
        pytest.skip('coincurve present; the unchecked path cannot be observed here')
    got = verify_signmessage(REAL_DOC['signer'], doc_bytes(),
                             REAL_DOC['signature'], b'\x00')
    assert got is None, 'no coincurve must yield None (unchecked), not False'


def test_structurally_bad_signature_is_false_not_none():
    # These are decided before any EC work, so they are answerable either way.
    assert verify_signmessage(REAL_DOC['signer'], doc_bytes(), 'not-base64!!', b'\x00') is False
    assert verify_signmessage(REAL_DOC['signer'], doc_bytes(),
                              'AAAA', b'\x00') is False        # wrong length


# --------------------------------------------------------------------------- index behaviour

class _Batch:
    def __init__(self, store):
        self.store = store

    def put(self, key, value):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _StubUtxoDB:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def write_batch(self):
        return _Batch(self.store)

    def iterator(self, prefix=b'', seek=None, reverse=False):
        keys = sorted(k for k in self.store if k.startswith(prefix))
        if seek is not None:
            keys = [k for k in keys if k >= seek]
        for key in keys:
            yield key, self.store[key]


class _Env:
    declaration_index = True
    declaration_verify_signatures = False      # keep tests independent of coincurve
    reorg_limit = 200
    coin = SimpleNamespace(P2PKH_VERBYTE=b'\x00')


class _In:
    def __init__(self, script, generation=False):
        self.script = script
        self._gen = generation

    def is_generation(self):
        return self._gen


class _Tx:
    def __init__(self, inputs):
        self.inputs = inputs


def _index():
    db = SimpleNamespace(utxo_db=_StubUtxoDB(), db_height=1000)
    return DeclarationIndex(db, _Env())


def _flush(idx):
    with idx.db.utxo_db.write_batch() as batch:
        idx.flush(batch)


def _mark(idx, txid_byte, height, tx_index=0, doc=None):
    tx = _Tx([_In(canon_push(doc))])
    idx.process_tx(bytes([txid_byte]) * 32, tx, height, tx_index)


def test_declaration_is_indexed_from_an_input():
    idx = _index()
    _mark(idx, 0x11, 500)
    _flush(idx)
    store = idx.db.utxo_db.store
    assert any(k.startswith(DeclarationDBKeys.DOC) for k in store)
    assert any(k.startswith(DeclarationDBKeys.BY_REF) for k in store)
    assert any(k.startswith(DeclarationDBKeys.BY_SIGNER) for k in store)


def test_coinbase_inputs_are_skipped():
    idx = _index()
    tx = _Tx([_In(canon_push(), generation=True)])
    idx.process_tx(bytes([0x11]) * 32, tx, 500, 0)
    _flush(idx)
    assert idx.db.utxo_db.store == {} or not any(
        k.startswith(DeclarationDBKeys.DOC) for k in idx.db.utxo_db.store)


def test_lookup_by_ref_and_by_signer():
    idx = _index()
    _mark(idx, 0x11, 500)
    _flush(idx)
    ref = bytes.fromhex(REAL_DOC['declares'][0]['ref'])

    by_ref = idx.get_by_ref(ref)
    assert len(by_ref['rows']) == 1
    row = by_ref['rows'][0]
    assert row['height'] == 500
    assert row['kind'] == 'creator' and row['action'] == 'declare'
    assert row['signer'] == REAL_DOC['signer']
    assert row['sig_valid'] is None, 'verification disabled -> unchecked, not false'

    by_signer = idx.get_by_signer(REAL_DOC['signer'])
    assert len(by_signer['rows']) == 1
    assert by_signer['rows'][0]['doc_hash'] == row['doc_hash']

    assert idx.get_by_signer('some-other-address')['rows'] == []


def test_rows_are_height_ascending():
    idx = _index()
    # Same document revealed at several heights, recorded out of order.
    for h in (900, 500, 700):
        _mark(idx, 0x20 + (h % 7), h)
    _flush(idx)
    ref = bytes.fromhex(REAL_DOC['declares'][0]['ref'])
    heights = [r['height'] for r in idx.get_by_ref(ref, limit=100)['rows']]
    assert heights == sorted(heights) == [500, 700, 900]


def test_earliest_reveal_wins_for_the_doc_record():
    idx = _index()
    _mark(idx, 0x11, 500)
    _flush(idx)
    doc_hash = sha256(doc_bytes())
    first = json.loads(idx.db.utxo_db.store[DeclarationDBKeys.DOC + doc_hash])
    assert first['height'] == 500

    _mark(idx, 0x22, 900)          # same doc, later reveal
    _flush(idx)
    still = json.loads(idx.db.utxo_db.store[DeclarationDBKeys.DOC + doc_hash])
    assert still['height'] == 500, 'a later reveal must not overwrite the earliest record'
    assert still['reveal_txid'] == first['reveal_txid']


def test_reorg_unwinds_only_the_disconnected_height():
    idx = _index()
    _mark(idx, 0x11, 500)
    _mark(idx, 0x22, 501, tx_index=1)
    _flush(idx)
    ref = bytes.fromhex(REAL_DOC['declares'][0]['ref'])
    assert len(idx.get_by_ref(ref, limit=100)['rows']) == 2

    with idx.db.utxo_db.write_batch() as batch:
        idx.backup(batch, 501)
    heights = [r['height'] for r in idx.get_by_ref(ref, limit=100)['rows']]
    assert heights == [500], 'height 500 must survive an unwind of 501'


def test_pagination_cursor():
    idx = _index()
    for i, h in enumerate((500, 600, 700)):
        _mark(idx, 0x30 + i, h)
    _flush(idx)
    ref = bytes.fromhex(REAL_DOC['declares'][0]['ref'])

    page1 = idx.get_by_ref(ref, limit=2)
    assert len(page1['rows']) == 2 and page1['next_cursor']
    page2 = idx.get_by_ref(ref, limit=2, cursor=bytes.fromhex(page1['next_cursor']))
    seen = [r['height'] for r in page1['rows']] + [r['height'] for r in page2['rows']]
    assert seen == [500, 600, 700], 'pages must not overlap or skip'


def test_invalid_documents_leave_no_rows():
    idx = _index()
    bad = json.loads(json.dumps(REAL_DOC))
    bad['version'] = 99
    tx = _Tx([_In(canon_push(bad))])
    idx.process_tx(bytes([0x11]) * 32, tx, 500, 0)
    _flush(idx)
    assert not any(k.startswith(DeclarationDBKeys.DOC) for k in idx.db.utxo_db.store)


def test_disabled_index_records_nothing():
    class _Off(_Env):
        declaration_index = False

    db = SimpleNamespace(utxo_db=_StubUtxoDB(), db_height=1000)
    idx = DeclarationIndex(db, _Off())
    _mark(idx, 0x11, 500)
    _flush(idx)
    assert idx.db.utxo_db.store == {}


def test_stats_spell_out_the_sig_valid_semantics():
    idx = _index()
    s = idx.stats()
    assert s['enabled'] is True
    assert s['signature_verification'] is False
    assert 'UNCHECKED' in s['sig_valid_semantics']


def test_key_prefixes_do_not_alias():
    prefixes = [v for k, v in vars(DeclarationDBKeys).items() if isinstance(v, bytes)]
    for a in prefixes:
        for b in prefixes:
            if a is not b and a != b:
                assert not b.startswith(a), f'{b!r} sits under {a!r}'


def test_signer_hash_is_fixed_width():
    assert len(_signer_hash('a')) == 16
    assert len(_signer_hash('x' * 90)) == 16
    assert _signer_hash('a') != _signer_hash('b')


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))


# --------------------------------------------------------------------------- bounded backfill

class _EnvStart(_Env):
    declaration_start_height = 459000


def _index_with_start(start):
    class E(_Env):
        declaration_start_height = start
    db = SimpleNamespace(utxo_db=_StubUtxoDB(), db_height=1000)
    return DeclarationIndex(db, E())


def test_start_height_is_read_and_reported():
    idx = _index_with_start(459000)
    assert idx.start_height == 459000
    assert idx.stats()['start_height'] == 459000


def test_start_height_defaults_to_zero_and_clamps_negatives():
    assert _index().start_height == 0
    assert _index_with_start(-5).start_height == 0


def test_backfill_scans_only_from_the_start_height():
    """The whole point: with an activation height set, the rescan covers a few thousand blocks
    instead of the whole chain."""
    import asyncio

    idx = _index_with_start(459000)
    # Live indexing began at 459480, so the backfill target is 459479.
    idx.db.utxo_db.store[DeclarationDBKeys.LIVE_FROM] = struct.pack('>I', 459480)

    scanned = []
    # A decodable (empty) block, so the test exercises the scan RANGE rather than the
    # undecodable-block error path.
    idx.env.coin = SimpleNamespace(
        P2PKH_VERBYTE=b'\x00',
        block=lambda raw: SimpleNamespace(transactions=[]),
    )

    class _Daemon:
        async def block_hex_hashes(self, first, count):
            scanned.append((first, count))
            return ['00' * 32] * count

        async def raw_blocks(self, hex_hashes):
            return [b''] * len(hex_hashes)   # undecodable -> logged and skipped

    asyncio.run(idx.backfill(459479, _Daemon()))
    assert scanned, 'backfill did not run'
    first_height = scanned[0][0]
    assert first_height == 459000, f'scan started at {first_height}, not the activation height'
    total = sum(c for _f, c in scanned)
    assert total == 480, f'scanned {total} blocks; expected 459000..459479'


def test_backfill_skips_entirely_when_start_is_above_the_target():
    import asyncio

    idx = _index_with_start(900000)
    idx.db.utxo_db.store[DeclarationDBKeys.LIVE_FROM] = struct.pack('>I', 459480)

    class _Daemon:
        async def block_hex_hashes(self, first, count):
            raise AssertionError('must not fetch any blocks')

        async def raw_blocks(self, hex_hashes):
            raise AssertionError('must not fetch any blocks')

    asyncio.run(idx.backfill(459479, _Daemon()))
    assert idx.db.utxo_db.store.get(DeclarationDBKeys.BACKFILL_DONE) == b'1'


# --------------------------------------------------------------------------- targeted scan

def test_scan_txid_indexes_one_known_transaction():
    """Two daemon calls, no rescan — the path for 'there is exactly one record and I know it'."""
    import asyncio

    idx = _index()
    txid = 'ab' * 32
    scriptsig = canon_push()
    def varint(n):
        # The scriptSig carries the whole JSON document, so it is well over 255 bytes and needs a
        # real varint length rather than a single byte.
        if n < 0xfd:
            return bytes([n])
        if n <= 0xffff:
            return b'\xfd' + struct.pack('<H', n)
        return b'\xfe' + struct.pack('<I', n)

    # Minimal serialised tx: version, 1 input, 1 output, locktime.
    tx_raw = (struct.pack('<i', 1)
              + b'\x01' + b'\x11' * 32 + struct.pack('<I', 0)
              + varint(len(scriptsig)) + scriptsig + struct.pack('<I', 0xffffffff)
              + b'\x01' + struct.pack('<q', 0) + b'\x01\x6a'
              + struct.pack('<I', 0))

    class _Daemon:
        async def getrawtransaction(self, h, verbose=False):
            return {'hex': tx_raw.hex(), 'blockhash': 'cd' * 32}

        async def deserialised_block(self, h):
            return {'height': 459475, 'tx': ['ff' * 32, txid]}

    from electrumx.lib.tx import Deserializer
    idx.env.coin = SimpleNamespace(P2PKH_VERBYTE=b'\x00', DESERIALIZER=Deserializer)

    out = asyncio.run(idx.scan_txid(txid, _Daemon()))
    assert out['declarations_indexed'] == 1, out
    assert out['height'] == 459475
    assert out['tx_index'] == 1

    ref = bytes.fromhex(REAL_DOC['declares'][0]['ref'])
    rows = idx.get_by_ref(ref)['rows']
    assert len(rows) == 1 and rows[0]['height'] == 459475


def test_scan_txid_rejects_unconfirmed():
    import asyncio

    idx = _index()

    class _Daemon:
        async def getrawtransaction(self, h, verbose=False):
            return {'hex': '00', 'blockhash': None}

    out = asyncio.run(idx.scan_txid('ab' * 32, _Daemon()))
    assert 'unconfirmed' in out['error'], out


# --------------------------------------------------------------------------- versioning

def test_v2_documents_are_indexed():
    """v2 anchors must not be silently dropped: the parser accepts every SUPPORTED_VERSION."""
    d = json.loads(json.dumps(REAL_DOC))
    d['version'] = 2
    out = parse_declaration(CANON_MAGIC + doc_bytes(d))
    assert out is not None, 'a v2 declaration was rejected'
    assert out['version'] == 2
    assert out['entries'][0]['ref'].hex() == REAL_DOC['declares'][0]['ref']


def test_v2_extra_fields_do_not_break_parsing():
    # JSON is keyed by name, so a version that only ADDS fields stays readable under v1 rules.
    d = json.loads(json.dumps(REAL_DOC))
    d['version'] = 2
    d['delegate'] = 'some-future-field'
    d['declares'][0]['scope'] = 'collection'
    out = parse_declaration(CANON_MAGIC + doc_bytes(d))
    assert out is not None and out['version'] == 2


def test_version_true_is_not_version_one():
    # JSON `true` == 1 in Python, and would otherwise pass the membership test.
    d = json.loads(json.dumps(REAL_DOC))
    d['version'] = True
    assert parse_declaration(CANON_MAGIC + doc_bytes(d)) is None


def test_version_reaches_the_indexed_row():
    idx = _index()
    d = json.loads(json.dumps(REAL_DOC))
    d['version'] = 2
    tx = _Tx([_In(canon_push(d))])
    idx.process_tx(bytes([0x11]) * 32, tx, 500, 0)
    _flush(idx)
    rows = idx.get_by_ref(bytes.fromhex(REAL_DOC['declares'][0]['ref']))['rows']
    assert len(rows) == 1
    assert rows[0]['version'] == 2, 'consumers must be able to tell v1 and v2 apart'


def test_stats_report_every_supported_version():
    from electrumx.server.declaration_index import SUPPORTED_VERSIONS
    assert 1 in SUPPORTED_VERSIONS and 2 in SUPPORTED_VERSIONS
    assert _index().stats()['supported_versions'] == list(SUPPORTED_VERSIONS)


# --------------------------------------------------------------------------- revokes

def _revoke_doc(refs, declares=None):
    d = json.loads(json.dumps(REAL_DOC))
    d['declares'] = declares if declares is not None else []
    d['revokes'] = refs
    return d


REF_HEX = REAL_DOC['declares'][0]['ref']
OTHER_REF_HEX = 'bb' * 32 + '01000000'


def test_revoke_only_document_is_indexed():
    """declares may be empty when revokes is not — the combined entry count is what matters."""
    out = parse_declaration(CANON_MAGIC + doc_bytes(_revoke_doc([REF_HEX])))
    assert out is not None, 'a revoke-only document was rejected'
    assert len(out['entries']) == 1
    e = out['entries'][0]
    assert e['action'] == 'revoke'
    assert e['kind'] is None and e['label'] is None
    assert e['ref'].hex() == REF_HEX


def test_declares_and_revokes_combine():
    d = _revoke_doc([OTHER_REF_HEX], declares=REAL_DOC['declares'])
    out = parse_declaration(CANON_MAGIC + doc_bytes(d))
    assert [e['action'] for e in out['entries']] == ['declare', 'revoke']
    assert out['entries'][0]['kind'] == 'creator'
    assert out['entries'][1]['kind'] is None


def test_document_with_neither_declares_nor_revokes_is_rejected():
    assert parse_declaration(CANON_MAGIC + doc_bytes(_revoke_doc([]))) is None
    d = json.loads(json.dumps(REAL_DOC))
    d['declares'] = []
    d.pop('revokes', None)
    assert parse_declaration(CANON_MAGIC + doc_bytes(d)) is None


def test_malformed_revokes_are_rejected():
    assert parse_declaration(CANON_MAGIC + doc_bytes(_revoke_doc('not-a-list'))) is None
    assert parse_declaration(CANON_MAGIC + doc_bytes(_revoke_doc(['ab' * 32]))) is None   # 64 hex
    assert parse_declaration(CANON_MAGIC + doc_bytes(_revoke_doc(['zz' * 36]))) is None
    assert parse_declaration(CANON_MAGIC + doc_bytes(_revoke_doc([{'ref': REF_HEX}]))) is None


def test_revoke_row_reports_null_kind_not_the_string_None():
    """The encode/decode round trip: an f-string over kind=None would store 'None'."""
    idx = _index()
    tx = _Tx([_In(canon_push(_revoke_doc([REF_HEX])))])
    idx.process_tx(bytes([0x11]) * 32, tx, 500, 0)
    _flush(idx)

    rows = idx.get_by_ref(bytes.fromhex(REF_HEX))['rows']
    assert len(rows) == 1
    assert rows[0]['action'] == 'revoke'
    assert rows[0]['kind'] is None, f"got {rows[0]['kind']!r}, expected null"


def test_declare_row_still_carries_its_kind():
    idx = _index()
    _mark(idx, 0x11, 500)
    _flush(idx)
    rows = idx.get_by_ref(bytes.fromhex(REF_HEX))['rows']
    assert rows[0]['kind'] == 'creator' and rows[0]['action'] == 'declare'
