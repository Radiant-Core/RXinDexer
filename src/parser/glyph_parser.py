# /Users/radiant/Desktop/RXinDexer/src/parser/glyph_parser.py
# This file handles parsing of Glyph tokens from transaction data.
# It extracts CBOR-encoded payloads and tracks token ownership across transactions.

import logging
import json
import cbor2
from typing import Dict, List, Any, Optional, Set
from sqlalchemy.orm import Session
from sqlalchemy import text, func

from src.models import UTXO, GlyphToken, Holder
from src.sync.rpc_client import RadiantRPC
from src.utils.transaction_helper import safe_transaction, get_token_addresses_safe, reset_failed_transactions
from src.utils.safe_queries import get_token_addresses_completely_safe

logger = logging.getLogger(__name__)

class GlyphParser:
    """
    Parser for Glyph tokens.
    Handles CBOR-encoded payloads and token ownership tracking.
    """
    
    def __init__(self, rpc: RadiantRPC, db: Session):
        """
        Initialize the Glyph token parser.
        
        Args:
            rpc: RPC client for Radiant Node
            db: Database session
        """
        self.rpc = rpc
        self.db = db
    
    def parse_transaction(self, tx: Dict[str, Any], height: int, block_hash: str) -> List[Dict[str, Any]]:
        """
        Parse a transaction to extract Glyph tokens.
        
        Args:
            tx: Transaction data from Radiant Node
            height: Block height
            block_hash: Block hash
            
        Returns:
            List of extracted token data
        """
        txid = tx.get("txid")
        tokens_found = []
        
        # Check if this transaction contains a Glyph token
        token_data = self._extract_glyph_token(tx)
        
        if token_data:
            token_ref = token_data.get("ref")
            
            if token_ref:
                # Check if token already exists in database
                token = self.db.query(GlyphToken).filter(GlyphToken.ref == token_ref).first()
                
                if not token:
                    # Create new token record
                    token = GlyphToken(
                        ref=token_ref,
                        type=token_data.get("type", "unknown"),
                        metadata=token_data.get("metadata", {}),
                        current_txid=txid,
                        current_vout=token_data.get("vout", 0),
                        genesis_txid=txid,
                        genesis_block_height=height
                    )
                    self.db.add(token)
                else:
                    # Update existing token location
                    token.current_txid = txid
                    token.current_vout = token_data.get("vout", 0)
                
                # Update the UTXO with token reference
                utxo = self.db.query(UTXO).filter(
                    UTXO.txid == txid,
                    UTXO.vout == token_data.get("vout", 0)
                ).first()
                
                if utxo:
                    utxo.token_ref = token_ref
                
                self.db.commit()
                tokens_found.append(token_data)
        
        return tokens_found
    
    def _extract_glyph_token(self, tx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Extract Glyph token data from a transaction.
        
        Args:
            tx: Transaction data
            
        Returns:
            Token data or None if no token found
        """
        # Check inputs for Glyph protocol signature
        for vin in tx.get("vin", []):
            if "scriptSig" not in vin:
                continue
                
            scriptSig = vin.get("scriptSig", {})
            asm = scriptSig.get("asm", "")
            
            # Look for "gly" prefix in the script
            if "gly" in asm:
                try:
                    # Get the raw transaction for better access to scripts
                    raw_tx = self.rpc.get_raw_transaction(tx["txid"], True)
                    
                    # Get the reveal script
                    reveal_script = raw_tx["vin"][0].get("scriptSig", {}).get("hex", "")
                    
                    if reveal_script.startswith("gly"):
                        # Extract CBOR data (skip "gly" prefix)
                        cbor_hex = reveal_script[6:]
                        cbor_data = cbor2.loads(bytes.fromhex(cbor_hex))
                        
                        # Extract token output
                        vout_idx = 0
                        if "vout" in cbor_data:
                            vout_idx = cbor_data["vout"]
                        
                        return {
                            "ref": cbor_data.get("ref"),
                            "type": cbor_data.get("type", "unknown"),  # "fungible", "non-fungible", "dmint"
                            "metadata": cbor_data.get("metadata", {}),
                            "vout": vout_idx
                        }
                except Exception as e:
                    logger.error(f"Failed to parse Glyph token in tx {tx['txid']}: {str(e)}")
        
        return None
    
    def update_token_balances(self):
        """
        Update token balances for all holders.
        This calculates which addresses hold which tokens.
        """
        # Reset any failed transactions before starting
        reset_failed_transactions(self.db)
        
        # Use our completely safe method to get token addresses without any JOINs
        try:
            # Use a direct connection with AUTOCOMMIT to bypass transaction issues completely
            with self.db.bind.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                token_owners = get_token_addresses_completely_safe(conn)
                logger.info(f"Successfully retrieved {len(token_owners)} token owners with completely safe method")
        except Exception as e:
            logger.error(f"Error getting token addresses: {str(e)}, falling back to empty list")
            token_owners = []
        
        # Group tokens by address
        address_tokens = {}
        for address, token_ref in token_owners:
            if address not in address_tokens:
                address_tokens[address] = []
            address_tokens[address].append(token_ref)
        
        # Update holder records with token balances
        for address, tokens in address_tokens.items():
            # Use SQL directly with ON CONFLICT to handle concurrent updates
            # This is safer for parallel processing than the check-then-insert pattern
            from sqlalchemy import text
            
            # Create token balances JSON
            token_balances = {token: 1 for token in tokens}
            
            self.db.execute(
                text("""
                INSERT INTO holders (address, rxd_balance, token_balances) 
                VALUES (:address, 0, :token_balances::jsonb)
                ON CONFLICT (address) DO UPDATE 
                SET token_balances = :token_balances::jsonb,
                    last_updated_at = NOW()
                """),
                {
                    "address": address,
                    "token_balances": json.dumps(token_balances)
                }
            )
        
        # Reset token balances for addresses that no longer hold tokens
        addresses_with_tokens = list(address_tokens.keys())
        holders_to_update = self.db.query(Holder).filter(
            Holder.address.notin_(addresses_with_tokens),
            (Holder.token_balances != '{}') & (Holder.token_balances != 'null')
        ).all()
        
        for holder in holders_to_update:
            holder.token_balances = {}
        
        self.db.commit()
    
    def get_token_holders(self, token_ref: str) -> List[str]:
        """
        Get all addresses holding a specific token.
        
        Args:
            token_ref: Token reference
            
        Returns:
            List of addresses holding the token
        """
        holders = self.db.query(UTXO.address).filter(
            UTXO.token_ref == token_ref,
            UTXO.spent == False
        ).distinct().all()
        
        return [h[0] for h in holders]
