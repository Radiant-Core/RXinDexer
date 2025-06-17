#!/usr/bin/env python
# /app/src/parser/utxo_parser.py - Fixed version with correct column names
# This file implements the UTXO parser for RXinDexer.

import logging
from typing import Dict, List, Any, Tuple
import json
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.sync.rpc_client import RadiantRPC

logger = logging.getLogger(__name__)

class UTXOParser:
    def __init__(self, rpc: RadiantRPC, db: Session):
        """Initialize the UTXO parser."""
        self.rpc = rpc
        self.db = db
    
    def parse_transaction(self, tx: Dict[str, Any], height: int, block_hash: str) -> Tuple[int, int]:
        """Parse a transaction and process its UTXOs."""
        tx_id = tx.get("txid", "")
        
        # Process inputs (mark UTXOs as spent)
        spent_count = 0
        utxos_to_spend = []
        
        for vin in tx.get("vin", []):
            prev_tx = vin.get("txid")
            prev_vout = vin.get("vout")
            
            # Skip coinbase inputs
            if prev_tx is None or prev_vout is None:
                continue
                
            utxos_to_spend.append((prev_tx, prev_vout, tx_id))
            spent_count += 1
        
        # Mark UTXOs as spent in batch
        if utxos_to_spend:
            spent_count = self._mark_utxos_spent(utxos_to_spend)
        
        # Process outputs (create new UTXOs)
        created_count = 0
        utxos_to_create = []
        
        for vout in tx.get("vout", []):
            # Skip non-standard outputs
            script_pub_key = vout.get("scriptPubKey", {})
            addresses = script_pub_key.get("addresses", [])
            
            # Extract single address or use empty string
            address = addresses[0] if addresses else ""
            
            # Add to batch
            utxos_to_create.append({
                "txid": tx_id,
                "vout": vout.get("n", 0),
                "address": address,
                "script_pubkey": script_pub_key.get("hex", ""),
                "amount": vout.get("value", 0),
                "block_height": height,
                "block_hash": block_hash
            })
        
        # Create UTXOs in batch
        if utxos_to_create:
            try:
                created_count = self._create_utxos_batch(utxos_to_create)
            except Exception as e:
                logger.error(f"Failed to batch create UTXOs: {e}")
        
        return created_count, spent_count
    
    def _create_utxos_batch(self, utxos: List[Dict[str, Any]]) -> int:
        """Create multiple UTXOs in a batch."""
        if not utxos:
            return 0
            
        try:
            for utxo in utxos:
                self.db.execute(
                    text("""
                        INSERT INTO utxos (txid, vout, address, amount, spent, block_height, block_hash, created_at, updated_at) 
                        VALUES (:txid, :vout, :address, :amount, FALSE, :block_height, :block_hash, NOW(), NOW())
                        ON CONFLICT (txid, vout) DO UPDATE
                        SET address = EXCLUDED.address, 
                            amount = EXCLUDED.amount, 
                            block_height = EXCLUDED.block_height, 
                            block_hash = EXCLUDED.block_hash,
                            updated_at = NOW()
                    """),
                    {
                        "txid": utxo["txid"],
                        "vout": utxo["vout"],
                        "address": utxo["address"],
                        "amount": utxo["amount"],
                        "block_height": utxo["block_height"],
                        "block_hash": utxo["block_hash"]
                    }
                )
            return len(utxos)
        except Exception as e:
            logger.error(f"Error creating UTXOs: {e}")
            raise
    
    def _mark_utxos_spent(self, utxos: List[Tuple[str, int, str]]) -> int:
        """Mark multiple UTXOs as spent in a batch."""
        if not utxos:
            return 0
            
        try:
            for prev_tx, prev_vout, spending_tx in utxos:
                self.db.execute(
                    text("""
                        UPDATE utxos
                        SET spent = TRUE,
                            spent_by_tx = :spending_tx,
                            updated_at = NOW()
                        WHERE txid = :prev_tx AND vout = :prev_vout
                    """),
                    {
                        "prev_tx": prev_tx,
                        "prev_vout": prev_vout,
                        "spending_tx": spending_tx
                    }
                )
            return len(utxos)
        except Exception as e:
            logger.error(f"Error marking UTXOs as spent: {e}")
            raise
