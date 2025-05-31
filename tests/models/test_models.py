# /Users/radiant/Desktop/RXinDexer/tests/models/test_models.py
# This file tests the database models to ensure they function correctly.
# It verifies that model attributes, relationships, and constraints work as expected.

import pytest
from decimal import Decimal
from sqlalchemy.exc import IntegrityError

from src.models import UTXO, GlyphToken, Holder, SyncState


class TestUTXOModel:
    """Tests for the UTXO model."""
    
    def test_create_utxo(self, db_session, sample_utxo):
        """Test creating a UTXO."""
        db_session.add(sample_utxo)
        db_session.commit()
        
        # Query the UTXO
        saved_utxo = db_session.query(UTXO).filter(
            UTXO.txid == sample_utxo.txid,
            UTXO.vout == sample_utxo.vout
        ).first()
        
        # Verify attributes
        assert saved_utxo is not None
        assert saved_utxo.txid == "d5ada6c79a4abd38fe2a95d8a4b86bec64af447eb3c5216e7e6bc278387d614e"
        assert saved_utxo.vout == 0
        assert saved_utxo.address == "12c6DSiU4Rq3P4ZxziKxzrL5LmMBrzjrJX"
        assert saved_utxo.amount == Decimal("50.0")
        assert saved_utxo.spent is False
        assert saved_utxo.block_height == 1
    
    def test_utxo_primary_key_constraint(self, db_session, sample_utxo):
        """Test that UTXO primary key constraint works."""
        # Add the first UTXO
        db_session.add(sample_utxo)
        db_session.commit()
        
        # Try to add a duplicate UTXO
        duplicate_utxo = UTXO(
            txid=sample_utxo.txid,
            vout=sample_utxo.vout,
            address="12c6DSiU4Rq3P4ZxziKxzrL5LmMBrzjrJX",
            amount=Decimal("50.0"),
            spent=False,
            block_height=1,
            block_hash="000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"
        )
        
        # Should raise IntegrityError
        with pytest.raises(IntegrityError):
            db_session.add(duplicate_utxo)
            db_session.commit()


class TestGlyphTokenModel:
    """Tests for the GlyphToken model."""
    
    def test_create_token(self, db_session, sample_glyph_token):
        """Test creating a Glyph token."""
        db_session.add(sample_glyph_token)
        db_session.commit()
        
        # Query the token
        saved_token = db_session.query(GlyphToken).filter(
            GlyphToken.ref == sample_glyph_token.ref
        ).first()
        
        # Verify attributes
        assert saved_token is not None
        assert saved_token.ref == "glyph:1234"
        assert saved_token.type == "fungible"
        assert saved_token.metadata == {"name": "Test Token", "decimals": 8}
        assert saved_token.current_txid == "d5ada6c79a4abd38fe2a95d8a4b86bec64af447eb3c5216e7e6bc278387d614e"
        assert saved_token.genesis_block_height == 1
    
    def test_token_primary_key_constraint(self, db_session, sample_glyph_token):
        """Test that GlyphToken primary key constraint works."""
        # Add the first token
        db_session.add(sample_glyph_token)
        db_session.commit()
        
        # Try to add a duplicate token
        duplicate_token = GlyphToken(
            ref=sample_glyph_token.ref,
            type="fungible",
            metadata={"name": "Duplicate Token", "decimals": 8},
            current_txid="d5ada6c79a4abd38fe2a95d8a4b86bec64af447eb3c5216e7e6bc278387d614e",
            current_vout=0,
            genesis_txid="d5ada6c79a4abd38fe2a95d8a4b86bec64af447eb3c5216e7e6bc278387d614e",
            genesis_block_height=1
        )
        
        # Should raise IntegrityError
        with pytest.raises(IntegrityError):
            db_session.add(duplicate_token)
            db_session.commit()


class TestHolderModel:
    """Tests for the Holder model."""
    
    def test_create_holder(self, db_session, sample_holder):
        """Test creating a Holder."""
        db_session.add(sample_holder)
        db_session.commit()
        
        # Query the holder
        saved_holder = db_session.query(Holder).filter(
            Holder.address == sample_holder.address
        ).first()
        
        # Verify attributes
        assert saved_holder is not None
        assert saved_holder.address == "12c6DSiU4Rq3P4ZxziKxzrL5LmMBrzjrJX"
        assert saved_holder.rxd_balance == Decimal("50.0")
        assert saved_holder.token_balances == {"glyph:1234": 1}
    
    def test_holder_primary_key_constraint(self, db_session, sample_holder):
        """Test that Holder primary key constraint works."""
        # Add the first holder
        db_session.add(sample_holder)
        db_session.commit()
        
        # Try to add a duplicate holder
        duplicate_holder = Holder(
            address=sample_holder.address,
            rxd_balance=Decimal("100.0"),
            token_balances={"glyph:5678": 1}
        )
        
        # Should raise IntegrityError
        with pytest.raises(IntegrityError):
            db_session.add(duplicate_holder)
            db_session.commit()


class TestSyncStateModel:
    """Tests for the SyncState model."""
    
    def test_create_sync_state(self, db_session, sample_sync_state):
        """Test creating a SyncState."""
        db_session.add(sample_sync_state)
        db_session.commit()
        
        # Query the sync state
        saved_sync_state = db_session.query(SyncState).filter(
            SyncState.id == sample_sync_state.id
        ).first()
        
        # Verify attributes
        assert saved_sync_state is not None
        assert saved_sync_state.id == 1
        assert saved_sync_state.current_height == 1
        assert saved_sync_state.current_hash == "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"
        assert saved_sync_state.is_syncing == 0
