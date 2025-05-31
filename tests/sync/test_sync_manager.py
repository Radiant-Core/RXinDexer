# /Users/radiant/Desktop/RXinDexer/tests/sync/test_sync_manager.py
# This file tests the SyncManager responsible for blockchain synchronization.
# It verifies block syncing, reorg handling, and database state management.

import pytest
from unittest.mock import patch, MagicMock, call
from decimal import Decimal

from src.sync.sync_manager import SyncManager
from src.models import SyncState, UTXO


class TestSyncManager:
    """Tests for the SyncManager."""
    
    @patch('src.sync.sync_manager.RadiantRPC')
    @patch('src.sync.sync_manager.BlockParser')
    def test_init_creates_sync_state(self, mock_parser, mock_rpc, db_session):
        """Test that SyncManager creates a sync state if none exists."""
        # Ensure no sync state exists
        db_session.query(SyncState).delete()
        db_session.commit()
        
        # Create SyncManager
        sync_manager = SyncManager(db_session)
        
        # Verify sync state was created
        sync_state = db_session.query(SyncState).first()
        assert sync_state is not None
        assert sync_state.id == 1
        assert sync_state.current_height == 0
        assert sync_state.is_syncing == 0
    
    @patch('src.sync.sync_manager.RadiantRPC')
    @patch('src.sync.sync_manager.BlockParser')
    def test_start_sync_sets_is_syncing(self, mock_parser, mock_rpc, db_session, sample_sync_state):
        """Test that start_sync sets is_syncing flag."""
        # Add sync state
        db_session.add(sample_sync_state)
        db_session.commit()
        
        # Set up mocks
        mock_rpc_instance = mock_rpc.return_value
        mock_rpc_instance.get_block_count.return_value = 10
        
        # Create SyncManager and start sync
        sync_manager = SyncManager(db_session)
        sync_manager.start_sync()
        
        # Verify is_syncing was set to 1 and then back to 0
        sync_state = db_session.query(SyncState).first()
        assert sync_state.is_syncing == 0  # Should be reset after sync
    
    @patch('src.sync.sync_manager.RadiantRPC')
    @patch('src.sync.sync_manager.BlockParser')
    def test_sync_blocks_processes_batch(self, mock_parser, mock_rpc, db_session, sample_sync_state):
        """Test that _sync_blocks processes blocks in batches."""
        # Set up sync state at height 1
        sample_sync_state.current_height = 1
        db_session.add(sample_sync_state)
        db_session.commit()
        
        # Set up mocks
        mock_rpc_instance = mock_rpc.return_value
        mock_rpc_instance.get_block_count.return_value = 10
        mock_rpc_instance.get_block_hash.side_effect = ["hash2", "hash3", "hash4"]
        mock_rpc_instance.get_block.side_effect = [
            {"hash": "hash2", "previousblockhash": "hash1", "chainwork": "00002"},
            {"hash": "hash3", "previousblockhash": "hash2", "chainwork": "00003"},
            {"hash": "hash4", "previousblockhash": "hash3", "chainwork": "00004"}
        ]
        
        mock_parser_instance = mock_parser.return_value
        
        # Create SyncManager with batch size of 3
        sync_manager = SyncManager(db_session)
        sync_manager.batch_size = 3
        
        # Run sync
        sync_manager._sync_blocks()
        
        # Verify blocks were processed
        assert mock_rpc_instance.get_block_hash.call_count == 3
        assert mock_rpc_instance.get_block.call_count == 3
        assert mock_parser_instance.parse_block.call_count == 3
        
        # Verify sync state was updated
        sync_state = db_session.query(SyncState).first()
        assert sync_state.current_height == 4
        assert sync_state.current_hash == "hash4"
        assert sync_state.current_chainwork == "00004"
    
    @patch('src.sync.sync_manager.RadiantRPC')
    @patch('src.sync.sync_manager.BlockParser')
    def test_check_for_reorg_detects_reorg(self, mock_parser, mock_rpc, db_session, sample_sync_state):
        """Test that _check_for_reorg correctly detects chain reorganizations."""
        # Set up sync state
        db_session.add(sample_sync_state)
        db_session.commit()
        
        # Set up mocks
        mock_rpc_instance = mock_rpc.return_value
        
        # Previous hash doesn't match expected hash (reorg)
        mock_rpc_instance.get_block_hash.side_effect = ["expected_hash", "current_hash"]
        mock_rpc_instance.get_block.return_value = {
            "hash": "current_hash",
            "previousblockhash": "wrong_hash"  # Should be "expected_hash"
        }
        
        # Create SyncManager
        sync_manager = SyncManager(db_session)
        
        # Check for reorg at height 2
        reorg_detected = sync_manager._check_for_reorg(2)
        
        # Verify reorg was detected
        assert reorg_detected is True
    
    @patch('src.sync.sync_manager.RadiantRPC')
    @patch('src.sync.sync_manager.BlockParser')
    def test_handle_reorg_rolls_back(self, mock_parser, mock_rpc, db_session, sample_sync_state, sample_utxo):
        """Test that _handle_reorg correctly rolls back to a stable height."""
        # Set up sync state at height 2
        sample_sync_state.current_height = 2
        db_session.add(sample_sync_state)
        
        # Add UTXOs at different heights
        utxo1 = sample_utxo
        utxo2 = UTXO(
            txid="tx2",
            vout=0,
            address="addr",
            amount=Decimal("50.0"),
            spent=False,
            block_height=2,
            block_hash="hash2"
        )
        
        db_session.add(utxo1)
        db_session.add(utxo2)
        db_session.commit()
        
        # Set up mocks
        mock_rpc_instance = mock_rpc.return_value
        mock_rpc_instance.get_block_hash.return_value = "hash1"
        mock_rpc_instance.get_block.return_value = {
            "hash": "hash1",
            "chainwork": "00001"
        }
        
        # Create SyncManager
        sync_manager = SyncManager(db_session)
        
        # Handle reorg by rolling back to height 1
        sync_manager._handle_reorg(1)
        
        # Verify sync state was updated
        sync_state = db_session.query(SyncState).first()
        assert sync_state.current_height == 1
        assert sync_state.current_hash == "hash1"
        assert sync_state.current_chainwork == "00001"
        
        # Verify UTXOs at height > 1 were deleted
        utxos = db_session.query(UTXO).all()
        assert len(utxos) == 1
        assert utxos[0].txid == utxo1.txid
    
    @patch('src.sync.sync_manager.RadiantRPC')
    @patch('src.sync.sync_manager.BlockParser')
    def test_get_sync_status(self, mock_parser, mock_rpc, db_session, sample_sync_state):
        """Test getting the sync status."""
        # Set up sync state
        db_session.add(sample_sync_state)
        db_session.commit()
        
        # Set up mocks
        mock_rpc_instance = mock_rpc.return_value
        mock_rpc_instance.get_block_count.return_value = 100
        
        # Create SyncManager
        sync_manager = SyncManager(db_session)
        
        # Get sync status
        status = sync_manager.get_sync_status()
        
        # Verify status
        assert status["current_height"] == 1
        assert status["node_height"] == 100
        assert status["is_syncing"] is False
        assert status["progress"] == 1.0  # 1/100 * 100
