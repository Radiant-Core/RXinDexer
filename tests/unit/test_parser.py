import pytest
from indexer.parser import parse_transactions
from database.models import Transaction, UTXO, GlyphToken
import datetime

def test_parse_simple_transaction(db):
    # Mock transaction data
    tx_data = [{
        'txid': 'tx1',
        'version': 1,
        'locktime': 0,
        'vin': [],
        'vout': [
            {
                'n': 0,
                'value': 50.0,
                'scriptPubKey': {
                    'type': 'pubkeyhash',
                    'addresses': ['addr1'],
                    'hex': '76a914...'
                }
            }
        ]
    }]
    
    parse_transactions(tx_data, db, block_id=1, block_height=100)
    
    # Verify Transaction stored
    tx = db.query(Transaction).filter_by(txid='tx1').first()
    assert tx is not None
    assert tx.block_height == 100
    
    # Verify UTXO stored
    utxo = db.query(UTXO).filter_by(txid='tx1', vout=0).first()
    assert utxo is not None
    assert utxo.address == 'addr1'
    assert utxo.value == 50.0
    assert utxo.spent == False

def test_parse_glyph_token(db):
    # Mock transaction with Glyph marker (676c79)
    # This hex corresponds to a simplified script with the marker
    # In a real scenario, this would be a valid GLYPH protocol script
    # For this test, we rely on the parser detecting '676c79' and attempting decode
    
    # We need to mock the decode_glyph function since we can't easily construct valid CBOR/Glyph bytes manually in a simple test string
    # We'll mock the return value of decode_glyph within parser.py scope if possible, 
    # but since we are testing parser.py integration, let's try to inject a mock at the module level
    
    from unittest.mock import patch
    
    mock_glyph_data = {
        'payload': {
            'p': [1], 
            'type': 'fungible', 
            'name': 'TestToken',
            'amt': 1000
        },
        'files': {},
        'raw': {'p': [1], 'name': 'TestToken'},
        'is_mineable': False
    }
    
    with patch('indexer.script_utils.decode_glyph', return_value=mock_glyph_data):
        with patch('indexer.script_utils.extract_refs_from_script', return_value=[b'ref1']):
            tx_data = [{
                'txid': 'tx_glyph',
                'vin': [],
                'vout': [
                    {
                        'n': 0,
                        'value': 0.0,
                        'scriptPubKey': {
                            'type': 'nulldata',
                            'hex': '6a04676c79' # starts with OP_RETURN (6a) + len + 'gly' (valid hex)
                        }
                    }
                ]
            }]
            
            parse_transactions(tx_data, db, block_id=2, block_height=101)
            
            # Verify GlyphToken stored
            glyph = db.query(GlyphToken).filter_by(txid='tx_glyph').first()
            assert glyph is not None
            assert glyph.type == 'fungible'
            assert glyph.current_supply == 1000

def test_spent_utxo_tracking(db):
    # 1. Create a UTXO
    tx_data_1 = [{
        'txid': 'tx_orig',
        'vin': [],
        'vout': [{'n': 0, 'value': 10.0, 'scriptPubKey': {'addresses': ['addr1']}}]
    }]
    parse_transactions(tx_data_1, db, block_id=1, block_height=100)
    
    # Verify it exists and is unspent
    utxo = db.query(UTXO).filter_by(txid='tx_orig', vout=0).first()
    assert utxo.spent == False
    
    # 2. Spend it in a new transaction
    tx_data_2 = [{
        'txid': 'tx_spend',
        'vin': [{'txid': 'tx_orig', 'vout': 0}],
        'vout': [{'n': 0, 'value': 9.9, 'scriptPubKey': {'addresses': ['addr2']}}]
    }]
    parse_transactions(tx_data_2, db, block_id=2, block_height=101)
    
    # Verify original UTXO is now spent
    db.refresh(utxo)
    assert utxo.spent == True
    assert utxo.spent_in_txid == 'tx_spend'
    
    # Verify new UTXO exists
    new_utxo = db.query(UTXO).filter_by(txid='tx_spend', vout=0).first()
    assert new_utxo is not None
    assert new_utxo.value == 9.9
