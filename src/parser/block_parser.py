# /Users/radiant/Desktop/RXinDexer/src/parser/block_parser.py
# This file handles parsing of blockchain blocks to extract transactions, UTXOs, and Glyph tokens.
# It coordinates the parsing process and updates the database with the extracted data.

import logging
import os
from typing import Dict, List, Any, Set
from sqlalchemy.orm import Session

from src.sync.rpc_client import RadiantRPC
from .utxo_parser import UTXOParser
from .glyph_parser import GlyphParser

logger = logging.getLogger(__name__)

class BlockParser:
    """
    Parser for blockchain blocks.
    Coordinates extraction of transactions, UTXOs, and Glyph tokens.
    """
    
    def __init__(self, rpc: RadiantRPC, db: Session):
        """
        Initialize the block parser.
        
        Args:
            rpc: RPC client for Radiant Node
            db: Database session
        """
        self.rpc = rpc
        self.db = db
        self.utxo_parser = UTXOParser(rpc, db)
        self.glyph_parser = GlyphParser(rpc, db)
        
        # Configure parallel processing - can be adjusted via environment variable
        self.parallel_threshold = int(os.environ.get('BLOCK_PARALLEL_THRESHOLD', '5'))
        self.enable_parallel = os.environ.get('ENABLE_PARALLEL_PROCESSING', 'true').lower() == 'true'
    
    def parse_block(self, block: Dict[str, Any], height: int) -> Dict[str, int]:
        """
        Parse a block and extract all relevant data.
        Uses parallel processing for blocks with many transactions.
        
        Args:
            block: Block data from Radiant Node
            height: Block height
            
        Returns:
            Statistics about the parsed data
        """
        block_hash = block.get("hash")
        txs = block.get("tx", [])
        tx_count = len(txs)
        
        logger.info(f"Parsing block {height} with {tx_count} transactions")
        
        # Determine if we should use parallel processing based on transaction count
        use_parallel = self.enable_parallel and tx_count >= self.parallel_threshold
        
        stats = {
            "transactions": tx_count,
            "utxos_created": 0,
            "utxos_spent": 0,
            "glyph_tokens": 0
        }
        
        # For blocks with many transactions, use parallel processing
        if use_parallel:
            # Add block context to the block data for parallel processing
            block["height"] = height
            
            # Process all transactions in parallel
            utxo_stats = self.utxo_parser.parse_block_parallel(block)
            
            # Update statistics
            stats["utxos_created"] = utxo_stats["utxos_created"]
            stats["utxos_spent"] = utxo_stats["utxos_spent"]
            
            # Process Glyph tokens (still sequential as this is typically less intensive)
            for tx in txs:
                tokens = self.glyph_parser.parse_transaction(tx, height, block_hash)
                stats["glyph_tokens"] += len(tokens)
            
            # Glyph token balances still need to be updated
            self.glyph_parser.update_token_balances()
            
            logger.info(f"Block {height} processed in parallel: {utxo_stats.get('processing_time', 0):.4f}s")
        else:
            # Standard sequential processing for blocks with few transactions
            for tx in txs:
                # Parse UTXOs (both inputs and outputs)
                utxos_created, utxos_spent = self.utxo_parser.parse_transaction(tx, height, block_hash)
                stats["utxos_created"] += utxos_created
                stats["utxos_spent"] += utxos_spent
                
                # Parse Glyph tokens if present
                tokens = self.glyph_parser.parse_transaction(tx, height, block_hash)
                stats["glyph_tokens"] += len(tokens)
            
            # Update holder balances based on the UTXOs
            self._update_holder_balances()
        
        logger.info(f"Block {height} stats: {stats}")
        return stats
    
    def _update_holder_balances(self):
        """
        Update holder balances based on the current UTXO set.
        This is typically done after processing a batch of blocks.
        """
        # Note: In a production system, this would be optimized to update only
        # the holders affected by the current block, rather than recalculating all balances
        self.utxo_parser.update_holder_balances()
        self.glyph_parser.update_token_balances()
