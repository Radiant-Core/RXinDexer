# /Users/radiant/Desktop/RXinDexer/tests/parser/test_glyph_parser.py
# This file tests the Glyph parser responsible for extracting token data.
# It verifies that CBOR-encoded token payloads are correctly parsed and tracked.

import pytest
from unittest.mock import patch, MagicMock
import cbor2
from decimal import Decimal

from src.parser.glyph_parser import GlyphParser
from src.models import GlyphToken, UTXO, Holder


class TestGlyphParser:
    """Tests for the GlyphParser."""
    
    def test_extract_glyph_token(self, db_session, mock_rpc):
        """Test that _extract_glyph_token correctly extracts token data from a transaction."""
        # Create sample transaction with Glyph token signature
        tx = {
            "txid": "tokentx",
            "vin": [
                {
                    "scriptSig": {
                        "asm": "some asm gly data"
                    }
                }
            ]
        }
        
        # Mock RPC call to get raw transaction
        raw_tx = {
            "vin": [
                {
                    "scriptSig": {
                        "hex": "676c7901020304"  # 'gly' + cbor hex data
                    }
                }
            ]
        }
        mock_rpc.get_raw_transaction.return_value = raw_tx
        
        # Mock cbor2.loads to return token data
        token_data = {
            "ref": "glyph:1234",
            "type": "fungible",
            "metadata": {"name": "Test Token", "decimals": 8},
            "vout": 1
        }
        
        with patch('src.parser.glyph_parser.cbor2.loads', return_value=token_data):
            # Create parser
            parser = GlyphParser(mock_rpc, db_session)
            
            # Extract token
            result = parser._extract_glyph_token(tx)
            
            # Verify result
            assert result is not None
            assert result["ref"] == "glyph:1234"
            assert result["type"] == "fungible"
            assert result["metadata"] == {"name": "Test Token", "decimals": 8}
            assert result["vout"] == 1
    
    def test_parse_transaction_new_token(self, db_session, mock_rpc):
        """Test that parse_transaction creates a new token if it doesn't exist."""
        # Create sample transaction
        tx = {
            "txid": "tokentx",
            "vin": [
                {
                    "scriptSig": {
                        "asm": "some asm gly data"
                    }
                }
            ]
        }
        
        # Mock _extract_glyph_token to return token data
        token_data = {
            "ref": "glyph:1234",
            "type": "fungible",
            "metadata": {"name": "Test Token", "decimals": 8},
            "vout": 1
        }
        
        # Create UTXO for the token
        utxo = UTXO(
            txid="tokentx",
            vout=1,
            address="tokenowner",
            amount=Decimal("0"),
            spent=False,
            block_height=10,
            block_hash="blockhash10"
        )
        db_session.add(utxo)
        db_session.commit()
        
        # Create parser with mocked _extract_glyph_token
        parser = GlyphParser(mock_rpc, db_session)
        parser._extract_glyph_token = MagicMock(return_value=token_data)
        
        # Parse transaction
        tokens = parser.parse_transaction(tx, 10, "blockhash10")
        
        # Verify token was created
        assert len(tokens) == 1
        assert tokens[0]["ref"] == "glyph:1234"
        
        # Check database
        token = db_session.query(GlyphToken).filter(GlyphToken.ref == "glyph:1234").first()
        assert token is not None
        assert token.ref == "glyph:1234"
        assert token.type == "fungible"
        assert token.metadata == {"name": "Test Token", "decimals": 8}
        assert token.current_txid == "tokentx"
        assert token.current_vout == 1
        assert token.genesis_txid == "tokentx"
        assert token.genesis_block_height == 10
        
        # Check UTXO was updated with token reference
        utxo = db_session.query(UTXO).filter(UTXO.txid == "tokentx", UTXO.vout == 1).first()
        assert utxo.token_ref == "glyph:1234"
    
    def test_parse_transaction_existing_token(self, db_session, mock_rpc):
        """Test that parse_transaction updates an existing token."""
        # Create existing token
        token = GlyphToken(
            ref="glyph:1234",
            type="fungible",
            metadata={"name": "Test Token", "decimals": 8},
            current_txid="oldtx",
            current_vout=0,
            genesis_txid="oldtx",
            genesis_block_height=1
        )
        db_session.add(token)
        
        # Create UTXO for the new token location
        utxo = UTXO(
            txid="newtx",
            vout=1,
            address="tokenowner",
            amount=Decimal("0"),
            spent=False,
            block_height=10,
            block_hash="blockhash10"
        )
        db_session.add(utxo)
        db_session.commit()
        
        # Create sample transaction
        tx = {
            "txid": "newtx",
            "vin": [
                {
                    "scriptSig": {
                        "asm": "some asm gly data"
                    }
                }
            ]
        }
        
        # Mock _extract_glyph_token to return token data
        token_data = {
            "ref": "glyph:1234",  # Same ref as existing token
            "type": "fungible",
            "metadata": {"name": "Test Token", "decimals": 8},
            "vout": 1
        }
        
        # Create parser with mocked _extract_glyph_token
        parser = GlyphParser(mock_rpc, db_session)
        parser._extract_glyph_token = MagicMock(return_value=token_data)
        
        # Parse transaction
        tokens = parser.parse_transaction(tx, 10, "blockhash10")
        
        # Verify token was found
        assert len(tokens) == 1
        assert tokens[0]["ref"] == "glyph:1234"
        
        # Check database for updated token
        updated_token = db_session.query(GlyphToken).filter(GlyphToken.ref == "glyph:1234").first()
        assert updated_token is not None
        assert updated_token.current_txid == "newtx"  # Updated
        assert updated_token.current_vout == 1
        assert updated_token.genesis_txid == "oldtx"  # Unchanged
        assert updated_token.genesis_block_height == 1  # Unchanged
    
    def test_update_token_balances(self, db_session, mock_rpc):
        """Test that update_token_balances correctly updates holder token balances."""
        # Create token
        token = GlyphToken(
            ref="glyph:1234",
            type="fungible",
            metadata={"name": "Test Token", "decimals": 8},
            current_txid="tx1",
            current_vout=0,
            genesis_txid="tx1",
            genesis_block_height=1
        )
        db_session.add(token)
        
        # Create UTXOs with token references
        utxo1 = UTXO(
            txid="tx1",
            vout=0,
            address="address1",
            amount=Decimal("1"),
            spent=False,
            token_ref="glyph:1234",
            block_height=1,
            block_hash="blockhash1"
        )
        utxo2 = UTXO(
            txid="tx2",
            vout=0,
            address="address2",
            amount=Decimal("1"),
            spent=False,
            token_ref="glyph:5678",  # Different token
            block_height=2,
            block_hash="blockhash2"
        )
        db_session.add_all([utxo1, utxo2])
        db_session.commit()
        
        # Create parser
        parser = GlyphParser(mock_rpc, db_session)
        
        # Update token balances
        parser.update_token_balances()
        
        # Check database for holders
        holders = db_session.query(Holder).all()
        
        # Find holders by address
        holder1 = next((h for h in holders if h.address == "address1"), None)
        holder2 = next((h for h in holders if h.address == "address2"), None)
        
        # Verify token balances
        assert holder1 is not None
        assert holder1.token_balances == {"glyph:1234": 1}
        
        assert holder2 is not None
        assert holder2.token_balances == {"glyph:5678": 1}
    
    def test_get_token_holders(self, db_session, mock_rpc):
        """Test that get_token_holders returns addresses holding a specific token."""
        # Create UTXOs with token references
        utxo1 = UTXO(
            txid="tx1",
            vout=0,
            address="address1",
            amount=Decimal("1"),
            spent=False,
            token_ref="glyph:1234",
            block_height=1,
            block_hash="blockhash1"
        )
        utxo2 = UTXO(
            txid="tx2",
            vout=0,
            address="address2",
            amount=Decimal("1"),
            spent=False,
            token_ref="glyph:1234",  # Same token
            block_height=2,
            block_hash="blockhash2"
        )
        utxo3 = UTXO(
            txid="tx3",
            vout=0,
            address="address3",
            amount=Decimal("1"),
            spent=False,
            token_ref="glyph:5678",  # Different token
            block_height=3,
            block_hash="blockhash3"
        )
        db_session.add_all([utxo1, utxo2, utxo3])
        db_session.commit()
        
        # Create parser
        parser = GlyphParser(mock_rpc, db_session)
        
        # Get token holders
        holders = parser.get_token_holders("glyph:1234")
        
        # Verify holders
        assert len(holders) == 2
        assert "address1" in holders
        assert "address2" in holders
        assert "address3" not in holders
