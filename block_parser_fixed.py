# /app/src/parser/block_parser.py
# This file implements the block parser for RXinDexer.
# It extracts and stores block and transaction data from the blockchain.

import os
import logging
from typing import Dict, List, Any, Set, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import text

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
    
    def parse_block(self, block: Dict[str, Any], height: int, block_hash: str = None) -> Dict[str, int]:
        """
        Parse a block and extract all relevant data.
        Uses parallel processing for blocks with many transactions.
        All database operations are wrapped in a transaction to ensure consistency.
        
        Args:
            block: Block data from Radiant Node
            height: Block height
            block_hash: Block hash (optional)
            
        Returns:
            Statistics about the parsed data
        """
        if not block_hash:
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
        
        # Check if a transaction is already in progress
        need_transaction = not self.db.in_transaction()
        logger.debug(f"Need transaction: {need_transaction}")
        
        try:
            # Start a transaction for the entire block processing if none exists
            if need_transaction:
                logger.info(f"Starting new transaction for block {height}")
                self.db.begin()
            
            # First, insert the block into the database
            logger.info(f"Inserting block {height} with hash {block_hash[:10]}...")
            self._insert_block(block, height, block_hash)
            
            # Next, insert all transactions into the database using the same transaction
            self._insert_transactions(txs, height, block_hash)
            
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
            
            # Commit transaction if we started one
            if need_transaction:
                logger.info(f"Committing transaction for block {height}")
                self.db.commit()
                logger.info(f"Successfully committed block {height} with {tx_count} transactions")
            
            logger.info(f"Block {height} stats: {stats}")
            return stats
        
        except Exception as e:
            # Rollback transaction if we started one and an error occurred
            if need_transaction:
                logger.error(f"Rolling back transaction for block {height} due to error: {str(e)}")
                self.db.rollback()
            # Re-raise the exception to be handled by the caller
            raise
    
    def _insert_block(self, block, height, block_hash):
        """
        Insert a block into the database.
        Uses the current session transaction.
        
        Args:
            block: Block data from Radiant Node
            height: Block height
            block_hash: Block hash
            
        Returns:
            bool: True if block was inserted successfully, False otherwise
        """
        try:
            if not block_hash:
                block_hash = block.get("hash")
                
            prev_hash = block.get("previousblockhash", "")
            merkle_root = block.get("merkleroot", "")
            # Ensure timestamp is an integer (Unix epoch seconds)
            timestamp = int(block.get("time", 0))
            if not isinstance(timestamp, int):
                logger.warning(f"Invalid timestamp type for block {height}, converting to int")
                timestamp = int(timestamp) if timestamp is not None else 0
                
            nonce = block.get("nonce", 0)
            bits = block.get("bits", "")
            version = block.get("version", 0)
            size = block.get("size", 0)
            weight = block.get("weight", 0)
            tx_count = len(block.get("tx", []))
            
            # Check if block already exists
            existing_block = self.db.execute(
                text("SELECT hash FROM blocks WHERE hash = :hash"),
                {"hash": block_hash}
            ).fetchone()
            
            if not existing_block:
                # Insert the block if it doesn't exist
                # Use text() for SQL with explicit parameter bindings
                logger.info(f"Inserting new block: {height}, hash={block_hash[:10]}...")
                
                self.db.execute(
                    text("""
                    INSERT INTO blocks (
                        hash, height, prev_hash, merkle_root, timestamp, nonce, 
                        bits, version, size, weight, tx_count, created_at, updated_at
                    ) VALUES (
                        :hash, :height, :prev_hash, :merkle_root, :timestamp, :nonce,
                        :bits, :version, :size, :weight, :tx_count, NOW(), NOW()
                    )
                    ON CONFLICT (hash) DO UPDATE
                    SET height = EXCLUDED.height,
                        prev_hash = EXCLUDED.prev_hash,
                        merkle_root = EXCLUDED.merkle_root,
                        timestamp = EXCLUDED.timestamp,
                        nonce = EXCLUDED.nonce,
                        bits = EXCLUDED.bits,
                        version = EXCLUDED.version,
                        size = EXCLUDED.size,
                        weight = EXCLUDED.weight,
                        tx_count = EXCLUDED.tx_count,
                        updated_at = NOW()
                    """),
                    {
                        "hash": block_hash,
                        "height": height,
                        "prev_hash": prev_hash,
                        "merkle_root": merkle_root,
                        "timestamp": timestamp,
                        "nonce": nonce,
                        "bits": bits,
                        "version": version,
                        "size": size,
                        "weight": weight,
                        "tx_count": tx_count
                    }
                )
                
                # Force a flush to detect any errors early
                self.db.flush()
                logger.info(f"Block {height} insert flushed to database")
                
                return True
            else:
                logger.info(f"Block {height} already exists, skipping insert")
                return False
        except Exception as e:
            logger.error(f"Error inserting block {height}: {str(e)}")
            raise
    
    def _insert_transactions(self, txs, height, block_hash):
        """
        Insert transactions into the database.
        Uses the current session transaction.
        
        Args:
            txs: Transaction data from Radiant Node
            height: Block height
            block_hash: Block hash
        """
        try:
            for tx_index, tx in enumerate(txs):
                # Get transaction hash
                tx_hash = tx.get("hash") or tx.get("txid")
                if not tx_hash:
                    logger.warning(f"Transaction at index {tx_index} in block {height} has no hash")
                    continue
                    
                # Get transaction size and weight
                size = tx.get("size", 0)
                weight = tx.get("weight", 0)
                
                # Get lock time
                lock_time = tx.get("locktime", 0)
                
                # Get input and output counts
                vin = tx.get("vin", [])
                vout = tx.get("vout", [])
                input_count = len(vin)
                output_count = len(vout)
                
                # Insert the transaction
                logger.debug(f"Inserting transaction {tx_hash[:10]}...")
                
                self.db.execute(
                    text("""
                    INSERT INTO transactions (
                        hash, block_hash, block_height, index_in_block, timestamp,
                        size, weight, lock_time, input_count, output_count,
                        created_at, updated_at
                    ) VALUES (
                        :hash, :block_hash, :height, :index, :timestamp,
                        :size, :weight, :lock_time, :input_count, :output_count,
                        NOW(), NOW()
                    )
                    ON CONFLICT (hash) DO UPDATE
                    SET block_hash = EXCLUDED.block_hash,
                        block_height = EXCLUDED.block_height,
                        index_in_block = EXCLUDED.index_in_block,
                        timestamp = EXCLUDED.timestamp,
                        size = EXCLUDED.size,
                        weight = EXCLUDED.weight,
                        lock_time = EXCLUDED.lock_time,
                        input_count = EXCLUDED.input_count,
                        output_count = EXCLUDED.output_count,
                        updated_at = NOW()
                    """),
                    {
                        "hash": tx_hash,
                        "block_hash": block_hash,
                        "height": height,
                        "index": tx_index,
                        # Ensure timestamp is an integer (Unix epoch seconds)
                        "timestamp": int(tx.get("time", 0) or block.get("time", 0)),
                        "size": size,
                        "weight": weight,
                        "lock_time": lock_time,
                        "input_count": input_count,
                        "output_count": output_count
                    }
                )
                
            # Force a flush to detect any errors early
            self.db.flush()
            logger.info(f"Inserted {len(txs)} transactions for block {height}")
            
        except Exception as e:
            logger.error(f"Error inserting transactions for block {height}: {str(e)}")
            raise
            
    def _update_holder_balances(self):
        """
        Update holder balances based on the current UTXO set.
        This is typically done after processing a batch of blocks.
        """
        # Note: In a production system, this would be optimized to update only
        # the holders affected by the current block, rather than recalculating all balances
        self.utxo_parser.update_holder_balances()
        self.glyph_parser.update_token_balances()
