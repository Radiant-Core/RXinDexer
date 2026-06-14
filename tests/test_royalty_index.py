"""Lifecycle test for RoyaltyIndex: beacon-gated discovery, query, close-on-spend
and reorg backup — over an in-memory DB, no chain needed. Vectors come from the
Photonic builder (see test_royalty_parse.py)."""
import struct

from electrumx.lib.coins import Radiant
from electrumx.server.royalty_index import RoyaltyIndex, RoyaltyDBKeys

REF_HEX = (
    "0011223344556677889900aabbccddeeff"
    "00112233445566778899aabbccddee03000000"
)
COVHEX = (
    "d80011223344556677889900aabbccddeeff00112233445566778899aabbccddee03000000"
    "756376a91462e907b15cbf27d5425399ebf6f0fb50ebb88f1888ac6700cd19"
    "76a91462e907b15cbf27d5425399ebf6f0fb50ebb88f1888ac8800cc03a08601a26952cd19"
    "76a91462e907b15cbf27d5425399ebf6f0fb50ebb88f1888ac8852cc028813a2695168"
)
BEACONHEX = (
    "6a045252594c5124"
    "0011223344556677889900aabbccddeeff00112233445566778899aabbccddee03000000"
)


class _Out:
    def __init__(self, script_hex, value=0):
        self.pk_script = bytes.fromhex(script_hex)
        self.value = value


class _Tx:
    def __init__(self, outs):
        self.outputs = outs


class _Batch:
    def __init__(self, store):
        self.store = store

    def put(self, k, v):
        self.store[k] = v

    def delete(self, k):
        self.store.pop(k, None)


class _UtxoDB:
    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def iterator(self, prefix=b'', reverse=False):
        items = sorted((k, v) for k, v in self.store.items() if k.startswith(prefix))
        if reverse:
            items = list(reversed(items))
        return iter(items)


class _DB:
    db_height = 100

    def __init__(self):
        self.utxo_db = _UtxoDB()


class _Env:
    coin = Radiant
    royalty_index = True
    reorg_limit = 100


def _idx():
    return RoyaltyIndex(_DB(), _Env())


def _list_tx(value=600):
    return _Tx([_Out(COVHEX, value), _Out(BEACONHEX, 0)])


def _flush(idx):
    batch = _Batch(idx.db.utxo_db.store)
    idx.flush(batch)


def test_listing_discovered_and_queryable():
    idx = _idx()
    tx_hash = bytes(32)
    idx.process_tx(tx_hash, _list_tx(600), height=101, tx_idx=0, spent_outpoints=set())
    _flush(idx)

    listings = idx.get_listings()
    assert len(listings) == 1
    rec = listings[0]
    assert rec["price"] == 100000
    assert rec["value"] == 600
    assert rec["royalty_total"] == 5000
    assert rec["status"] == "active"
    assert rec["ref"].endswith("_3")
    assert rec["covenant_script"] == COVHEX
    assert rec["txid"]  # present
    assert rec["vout"] == 0

    # by-ref query returns the same listing
    ref_bytes = bytes.fromhex(REF_HEX)
    assert len(idx.get_listings(ref=ref_bytes)) == 1
    # by a different ref returns nothing
    assert idx.get_listings(ref=bytes(36)) == []


def test_no_beacon_no_listing():
    idx = _idx()
    idx.process_tx(bytes(32), _Tx([_Out(COVHEX, 600)]), height=101, tx_idx=0,
                   spent_outpoints=set())
    _flush(idx)
    assert idx.get_listings() == []  # covenant present but no beacon -> not indexed


def test_close_on_spend_removes_from_active():
    idx = _idx()
    tx_hash = bytes(32)
    idx.process_tx(tx_hash, _list_tx(600), height=101, tx_idx=0, spent_outpoints=set())
    _flush(idx)
    assert len(idx.get_listings()) == 1

    listing_id = tx_hash + struct.pack('<I', 0)
    # A later tx spends the covenant UTXO (bought or cancelled).
    idx.process_tx(bytes([1]) * 32, _Tx([_Out("76a914" + "11" * 20 + "88ac", 600)]),
                   height=102, tx_idx=0, spent_outpoints={listing_id})
    _flush(idx)

    assert idx.get_listings() == []                       # gone from global feed
    assert idx.get_listings(ref=bytes.fromhex(REF_HEX)) == []  # and from by-ref


def test_reorg_backup_reopens_closed_listing():
    idx = _idx()
    tx_hash = bytes(32)
    idx.process_tx(tx_hash, _list_tx(600), height=101, tx_idx=0, spent_outpoints=set())
    _flush(idx)
    listing_id = tx_hash + struct.pack('<I', 0)
    idx.process_tx(bytes([1]) * 32, _Tx([_Out("76a914" + "11" * 20 + "88ac", 600)]),
                   height=102, tx_idx=0, spent_outpoints={listing_id})
    _flush(idx)
    assert idx.get_listings() == []

    # Reorg unwinds height 102 -> the close is reverted, listing is ACTIVE again.
    batch = _Batch(idx.db.utxo_db.store)
    idx.backup(batch, 102)
    assert len(idx.get_listings()) == 1
    assert idx.get_listings()[0]["status"] == "active"
