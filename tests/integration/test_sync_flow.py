import pytest
from unittest.mock import MagicMock, patch
from indexer.sync import sync_blocks
from database.models import Block, Transaction, UTXO
import time

def test_sync_blocks_integration(db, mocker):
    """
    Integration test for sync_blocks.
    Mocks the RPC calls but uses the real (in-memory) database.
    """
    
    # Mock RPC calls
    # Scenario:
    # - Current DB tip: 0 (empty)
    # - Node tip: 2
    # - Block 1: 1 transaction
    # - Block 2: 1 transaction
    
    mock_rpc = mocker.patch("indexer.sync.rpc_call")
    
    def side_effect(method, params=None):
        if method == "getblockcount":
            return 2
        elif method == "getblockhash":
            height = params[0]
            return f"hash_{height}"
        elif method == "getblock":
            block_hash = params[0]
            height = int(block_hash.split("_")[1])
            return {
                'hash': block_hash,
                'height': height,
                'time': int(time.time()),
                'tx': [
                    {
                        'txid': f'tx_{height}_1',
                        'vout': [{'n': 0, 'value': 50.0, 'scriptPubKey': {'addresses': ['addr1']}}]
                    }
                ]
            }
        return None

    mock_rpc.side_effect = side_effect
    
    # Mock PartitionManager to do nothing or use a real one if possible with SQLite
    # Since SQLite doesn't support partitioning the same way Postgres does (usually), 
    # we might need to mock it out if it executes raw SQL that is PG-specific.
    # Looking at sync.py, it imports PartitionManager.
    # Let's mock it to avoid PG-specific SQL errors on SQLite
    mocker.patch("database.partition_manager.PartitionManager")
    
    # Run sync
    # We need a real parser callback or mock it. 
    # Let's use the real parser to verify end-to-end flow
    from indexer.parser import parse_transactions
    
    # Patch extract_refs_from_script in parser to avoid import errors if deps missing or complex
    # But we already patched things in unit tests, maybe it's fine.
    # Let's patch decode_glyph just in case to speed things up/avoid complexity
    with patch("indexer.script_utils.decode_glyph", return_value=None):
        sync_blocks(db, parse_tx_callback=parse_transactions, batch_size=10)
    
    # Verify DB state
    # 1. Blocks
    blocks = db.query(Block).order_by(Block.height).all()
    assert len(blocks) == 2
    assert blocks[0].height == 1
    assert blocks[0].hash == "hash_1"
    assert blocks[1].height == 2
    
    # 2. Transactions
    txs = db.query(Transaction).all()
    assert len(txs) == 2
    assert txs[0].txid == "tx_1_1"
    
    # 3. UTXOs
    utxos = db.query(UTXO).all()
    assert len(utxos) == 2
    assert utxos[0].value == 50.0
    assert utxos[0].address == "addr1"

def test_sync_idempotency(db, mocker):
    """
    Ensure running sync twice doesn't duplicate data or fail.
    """
    # Setup same mocks
    mock_rpc = mocker.patch("indexer.sync.rpc_call")
    mock_rpc.side_effect = lambda method, params=None: {
        "getblockcount": 1,
        "getblockhash": "hash_1",
        "getblock": {'hash': 'hash_1', 'height': 1, 'time': 123, 'tx': []}
    }.get(method, None)
    
    if mock_rpc.side_effect("getblockhash") is None: # Fix lambda limitation
         def side_effect(method, params=None):
            if method == "getblockcount": return 1
            if method == "getblockhash": return "hash_1"
            if method == "getblock": return {'hash': 'hash_1', 'height': 1, 'time': 123, 'tx': []}
         mock_rpc.side_effect = side_effect

    mocker.patch("database.partition_manager.PartitionManager")
    from indexer.parser import parse_transactions
    
    # First run
    sync_blocks(db, parse_tx_callback=parse_transactions)
    assert db.query(Block).count() == 1
    
    # Second run
    sync_blocks(db, parse_tx_callback=parse_transactions)
    assert db.query(Block).count() == 1
