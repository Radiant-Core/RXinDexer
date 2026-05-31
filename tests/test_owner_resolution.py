"""Regression tests for resolvable Glyph holder identity + ref-format helpers.

Covers the P0 owner index (GO: hashX -> base scriptPubKey) and the P2 canonical
ref parsing/rendering, exercised through the real GlyphIndex methods against a
fake RocksDB.  No node / regtest required.
"""
import struct

from electrumx.lib.coins import Radiant
from electrumx.lib.hash import sha256, HASHX_LEN, Base58
from electrumx.lib.script import ScriptPubKey
from electrumx.server.glyph_index import (
    GlyphIndex, GlyphDBKeys, pack_holder_key, pack_owner_key,
    parse_ref_any, ref_to_display,
)


class FakeUtxoDB:
    """Minimal RocksDB stand-in: a sorted dict supporting get + prefix/seek scan."""
    def __init__(self):
        self.d = {}

    def get(self, key):
        return self.d.get(key)

    def put(self, key, value):
        self.d[key] = value

    def iterator(self, prefix=b'', seek=None):
        start = seek if seek is not None else prefix
        for k in sorted(self.d):
            if k < start:
                continue
            if not k.startswith(prefix):
                continue
            yield k, self.d[k]


class FakeDB:
    def __init__(self):
        self.utxo_db = FakeUtxoDB()
        self.db_height = 1000


class FakeEnv:
    coin = Radiant
    glyph_index = True
    reorg_limit = 0


def _make_index():
    gi = GlyphIndex(FakeDB(), FakeEnv())
    return gi


def _p2pkh(hash160: bytes):
    """Return (base_script, hashX, electrum_scripthash_hex, address)."""
    script = ScriptPubKey.P2PKH_script(hash160)
    digest = sha256(script)
    hashX = digest[:HASHX_LEN]
    scripthash_hex = digest[::-1].hex()
    address = Base58.encode_check(Radiant.P2PKH_VERBYTE + hash160)
    return script, hashX, scripthash_hex, address


def test_parse_ref_any_both_forms_equal():
    txid_display = "b3d8a9b16e36161f994a83492931140e279b076a0556eab260439a02e25ccf06"
    raw = parse_ref_any(txid_display + "_0")           # display txid_vout
    raw2 = parse_ref_any(bytes.fromhex(txid_display)[::-1].hex() + "00000000")  # 72-hex internal
    assert raw == raw2
    assert len(raw) == 36
    # round-trips back to display form
    assert ref_to_display(raw) == txid_display + "_0"


def test_parse_ref_any_rejects_garbage():
    for bad in ["xyz", "1234", "abcd_", "_5", "zz" * 36]:
        try:
            parse_ref_any(bad)
            raised = False
        except (ValueError, Exception):
            raised = True
        assert raised, f"expected ValueError for {bad!r}"


def test_script_to_address_p2pkh():
    gi = _make_index()
    hash160 = bytes(range(20))
    script, _, _, address = _p2pkh(hash160)
    assert gi._script_to_address(script) == address


def test_holders_resolve_to_address():
    gi = _make_index()
    ref = bytes(range(36))
    hash160 = bytes([7]) * 20
    script, hashX, scripthash_hex, address = _p2pkh(hash160)

    # Index rows as the block processor / flush would write them.
    gi.db.utxo_db.put(pack_owner_key(hashX), script)
    gi.db.utxo_db.put(pack_holder_key(ref, hashX), struct.pack('<Q', 5))

    out = gi.get_token_holders(ref)
    assert len(out['holders']) == 1
    h = out['holders'][0]
    assert h['address'] == address
    assert h['scripthash'] == scripthash_hex
    assert len(h['scripthash']) == 64           # full 32-byte electrum scripthash
    assert h['hashX'] == hashX.hex()
    assert h['amount'] == 5 and h['balance'] == 5
    assert out['ref'] == ref_to_display(ref)
    assert out['ref_hex'] == ref.hex()


def test_holders_without_owner_index_degrade_gracefully():
    """Pre-resync rows (no GO entry) still return, with null address/scripthash."""
    gi = _make_index()
    ref = bytes(range(36))
    hashX = bytes([9]) * HASHX_LEN
    gi.db.utxo_db.put(pack_holder_key(ref, hashX), struct.pack('<Q', 1))

    out = gi.get_token_holders(ref)
    h = out['holders'][0]
    assert h['address'] is None
    assert h['scripthash'] is None
    assert h['hashX'] == hashX.hex()
    assert h['amount'] == 1


