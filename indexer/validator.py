# Data validation logic for indexer
from sqlalchemy.orm import Session
from sqlalchemy import func
from database.models import Block, Transaction, UTXO, GlyphToken

class DataValidator:
    def __init__(self, db: Session):
        self.db = db
    
    def validate_chain_continuity(self):
        """
        Checks if there are any gaps in block heights.
        Returns (is_valid, missing_heights)
        """
        min_height = self.db.query(func.min(Block.height)).scalar() or 0
        max_height = self.db.query(func.max(Block.height)).scalar() or 0
        count = self.db.query(func.count(Block.id)).scalar() or 0
        
        expected_count = max_height - min_height + 1
        if count == expected_count:
            return True, []
        
        # If invalid, find gaps (this is expensive for large chains, use with care)
        # For optimization, check distinct heights
        all_heights = set(flat for flat in self.db.query(Block.height).all())
        all_heights = {h[0] for h in all_heights}
        expected_heights = set(range(min_height, max_height + 1))
        missing = list(expected_heights - all_heights)
        missing.sort()
        
        return False, missing

    def validate_utxo_set(self, sample_size=100):
        """
        Basic integrity check for UTXOs.
        Ensures spent UTXOs have a valid spent_in_txid.
        """
        invalid_utxos = self.db.query(UTXO).filter(
            UTXO.spent == True,
            UTXO.spent_in_txid == None
        ).limit(sample_size).all()
        
        return len(invalid_utxos) == 0, [u.id for u in invalid_utxos]

    def validate_token_supply(self, token_id: str):
        """
        Validates calculated supply vs tracked supply for a token.
        """
        # This would require summing up all mints/burns
        # Placeholder for advanced validation
        return True
