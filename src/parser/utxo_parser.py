# /Users/radiant/Desktop/RXinDexer/src/parser/utxo_parser.py
# This file handles parsing of transaction outputs to extract and track UTXOs.
# It identifies addresses, amounts, and spent status for accurate balance tracking.

# /Users/radiant/Desktop/RXinDexer/src/parser/utxo_parser.py
# This file handles parsing of transaction outputs to extract and track UTXOs.
# It identifies addresses, amounts, and spent status for accurate balance tracking.

import logging
import json
import os
import time
from typing import Dict, List, Tuple, Any, Optional, Set
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import text, func
from sqlalchemy.orm import Session, sessionmaker

from src.models import UTXO, Holder
from src.models.database import engine
from src.sync.rpc_client import RadiantRPC
from src.utils.query_optimizer import create_optimized_temp_balances, get_large_balances, update_holder_balances_efficient, get_address_balance as get_balance_efficient, batch_update_utxos as batch_update_utxos_efficient

logger = logging.getLogger(__name__)

class UTXOParser:
    """
    Parser for transaction outputs (UTXOs).
    Handles extraction of addresses, amounts, and spent status.
    Supports parallel processing for high-volume blocks.
    """
    
    def __init__(self, rpc: RadiantRPC, db: Session):
        """
        Initialize the UTXO parser.
        
        Args:
            rpc: RPC client for Radiant Node
            db: Database session
        """
        self.rpc = rpc
        self.db = db
        
        # Make the engine available as an instance variable for consistency
        self.engine = engine
        
        # Create a sessionmaker for independent transactions
        self.Session = sessionmaker(bind=self.engine)
        
        # Configure parallel processing - can be adjusted via environment variable
        self.max_workers = int(os.environ.get('UTXO_MAX_WORKERS', '4'))
        self.parallel_threshold = int(os.environ.get('UTXO_PARALLEL_THRESHOLD', '5'))
        
        # Performance metrics
        self.processing_times = []
        self.last_time_report = time.time()
    
    def _process_transaction_worker(self, tx_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a single transaction in a worker thread.
        This is used for parallel processing of transactions in a block.
        
        Args:
            tx_data: Dictionary with transaction data and context
            
        Returns:
            Dictionary with processed UTXOs and stats
        """
        tx = tx_data['tx']
        height = tx_data['height']
        block_hash = tx_data['block_hash']
        txid = tx.get("txid")
        
        # Prepare batch operations
        spent_utxos = []
        new_utxos = []
        utxos_created = 0
        utxos_spent = 0
        
        try:
            # Process inputs (spending UTXOs)
            for vin in tx.get("vin", []):
                # Skip coinbase transactions
                if "coinbase" in vin:
                    continue
                    
                prev_txid = vin.get("txid")
                prev_vout = vin.get("vout")
                
                if prev_txid and prev_vout is not None:
                    # Add to batch of UTXOs to mark as spent
                    spent_utxos.append({
                        "txid": txid,
                        "prev_txid": prev_txid,
                        "prev_vout": prev_vout
                    })
                    utxos_spent += 1
            
            # Process outputs (creating UTXOs)
            for vout_idx, vout in enumerate(tx.get("vout", [])):
                try:
                    scriptPubKey = vout.get("scriptPubKey", {})
                    
                    # Skip non-standard and OP_RETURN outputs
                    if scriptPubKey.get("type") in ["nonstandard", "nulldata"]:
                        continue
                        
                    # Extract address(es)
                    addresses = scriptPubKey.get("addresses", [])
                    if not addresses and "address" in scriptPubKey:
                        addresses = [scriptPubKey["address"]]
                        
                    if not addresses:
                        continue
                        
                    # Use the first address (multi-sig support would need enhancement)
                    address = addresses[0]
                    amount = Decimal(str(vout.get("value", 0)))
                    
                    # Add to batch of new UTXOs
                    new_utxos.append({
                        "txid": txid,
                        "vout": vout_idx,
                        "address": address,
                        "amount": amount,
                        "height": height,
                        "block_hash": block_hash
                    })
                    utxos_created += 1
                except Exception as e:
                    pass  # Handle errors when aggregating results
        except Exception as e:
            pass  # Handle errors when aggregating results
        
        # Return the processed data for aggregation
        return {
            'txid': txid,
            'spent_utxos': spent_utxos,
            'new_utxos': new_utxos,
            'utxos_created': utxos_created,
            'utxos_spent': utxos_spent
        }
    
    def parse_block_parallel(self, block_data: Dict[str, Any]) -> Dict[str, int]:
        """
        Parse all transactions in a block using parallel processing for improved performance.
        This is used for blocks with many transactions to better utilize CPU cores.
        
        Args:
            block_data: Block data including transactions
            
        Returns:
            Dictionary with block processing statistics
        """
        start_time = time.time()
        height = block_data.get("height")
        block_hash = block_data.get("hash")
        transactions = block_data.get("tx", [])
        
        # Prepare context for each transaction
        tx_work_items = []
        for tx in transactions:
            tx_work_items.append({
                'tx': tx,
                'height': height,
                'block_hash': block_hash
            })
        
        # Process transactions in parallel
        all_spent_utxos = []
        all_new_utxos = []
        total_utxos_created = 0
        total_utxos_spent = 0
        glyph_tokens = 0  # Placeholder for future implementation
        
        # Use ThreadPoolExecutor for parallel processing
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all transactions for processing
            future_results = {executor.submit(self._process_transaction_worker, tx_data): tx_data for tx_data in tx_work_items}
            
            # Collect results as they complete
            for future in concurrent.futures.as_completed(future_results):
                try:
                    result = future.result()
                    all_spent_utxos.extend(result['spent_utxos'])
                    all_new_utxos.extend(result['new_utxos'])
                    total_utxos_created += result['utxos_created']
                    total_utxos_spent += result['utxos_spent']
                except Exception as e:
                    logger.error(f"Error processing transaction: {str(e)}")
        
        # Process all UTXOs in batch
        self._process_utxos_batch(all_spent_utxos, all_new_utxos)
        
        # Update holder balances (single operation for the entire block)
        self.update_holder_balances()
        
        # Record metrics
        elapsed = time.time() - start_time
        self.processing_times.append(elapsed)
        
        # Periodically log performance metrics
        current_time = time.time()
        if current_time - self.last_time_report >= 60:  # Report every minute
            if len(self.processing_times) > 0:
                avg_time = sum(self.processing_times) / len(self.processing_times)
                logger.info(f"Average block processing time: {avg_time:.4f}s over {len(self.processing_times)} blocks")
                self.processing_times = []  # Reset after reporting
            self.last_time_report = current_time
        
        return {
            'transactions': len(transactions),
            'utxos_created': total_utxos_created,
            'utxos_spent': total_utxos_spent,
            'glyph_tokens': glyph_tokens,
            'processing_time': elapsed
        }
    
    def _process_utxos_batch(self, spent_utxos, new_utxos):
        """
        Process batches of UTXOs (both spent and new) in a single database transaction.
        
        Args:
            spent_utxos: List of UTXOs to mark as spent
            new_utxos: List of new UTXOs to create
        """
        with self.engine.begin() as conn:
            # 1. Mark UTXOs as spent in true batch operation (much faster)
            if spent_utxos:
                try:
                    # Build the values list for a single bulk update
                    values_list = []
                    txid_vout_pairs = []
                    
                    for utxo in spent_utxos:
                        # Store the (txid, vout) pairs for the WHERE clause
                        txid_vout_pairs.append((utxo['prev_txid'], utxo['prev_vout']))
                        # Store the value for spent_txid
                        values_list.append(utxo['txid'])
                    
                    # Build a dynamic SQL query for bulk updates
                    # This uses PostgreSQL's unnest() function to efficiently update multiple rows
                    if len(spent_utxos) > 0:
                        # Create arrays for the WHERE conditions
                        txids_array = "ARRAY[{}]".format(
                            ','.join([f"'{pair[0]}'" for pair in txid_vout_pairs])
                        )
                        vouts_array = "ARRAY[{}]".format(
                            ','.join([str(pair[1]) for pair in txid_vout_pairs])
                        )
                        spent_txids_array = "ARRAY[{}]".format(
                            ','.join([f"'{txid}'" for txid in values_list])
                        )
                        
                        # Execute a single bulk update using arrays
                        conn.execute(text(f"""
                            UPDATE utxos u SET 
                                spent = TRUE, 
                                spent_txid = v.spent_txid,
                                updated_at = NOW()
                            FROM (
                                SELECT 
                                    unnest({txids_array}) as txid,
                                    unnest({vouts_array}) as vout,
                                    unnest({spent_txids_array}) as spent_txid
                            ) as v
                            WHERE u.txid = v.txid AND u.vout = v.vout
                        """))
                        
                        logger.info(f"Marked {len(spent_utxos)} UTXOs as spent in a single batch operation")
                except Exception as e:
                    logger.error(f"Failed to process batch spent UTXOs: {str(e)}")
            
            # 2. Create new UTXOs using true batch operations
            if new_utxos:
                try:
                    # For small numbers of UTXOs, use VALUES approach
                    if len(new_utxos) <= 100:
                        # Construct a single VALUES clause with all UTXOs
                        values_list = []
                        for utxo in new_utxos:
                            values_list.append(f"('{utxo['txid']}', {utxo['vout']}, '{utxo['address']}', {utxo['amount']}, FALSE, {utxo['height']}, '{utxo['block_hash']}', NOW(), NOW())")
                        
                        # Execute a single INSERT with multiple VALUES
                        values_sql = ",\n".join(values_list)
                        conn.execute(text(f"""
                            INSERT INTO utxos (txid, vout, address, amount, spent, block_height, block_hash, created_at, updated_at) 
                            VALUES {values_sql}
                            ON CONFLICT (txid, vout) DO UPDATE
                            SET address = EXCLUDED.address, 
                                amount = EXCLUDED.amount, 
                                block_height = EXCLUDED.block_height, 
                                block_hash = EXCLUDED.block_hash,
                                updated_at = NOW()
                        """))
                    else:
                        # For larger batches, use temporary table approach which scales better
                        # Create temporary table
                        conn.execute(text("""
                            CREATE TEMPORARY TABLE temp_utxos (
                                txid VARCHAR(64),
                                vout INTEGER,
                                address VARCHAR(64),
                                amount NUMERIC(38,8),
                                block_height INTEGER,
                                block_hash VARCHAR(64)
                            ) ON COMMIT DROP
                        """))
                        
                        # Use a COPY approach with StringIO for better performance
                        from io import StringIO
                        import csv
                        
                        # Create CSV in memory
                        csv_data = StringIO()
                        csv_writer = csv.writer(csv_data)
                        for utxo in new_utxos:
                            csv_writer.writerow([
                                utxo['txid'],
                                utxo['vout'],
                                utxo['address'],
                                utxo['amount'],
                                utxo['height'],
                                utxo['block_hash']
                            ])
                        
                        # Reset to beginning of StringIO
                        csv_data.seek(0)
                        
                        # Use raw connection for COPY command
                        raw_conn = conn.connection.connection
                        with raw_conn.cursor() as cursor:
                            cursor.copy_from(
                                csv_data,
                                'temp_utxos',
                                sep=',',
                                columns=('txid', 'vout', 'address', 'amount', 'block_height', 'block_hash')
                            )
                        
                        # Insert from temp table with single operation
                        conn.execute(text("""
                            INSERT INTO utxos (txid, vout, address, amount, spent, block_height, block_hash, created_at, updated_at)
                            SELECT txid, vout, address, amount, FALSE, block_height, block_hash, NOW(), NOW()
                            FROM temp_utxos
                            ON CONFLICT (txid, vout) DO UPDATE
                            SET address = EXCLUDED.address,
                                amount = EXCLUDED.amount,
                                block_height = EXCLUDED.block_height,
                                block_hash = EXCLUDED.block_hash,
                                updated_at = NOW()
                        """))
                        
                    logger.info(f"Created {len(new_utxos)} UTXOs in a single batch operation")
                except Exception as e:
                    logger.error(f"Failed to batch create UTXOs: {str(e)}")
        
    def parse_transaction(self, tx: Dict[str, Any], height: int, block_hash: str) -> Tuple[int, int]:
        """
        Parse a transaction to extract UTXOs with robust error handling and batch processing.
        Uses efficient batch operations for better performance while maintaining safety.
        
        Args:
            tx: Transaction data from Radiant Node
            height: Block height
            block_hash: Block hash
            
        Returns:
            Tuple of (created UTXOs count, spent UTXOs count)
        """
        txid = tx.get("txid")
        utxos_created = 0
        utxos_spent = 0
        
        # Prepare batch operations
        spent_utxos = []
        new_utxos = []
        
        try:
            # Process inputs (spending UTXOs)
            for vin in tx.get("vin", []):
                # Skip coinbase transactions
                if "coinbase" in vin:
                    continue
                    
                prev_txid = vin.get("txid")
                prev_vout = vin.get("vout")
                
                if prev_txid and prev_vout is not None:
                    # Add to batch of UTXOs to mark as spent
                    spent_utxos.append({
                        "txid": txid,
                        "prev_txid": prev_txid,
                        "prev_vout": prev_vout
                    })
                    utxos_spent += 1
            
            # Process outputs (creating UTXOs)
            for vout_idx, vout in enumerate(tx.get("vout", [])):
                try:
                    scriptPubKey = vout.get("scriptPubKey", {})
                    
                    # Skip non-standard and OP_RETURN outputs
                    if scriptPubKey.get("type") in ["nonstandard", "nulldata"]:
                        continue
                        
                    # Extract address(es)
                    addresses = scriptPubKey.get("addresses", [])
                    if not addresses and "address" in scriptPubKey:
                        addresses = [scriptPubKey["address"]]
                        
                    if not addresses:
                        logger.warning(f"No addresses found for output {txid}:{vout_idx}")
                        continue
                        
                    # Use the first address (multi-sig support would need enhancement)
                    address = addresses[0]
                    amount = Decimal(str(vout.get("value", 0)))
                    
                    # Add to batch of new UTXOs
                    new_utxos.append({
                        "txid": txid,
                        "vout": vout_idx,
                        "address": address,
                        "amount": amount,
                        "height": height,
                        "block_hash": block_hash
                    })
                    utxos_created += 1
                except Exception as e:
                    logger.warning(f"Error processing output {txid}:{vout_idx}: {str(e)}")
            
            # Execute batch operations within a transaction to support savepoints
            with self.engine.begin() as conn:
                # 1. Mark UTXOs as spent in true batch operation (much faster)
                if spent_utxos:
                    try:
                        # Build the values list for a single bulk update
                        values_list = []
                        txid_vout_pairs = []
                        
                        for utxo in spent_utxos:
                            # Store the (txid, vout) pairs for the WHERE clause
                            txid_vout_pairs.append((utxo['prev_txid'], utxo['prev_vout']))
                            # Store the value for spent_txid
                            values_list.append(utxo['txid'])
                        
                        # Build a dynamic SQL query for bulk updates
                        # This uses PostgreSQL's unnest() function to efficiently update multiple rows
                        if len(spent_utxos) > 0:
                            # Create arrays for the WHERE conditions
                            txids_array = "ARRAY[{}]".format(
                                ','.join([f"'{pair[0]}'" for pair in txid_vout_pairs])
                            )
                            vouts_array = "ARRAY[{}]".format(
                                ','.join([str(pair[1]) for pair in txid_vout_pairs])
                            )
                            spent_txids_array = "ARRAY[{}]".format(
                                ','.join([f"'{txid}'" for txid in values_list])
                            )
                            
                            # Execute a single bulk update using arrays
                            conn.execute(text(f"""
                                UPDATE utxos u SET 
                                    spent = TRUE, 
                                    spent_txid = v.spent_txid,
                                    updated_at = NOW()
                                FROM (
                                    SELECT 
                                        unnest({txids_array}) as txid,
                                        unnest({vouts_array}) as vout,
                                        unnest({spent_txids_array}) as spent_txid
                                ) as v
                                WHERE u.txid = v.txid AND u.vout = v.vout
                            """))
                            
                            logger.info(f"Marked {len(spent_utxos)} UTXOs as spent in a single batch operation")
                    except Exception as e:
                        logger.error(f"Failed to process batch spent UTXOs: {str(e)}")
                
                # 2. Create new UTXOs using true batch operations
                if new_utxos:
                    try:
                        # For small numbers of UTXOs, use VALUES approach
                        if len(new_utxos) <= 100:
                            # Construct a single VALUES clause with all UTXOs
                            values_list = []
                            for utxo in new_utxos:
                                values_list.append(f"('{utxo['txid']}', {utxo['vout']}, '{utxo['address']}', {utxo['amount']}, FALSE, {utxo['height']}, '{utxo['block_hash']}', NOW(), NOW())")
                            
                            # Execute a single INSERT with multiple VALUES
                            values_sql = ",\n".join(values_list)
                            conn.execute(text(f"""
                                INSERT INTO utxos (txid, vout, address, amount, spent, block_height, block_hash, created_at, updated_at) 
                                VALUES {values_sql}
                                ON CONFLICT (txid, vout) DO UPDATE
                                SET address = EXCLUDED.address, 
                                    amount = EXCLUDED.amount, 
                                    block_height = EXCLUDED.block_height, 
                                    block_hash = EXCLUDED.block_hash,
                                    updated_at = NOW()
                            """))
                        else:
                            # For larger batches, use temporary table approach which scales better
                            # Create temporary table
                            conn.execute(text("""
                                CREATE TEMPORARY TABLE temp_utxos (
                                    txid VARCHAR(64),
                                    vout INTEGER,
                                    address VARCHAR(64),
                                    amount NUMERIC(38,8),
                                    block_height INTEGER,
                                    block_hash VARCHAR(64)
                                ) ON COMMIT DROP
                            """))
                            
                            # Use a COPY approach with StringIO for better performance
                            from io import StringIO
                            import csv
                            
                            # Create CSV in memory
                            csv_data = StringIO()
                            csv_writer = csv.writer(csv_data)
                            for utxo in new_utxos:
                                csv_writer.writerow([
                                    utxo['txid'],
                                    utxo['vout'],
                                    utxo['address'],
                                    utxo['amount'],
                                    utxo['height'],
                                    utxo['block_hash']
                                ])
                            
                            # Reset to beginning of StringIO
                            csv_data.seek(0)
                            
                            # Use raw connection for COPY command
                            raw_conn = conn.connection.connection
                            with raw_conn.cursor() as cursor:
                                cursor.copy_from(
                                    csv_data,
                                    'temp_utxos',
                                    sep=',',
                                    columns=('txid', 'vout', 'address', 'amount', 'block_height', 'block_hash')
                                )
                            
                            # Insert from temp table with single operation
                            conn.execute(text("""
                                INSERT INTO utxos (txid, vout, address, amount, spent, block_height, block_hash, created_at, updated_at)
                                SELECT txid, vout, address, amount, FALSE, block_height, block_hash, NOW(), NOW()
                                FROM temp_utxos
                                ON CONFLICT (txid, vout) DO UPDATE
                                SET address = EXCLUDED.address,
                                    amount = EXCLUDED.amount,
                                    block_height = EXCLUDED.block_height,
                                    block_hash = EXCLUDED.block_hash,
                                    updated_at = NOW()
                            """))
                            
                        logger.info(f"Created {len(new_utxos)} UTXOs in a single batch operation")
                    except Exception as e:
                        logger.error(f"Failed to batch create UTXOs: {str(e)}")
        
            # Return the results
            return utxos_created, utxos_spent
        except Exception as e:
            logger.error(f"Error parsing transaction {txid}: {str(e)}")
            # Don't propagate the error, return what we have so far
            return utxos_created, utxos_spent
    
    def update_holder_balances(self):
        """
        Update all holder balances based on the current UTXO set.
        This calculates the RXD balance for each address.
        Uses optimized utility functions for maximum performance.
        """
        try:
            start_time = time.time()
            
            # Use the optimized utility function from our query_optimizer module
            # This completely eliminates the slow balance queries
            result = update_holder_balances_efficient(self.db)
            
            # Log the performance impact
            end_time = time.time()
            logger.info(f"Updated holder balances using optimized function in {end_time - start_time:.2f}s")
            
            # Get large balances for monitoring using the optimized function
            large_balances = get_large_balances(self.db, 1000000000)
            for address, balance in large_balances:
                logger.info(f"Address {address} has a large balance of {balance} RXD")
            
            return True
        
        except Exception as e:
            logger.error(f"Error updating holder balances: {str(e)}")
            return False
        
    def get_address_balance(self, address: str) -> Decimal:
        """
        Get the current balance for an address using the optimized materialized view.
        
        Args:
            address: Wallet address
            
        Returns:
            Current RXD balance
        """
        # Use the optimized function from our query_optimizer module
        return get_balance_efficient(self.db, address)
