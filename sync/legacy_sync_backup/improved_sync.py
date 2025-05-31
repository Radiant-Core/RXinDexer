# /Users/radiant/Desktop/RXinDexer/improved_sync.py
# Consolidated blockchain synchronization script for RXinDexer
# Incorporates all fixes for timestamp handling and JOIN query issues

import os
import sys
import time
import logging
import psycopg2
import json
import cbor2
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Any, Optional
import requests
from decimal import Decimal

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Database connection parameters
DB_HOST = os.getenv('DB_HOST', 'db')
DB_PORT = int(os.getenv('DB_PORT', 5432))
DB_NAME = os.getenv('DB_NAME', 'rxindexer')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'postgres')

# RPC connection parameters
RPC_URL = os.getenv('RADIANT_RPC_URL', 'http://radiant:7332')
RPC_USER = os.getenv('RADIANT_RPC_USER', 'rxin')
RPC_PASSWORD = os.getenv('RADIANT_RPC_PASSWORD', 'securepassword')

# Sync configuration
MAX_WORKERS = int(os.getenv('SYNC_MAX_WORKERS', 4))
BATCH_SIZE = int(os.getenv('SYNC_BATCH_SIZE', 100))


class RXinDexerSync:
    """
    A robust blockchain synchronization manager for RXinDexer.
    Uses isolated connections to avoid transaction conflicts and implements
    safe querying practices.
    """
    
    def __init__(self):
        """Initialize the sync manager."""
        self.stop_requested = False
        
    def get_db_connection(self):
        """Create a fresh database connection with AUTOCOMMIT mode to avoid transaction issues."""
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        conn.autocommit = True  # Critical for avoiding transaction issues
        return conn
    
    def get_rpc_connection(self):
        """Create an RPC connection to the Radiant node with robust error handling."""
        class RadiantRPC:
            def __init__(self, url, user, password):
                self.url = url
                self.auth = (user, password)
                
            def _call_method(self, method, params=None):
                """Make an RPC call to the Radiant node with retry logic."""
                headers = {'content-type': 'application/json'}
                payload = {
                    'method': method,
                    'params': params or [],
                    'jsonrpc': '2.0',
                    'id': int(time.time() * 1000),
                }
                
                max_retries = 5
                retry_delay = 2  # seconds
                
                for attempt in range(1, max_retries + 1):
                    try:
                        response = requests.post(
                            self.url,
                            json=payload,
                            headers=headers,
                            auth=self.auth,
                            timeout=60
                        )
                        response.raise_for_status()
                        return response.json()['result']
                    except Exception as e:
                        if attempt < max_retries:
                            logger.warning(f"RPC call {method} failed (attempt {attempt}/{max_retries}): {str(e)}")
                            time.sleep(retry_delay)
                            retry_delay *= 1.5  # Exponential backoff
                        else:
                            logger.error(f"RPC call {method} failed after {max_retries} attempts: {str(e)}")
                            raise
            
            def get_blockchain_info(self):
                """Get current blockchain information."""
                return self._call_method('getblockchaininfo')
            
            def get_block_count(self):
                """Get current block height."""
                return self._call_method('getblockcount')
            
            def get_block_hash(self, height):
                """Get block hash for the given height."""
                return self._call_method('getblockhash', [height])
            
            def get_block(self, block_hash, verbosity=2):
                """Get block data for the given hash."""
                return self._call_method('getblock', [block_hash, verbosity])
            
            def get_raw_transaction(self, txid, verbose=True):
                """Get transaction data for the given txid."""
                return self._call_method('getrawtransaction', [txid, verbose])
                
        return RadiantRPC(RPC_URL, RPC_USER, RPC_PASSWORD)
    
    def get_sync_status(self):
        """Get current sync status from the database."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_height FROM sync_state WHERE id = 1")
                result = cur.fetchone()
                if result:
                    return result[0]
                return 0
    
    def update_sync_state(self, height, block_hash):
        """Update sync state in the database."""
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE sync_state 
                    SET current_height = %s, current_hash = %s, last_updated_at = NOW()
                    WHERE id = 1
                """, (height, block_hash))
    
    def process_transaction(self, tx, height, block_hash):
        """
        Process a transaction to extract UTXOs and token data.
        Uses isolated connections for different operations to avoid transaction conflicts.
        """
        txid = tx.get('txid')
        
        # Process inputs (mark UTXOs as spent)
        spent_utxos = []
        for vin in tx.get('vin', []):
            if 'txid' in vin:  # Skip coinbase
                spent_utxos.append({
                    'prev_txid': vin['txid'],
                    'prev_vout': vin['vout'],
                    'spent_by': txid
                })
        
        # Mark UTXOs as spent
        if spent_utxos:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    for utxo in spent_utxos:
                        cur.execute("""
                            UPDATE utxos 
                            SET spent = TRUE, spent_txid = %s, updated_at = NOW()
                            WHERE txid = %s AND vout = %s
                        """, (
                            utxo['spent_by'],
                            utxo['prev_txid'],
                            utxo['prev_vout']
                        ))
        
        # Process outputs (create new UTXOs)
        new_utxos = []
        for vout_idx, vout in enumerate(tx.get('vout', [])):
            script_pub_key = vout.get('scriptPubKey', {})
            
            # Skip non-standard and OP_RETURN outputs
            if script_pub_key.get('type') in ['nonstandard', 'nulldata']:
                continue
            
            # Extract address(es)
            addresses = script_pub_key.get('addresses', [])
            if not addresses and 'address' in script_pub_key:
                addresses = [script_pub_key['address']]
            
            if not addresses:
                logger.warning(f"No addresses found for output {txid}:{vout_idx}")
                continue
            
            # Use the first address (multi-sig support would need enhancement)
            address = addresses[0]
            amount = Decimal(str(vout.get('value', 0)))
            script_type = script_pub_key.get('type', 'unknown')
            
            # Add to batch of new UTXOs
            new_utxos.append({
                'txid': txid,
                'vout': vout_idx,
                'address': address,
                'amount': amount,
                'script_type': script_type,
                'height': height,
                'block_hash': block_hash
            })
        
        # Insert new UTXOs
        if new_utxos:
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    for utxo in new_utxos:
                        cur.execute("""
                            INSERT INTO utxos (
                                txid, vout, address, amount, script_type, spent, block_height, block_hash, created_at, updated_at
                            ) VALUES (%s, %s, %s, %s, %s, FALSE, %s, %s, NOW(), NOW())
                            ON CONFLICT (txid, vout) DO UPDATE
                            SET address = EXCLUDED.address,
                                amount = EXCLUDED.amount,
                                script_type = EXCLUDED.script_type,
                                block_height = EXCLUDED.block_height,
                                block_hash = EXCLUDED.block_hash,
                                updated_at = NOW()
                        """, (
                            utxo['txid'],
                            utxo['vout'],
                            utxo['address'],
                            utxo['amount'],
                            utxo['script_type'],
                            utxo['height'],
                            utxo['block_hash']
                        ))
        
        # Process Glyph tokens
        self.process_glyph_tokens(tx, height, block_hash)
        
        return len(new_utxos), len(spent_utxos)
    
    def process_glyph_tokens(self, tx, height, block_hash):
        """
        Process Glyph tokens in a transaction.
        Uses isolated connections to avoid transaction conflicts.
        """
        txid = tx.get('txid')
        
        # Check inputs for Glyph protocol signature
        for vin in tx.get('vin', []):
            if 'scriptSig' not in vin:
                continue
            
            script_sig = vin.get('scriptSig', {})
            asm = script_sig.get('asm', '')
            
            # Look for "gly" prefix in the script
            if 'gly' in asm:
                try:
                    # Get the raw transaction
                    rpc = self.get_rpc_connection()
                    raw_tx = rpc.get_raw_transaction(txid, True)
                    
                    # Get the reveal script
                    reveal_script = raw_tx['vin'][0].get('scriptSig', {}).get('hex', '')
                    
                    if reveal_script.startswith('gly'):
                        # Extract CBOR data (skip "gly" prefix)
                        cbor_hex = reveal_script[6:]
                        cbor_data = cbor2.loads(bytes.fromhex(cbor_hex))
                        
                        # Extract token data
                        token_data = {
                            'ref': cbor_data.get('ref'),
                            'type': cbor_data.get('type', 'unknown'),
                            'metadata': json.dumps(cbor_data.get('metadata', {})),
                            'vout': cbor_data.get('vout', 0),
                            'height': height,
                            'block_hash': block_hash,
                            'txid': txid,
                            'minter_address': None,  # Will be set if found
                            'token_supply': cbor_data.get('supply', 1)
                        }
                        
                        # Try to determine minter address from the transaction outputs
                        if len(tx.get('vout', [])) > token_data['vout']:
                            vout = tx['vout'][token_data['vout']]
                            script_pub_key = vout.get('scriptPubKey', {})
                            addresses = script_pub_key.get('addresses', [])
                            if addresses:
                                token_data['minter_address'] = addresses[0]
                        
                        # Try to determine collection ID if possible
                        if 'metadata' in cbor_data and isinstance(cbor_data['metadata'], dict):
                            if 'collection' in cbor_data['metadata']:
                                token_data['collection_id'] = str(cbor_data['metadata']['collection'])
                        
                        # Store token data in a separate isolated connection
                        with self.get_db_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO glyph_tokens (
                                        ref, type, token_metadata, current_txid, current_vout, 
                                        genesis_txid, genesis_block_height, token_supply, 
                                        minter_address, collection_id, created_at, updated_at
                                    ) VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                                    ON CONFLICT (ref) DO UPDATE
                                    SET type = EXCLUDED.type,
                                        token_metadata = EXCLUDED.token_metadata,
                                        current_txid = EXCLUDED.current_txid,
                                        current_vout = EXCLUDED.current_vout,
                                        token_supply = EXCLUDED.token_supply,
                                        updated_at = NOW()
                                """, (
                                    token_data['ref'],
                                    token_data['type'],
                                    token_data['metadata'],
                                    token_data['txid'],
                                    token_data['vout'],
                                    token_data['txid'],
                                    token_data['height'],
                                    token_data['token_supply'],
                                    token_data['minter_address'],
                                    token_data.get('collection_id')
                                ))
                                
                                # Update UTXO with token reference
                                cur.execute("""
                                    UPDATE utxos
                                    SET token_ref = %s, updated_at = NOW()
                                    WHERE txid = %s AND vout = %s
                                """, (
                                    token_data['ref'],
                                    token_data['txid'],
                                    token_data['vout']
                                ))
                except Exception as e:
                    logger.error(f"Error processing Glyph token in tx {txid}: {str(e)}")
    
    def update_token_balances(self):
        """
        Update token balances using a completely safe approach without JOIN queries.
        This avoids the transaction issues that were occurring with JOIN queries.
        """
        logger.info("Updating token balances with completely safe approach")
        
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                # Step 1: First, get all unspent UTXOs with token references
                cur.execute("""
                    SELECT address, token_ref 
                    FROM utxos 
                    WHERE spent = false AND token_ref IS NOT NULL
                """)
                unspent_utxos = cur.fetchall()
                
                if not unspent_utxos:
                    logger.info("No token references found in unspent UTXOs")
                    return
                
                # Step 2: Get all valid token references
                token_refs = [row[1] for row in unspent_utxos]
                
                # Step 3: Format for SQL IN clause
                placeholders = ", ".join(f"'{ref}'" for ref in token_refs)
                
                # Step 4: Check which token references actually exist in glyph_tokens
                cur.execute(f"""
                    SELECT ref FROM glyph_tokens WHERE ref IN ({placeholders})
                """)
                valid_tokens = cur.fetchall()
                
                # Step 5: Create a set of valid token references for faster lookup
                valid_token_set = {row[0] for row in valid_tokens}
                
                # Step 6: Filter utxos to only those with valid token references
                token_owners = [(row[0], row[1]) for row in unspent_utxos if row[1] in valid_token_set]
                
                # Group tokens by address
                address_tokens = {}
                for address, token_ref in token_owners:
                    if address not in address_tokens:
                        address_tokens[address] = []
                    address_tokens[address].append(token_ref)
                
                # Update holder records with token balances
                for address, tokens in address_tokens.items():
                    # Create token balances JSON
                    token_balances = {token: 1 for token in tokens}
                    token_count = len(tokens)
                    
                    # Get RXD balance for this address
                    cur.execute("""
                        SELECT COALESCE(SUM(amount), 0)
                        FROM utxos
                        WHERE address = %s AND spent = false
                    """, (address,))
                    
                    rxd_balance = cur.fetchone()[0] or Decimal('0')
                    
                    cur.execute("""
                        INSERT INTO holders (
                            address, rxd_balance, token_balances, token_count, transaction_count
                        ) VALUES (%s, %s, %s::jsonb, %s, 0)
                        ON CONFLICT (address) DO UPDATE 
                        SET token_balances = %s::jsonb,
                            token_count = %s,
                            rxd_balance = %s,
                            last_updated_at = NOW()
                    """, (
                        address,
                        rxd_balance,
                        json.dumps(token_balances),
                        token_count,
                        json.dumps(token_balances),
                        token_count,
                        rxd_balance
                    ))
    
    def refresh_materialized_views(self):
        """Refresh materialized views for API optimization."""
        logger.info("Refreshing materialized views")
        
        with self.get_db_connection() as conn:
            with conn.cursor() as cur:
                # Check if token_holder_view exists
                cur.execute("""
                    SELECT 1 FROM pg_class WHERE relname = 'token_holder_view' AND relkind = 'm'
                """)
                
                if cur.fetchone():
                    logger.info("Refreshing token_holder_view")
                    cur.execute("REFRESH MATERIALIZED VIEW token_holder_view")
                
                # Check if rich_list_view exists
                cur.execute("""
                    SELECT 1 FROM pg_class WHERE relname = 'rich_list_view' AND relkind = 'm'
                """)
                
                if cur.fetchone():
                    logger.info("Refreshing rich_list_view")
                    cur.execute("REFRESH MATERIALIZED VIEW rich_list_view")
    
    def process_block(self, height):
        """Process a single block at the specified height."""
        rpc = self.get_rpc_connection()
        
        try:
            # Get block hash and data
            block_hash = rpc.get_block_hash(height)
            block_data = rpc.get_block(block_hash)
            
            logger.info(f"Processing block {height} with {len(block_data.get('tx', []))} transactions")
            
            # Store block in database
            with self.get_db_connection() as conn:
                with conn.cursor() as cur:
                    # Insert block data
                    cur.execute("""
                        INSERT INTO blocks (
                            hash, height, version, prev_hash, merkle_root, 
                            timestamp, bits, nonce, chainwork
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (hash) DO UPDATE
                        SET height = EXCLUDED.height,
                            version = EXCLUDED.version,
                            prev_hash = EXCLUDED.prev_hash,
                            merkle_root = EXCLUDED.merkle_root,
                            timestamp = EXCLUDED.timestamp,
                            bits = EXCLUDED.bits,
                            nonce = EXCLUDED.nonce,
                            chainwork = EXCLUDED.chainwork,
                            updated_at = NOW()
                    """, (
                        block_hash,
                        height,
                        block_data.get('version'),
                        block_data.get('previousblockhash', ''),
                        block_data.get('merkleroot'),
                        block_data.get('time'),
                        block_data.get('bits'),
                        block_data.get('nonce'),
                        block_data.get('chainwork', '')
                    ))
            
            # Process each transaction
            utxos_created = 0
            utxos_spent = 0
            
            for tx in block_data.get('tx', []):
                created, spent = self.process_transaction(tx, height, block_hash)
                utxos_created += created
                utxos_spent += spent
            
            # Update sync state
            self.update_sync_state(height, block_hash)
            
            # Periodically update token balances and refresh views
            if height % 10 == 0:
                self.update_token_balances()
                self.refresh_materialized_views()
            
            logger.info(f"Processed block {height}: {utxos_created} UTXOs created, {utxos_spent} UTXOs spent")
            return True
        except Exception as e:
            logger.error(f"Error processing block {height}: {str(e)}")
            return False
    
    def process_blocks(self, start_height, end_height):
        """Process a range of blocks."""
        success_count = 0
        failure_count = 0
        
        for height in range(start_height, end_height + 1):
            if self.process_block(height):
                success_count += 1
            else:
                failure_count += 1
                
            # Check if we should stop
            if self.stop_requested:
                logger.info("Stop requested, halting block processing")
                break
        
        return success_count, failure_count
    
    def process_blocks_parallel(self, start_height, end_height):
        """Process a range of blocks in parallel."""
        logger.info(f"Processing blocks {start_height} to {end_height} with {MAX_WORKERS} workers")
        
        # Break into smaller chunks
        chunk_size = min(BATCH_SIZE, end_height - start_height + 1)
        chunks = []
        
        for i in range(start_height, end_height + 1, chunk_size):
            chunk_end = min(i + chunk_size - 1, end_height)
            chunks.append((i, chunk_end))
        
        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Submit all chunks for processing
            futures = {
                executor.submit(self.process_blocks, chunk[0], chunk[1]): chunk
                for chunk in chunks
            }
            
            # Process results as they complete
            for future in futures:
                try:
                    success, failure = future.result()
                    chunk = futures[future]
                    logger.info(f"Chunk {chunk[0]}-{chunk[1]}: {success} blocks processed, {failure} blocks failed")
                    results.append((success, failure))
                except Exception as e:
                    logger.error(f"Error processing chunk: {str(e)}")
        
        total_success = sum(r[0] for r in results)
        total_failure = sum(r[1] for r in results)
        
        logger.info(f"Processed {total_success + total_failure} blocks: {total_success} successful, {total_failure} failed")
        return total_success, total_failure
    
    def run_sync(self):
        """Run the blockchain synchronization process."""
        try:
            rpc = self.get_rpc_connection()
            
            # Get current blockchain height
            chain_height = rpc.get_block_count()
            
            # Get current sync status
            current_height = self.get_sync_status()
            
            logger.info(f"Current sync height: {current_height}, blockchain height: {chain_height}")
            
            if current_height >= chain_height:
                logger.info("Already fully synced!")
                return
            
            # Calculate blocks to sync
            blocks_to_sync = chain_height - current_height
            logger.info(f"Need to sync {blocks_to_sync} blocks")
            
            # Start syncing
            start_time = time.time()
            
            if blocks_to_sync > 0:
                success, failure = self.process_blocks_parallel(current_height + 1, chain_height)
                
                # Final token balance update and view refresh
                self.update_token_balances()
                self.refresh_materialized_views()
                
                end_time = time.time()
                elapsed = end_time - start_time
                
                logger.info(f"Sync completed in {elapsed:.2f}s: {success} blocks processed, {failure} blocks failed")
        except Exception as e:
            logger.error(f"Error during sync: {str(e)}")


def main():
    """Main entry point."""
    logger.info("Starting RXinDexer blockchain sync")
    
    sync_manager = RXinDexerSync()
    sync_manager.run_sync()
    
    logger.info("Sync process completed")


if __name__ == "__main__":
    main()
