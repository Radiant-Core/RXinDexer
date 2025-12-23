import pytest
from database.models import Block, Transaction, UTXO

def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "rxindexer-api"}

def test_get_block_mocked(client, mock_rpc):
    # Mock RPC responses
    mock_rpc.return_value.json.side_effect = [
        {'result': '0000abc'}, # getblockhash
        {'result': {'hash': '0000abc', 'height': 100, 'time': 1234567890, 'tx': ['tx1']}} # getblock
    ]
    
    response = client.get("/block/100")
    assert response.status_code == 200
    data = response.json()
    assert data['height'] == 100
    assert data['hash'] == '0000abc'

def test_get_address_utxos(client, db):
    # Seed DB
    utxo = UTXO(
        txid='tx1', vout=0, address='addr1', value=50.0, spent=False, 
        transaction_block_height=100
    )
    db.add(utxo)
    db.commit()
    
    response = client.get("/address/addr1/utxos")
    assert response.status_code == 200
    data = response.json()
    assert data['address'] == 'addr1'
    assert len(data['utxos']) == 1
    assert data['total_balance'] == 50.0

def test_get_wallet_balance(client, db):
    # Seed DB with multiple UTXOs
    db.add(UTXO(txid='tx1', vout=0, address='addr_bal', value=10.0, spent=False, transaction_block_height=100))
    db.add(UTXO(txid='tx2', vout=1, address='addr_bal', value=20.0, spent=False, transaction_block_height=101))
    db.add(UTXO(txid='tx3', vout=0, address='addr_bal', value=5.0, spent=True, transaction_block_height=90)) # Spent, shouldn't count
    db.commit()
    
    response = client.get("/wallet/addr_bal")
    assert response.status_code == 200
    data = response.json()
    assert data['balance'] == 30.0  # 10 + 20