def test_flush_writes_owner_index():
    """process_balance_changes(credit with base_script) -> flush writes GO + GR."""
    gi = _make_index()
    ref = bytes(range(36))
    hash160 = bytes([3]) * 20
    script, hashX, scripthash_hex, address = _p2pkh(hash160)
    # Pretend the token is known so the credit is applied.
    gi._known_refs.add(ref)

    gi.process_balance_changes(
        height=10,
        debits=[],
        credits=[(hashX, 42, [ref], script)],
    )

    class FakeBatch:
        def __init__(self, db): self.db = db
        def put(self, k, v): self.db.put(k, v)
        def delete(self, k): self.db.d.pop(k, None)

    gi.flush(FakeBatch(gi.db.utxo_db))

    # GO + GR persisted; holders resolve.
    assert gi.db.utxo_db.get(pack_owner_key(hashX)) == script
    out = gi.get_token_holders(ref)
    assert out['holders'][0]['address'] == address
    assert out['holders'][0]['amount'] == 42


class _FakeBatch:
    def __init__(self, db): self.db = db
    def put(self, k, v): self.db.put(k, v)
    def delete(self, k): self.db.d.pop(k, None)


def test_stats_buckets_sum_to_total():
    """Every token type increments a bucket, so by_type sums to total."""
    from electrumx.lib.glyph import GlyphTokenType

    class T:
        def __init__(self, tt, ver=1):
            self.token_type = tt
            self.glyph_version = ver

    gi = _make_index()
    types = [GlyphTokenType.FT, GlyphTokenType.FT, GlyphTokenType.NFT,
             GlyphTokenType.DAT, GlyphTokenType.DMINT, GlyphTokenType.WAVE,
             GlyphTokenType.CONTAINER, GlyphTokenType.AUTHORITY, 99]  # 99 -> unknown
    for tt in types:
        gi._update_stats_delta(T(tt), +1)
    gi._flush_stats_counter(_FakeBatch(gi.db.utxo_db))

    stats = gi.get_stats()
    assert stats['total_tokens'] == len(types)
    assert sum(stats['by_type'].values()) == stats['total_tokens'], stats['by_type']
    assert sum(stats['by_version'].values()) == stats['total_tokens']
    assert stats['by_type']['WAVE'] == 1
    assert stats['by_type']['unknown'] == 1
    assert stats['by_type']['FT'] == 2


def _make_token(ref, token_type, name=None):
    from electrumx.server.glyph_index import GlyphTokenInfo
    t = GlyphTokenInfo()
    t.ref = ref
    t.token_type = token_type
    t.name = name
    return t


def test_glyphs_summary_total_is_o1_and_paginates():
    """total comes from GSTAT (not a row count); page respects limit/offset."""
    import cbor2
    from electrumx.server.glyph_index import GlyphDBKeys, pack_token_key, GlyphTokenInfo
    from electrumx.lib.glyph import GlyphTokenType

    gi = _make_index()
    refs = [bytes([i]) + bytes(35) for i in range(5)]
    for i, ref in enumerate(refs):
        gi.db.utxo_db.put(pack_token_key(ref), _make_token(ref, GlyphTokenType.NFT, f"t{i}").to_bytes())
    # GSTAT total deliberately != actual row count, to prove total is read O(1)
    gi.db.utxo_db.put(GlyphDBKeys.STATS, cbor2.dumps({'total': 9999, 'nft': 9999}))

    out = gi.get_all_tokens_summary(limit=2, offset=0)
    assert out['total'] == 9999                      # from GSTAT, not a scan
    assert len(out['tokens']) == 2
    # canonical ref shape on output
    assert '_' in out['tokens'][0]['ref']
    assert len(out['tokens'][0]['ref_hex']) == 72

    page2 = gi.get_all_tokens_summary(limit=2, offset=2)
    assert len(page2['tokens']) == 2
    assert page2['tokens'][0]['ref_hex'] != out['tokens'][0]['ref_hex']


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns)-failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
