# /Users/radiant/Desktop/RXinDexer/src/sync/rpc_client_dev.py
# This file provides a development-friendly RPC client that can operate without a live Radiant node.
# It returns mock data for development and testing purposes.

import os
import logging
import time
from typing import Dict, List, Any, Optional, Tuple
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

class RadiantRPC:
    """
    Development version of RadiantRPC client for testing APIs without a live Radiant node.
    Provides mock blockchain data responses for development and testing.
    """
    
    def __init__(self, pool_size=None):
        """Initialize the mock RPC client.
        
        Args:
            pool_size: Ignored in development version
        """
        self.enable_cache = os.getenv("ENABLE_CACHE", "true").lower() == "true"
        self.connected = True
        self.mock_mode = True
        logger.info("Initialized RadiantRPC in development mode (mock responses)")
    
    def get_block_count(self) -> int:
        """Get current blockchain height."""
        return 1000000  # Mock block height
    
    def get_block_hash(self, height: int) -> str:
        """Get block hash for a given height."""
        return f"000000000000000000{height:010d}"  # Mock block hash
    
    def get_block(self, block_hash: str, verbose: int = 1) -> Dict[str, Any]:
        """Get block data for a given hash."""
        # Create a mock block with common fields
        height = int(block_hash[-8:])
        return {
            "hash": block_hash,
            "confirmations": 10,
            "size": 1000,
            "height": height,
            "version": 536870912,
            "versionHex": "20000000",
            "merkleroot": f"abcd1234{height:08d}",
            "time": int(time.time()) - (1000000 - height) * 600,
            "mediantime": int(time.time()) - (1000000 - height) * 600 - 300,
            "nonce": 123456789,
            "bits": "1d00ffff",
            "difficulty": 1,
            "chainwork": "000000000000000000000000000000000000000000000000000000000000",
            "nTx": 5,
            "previousblockhash": f"000000000000000000{height-1:010d}" if height > 0 else None,
            "nextblockhash": f"000000000000000000{height+1:010d}" if height < 1000000 else None,
            "tx": [
                f"tx{i}_{height}" for i in range(5)
            ]
        }
    
    def get_raw_transaction(self, txid: str, verbose: int = 1) -> Dict[str, Any]:
        """Get transaction data for a given transaction ID."""
        # Extract block height from mock txid format
        parts = txid.split('_')
        height = int(parts[1]) if len(parts) > 1 else 1000000
        tx_index = int(parts[0][2:]) if parts[0].startswith('tx') else 0
        
        # Create a mock transaction with common fields
        return {
            "txid": txid,
            "hash": txid,
            "version": 2,
            "size": 500,
            "vsize": 500,
            "weight": 2000,
            "locktime": 0,
            "vin": [
                {
                    "txid": f"tx{tx_index-1}_{height-1}" if tx_index > 0 else f"tx5_{height-2}",
                    "vout": 0,
                    "scriptSig": {
                        "asm": "304502...",
                        "hex": "48304502..."
                    },
                    "sequence": 4294967295
                }
            ],
            "vout": [
                {
                    "value": 50.0,
                    "n": 0,
                    "scriptPubKey": {
                        "asm": "OP_DUP OP_HASH160 ...",
                        "hex": "76a914...",
                        "reqSigs": 1,
                        "type": "pubkeyhash",
                        "addresses": [
                            f"radiantdev{height}{tx_index}"
                        ]
                    }
                },
                {
                    "value": 0.99,
                    "n": 1,
                    "scriptPubKey": {
                        "asm": "OP_DUP OP_HASH160 ...",
                        "hex": "76a914...",
                        "reqSigs": 1,
                        "type": "pubkeyhash",
                        "addresses": [
                            f"radiantchange{height}{tx_index}"
                        ]
                    }
                }
            ],
            "hex": "0200000001...",
            "blockhash": f"000000000000000000{height:010d}",
            "confirmations": 1000000 - height + 1,
            "time": int(time.time()) - (1000000 - height) * 600,
            "blocktime": int(time.time()) - (1000000 - height) * 600
        }
    
    def get_mempool_contents(self) -> Dict[str, Any]:
        """Get mempool contents."""
        return {
            f"mptx{i}": {
                "vsize": 300,
                "weight": 1200,
                "fee": 0.0001,
                "time": int(time.time()) - i * 10,
                "height": 1000000,
                "descendantcount": 1,
                "descendantsize": 300,
                "descendantfees": 10000,
                "ancestorcount": 1,
                "ancestorsize": 300,
                "ancestorfees": 10000,
            } for i in range(10)
        }
    
    def get_address_utxos(self, address: str) -> List[Dict[str, Any]]:
        """Get UTXOs for a given address."""
        # Create mock UTXOs for the address
        return [
            {
                "txid": f"tx{i}_999900",
                "vout": 0,
                "address": address,
                "scriptPubKey": "76a914...",
                "amount": 10.0 - i,
                "confirmations": 100 + i,
                "height": 999900 + i
            } for i in range(5)
        ]
    
    def decode_script(self, script_hex: str) -> Dict[str, Any]:
        """Decode a script hex into its ASM representation."""
        return {
            "asm": "OP_DUP OP_HASH160 ... OP_EQUALVERIFY OP_CHECKSIG",
            "type": "pubkeyhash",
            "reqSigs": 1,
            "addresses": [
                "radiantdevelopment1234"
            ],
            "p2sh": "abc123..."
        }
    
    # Helper methods for mock data generation
    
    def batch_rpc_calls(self, method_params_pairs, max_workers=None):
        """Execute a batch of RPC calls in parallel."""
        results = []
        for method, params in method_params_pairs:
            method_func = getattr(self, method, None)
            if method_func and callable(method_func):
                results.append(method_func(*params))
            else:
                results.append(None)
        return results
