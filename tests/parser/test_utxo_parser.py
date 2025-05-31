# /Users/radiant/Desktop/RXinDexer/tests/parser/test_utxo_parser.py
# This file tests the UTXO parser responsible for extracting transaction outputs.
# It verifies that UTXOs are correctly parsed, spent status is tracked, and balances are updated.

import pytest
from unittest.mock import patch, MagicMock
from decimal import Decimal

from src.parser.utxo_parser import UTXOParser
from src.models import UTXO, Holder


class TestUTXOParser:
    """Tests for the UTXOParser."""
    
    def test_parse_transaction_creates_utxos(self, db_session, mock_rpc):
        """Test that parse_transaction creates UTXOs from transaction outputs."""
        # Create sample transaction
        tx = {
            "txid": "txid123",
            "vin": [],  # No inputs (coinbase)
            "vout": [
                {
                    "value": 50,
                    "n": 0,
                    "scriptPubKey": {
                        "type": "pubkeyhash",
                        "addresses": ["address1"]
                    }
                },
                {
                    "value": 25,
                    "n": 1,
                    "scriptPubKey": {
                        "type": "pubkeyhash",
                        "addresses": ["address2"]
                    }
                }
            ]
        }
        
        # Create parser
        parser = UTXOParser(mock_rpc, db_session)
        
        # Parse transaction
        utxos_created, utxos_spent = parser.parse_transaction(tx, 1, "blockhash123")
        
        # Verify UTXOs were created
        assert utxos_created == 2
        assert utxos_spent == 0
        
        # Check database
        utxos = db_session.query(UTXO).all()
        assert len(utxos) == 2
        
        # Verify first UTXO
        assert utxos[0].txid == "txid123"
        assert utxos[0].vout == 0
        assert utxos[0].address == "address1"
        assert utxos[0].amount == Decimal("50")
        assert utxos[0].spent is False
        assert utxos[0].block_height == 1
        
        # Verify second UTXO
        assert utxos[1].txid == "txid123"
        assert utxos[1].vout == 1
        assert utxos[1].address == "address2"
        assert utxos[1].amount == Decimal("25")
        assert utxos[1].spent is False
        assert utxos[1].block_height == 1
    
    def test_parse_transaction_spends_utxos(self, db_session, mock_rpc):
        """Test that parse_transaction marks spent UTXOs."""
        # Create existing UTXO
        utxo = UTXO(
            txid="prevtx",
            vout=0,
            address="address1",
            amount=Decimal("50"),
            spent=False,
            block_height=1,
            block_hash="blockhash1"
        )
        db_session.add(utxo)
        db_session.commit()
        
        # Create transaction that spends the UTXO
        tx = {
            "txid": "txid123",
            "vin": [
                {
                    "txid": "prevtx",
                    "vout": 0
                }
            ],
            "vout": [
                {
                    "value": 49.9,
                    "n": 0,
                    "scriptPubKey": {
                        "type": "pubkeyhash",
                        "addresses": ["address2"]
                    }
                }
            ]
        }
        
        # Create parser
        parser = UTXOParser(mock_rpc, db_session)
        
        # Parse transaction
        utxos_created, utxos_spent = parser.parse_transaction(tx, 2, "blockhash2")
        
        # Verify UTXOs were created and spent
        assert utxos_created == 1
        assert utxos_spent == 1
        
        # Check database for the spent UTXO
        spent_utxo = db_session.query(UTXO).filter(UTXO.txid == "prevtx", UTXO.vout == 0).first()
        assert spent_utxo.spent is True
        assert spent_utxo.spent_txid == "txid123"
    
    def test_update_holder_balances(self, db_session, mock_rpc):
        """Test that update_holder_balances updates holder RXD balances."""
        # Create UTXOs for two addresses
        utxo1 = UTXO(
            txid="tx1",
            vout=0,
            address="address1",
            amount=Decimal("50"),
            spent=False,
            block_height=1,
            block_hash="blockhash1"
        )
        utxo2 = UTXO(
            txid="tx2",
            vout=0,
            address="address1",
            amount=Decimal("25"),
            spent=False,
            block_height=1,
            block_hash="blockhash1"
        )
        utxo3 = UTXO(
            txid="tx3",
            vout=0,
            address="address2",
            amount=Decimal("10"),
            spent=False,
            block_height=1,
            block_hash="blockhash1"
        )
        db_session.add_all([utxo1, utxo2, utxo3])
        db_session.commit()
        
        # Create parser
        parser = UTXOParser(mock_rpc, db_session)
        
        # Update holder balances
        parser.update_holder_balances()
        
        # Check database for holders
        holders = db_session.query(Holder).all()
        assert len(holders) == 2
        
        # Find holders by address
        holder1 = next((h for h in holders if h.address == "address1"), None)
        holder2 = next((h for h in holders if h.address == "address2"), None)
        
        # Verify balances
        assert holder1.rxd_balance == Decimal("75")  # 50 + 25
        assert holder2.rxd_balance == Decimal("10")
    
    def test_get_address_balance(self, db_session, mock_rpc):
        """Test that get_address_balance returns the correct balance."""
        # Create UTXOs
        utxo1 = UTXO(
            txid="tx1",
            vout=0,
            address="address1",
            amount=Decimal("50"),
            spent=False,
            block_height=1,
            block_hash="blockhash1"
        )
        utxo2 = UTXO(
            txid="tx2",
            vout=0,
            address="address1",
            amount=Decimal("25"),
            spent=True,  # Spent UTXO should not count
            block_height=1,
            block_hash="blockhash1"
        )
        db_session.add_all([utxo1, utxo2])
        db_session.commit()
        
        # Create parser
        parser = UTXOParser(mock_rpc, db_session)
        
        # Get balance
        balance = parser.get_address_balance("address1")
        
        # Verify balance (only unspent UTXOs count)
        assert balance == Decimal("50")
